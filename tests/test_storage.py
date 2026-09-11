import pytest

from app import storage
from app.config import get_settings
from app.models import AuditLog, Resume, UserRole


def test_local_storage_round_trip_and_legacy_paths(tmp_path):
    local = storage.LocalStorage(tmp_path / "root")
    assert local.save("resumes/a.pdf", b"pdf-bytes", "application/pdf") == "resumes/a.pdf"
    assert (tmp_path / "root" / "resumes" / "a.pdf").read_bytes() == b"pdf-bytes"
    assert local.exists("resumes/a.pdf") and local.read("resumes/a.pdf") == b"pdf-bytes"

    legacy = tmp_path / "old" / "cv.pdf"  # rows saved before storage.py hold a full path
    legacy.parent.mkdir()
    legacy.write_bytes(b"old")
    assert local.exists(str(legacy)) and local.read(str(legacy)) == b"old"

    local.delete("resumes/a.pdf")
    assert not local.exists("resumes/a.pdf")
    with pytest.raises(storage.StorageError):
        local.read("resumes/a.pdf")


class FakeBlob:
    def __init__(self, bucket, key):
        self.bucket, self.key = bucket, key

    def upload_from_string(self, data, content_type=None):
        self.bucket.objects[self.key] = (data, content_type)

    def download_as_bytes(self):
        if self.key not in self.bucket.objects:
            raise RuntimeError("404 not found")
        return self.bucket.objects[self.key][0]

    def exists(self):
        return self.key in self.bucket.objects

    def delete(self):
        self.bucket.objects.pop(self.key)


class FakeBucket:
    def __init__(self, name):
        self.name, self.objects = name, {}

    def blob(self, key):
        return FakeBlob(self, key)


def test_gcs_storage_uses_the_bucket(monkeypatch):
    from google.cloud import storage as gcs

    buckets = {}

    class FakeClient:
        def bucket(self, name):
            return buckets.setdefault(name, FakeBucket(name))

    monkeypatch.setattr(gcs, "Client", FakeClient)
    store = storage.GCSStorage("outreach-files")
    store.save("outbox/x.eml", b"mail", "message/rfc822")
    assert buckets["outreach-files"].objects["outbox/x.eml"] == (b"mail", "message/rfc822")
    assert store.exists("outbox/x.eml") and store.read("outbox/x.eml") == b"mail"
    with pytest.raises(storage.StorageError):
        store.read("missing.pdf")
    store.delete("outbox/x.eml")
    store.delete("outbox/x.eml")  # deleting twice is fine
    assert not store.exists("outbox/x.eml")


def test_get_storage_picks_backend_from_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "gcs")
    monkeypatch.setattr(settings, "gcs_bucket", "")
    storage.get_storage.cache_clear()
    with pytest.raises(RuntimeError, match="GCS_BUCKET"):
        storage.get_storage()
    monkeypatch.setattr(settings, "storage_backend", "local")
    storage.get_storage.cache_clear()
    assert isinstance(storage.get_storage(), storage.LocalStorage)


def test_upload_stores_a_key_and_the_file(db, make_user, login, tmp_path):
    client = login(make_user(UserRole.operator))
    body = client.post("/resumes", files={"file": ("My CV.pdf", b"%PDF-1.4 test", "application/pdf")}).json()
    resume = db.get(Resume, __import__("uuid").UUID(body["id"]))
    assert resume.storage_path.startswith("resumes/") and resume.storage_path.endswith(".pdf")
    assert (tmp_path / resume.storage_path).read_bytes() == b"%PDF-1.4 test"
    assert resume.filename == "My CV.pdf"


def test_failed_upload_leaves_no_orphan_file(db, make_user, login, tmp_path, monkeypatch):
    client = login(make_user(UserRole.operator))
    real_commit = db.commit
    calls = {"n": 0}

    def failing_commit():
        calls["n"] += 1
        raise RuntimeError("database went away")

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        client.post("/resumes", files={"file": ("cv.pdf", b"%PDF-1.4", "application/pdf")})
    monkeypatch.setattr(db, "commit", real_commit)
    assert calls["n"] == 1
    assert not list((tmp_path / "resumes").glob("*.pdf")) if (tmp_path / "resumes").exists() else True
