"""Run only in the explicitly selected upstream environment containing PyArrow."""

import json
import sys


def inspect(path, limit):
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    names = parquet.schema_arrow.names
    if len(names) > 200 or len(set(names)) != len(names):
        raise ValueError("列数超过200或列名重复")
    missing, count, rows = [0] * len(names), 0, []
    duplicates, seen = 0, set()
    for batch in parquet.iter_batches(batch_size=min(512, limit)):
        batch = batch.slice(0, min(batch.num_rows, limit - count))
        for index, column in enumerate(batch.columns):
            missing[index] += column.null_count
        values = batch.to_pylist()
        for row in values:
            encoded = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)
            duplicates += encoded in seen
            seen.add(encoded)
            if len(rows) < 20:
                rows.append(
                    [
                        "" if row[name] is None else str(row[name])[:2000]
                        for name in names
                    ]
                )
        count += batch.num_rows
        if count >= limit:
            break
    return {
        "columns": [
            {
                "name": field.name,
                "type": str(field.type),
                "description": "未声明",
                "missing": missing[i],
            }
            for i, field in enumerate(parquet.schema_arrow)
        ],
        "rows": rows,
        "scanned_rows": count,
        "total_rows": parquet.metadata.num_rows,
        "scan_complete": count == parquet.metadata.num_rows,
        "duplicate_rows": duplicates,
        "scope": "空值按Arrow null计数；重复按整行比较；质量计数仅覆盖已扫描行。",
    }


if __name__ == "__main__":
    print(json.dumps(inspect(sys.argv[1], int(sys.argv[2])), ensure_ascii=False))
