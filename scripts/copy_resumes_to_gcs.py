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

from app.db import SessionLocal  # noqa: E402
from app.models import Resume  # noqa: E402

BUCKET = "outreach-io-sj26-files"
ACCOUNT = "sourav.jhinjha@gmail.com"


def main() -> None:
    apply = "--apply" in sys.argv
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    with SessionLocal() as db:
        for resume in db.scalars(select(Resume)):
            local = Path(resume.storage_path)
            if resume.storage_path.startswith("resumes/") and not local.exists():
                print(f"skip {resume.filename}: already a bucket key ({resume.storage_path})")
                continue
            if not local.is_file():
                print(f"skip {resume.filename}: local file missing ({resume.storage_path})")
                continue
            key = f"resumes/{local.name}"
            print(f"{'copy' if apply else 'would copy'} {resume.filename}: {local} -> gs://{BUCKET}/{key}")
            if not apply:
                continue
            upload = subprocess.run([gcloud, "storage", "cp", str(local), f"gs://{BUCKET}/{key}", f"--account={ACCOUNT}"],
                                    capture_output=True, text=True)
            if upload.returncode:
                print(f"  upload failed, row unchanged: {upload.stderr.strip()[-200:]}")
                continue
            size = subprocess.run([gcloud, "storage", "objects", "describe", f"gs://{BUCKET}/{key}",
                                   "--format=value(size)", f"--account={ACCOUNT}"], capture_output=True, text=True)
            if size.returncode or size.stdout.strip() != str(local.stat().st_size):
                print("  size check failed, row unchanged")
                continue
            resume.storage_path = key
            db.commit()
            print(f"  done: row now points at {key}")


if __name__ == "__main__":
    main()
