"""Standalone research-bundle runner. No BioQuorum import or model access."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
import shutil
import sys
import time
from importlib.metadata import distributions
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent
IMAGES = ("image/png", "image/jpeg", "image/svg+xml")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_file(path: Path, expected: str) -> None:
    with path.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != expected:
            raise ValueError("File hash differs: " + path.relative_to(ROOT).as_posix())


def inside(name: str, root: Path = ROOT) -> Path:
    path = PurePosixPath(name)
    if (
        not path.parts
        or path.is_absolute()
        or "\\" in name
        or ":" in name
        or any(part in ("..", ".") for part in path.parts)
    ):
        raise ValueError("Unsafe bundle path")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Bundle path escapes its directory")
    return target


def environment() -> str:
    packages = sorted((dist.metadata["Name"] or "", dist.version) for dist in distributions())
    payload = [sys.version, platform.system(), platform.machine(), packages]
    return digest(json.dumps(payload).encode())


def verify(manifest: dict) -> None:
    if manifest["spec_version"] != "1":
        raise ValueError("Unsupported bundle version")
    for path, sha in manifest["files"].items():
        verify_file(inside(path), sha)
    for run in manifest["runs"]:
        referenced(manifest, run["code_path"])
    for rec in manifest["run_records"]:
        for sha in rec["inputs"].values():
            if manifest["files"].get("objects/" + sha) != sha:
                raise ValueError("An input is missing from the manifest")


def referenced(manifest: dict, name: str) -> Path:
    if name not in manifest["files"]:
        raise ValueError("A required file is not listed in the manifest")
    return inside(name)


def get_outputs(kernel, message: str) -> list[tuple[str, dict]]:
    output = []
    deadline = time.monotonic() + 600
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or len(output) > 100_000:
            raise RuntimeError("The replay exceeded its output limit or deadline")
        reply = kernel.get_iopub_msg(timeout=min(remaining, 60))
        if reply.get("parent_header", {}).get("msg_id") != message:
            continue
        kind, content = reply["msg_type"], reply["content"]
        if kind == "status" and content["execution_state"] == "idle":
            return output
        if kind == "error":
            raise RuntimeError(str(content.get("ename", "execution error")))
        output.append((kind, content))


def rerun(manifest: dict) -> list[dict]:
    from jupyter_client.manager import KernelManager

    if manifest["project"]["classification"] != "public":
        raise ValueError(
            "This standalone runner only executes public bundles; keep sensitive data in an isolated BioQuorum project"
        )
    runs = {run["run_id"]: run for run in manifest["runs"]}
    results = []
    for record in manifest["run_records"]:
        run_id = record["run_id"]
        run = runs[run_id]
        with TemporaryDirectory(prefix="bq-portable-") as temp:
            work = Path(temp)
            for name, sha in record["inputs"].items():
                target = inside(name, work)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(referenced(manifest, "objects/" + sha), target)
            # Use this interpreter even when a global python3 kernelspec is installed.
            if sys.platform == "win32":
                manager = KernelManager(kernel_name="python3")
            else:
                socket_prefix = str(work / "ipc")
                if len(socket_prefix) > 88:
                    raise ValueError("The temporary path is too long for local IPC")
                manager = KernelManager(
                    kernel_name="python3",
                    transport="ipc",
                    ip=socket_prefix,
                    connection_file=str(work / "connection.json"),
                )
            spec = manager.kernel_spec
            if spec is None:
                raise RuntimeError("Python kernelspec missing")
            spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
            env = {"PATH": str(Path(sys.executable).parent), "HOME": str(work)}
            manager.start_kernel(cwd=work, env=env)
            client = manager.client()
            try:
                client.start_channels()
                client.wait_for_ready(timeout=30)
                get_outputs(
                    client,
                    client.execute(referenced(manifest, "values.py").read_text(encoding="utf-8")),
                )
                for precursor_id in record.get("preceding_runs", []):
                    path = referenced(manifest, runs[precursor_id]["code_path"])
                    get_outputs(client, client.execute(path.read_text(encoding="utf-8")))
                parts = get_outputs(
                    client,
                    client.execute(
                        referenced(manifest, run["code_path"]).read_text(encoding="utf-8")
                    ),
                )
            finally:
                client.stop_channels()
                manager.shutdown_kernel(now=True)
        values = {}
        stdout = []
        figures = []
        for kind, item in parts:
            if kind == "stream" and item["name"] != "stderr":
                stdout.append(item["text"])
            elif kind in ("display_data", "execute_result"):
                data = item["data"]
                if "application/vnd.bioquorum.value+json" in data:
                    value = data["application/vnd.bioquorum.value+json"]
                    values[value["name"]] = (value["value"], value["unit"])
                image = next((mime for mime in IMAGES if mime in data), None)
                if image:
                    raw = data[image]
                    figures.append(
                        digest(raw.encode() if image == "image/svg+xml" else base64.b64decode(raw))
                    )
                else:
                    stdout.append(data.get("text/markdown", data.get("text/plain", "")))
        expected = {v["name"]: (v["value"], v["unit"]) for v in run["values"]}
        matched = (
            values == expected
            and figures == [ref["content_hash"] for ref in run["outputs"]]
            and (digest("".join(stdout).encode()) if any(stdout) else None)
            == record["stdout_sha256"]
        )
        results.append(
            {
                "run_id": run_id,
                "outputs_matched": matched,
                "environment_matched": bool(
                    record["environment"] and record["environment"] == environment()
                ),
                "values": values,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify or rerun a portable BioQuorum record.")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        verify(manifest)
        if args.verify_only:
            print("Bundle file hashes verified.")
        elif not manifest["run_records"]:
            parser.exit(1, "No computational runs are included. Use --verify-only.\n")
        else:
            results = rerun(manifest)
            print(json.dumps(results, indent=2))
            if not results or not all(r["outputs_matched"] for r in results):
                raise SystemExit(1)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Bundle could not be verified: {exc}\n")


if __name__ == "__main__":
    main()
