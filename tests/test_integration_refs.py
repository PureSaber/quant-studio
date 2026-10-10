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
    derivatives = workflow["jobs"]["derivatives"]["steps"][1]["with"]
    tested["quant-futures-global"] = derivatives["ref"]
    for name in template_ids():
        template = load_template(name)
        if template.kind == "synthetic":
            continue
        assert re.fullmatch(r"[0-9a-f]{40}", template.upstream_ref), name
        runtime = template.metadata.get("runtime_key") or template.workspace_repo
        if runtime in tested:
            assert template.upstream_ref == tested[runtime], name
