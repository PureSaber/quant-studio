"""Launch the configured optional Lab on loopback with explicit authentication."""

import argparse
import os
import subprocess
from pathlib import Path

from quant_studio.notebooks import notebook_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8890)
    args = parser.parse_args(argv)
    token = args.token_file.resolve()
    if not token.is_file() or not token.read_text(encoding="utf-8").strip():
        parser.error("Provide a nonempty private Jupyter token file")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be 1024..65535")
    config = notebook_config()
    root = args.runs_root.resolve() / ".notebooks"
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JUPYTER_TOKEN_FILE=str(token), PYTHONUTF8="1")
    # The URL in the Studio configuration may point to the operator's HTTPS proxy;
    # this launcher always binds locally and never weakens existing access control.
    return subprocess.call(
        [
            config["python"],
            "-I",
            "-X",
            "utf8",
            "-m",
            "jupyterlab",
            "--ServerApp.ip=127.0.0.1",
            f"--ServerApp.port={args.port}",
            "--ServerApp.port_retries=0",
            "--ServerApp.open_browser=False",
            f"--ServerApp.root_dir={root}",
        ],
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
