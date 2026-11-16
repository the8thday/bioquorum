from datetime import UTC

from bioquorum.core.ids import id_time, is_id, new_id, utc_now


def test_new_id_is_valid_ulid():
    i = new_id()
    assert len(i) == 26
    assert is_id(i)
    assert not is_id("not-an-id")


def test_ids_strictly_increase_within_process():
    ids = [new_id() for _ in range(2000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_id_time_is_utc_and_close_to_now():
    before = utc_now()
    t = id_time(new_id())
    assert t.tzinfo is UTC
    assert abs((t - before).total_seconds()) < 2


def test_utc_now_has_millisecond_precision():
    assert utc_now().microsecond % 1000 == 0
