"""OS-level isolation for kernels (ADR-017): bubblewrap, no network by default.

This module only plans: it builds the ``bwrap`` argument list and decides whether a kernel
may start. It starts no kernel itself, so everything here is unit-testable.

What the sandboxed kernel gets:
  * network: an empty network namespace unless ``SandboxSpec.network`` is set;
  * files: the project dir read-write; data dirs and the Python environment read-only;
    a private ``/tmp`` and ``$HOME``; nothing else from the user's home;
  * environment: cleared, then a short allowlist. API keys never reach kernel code.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path

SANDBOX_HOME = "/tmp/home"

_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64")
# Only what a Python process needs from /etc; the rest may hold host configuration.
_ETC = (
    "ld.so.cache",
    "ld.so.conf",
    "ld.so.conf.d",
    "alternatives",
    "passwd",
    "group",
    "nsswitch.conf",
    "localtime",
    "ssl",
    "ca-certificates",
    "fonts",
    "mime.types",
)
_ETC_NETWORK = ("resolv.conf", "hosts")
_SECRET_NAME = re.compile(
    r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)($|_)", re.IGNORECASE
)


class Isolation(StrEnum):
    """How a kernel is isolated. Recorded on every ``run_started`` event."""

    BWRAP = "bwrap"  # sandboxed, no network
    BWRAP_NET = "bwrap+net"  # sandboxed files and env, network allowed
    SEATBELT = "seatbelt"
    NONE = "none"  # not isolated


class IsolationUnavailable(RuntimeError):  # noqa: N818
    """Isolation is required for this project but cannot be provided. Never downgrade."""


@dataclass(frozen=True)
class SandboxSpec:
    workdir: str | Path  # read-write, and the kernel's working directory
    read_only: Sequence[str | Path] = ()  # data dirs; files inside workdir to protect
    hidden: Sequence[str | Path] = ()  # replaced by an empty dir (or empty file)
    network: bool = False
    macos: bool = False
    standalone: bool = False


@cache
def bwrap_available() -> bool:
    """True if bubblewrap exists and unprivileged namespaces actually work here."""
    if not sys.platform.startswith("linux"):
        return False
    exe = shutil.which("bwrap")
    if exe is None:
        return False
    probe = [exe, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/"]
    probe += ["--proc", "/proc", "--dev", "/dev", "true"]
    try:
        return subprocess.run(probe, capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@cache
def seatbelt_available() -> bool:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        return False
    try:
        probe = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                "(version 1)(deny default)(allow process-exec)(allow file-read*)(allow sysctl-read)",
                "/usr/bin/true",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def choose_isolation(
    spec: SandboxSpec | None, *, available: bool, required: bool, macos_supported: bool = False
) -> Isolation:
    """Decide the isolation for a new kernel, or raise if the requirement cannot be met."""
    if spec is not None and available:
        return Isolation.BWRAP_NET if spec.network else Isolation.BWRAP
    if spec is not None and spec.macos and macos_supported:
        if spec.network:
            raise IsolationUnavailable(
                "The macOS isolation profile requires kernel network to be disabled."
            )
        return Isolation.SEATBELT
    if required:
        raise IsolationUnavailable(
            "This project requires an isolated kernel, but bubblewrap isolation is not "
            "available on this machine. Linux requires bubblewrap and user namespaces. "
            "macOS requires an enabled and available seatbelt profile."
        )
    return Isolation.NONE


def _seatbelt_path(path: str | Path) -> str:
    return f"(subpath {json.dumps(str(Path(path).resolve()), ensure_ascii=False)})"


def seatbelt_argv(
    spec: SandboxSpec,
    *,
    executable: str,
    writable: Sequence[str | Path],
) -> list[str]:
    """Use a default-deny profile with private Unix IPC and selected filesystem roots."""
    workdir = Path(spec.workdir).resolve()
    env_roots, _ = interpreter_mounts(executable, standalone=spec.standalone)
    read_roots = (
        workdir,
        *spec.read_only,
        *writable,
        *env_roots,
        "/System",
        "/usr",
        "/bin",
        "/sbin",
        "/Library/Apple/System",
        "/private/var/db/dyld",
        "/dev",
    )
    # macOS traverses system and interpreter symlinks before launching a kernel.
    # Permit metadata on those links, without exposing unrelated files by name.
    ancestors = {"/var", "/etc", "/tmp", "/private/var/select", "/private/var/select/sh"}
    for root in (*read_roots, executable):
        path = Path(root).absolute()
        for spelling in (path, path.resolve()):
            ancestors.update(str(parent) for parent in (spelling, *spelling.parents))
    _, hops = _interpreter_chain(Path(executable).absolute())
    for link, target in hops:
        for spelling in (link, target):
            ancestors.update(str(parent) for parent in (spelling, *spelling.parents))
    writes = (workdir, *writable)
    profile = [
        "(version 1)",
        "(deny default)",
        '(import "dyld-support.sb")',
        "(allow sysctl-read)",
        "(allow process-exec process-fork)",
        "(allow signal (target self))",
        "(allow file-read-metadata "
        + " ".join(f"(literal {json.dumps(p)})" for p in sorted(ancestors))
        + ")",
        "(allow file-read* file-map-executable "
        + " ".join(_seatbelt_path(root) for root in read_roots)
        + ")",
        "(allow file-write* "
        + " ".join(_seatbelt_path(root) for root in writes)
        + ' (literal "/dev/null"))',
    ]
    for path in writable:
        socket_policy = _seatbelt_path(path)
        profile.extend(
            (
                f"(allow network-bind (local unix-socket {socket_policy}))",
                f"(allow network-outbound (remote unix-socket {socket_policy}))",
                f"(allow network-inbound (local unix-socket {socket_policy}))",
            )
        )
    for path in (*spec.read_only, *env_roots):
        profile.append(f"(deny file-write* {_seatbelt_path(path)})")
    for path in spec.hidden:
        profile.append(f"(deny file-read* file-write* {_seatbelt_path(path)})")
    return ["/usr/bin/sandbox-exec", "-p", "\n".join(profile)]


def scrub_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Environment for a kernel that is *not* sandboxed: drop anything that looks secret."""
    return {k: v for k, v in environ.items() if not _SECRET_NAME.search(k)}


