import re
from urllib.parse import unquote, urlparse

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|gmbh|corp|corporation|co|pvt|private|plc|sa|bv)\b\.?", re.I
)


def linkedin_profile_url(url: str | None) -> str | None:
    """Canonical https://www.linkedin.com/in/<slug>, or None if it isn't a personal profile URL.

    Collapses country subdomains (uk.linkedin.com), query strings, trailing paths and case, so the
    same person always dedupes to the same string.
    """
    if not url:
        return None
    parsed = urlparse(url.strip() if "://" in url else f"https://{url.strip()}")
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0].lower() != "in":
        return None
    slug = unquote(parts[1]).strip().lower()
    if not slug:
        return None
    return f"https://www.linkedin.com/in/{slug}"


def company_key(name: str | None) -> str:
    """Loose key for spotting the same company twice: 'Acme Vector, Inc.' → 'acmevector'."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", _LEGAL_SUFFIXES.sub("", name.lower()))


# Company name after "at"/"@", up to a separator. A dash only separates when spaced
# ("Beta Labs - hiring"), so hyphenated names ("Hugging-Face") stay whole.
_TITLE_COMPANY = re.compile(r"(?:\bat\b|@)\s*(.+?)(?=\s[-–—]\s|[|,·()]|$)", re.I)
_GENERIC_SUFFIXES = {
    "ai", "labs", "lab", "hq", "io", "app", "tech", "technologies", "technology", "health",
    "software", "systems", "group", "global", "computing", "inc", "co",
}


def _same_company(named: str, target: str) -> bool:
    """Equal, or one name is the other plus a generic tail, in either direction.

    'Multiverse' ~ 'Multiverse Computing', 'Hippocratic AI' ~ 'Hippocratic' — but not
    'Sapling Says' ~ 'Sapling', 'Meta' ~ 'Metaview', 'Scale' ~ 'Upscale'.
    """
    if named == target:
        return True
    shorter, longer = sorted((named, target), key=len)
    return longer.startswith(shorter) and longer[len(shorter):] in _GENERIC_SUFFIXES


def title_names_other_company(title: str | None, startup_name: str, website: str | None = None) -> bool:
    """True when a title like 'Founder at Sapling Says' clearly names a company other than the startup.

    Reads only the first 'at X' / '@X'. No company in the title → False (can't tell, so keep).
    """
    match = _TITLE_COMPANY.search(title or "")
    if not match:
        return False
    named = company_key(match.group(1))
    if not named:
        return False
    targets = [k for k in (company_key(startup_name), company_key(website_domain(website).split(".")[0])) if k]
    return not any(_same_company(named, target) for target in targets)


def website_domain(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip() if "://" in url else f"https://{url.strip()}")
    host = (parsed.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host
