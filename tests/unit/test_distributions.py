import pytest
from scripts.check_distributions import check_member


@pytest.mark.parametrize("wheel", [True, False])
def test_chem_worker_virtual_home_and_private_paths(wheel):
    root = "" if wheel else "bioquorum-0.7.0.dev0/src/"
    worker = root + "bioquorum/server/web/chem-worker-example.js"
    virtual = b"A.mkdir(`/home/web_user`); const env={HOME:`/home/web_user`}"
    private = [b"/home/", b"/Users/"]

    check_member(worker, virtual, wheel=wheel, private_paths=private)
    for suffix in (b'"/home/researcher"', b'"/home/web_user/private"', b'"/Users/researcher"'):
        with pytest.raises(ValueError):
            check_member(worker, virtual + suffix, wheel=wheel, private_paths=private)
    with pytest.raises(ValueError):
        check_member(root + "bioquorum/other.js", virtual, wheel=wheel, private_paths=private)


def test_publication_tag_must_identify_the_built_version():
    from scripts.check_distributions import check_release_tag

    check_release_tag("v1.0.0rc1", "1.0.0rc1")
    with pytest.raises(ValueError, match="tag"):
        check_release_tag("v1.0.0", "1.0.0rc1")
