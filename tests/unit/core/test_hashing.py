from bioquorum.core.hashing import (
    canonical_json,
    cas_relpath,
    code_hash,
    env_lock_hash,
    hash_object,
    normalize_code,
    register_serializer,
    run_key,
    sha256_bytes,
    sha256_file,
    sha256_text,
)


def test_sha256_helpers(tmp_path):
    assert sha256_bytes(b"abc") == sha256_text("abc")
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc")
    assert sha256_file(f) == sha256_bytes(b"abc")


def test_normalize_code_ignores_trailing_whitespace_and_newline_style():
    a = "x = 1   \r\nprint(x)\r\n\r\n"
    b = "x = 1\nprint(x)"
    assert normalize_code(a) == normalize_code(b) == "x = 1\nprint(x)\n"
    assert code_hash(a) == code_hash(b)
    assert code_hash("x = 1") != code_hash("x = 2")


def test_run_key_is_order_invariant_and_sensitive():
    ch = code_hash("1+1")
    k1 = run_key(ch, env_lock_hash("lock"), ["b", "a", "a"])
    k2 = run_key(ch, env_lock_hash("lock"), ["a", "b"])
    assert k1 == k2
    assert run_key(ch, env_lock_hash("lock2"), ["a", "b"]) != k1
    assert run_key(ch, None, ["a", "b"]) != k1


def test_hash_object_kinds():
    assert hash_object(b"x").serializer == "bytes" and not hash_object(b"x").weak
    assert hash_object("x").serializer == "text"
    j = hash_object({"b": 1, "a": [1, 2]})
    assert j.serializer == "json" and not j.weak
    assert j.hash == sha256_bytes(canonical_json({"a": [1, 2], "b": 1}))
    weak = hash_object(object())
    assert weak.weak and weak.serializer == "pickle"


def test_registered_serializer_takes_precedence():
    class Thing:
        def __init__(self, v):
            self.v = v

    register_serializer("thing", lambda o: isinstance(o, Thing), lambda o: str(o.v).encode())
    h = hash_object(Thing(7))
    assert h.serializer == "thing" and h.hash == sha256_text("7") and not h.weak


def test_cas_relpath():
    assert str(cas_relpath("abcdef")) == "ab/abcdef"
