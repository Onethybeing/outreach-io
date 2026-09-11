"""Apollo: company enrichment (works on the free plan) and email lookup (paid plans only).

Checked live on this account: organizations/enrich → 200; people/match and mixed_people/api_search
→ 403 API_INACCESSIBLE ("not included in your Free plan").
"""

import logging
import re
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app import telemetry, vault
from app.discovery.normalize import linkedin_profile_url, names_match, website_domain

logger = logging.getLogger(__name__)
BASE = "https://api.apollo.io/api/v1"


class ProviderUnavailable(Exception):
    """Asking is impossible right now (plan, key). Don't retry per contact."""


class EmailLookupError(Exception):
    """This one lookup failed (network, rate limit). Retryable."""


@dataclass
class EmailResult:
    found: bool
    email: str | None
    note: str
    # A free, later-fixable miss (e.g. "not saved in Apollo yet"): recorded as failed so it can be
    # retried, instead of not_found which blocks re-lookup and counts as a paid miss.
    retryable: bool = False


def _headers(db: Session) -> dict:
    return {"X-Api-Key": vault.get_credential(db, "apollo")["api_key"], "Cache-Control": "no-cache"}


@dataclass
class OrgLookup:
    org: dict | None  # {'name', 'linkedin_url', 'website_url'}
    definitive: bool  # Apollo answered (found or not). False → transient problem; try again later.


def enrich_org(db: Session, website: str | None, name: str | None = None) -> OrgLookup:
    """Company lookup by domain if known, else by name. Never raises.

    A name lookup can return a different company with the same name, so it's only trusted when the
    returned name matches (checked live: domain and name lookups both work on the free plan).
    """
    domain = website_domain(website)
    params = {"domain": domain} if domain else {"name": name} if name else None
    if not params:
        return OrgLookup(None, definitive=True)
    try:
        with telemetry.observation("apollo_org_enrich", as_type="tool", input=params) as obs:
            telemetry.heartbeat()
            response = httpx.get(f"{BASE}/organizations/enrich", headers=_headers(db), params=params, timeout=30)
            obs.update(output={"status": response.status_code})
            if response.status_code in (404, 422):
                return OrgLookup(None, definitive=True)
            if response.status_code != 200:
                return OrgLookup(None, definitive=False)  # rate limit, auth, server error
            org = response.json().get("organization") or {}
            result = {"name": org.get("name"), "linkedin_url": org.get("linkedin_url"), "website_url": org.get("website_url")}
            obs.update(output=result)
            if not domain and not names_match(result["name"], name or ""):
                return OrgLookup(None, definitive=True)
            return OrgLookup(result if result["name"] or result["linkedin_url"] else None, definitive=True)
    except (httpx.HTTPError, vault.VaultError, ValueError, KeyError):
        logger.warning("Apollo organization enrich failed for %s", params, exc_info=True)
        return OrgLookup(None, definitive=False)


def find_email(db: Session, name: str, linkedin_url: str, website: str | None) -> EmailResult:
    body = {
        "linkedin_url": linkedin_url,
        "name": name,
        "domain": website_domain(website) or None,
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
    }
    with telemetry.observation("apollo_people_match", as_type="tool", input={"linkedin_url": linkedin_url}) as obs:
        try:
            headers = _headers(db)
        except (vault.VaultError, KeyError) as exc:
            raise ProviderUnavailable(f"Apollo is not configured — add its key in Settings → Vault ({exc})")
        try:
            telemetry.heartbeat()
            response = httpx.post(f"{BASE}/people/match", headers=headers, json=body, timeout=30)
        except httpx.HTTPError as exc:
            raise EmailLookupError(f"Could not reach Apollo: {type(exc).__name__}")

        if response.status_code == 403:
            try:
                code = response.json().get("error_code")
            except ValueError:
                code = None
            if code == "API_INACCESSIBLE":
                raise ProviderUnavailable(
                    "Apollo's current plan doesn't include email lookup. Upgrade Apollo, switch the "
                    "email provider in Settings, or enter the email manually."
                )
            raise ProviderUnavailable("Apollo refused the request (HTTP 403)")
        if response.status_code == 401:
            raise ProviderUnavailable("Apollo rejected the API key — check Settings → Vault")
        if response.status_code == 429:
            raise EmailLookupError("Apollo rate limit reached — try again later")
        if response.status_code != 200:
            raise EmailLookupError(f"Apollo error (HTTP {response.status_code})")

        telemetry.count("apollo_email_lookups")
        try:
            person = response.json().get("person") or {}
        except (ValueError, AttributeError):
            raise EmailLookupError("Apollo returned an unreadable response")
        email = (person.get("email") or "").strip().lower()
        status = person.get("email_status")
        obs.update(output={"has_person": bool(person), "email_status": status, "has_email": bool(email)})
        # Apollo returns a placeholder like email_not_unlocked@domain.com when an email isn't revealed.
        if not person:
            return EmailResult(False, None, "Apollo has no record of this person")
        if not email or "not_unlocked" in email or "@" not in email:
            return EmailResult(False, None, f"Apollo has no email for this person (status: {status or 'unknown'})")
        return EmailResult(True, email, f"Found by Apollo (status: {status or 'unknown'})")


