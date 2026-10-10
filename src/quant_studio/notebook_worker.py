"""Notebook execution entry point for the optional, separately locked environment."""

import base64
import importlib.metadata
import json
import sys
from html import escape
from pathlib import Path


def environment_identity():
    return {
        "python": sys.executable,
        "version": sys.version,
        "packages": sorted(
            [
                {"name": d.metadata["Name"], "version": d.version}
                for d in importlib.metadata.distributions()
            ],
            key=lambda d: d["name"].lower(),
        ),
    }


def execute(directory, timeout):
    import nbformat
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager
    from nbclient import NotebookClient

    directory = Path(directory)
    kernel_root = directory / ".kernel"
    spec = kernel_root / "research"
    spec.mkdir(parents=True)
    (spec / "kernel.json").write_text(
        json.dumps(
            {
                "argv": [
                    sys.executable,
                    "-m",
                    "ipykernel_launcher",
                    "-f",
                    "{connection_file}",
                ],
                "display_name": "Frozen research environment",
                "language": "python",
            }
        ),
        encoding="utf-8",
    )
    environment = environment_identity()
    (directory / "environment.json").write_text(
        json.dumps(environment, indent=2), encoding="utf-8"
    )
    requested = json.loads(
        (directory / "requested-environment.json").read_text(encoding="utf-8")
    )
    if requested != environment:
        raise RuntimeError("Notebook environment changed after submission")
    notebook = nbformat.read(directory / "source.ipynb", as_version=4)
    manager = KernelManager(
        kernel_name="research",
        kernel_spec_manager=KernelSpecManager(
            kernel_dirs=[str(kernel_root)], ensure_native_kernel=False
        ),
    )
    client = NotebookClient(
        notebook,
        km=manager,
        timeout=timeout,
        allow_errors=False,
        record_timing=True,
        resources={"metadata": {"path": str(directory)}},
    )
    failed = False
    try:
        client.execute(cwd=str(directory))
    except Exception as exc:
        failed = True
        print(str(exc), file=sys.stderr)
    finally:
        nbformat.write(notebook, directory / "executed.ipynb")
        body = [
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            "<title>研究实验</title>",
            "<style>body{max-width:1000px;margin:32px auto;font:16px sans-serif}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;"
            "background:#f4f6f8;padding:16px}</style>",
            "<h1>Notebook研究实验</h1>",
            "<p>执行失败；以下保留已完成输出。</p>" if failed else "<p>执行完成。</p>",
        ]
        for cell in notebook.cells:
            body.append("<pre>" + escape(cell.source) + "</pre>")
            for output in cell.get("outputs", []):
                png = output.get("data", {}).get("image/png")
                if isinstance(png, str):
                    try:
                        raw = base64.b64decode(png, validate=True)
                    except ValueError:
                        raw = b""
                    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) <= 10_000_000:
                        encoded = base64.b64encode(raw).decode("ascii")
                        body.append(
                            '<img alt="研究图表" style="max-width:100%" '
                            'src="data:image/png;base64,' + encoded + '">'
                        )
                text = output.get("text", output.get("data", {}).get("text/plain", ""))
                if output.get("output_type") == "error":
                    text = output.get("ename", "") + ": " + output.get("evalue", "")
                body.append("<pre>" + escape(str(text)) + "</pre>")
        body.append("</html>")
        (directory / "report.html").write_text("".join(body), encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    if sys.argv[1:] == ["--environment"]:
        print(json.dumps(environment_identity()))
    else:
        raise SystemExit(execute(sys.argv[1], int(sys.argv[2])))
