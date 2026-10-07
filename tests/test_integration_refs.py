import re
from pathlib import Path

import yaml

from quant_studio.templates import load_template, template_ids


def test_template_revisions_match_the_public_integration_matrix():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/upstream-integration.yml").read_text()
    )
    tested = {
        item["repo"]: item["ref"]
        for item in workflow["jobs"]["integration"]["strategy"]["matrix"]["upstream"]
    }
    timing = workflow["jobs"]["timing"]["steps"][1]["with"]
    tested["quant-timing"] = timing["ref"]
    for name in template_ids():
        template = load_template(name)
        if template.kind == "synthetic":
            continue
        assert re.fullmatch(r"[0-9a-f]{40}", template.upstream_ref), name
        if template.workspace_repo in tested:
            assert template.upstream_ref == tested[template.workspace_repo], name
