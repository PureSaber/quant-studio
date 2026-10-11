"""Render cell proposals against the exact previewed source, without applying them."""

import difflib
import hashlib
import json

from quant_studio import QuantStudioError


def proposal_diff(directory, patches):
    notebook = json.loads((directory / "source.ipynb").read_text("utf-8"))
    cells = {str(index): cell for index, cell in enumerate(notebook["cells"])}
    output, seen = [], set()
    for patch in patches:
        key = str(patch["cell"])
        if key in seen or key not in cells or cells[key]["cell_type"] != "code":
            raise QuantStudioError("代码建议引用了重复或无效的单元格")
        seen.add(key)
        before = "".join(cells[key]["source"])
        if hashlib.sha256(before.encode()).hexdigest() != patch["before_sha256"]:
            raise QuantStudioError("代码建议与冻结源码不匹配")
        output.append(patch["reason"] + "\n")
        output.extend(
            difflib.unified_diff(
                [line + "\n" for line in before.splitlines()],
                [line + "\n" for line in patch["source"].splitlines()],
                fromfile=f"原单元格{key}",
                tofile=f"建议单元格{key}",
            )
        )
    return "".join(output)
