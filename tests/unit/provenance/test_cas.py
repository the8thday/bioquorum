import pytest

from bioquorum.core.enums import DataKind
from bioquorum.core.hashing import sha256_bytes
from bioquorum.provenance import ContentStore, IntegrityError, file_ref


def test_put_and_get_roundtrip(tmp_path):
    cas = ContentStore(tmp_path / "cas")
    ref = cas.put_bytes(b"hello", kind=DataKind.FIGURE)
    assert ref.content_hash == sha256_bytes(b"hello")
    assert ref.uri == f"cas://{ref.content_hash}" and ref.size == 5 and ref.kind is DataKind.FIGURE
    assert cas.path(ref.content_hash).relative_to(cas.root).parts[0] == ref.content_hash[:2]
    assert cas.get(ref) == b"hello"
    assert cas.verified_path(ref.content_hash) == cas.path(ref.content_hash)


def test_put_is_idempotent(tmp_path):
    cas = ContentStore(tmp_path)
    assert cas.put_bytes(b"x") == cas.put_bytes(b"x")
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1


def test_expect_mismatch_rejected(tmp_path):
    with pytest.raises(IntegrityError):
        ContentStore(tmp_path).put_bytes(b"x", expect="0" * 64)


def test_corruption_detected_on_read(tmp_path):
    cas = ContentStore(tmp_path)
    ref = cas.put_bytes(b"data")
    cas.path(ref.content_hash).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        cas.get(ref)
    with pytest.raises(LookupError):
        cas.verified_path(ref.content_hash)


def test_put_file_and_file_ref_agree(tmp_path):
    src = tmp_path / "a.csv"
    src.write_text("g,v\na,1\n")
    cas = ContentStore(tmp_path / "cas")
    stored = cas.put_file(src)
    in_place = file_ref(src)
    assert stored.content_hash == in_place.content_hash
    assert in_place.uri.startswith("file://") and stored.uri.startswith("cas://")
    assert not list((tmp_path / "cas").glob(".tmp-*"))


def test_verified_path_rejects_malformed_hash_before_resolving_path(tmp_path, monkeypatch):
    store = ContentStore(tmp_path)

    def unexpected_path(_value):
        pytest.fail("Invalid digest reached filesystem resolution")

    monkeypatch.setattr(store, "path", unexpected_path)
    for digest in ("../private", "A" * 64, "a" * 63, "/private"):
        with pytest.raises(LookupError, match="Invalid recorded content hash"):
            store.verified_path(digest)
