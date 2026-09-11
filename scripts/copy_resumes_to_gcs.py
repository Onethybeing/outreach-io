"""One-off: copy CVs stored on this machine's disk into the GCS bucket and point their rows at it.

    .venv/Scripts/python scripts/copy_resumes_to_gcs.py            # dry run: shows what would change
    .venv/Scripts/python scripts/copy_resumes_to_gcs.py --apply    # upload, verify, then update rows

Uses the gcloud CLI (with an explicit --account), so no local application-default credentials are needed.
A row is only updated after its object is confirmed in the bucket with the same size.
"""

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Resume  # noqa: E402

BUCKET = "outreach-io-sj26-files"
ACCOUNT = "sourav.jhinjha@gmail.com"


def _bucket_size(gcloud: str, key: str) -> int | None:
    """Size of the object in the bucket, or None if it isn't there."""
    result = subprocess.run([gcloud, "storage", "objects", "describe", f"gs://{BUCKET}/{key}",
                             "--format=value(size)", f"--account={ACCOUNT}"], capture_output=True, text=True)
    size = result.stdout.strip()
    return int(size) if result.returncode == 0 and size.isdigit() else None


def main() -> None:
    apply = "--apply" in sys.argv
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    local_root = Path(get_settings().local_storage_dir)
    with SessionLocal() as db:
        for resume in db.scalars(select(Resume)):
            # Old rows hold a full local path; rows uploaded with the local backend hold a key like
            # resumes/<uuid>.pdf whose file lives under LOCAL_STORAGE_DIR. Check both places.
            local = next((p for p in (Path(resume.storage_path), local_root / resume.storage_path) if p.is_file()), None)
            key = resume.storage_path if resume.storage_path.startswith("resumes/") else f"resumes/{Path(resume.storage_path).name}"
            in_bucket = _bucket_size(gcloud, key)

            if local is None:
                state = "in the bucket only" if in_bucket is not None else "MISSING locally and in the bucket"
                print(f"skip {resume.filename}: {state} ({key})")
                continue
            if in_bucket == local.stat().st_size and resume.storage_path == key:
                print(f"skip {resume.filename}: already in the bucket ({key})")
                continue

            print(f"{'copy' if apply else 'would copy'} {resume.filename}: {local} -> gs://{BUCKET}/{key}")
            if not apply:
                continue
            if in_bucket != local.stat().st_size:
                upload = subprocess.run([gcloud, "storage", "cp", str(local), f"gs://{BUCKET}/{key}", f"--account={ACCOUNT}"],
                                        capture_output=True, text=True)
                if upload.returncode:
                    print(f"  upload failed, row unchanged: {upload.stderr.strip()[-200:]}")
                    continue
                if _bucket_size(gcloud, key) != local.stat().st_size:
                    print("  size check failed, row unchanged")
                    continue
            resume.storage_path = key
            db.commit()
            print(f"  done: row now points at {key}")


if __name__ == "__main__":
    main()
