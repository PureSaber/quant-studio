import copy
import csv
import io

from quant_studio.batch_web import batch_csv, batch_detail_body, batch_list_body
from quant_studio.recipes import RecipeStore
from quant_studio.research_batches import BatchStore
from quant_studio.templates import load_template


def _batch(tmp_path):
    template = load_template("crypto-basis-fixture")
    recipe = RecipeStore(tmp_path).save(
        '<script>alert("x")</script>',
        template.id,
        copy.deepcopy(template.base_config),
    )
    return BatchStore(tmp_path).prepare(
        recipe["id"],
        recipe["revision"],
        {"source": ["binance", "okx"], "seed": [1]},
        max_candidates=2,
    )


def test_batch_pages_escape_values_have_labels_filters_and_scrollable_table(tmp_path):
    batch = _batch(tmp_path)
    detail = batch_detail_body(batch, '<csrf&"token>')
    assert "<script>" not in detail
    assert "&lt;script&gt;" in detail
    assert '<label for="batch-status-filter">状态筛选</label>' in detail
    assert 'aria-label="显式提交全部候选"' in detail
    assert 'class="table-scroll batch-table-scroll"' in detail
    assert "&lt;csrf&amp;&quot;token&gt;" in detail
    listing = batch_list_body(
        [batch], query='<img src=x onerror="x">', status="prepared"
    )
    assert "<img" not in listing
    assert "&lt;img" in listing
    assert '<label for="batch-query">搜索批次</label>' in listing


def test_batch_csv_keeps_attempts_and_uses_neutral_experiment_language(tmp_path):
    batch = _batch(tmp_path)
    text = batch_csv(batch)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 2
    assert {row["seed"] for row in rows} == {"1"}
    assert {row["source"] for row in rows} == {"binance", "okx"}
    assert "盈利" not in text
    assert "candidate_status" in text
