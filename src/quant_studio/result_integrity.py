"""One integrity gate for saved result pages, comparisons and downloads.

Hashes detect changes since collection; they do not certify economic validity.
Historical runs without a collection manifest are retained but not certified.
"""

import hashlib
import json
from pathlib import Path

from quant_studio import QuantStudioError


def output_hashes(directory):
    from quant_studio.runner import safe_run_file

    directory = Path(directory)
    paths = [p for p in directory.iterdir() if p.suffix in {".csv", ".html"}]
    paths += list((directory / "strategy-output").rglob("*"))
    # Freeze the basis of the displayed returns as well as their output bytes.
    paths += [directory / "request.json", *directory.glob("config.*")]
    if (directory / "provenance.json").is_file():
        paths.append(directory / "provenance.json")
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(
            safe_run_file(directory, p.relative_to(directory).as_posix()).read_bytes()
        ).hexdigest()
        for p in sorted(paths)
        if p.is_file() and p.suffix in {
            ".json", ".yaml", ".yml", ".csv", ".html", ".parquet", ".db", ".sqlite"
        }
    }


def verify_result(directory, result, *, asset=None):
    from quant_studio.runner import safe_run_file

    directory = Path(directory)
    if result.get("verification_status") == "failed":
        raise QuantStudioError("账本核验未通过")
    evidence = None
    if result.get("verification_status") == "pass":
        content = safe_run_file(directory, "verification.json").read_bytes()
        if hashlib.sha256(content).hexdigest() != result.get("verification_sha256"):
            raise QuantStudioError("账本核验记录已改变")
        evidence = json.loads(content)
        if not isinstance(evidence, dict):
            raise QuantStudioError("账本核验记录无效")
    manifest = result.get("output_hashes")
    if not isinstance(manifest, dict) or not manifest:
        raise QuantStudioError("历史结果缺少产物哈希清单；请重新运行后比较或查看结果")
    if asset is not None and asset not in manifest:
        raise QuantStudioError("结果文件不在本次核验清单中")
    for name, expected in manifest.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise QuantStudioError("产物哈希清单无效")
        content = safe_run_file(directory, name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise QuantStudioError(f"结果文件已改变：{name}")
    return evidence
