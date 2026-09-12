"""LinkedIn profile scrape via BrightData's Web Scraper API (async: trigger → poll → download).

Checked live: one profile takes ~60s and returns current_company {name, link, company_id}.
"""

import time
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app import httpclient, telemetry, vault
from app.discovery.normalize import linkedin_company_slug

BASE = "https://api.brightdata.com/datasets/v3"
PEOPLE_DATASET = "gd_l1viktl72bvl7bjuj0"  # "LinkedIn people profiles"
POLL_SECONDS = 10
MAX_WAIT_SECONDS = 600


class ProfileError(Exception):
    pass


@dataclass
class Profile:
    name: str | None
    current_company: str | None
    current_company_slug: str | None
    current_company_url: str | None
    position: str | None


def _check(response: httpx.Response, step: str) -> None:
    if response.status_code == 401:
        raise ProfileError("BrightData rejected the API key. Check Settings > Vault")
    if response.status_code in (402, 403):
        raise ProfileError(f"BrightData refused the {step} (HTTP {response.status_code}). Check account billing/access")
    if response.status_code >= 400:
        raise ProfileError(f"BrightData {step} failed (HTTP {response.status_code})")


def parse_record(record: dict) -> Profile:
    company = record.get("current_company") or {}
    link = company.get("link")
    slug = (company.get("company_id") or "").strip().lower() or linkedin_company_slug(link)
    name = (company.get("name") or record.get("current_company_name") or "").strip() or None
    return Profile(
        name=record.get("name"),
        current_company=name,
        current_company_slug=slug,
        current_company_url=f"https://www.linkedin.com/company/{slug}" if slug else None,
        position=(record.get("position") or "").strip() or None,
    )


def scrape_profile(db: Session, linkedin_url: str) -> Profile:
    headers = {"Authorization": f"Bearer {vault.get_credential(db, 'brightdata')['api_key']}"}
    with telemetry.observation("brightdata_profile", as_type="tool", input={"url": linkedin_url}) as obs:
        try:
            trigger = httpclient.post(
                f"{BASE}/trigger", headers=headers, params={"dataset_id": PEOPLE_DATASET, "format": "json"},
                json=[{"url": linkedin_url}], timeout=60,
            )
            _check(trigger, "scrape request")
            snapshot_id = trigger.json().get("snapshot_id")
            if not snapshot_id:
                raise ProfileError("BrightData returned no snapshot id")

            waited = 0
            while True:
                telemetry.heartbeat()
                progress = httpclient.get(f"{BASE}/progress/{snapshot_id}", headers=headers, timeout=60)
                _check(progress, "progress check")
                status = progress.json().get("status")
                if status == "ready":
                    break
                if status == "failed":
                    raise ProfileError("BrightData could not scrape this profile")
                if waited >= MAX_WAIT_SECONDS:
                    raise ProfileError(f"BrightData scrape still not ready after {MAX_WAIT_SECONDS // 60} minutes")
                time.sleep(POLL_SECONDS)
                waited += POLL_SECONDS

            download = httpclient.get(f"{BASE}/snapshot/{snapshot_id}", headers=headers, params={"format": "json"}, timeout=120)
            _check(download, "download")
        except httpx.HTTPError as exc:
            raise ProfileError(f"Could not reach BrightData: {type(exc).__name__}")

        records = download.json()
        record = records[0] if isinstance(records, list) and records else records if isinstance(records, dict) else {}
        if not record or (record.get("error") and not record.get("name")):
            raise ProfileError(f"LinkedIn profile unavailable: {record.get('error') or 'empty result'}")
        telemetry.count("brightdata_profiles")
        profile = parse_record(record)
        obs.update(output=profile.__dict__)
        return profile
