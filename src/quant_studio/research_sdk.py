"""Small research facade, copied and hashed with each Notebook execution.

Only pandas/matplotlib/quant-factors in the selected research environment are used.
No Studio dependency is required in the kernel; no network or implicit filling occurs.
"""

import hashlib
import json
import math
from pathlib import Path


class Research:
    def __init__(self, context="inputs.json"):
        path = Path(context).resolve()
        self.base = path.parent
        self.context = json.loads(path.read_text("utf-8"))
        self.parameters = self.context.get("parameters", {})
        self.metrics = {}

    def datasets(self):
        return [
            {key: d.get(key) for key in ("id", "name", "files", "scope")}
            for d in self.context["datasets"]
        ]

    def load(
        self,
        dataset=0,
        *,
        file=None,
        numeric=(),
        identifiers=("symbol", "code", "instrument_id"),
    ):
        import pandas as pd

        items = self.context["datasets"]
        if type(dataset) is int:
            if not 0 <= dataset < len(items):
                raise ValueError("数据集序号不存在")
            item = items[dataset]
        else:
            matches = [d for d in items if d["id"] == dataset]
            if len(matches) != 1:
                raise ValueError("数据集必须属于当前冻结研究")
            item = matches[0]
        if item.get("kind") == "intake":
            if file is not None:
                raise ValueError("准入数据通过固定用途读取，不能指定底层文件")
            from notebook_data_reader import read_intake_dataset

            data = read_intake_dataset(
                item, self.context["intake_runtime"], base=self.base
            )
            frame = pd.DataFrame(data["rows"])
        else:
            files = [
                name
                for name in item["files"]
                if Path(name).suffix.lower() in {".csv", ".tsv", ".parquet"}
            ]
            if file is None:
                if len(files) != 1:
                    raise ValueError(
                        "存在多份数据文件，请从research.datasets()中显式选择file"
                    )
                file = files[0]
            if file not in files:
                raise ValueError("只能读取登记的表格文件")
            root = (self.base / item["path"]).resolve()
            path = (root / file).resolve()
            expected = item["hashes"].get(file, item["hashes"].get("."))
            if not path.is_relative_to(root) or not expected:
                raise ValueError("数据路径或身份无效")
            self._verify(path, expected)
            if path.suffix.lower() == ".parquet":
                frame = pd.read_parquet(path)
            else:
                frame = pd.read_csv(
                    path,
                    sep="\t" if path.suffix.lower() == ".tsv" else ",",
                    dtype={name: str for name in identifiers},
                )
            self._verify(path, expected)
        for column in numeric:
            if column in identifiers:
                raise ValueError("标识列不能隐式转换成数值")
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        return frame

    @staticmethod
    def _verify(path, expected):
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("登记数据完整性失败")

    @staticmethod
    def align(frame, *, value, date="date", symbol="symbol", join="outer"):
        import pandas as pd

        if join not in {"outer", "inner"}:
            raise ValueError("join须为outer或inner")
        data = frame[[date, symbol, value]].copy()
        if data[[date, symbol]].isna().any().any():
            raise ValueError("日期与标的不可缺失")
        data[date] = pd.to_datetime(data[date], errors="raise")
        if data.duplicated([date, symbol]).any():
            raise ValueError("同一标的日期存在重复，请先明确聚合规则")
        panel = data.pivot(index=date, columns=symbol, values=value).sort_index()
        return panel.dropna(how="any") if join == "inner" else panel

    @staticmethod
    def returns(prices):
        return prices.pct_change(fill_method=None)

    @staticmethod
    def statistics(values):
        import pandas as pd

        series = pd.to_numeric(values, errors="raise")
        return {
            "count": int(series.count()),
            "missing": int(series.isna().sum()),
            "mean": None if not series.count() else float(series.mean()),
            "std": None if series.count() < 2 else float(series.std(ddof=1)),
        }

    @staticmethod
    def factors(frame, names):
        from quant_factors import compute_factors

        return compute_factors(frame, factors=names)

    @staticmethod
    def plot(values, *, title, ylabel):
        return values.plot(title=title, ylabel=ylabel)

    def metric(self, name, value, *, unit, definition, reason=None):
        if not isinstance(name, str) or not name.strip() or not unit or not definition:
            raise ValueError("指标需声明名称、单位与统计口径")
        if value is None:
            if not reason:
                raise ValueError("缺失指标须说明原因")
        elif (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("指标须为有限数值或有说明的缺失")
        item = {"value": value, "unit": unit, "definition": definition}
        if reason is not None:
            item["reason"] = reason
        self.metrics[name] = item
        return value
