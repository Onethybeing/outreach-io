"""File storage for CVs and dev-mode outbox emails: local disk in development, a GCS bucket on Cloud Run.

Cloud Run's filesystem is wiped on every restart, so anything that must survive (uploaded CVs,
.eml files) goes through here. Keys look like "resumes/<uuid>.pdf"; rows created before this module
may hold a plain local path, which LocalStorage still reads.
"""

from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class StorageError(Exception):
    pass


class Storage(Protocol):
    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def read(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        path = Path(key)
        if path.is_absolute() or path.exists():
            return path  # legacy rows / tests hold a full path
        return self.root / key

    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError(f"Could not read {key}: {exc}")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class GCSStorage:
    def __init__(self, bucket_name: str) -> None:
        from google.cloud import storage as gcs  # imported lazily: not needed for local development

        self.bucket = gcs.Client().bucket(bucket_name)

    def save(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.bucket.blob(key).upload_from_string(data, content_type=content_type)
        return key

    def read(self, key: str) -> bytes:
        try:
            return self.bucket.blob(key).download_as_bytes()
        except Exception as exc:  # noqa: BLE001 — google.api_core errors vary by failure
            raise StorageError(f"Could not read {key} from the bucket: {type(exc).__name__}")

    def exists(self, key: str) -> bool:
        return self.bucket.blob(key).exists()

    def delete(self, key: str) -> None:
        blob = self.bucket.blob(key)
        if blob.exists():
            blob.delete()


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "gcs":
        if not settings.gcs_bucket:
            raise RuntimeError("STORAGE_BACKEND=gcs needs GCS_BUCKET")
        return GCSStorage(settings.gcs_bucket)
    return LocalStorage(Path(settings.local_storage_dir))
