"""Launch a candidate entry point and read an offline record through its HTTP interface."""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile


def smoke(command: list[str]) -> None:
    fixture = Path(__file__).resolve().parents[1] / "examples/v1/projects/review.zip"
    with TemporaryDirectory(prefix="bq-wheel-") as directory:
        root = Path(directory)
        with ZipFile(fixture) as archive:
            archive.extractall(root)
        log = root / "startup.log"
        with log.open("wb") as output:
            process = subprocess.Popen(
                [*command, "--no-browser"],
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        try:
            for _ in range(300):
                if process.poll() is not None:
                    raise RuntimeError(f"Workbench exited with code {process.returncode}")
                ready = re.search(r"http://127\.0\.0\.1:\d+/", log.read_text(errors="replace"))
                if ready and "Application startup complete" in log.read_text(errors="replace"):
                    break
                time.sleep(0.2)
            else:
                raise TimeoutError("The workbench did not report its local URL")
            base = ready.group()
            with urlopen(base, timeout=10) as response:
                page = response.read()
                assert b"BioQuorum" in page and b'href="/assets/logo.svg"' in page
            with urlopen(base + "assets/logo.svg", timeout=10) as response:
                assert response.headers.get_content_type() == "image/svg+xml"
                assert b"<svg" in response.read()
            with urlopen(base + "api/sessions", timeout=10) as response:
                sessions = json.load(response)
            assert "Metformin evidence review" in json.dumps(sessions)
            print("Packaged workbench opened the offline project over HTTP.")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)


if __name__ == "__main__":
    smoke(sys.argv[1:])