SAVED_SEARCH_PAGES = 3
SAVED_SEARCH_PER_PAGE = 100
_NAME_NOISE = re.compile(r"\([^)]*\)|\b(dr|mr|mrs|ms|prof|phd|mba|md|jr|sr|ii|iii|cfa|cpa|pmp)\b\.?", re.I)


def _clean_person_name(name: str) -> str:
    """'Dr. Jane Doe, PhD (she/her)' → 'jane doe'."""
    return " ".join(re.sub(r"[^\w\s'-]", " ", _NAME_NOISE.sub(" ", name.lower())).split())


def _search_saved_contacts(headers: dict, query: str, page: int) -> tuple[list[dict], int]:
    try:
        telemetry.heartbeat()
        response = httpx.post(f"{BASE}/contacts/search", headers=headers, timeout=30,
                              json={"q_keywords": query, "per_page": SAVED_SEARCH_PER_PAGE, "page": page})
    except httpx.HTTPError as exc:
        raise EmailLookupError(f"Could not reach Apollo: {type(exc).__name__}")
    if response.status_code == 401:
        raise ProviderUnavailable("Apollo rejected the API key — check Settings → Vault")
    if response.status_code == 403:
        raise ProviderUnavailable("Apollo refused the contacts search (HTTP 403)")
    if response.status_code == 429:
        raise EmailLookupError("Apollo rate limit reached — try again later")
    if response.status_code != 200:
        raise EmailLookupError(f"Apollo error (HTTP {response.status_code})")
    try:
        body = response.json()
        contacts = [c for c in (body.get("contacts") or []) if isinstance(c, dict)]
        total_pages = int((body.get("pagination") or {}).get("total_pages") or 1)
    except (ValueError, AttributeError, TypeError):
        raise EmailLookupError("Apollo returned an unreadable response")
    return contacts, total_pages


def _usable_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    return email if email and "@" in email and "not_unlocked" not in email else None


def find_email_in_saved_contacts(db: Session, name: str, linkedin_url: str, website: str | None) -> EmailResult:
    """Free-plan path: the user reveals an email on apollo.io (free credits) and saves the person as a
    contact; this finds that saved contact through contacts/search (checked live: allowed on the free plan).

    Match: same LinkedIn profile; otherwise the same full name with an email at the startup's domain.
    """
    try:
        headers = _headers(db)
    except (vault.VaultError, KeyError) as exc:
        raise ProviderUnavailable(f"Apollo is not configured — add its key in Settings → Vault ({exc})")

    target = linkedin_profile_url(linkedin_url)
    domain = website_domain(website)
    clean = _clean_person_name(name)
    # By name first; the startup's domain as a second query catches contacts saved under another
    # spelling of the name (searching matches emails/companies too). Several pages per query.
    queries = [q for q in (clean, domain) if q]
    saved: dict[str, dict] = {}
    with telemetry.observation("apollo_saved_contacts_search", as_type="tool", input={"queries": queries}) as obs:
        profile_found = False
        for query in queries:
            if profile_found:
                break  # the definitive match is in hand; further searches only cost requests
            for page in range(1, SAVED_SEARCH_PAGES + 1):
                batch, total_pages = _search_saved_contacts(headers, query, page)
                for c in batch:
                    # The same person comes back from both queries; dedupe on stable content, not object identity.
                    key = c.get("id") or linkedin_profile_url(c.get("linkedin_url")) or _usable_email(c.get("email")) \
                        or _clean_person_name(str(c.get("name") or ""))
                    saved.setdefault(str(key), c)
                if target and any(linkedin_profile_url(c.get("linkedin_url")) == target and _usable_email(c.get("email"))
                                  for c in batch):
                    profile_found = True
                    break
                if page >= total_pages:
                    break
        obs.update(output={"saved_contacts_seen": len(saved)})

    contacts = list(saved.values())
    by_profile = [c for c in contacts if target and linkedin_profile_url(c.get("linkedin_url")) == target]
    by_name = [
        c for c in contacts
        if _clean_person_name(str(c.get("name") or "")) == clean
        and (email := _usable_email(c.get("email"))) and domain and email.endswith("@" + domain)
    ]
    if by_profile:
        # A person saved twice: prefer the copy whose email was actually revealed.
        match = next((c for c in by_profile if _usable_email(c.get("email"))), by_profile[0])
    elif len({_usable_email(c.get("email")) for c in by_name}) == 1:
        match = by_name[0]  # one person (possibly saved twice with the same email), not an ambiguous name
    else:
        match = None
    if match is None:
        return EmailResult(False, None, "Not in your saved Apollo contacts yet — reveal the email on apollo.io, "
                                        "save the person, then look up again", retryable=True)
    email = _usable_email(match.get("email"))
    if not email:
        return EmailResult(False, None, "Saved in Apollo, but the email isn't revealed yet — reveal it on apollo.io, "
                                        "then look up again", retryable=True)
    telemetry.count("apollo_saved_contact_hits")
    return EmailResult(True, email, f"Found in saved Apollo contacts (status: {match.get('email_status') or 'unknown'})")
