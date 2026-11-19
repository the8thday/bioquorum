"""Kernel isolation plan (ADR-017): pure argument building and policy, no processes."""

import pytest

from bioquorum.runtime.manager import KernelManager, jupyter_factory
from bioquorum.runtime.sandbox import (
    Isolation,
    IsolationUnavailable,
    SandboxSpec,
    bwrap_argv,
    choose_isolation,
    interpreter_mounts,
    scrub_env,
)


def _pairs(argv: list[str], flag: str) -> list[tuple[str, ...]]:
    """Arguments following each occurrence of ``flag`` (two for binds, one for tmpfs)."""
    width = 1 if flag in ("--tmpfs", "--dir", "--chdir") else 2
    return [tuple(argv[i + 1 : i + 1 + width]) for i, a in enumerate(argv) if a == flag]


def test_network_is_off_by_default_and_env_is_cleared(tmp_path):
    argv = bwrap_argv(SandboxSpec(workdir=tmp_path), executable="/usr/bin/python3")
    assert argv[0] == "bwrap"
    assert "--unshare-all" in argv and "--share-net" not in argv
    assert "--clearenv" in argv and "--die-with-parent" in argv
    assert argv[-1] == "--"


def test_network_only_when_asked(tmp_path):
    argv = bwrap_argv(SandboxSpec(workdir=tmp_path, network=True), executable="/usr/bin/python3")
    assert "--share-net" in argv


def test_workdir_writable_data_read_only_state_hidden(tmp_path):
    work = tmp_path / "proj"
    state = work / ".bioquorum"
    state.mkdir(parents=True)
    cfg = work / "bioquorum.toml"
    cfg.write_text("")
    data = tmp_path / "data"
    data.mkdir()
    sock = tmp_path / "sock"
    sock.mkdir()
    argv = bwrap_argv(
        SandboxSpec(workdir=work, read_only=(data, cfg), hidden=(state,)),
        executable="/usr/bin/python3",
        writable=(sock,),
    )
    assert (str(work), str(work)) in _pairs(argv, "--bind")
    assert (str(sock), str(sock)) in _pairs(argv, "--bind")
    ro = _pairs(argv, "--ro-bind")
    assert (str(data), str(data)) in ro and (str(cfg), str(cfg)) in ro
    assert (str(state),) in _pairs(argv, "--tmpfs")
    assert _pairs(argv, "--chdir") == [(str(work),)]
    # Later mounts win: protection must come after the writable project bind.
    assert argv.index(str(state)) > argv.index(str(work))
    assert argv.index(str(cfg)) > argv.index(str(work))


def test_missing_protected_paths_are_skipped(tmp_path):
    argv = bwrap_argv(
        SandboxSpec(workdir=tmp_path, read_only=(tmp_path / "nope",), hidden=(tmp_path / "gone",)),
        executable="/usr/bin/python3",
    )
    assert str(tmp_path / "nope") not in argv and str(tmp_path / "gone") not in argv


def test_secrets_never_reach_the_kernel_env(tmp_path):
    argv = bwrap_argv(SandboxSpec(workdir=tmp_path), executable="/usr/bin/python3")
    names = [p[0] for p in _pairs(argv, "--setenv")]
    assert "HOME" in names and "PATH" in names
    assert not any("KEY" in n or "TOKEN" in n for n in names)


def test_scrub_env_drops_secret_looking_names():
    env = scrub_env(
        {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-x",
            "HF_TOKEN": "t",
            "DB_PASSWORD": "p",
            "AWS_SECRET_ACCESS_KEY": "s",
            "LANG": "C",
        }
    )
    assert env == {"PATH": "/usr/bin", "LANG": "C"}


def test_interpreter_mounts_follow_symlinked_installs(tmp_path):
    real = tmp_path / "py-3.12.1"
    (real / "bin").mkdir(parents=True)
    exe = real / "bin" / "python3.12"
    exe.write_text("")
    alias = tmp_path / "py-3.12"
    alias.symlink_to(real)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(alias / "bin" / "python3.12")

    dirs, links = interpreter_mounts(str(venv / "bin" / "python"))
    assert venv in dirs and real in dirs
    # The alias directory is not inside any mounted dir, so it must be recreated.
    assert (str(real), str(alias)) in links
    # Links that live inside a mounted dir come along with it.
    assert all(link != str(venv / "bin" / "python") for _, link in links)


def test_choose_isolation():
    spec = SandboxSpec(workdir="/p")
    assert choose_isolation(spec, available=True, required=True) is Isolation.BWRAP
    assert (
        choose_isolation(SandboxSpec(workdir="/p", network=True), available=True, required=False)
        is Isolation.BWRAP_NET
    )
    assert choose_isolation(spec, available=False, required=False) is Isolation.NONE
    assert choose_isolation(None, available=True, required=False) is Isolation.NONE


def test_macos_requires_an_explicit_supported_offline_profile():
    spec = SandboxSpec(workdir="/p", macos=True)
    assert (
        choose_isolation(spec, available=False, required=True, macos_supported=True)
        is Isolation.SEATBELT
    )
    with pytest.raises(IsolationUnavailable):
        choose_isolation(
            SandboxSpec(workdir="/p", macos=True, network=True),
            available=False,
            required=True,
            macos_supported=True,
        )
    assert (
        choose_isolation(
            SandboxSpec(workdir="/p"), available=False, required=False, macos_supported=True
        )
        is Isolation.NONE
    )
    with pytest.raises(IsolationUnavailable):
        choose_isolation(spec, available=False, required=True)
    with pytest.raises(IsolationUnavailable):
        choose_isolation(None, available=True, required=True)


@pytest.mark.anyio
async def test_factory_refuses_before_starting_anything(tmp_path, monkeypatch):
    monkeypatch.setattr("bioquorum.runtime.sandbox.bwrap_available", lambda: False)
    factory = jupyter_factory(
        tmp_path, sandbox=SandboxSpec(workdir=tmp_path), require_isolation=True
    )
    with pytest.raises(IsolationUnavailable):
        await KernelManager(factory).get("s1")


def test_mcp_command_has_no_implicit_sibling_grant(tmp_path):
    command = tmp_path / "helper"
    argv = bwrap_argv(
        SandboxSpec(workdir=tmp_path / "private", standalone=True), executable=str(command)
    )
    assert (str(command), str(command)) in _pairs(argv, "--ro-bind")
    assert (str(tmp_path), str(tmp_path)) not in _pairs(argv, "--ro-bind")


def test_project_local_kernel_environment_is_write_protected(tmp_path):
    from bioquorum.runtime.sandbox import seatbelt_argv

    prefix = tmp_path / ".pixi/envs/default"
    profile = seatbelt_argv(
        SandboxSpec(workdir=tmp_path), executable=str(prefix / "bin/python"), writable=()
    )[2]
    assert f'(deny file-write* (subpath "{prefix.resolve()}"))' in profile