def _env_root(exe: Path) -> Path:
    return exe.parent.parent if exe.parent.name in ("bin", "Scripts") else exe.parent


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def _interpreter_chain(path: Path) -> tuple[Path, list[tuple[Path, Path]]]:
    hops: list[tuple[Path, Path]] = []  # (link, target)
    for _ in range(40):  # symlink loops end here
        link = next((p for p in [*reversed(path.parents), path] if p.is_symlink()), None)
        if link is None:
            break
        target = Path(os.path.normpath(link.parent / os.readlink(link)))
        hops.append((link, target))
        path = target / path.relative_to(link)
    return path, hops


def interpreter_mounts(
    executable: str, *, standalone: bool = False
) -> tuple[list[Path], list[tuple[str, str]]]:
    """Directories to mount read-only, and symlinks to recreate, so ``executable`` runs.

    A venv's ``bin/python`` is usually a chain of symlinks into a separate install (uv, pyenv,
    conda), sometimes through a symlinked directory. Returns ``(dirs, [(target, link), ...])``.
    """
    path = Path(os.path.abspath(executable))
    # Only the host interpreter has a known installation prefix. Other MCP
    # commands receive their executable and explicitly selected dependencies.
    if standalone and path != Path(sys.executable).absolute():
        return [path], []
    dirs = [_env_root(path)]
    path, hops = _interpreter_chain(path)
    root = _env_root(path)
    if root not in dirs:
        dirs.append(root)
    mounted = [*dirs, *(Path(d) for d in _SYSTEM_DIRS)]
    links = [(str(target), str(link)) for link, target in hops if not _inside(link, mounted)]
    return dirs, links


def bwrap_argv(
    spec: SandboxSpec,
    *,
    executable: str,
    writable: Sequence[str | Path] = (),
    bwrap: str = "bwrap",
) -> list[str]:
    """The ``bwrap ... --`` prefix to put in front of the kernel command line.

    ``writable`` are extra read-write paths (the kernel's socket directory). Order matters:
    later mounts cover earlier ones, so protection comes after the project bind.
    """
    workdir = str(Path(spec.workdir))
    argv = [bwrap, "--unshare-all", "--die-with-parent", "--clearenv"]
    if spec.network:
        argv.append("--share-net")
    for d in _SYSTEM_DIRS:
        argv += ["--ro-bind-try", d, d]
    for name in (*_ETC, *(_ETC_NETWORK if spec.network else ())):
        argv += ["--ro-bind-try", f"/etc/{name}", f"/etc/{name}"]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", SANDBOX_HOME]

    env_dirs, links = interpreter_mounts(executable, standalone=spec.standalone)
    for path in writable:
        argv += ["--bind", str(path), str(path)]
    argv += ["--bind", workdir, workdir]
    for d in env_dirs:
        argv += ["--ro-bind", str(d), str(d)]
    for target, link in links:
        argv += ["--symlink", target, link]
    for path in map(Path, spec.read_only):
        if path.exists():
            argv += ["--ro-bind", str(path), str(path)]
    for path in map(Path, spec.hidden):
        if path.is_dir():
            argv += ["--tmpfs", str(path)]
        elif path.exists():
            argv += ["--ro-bind", "/dev/null", str(path)]

    search = [str(Path(os.path.abspath(executable)).parent), "/usr/local/bin", "/usr/bin", "/bin"]
    for name, value in (
        ("HOME", SANDBOX_HOME),
        ("PATH", ":".join(search)),
        ("LANG", "C.UTF-8"),
        ("TMPDIR", "/tmp"),
    ):
        argv += ["--setenv", name, value]
    argv += ["--chdir", workdir, "--"]
    return argv
