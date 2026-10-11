# Project research evidence workbench

Open **研究项目 → 研究证据与复核**. The page uses the project's stable ID to
show its question, explicitly linked Lab preregistrations, every Notebook attempt,
exact recipe-version runs, failed-before-run jobs, explicit interpretations and
candidate/review/observe/paused/retired research state.

New native jobs submitted from a project carry the exact project ID and revision.
They are excluded from other projects even when both link the same recipe. Historical
runs without project identity retain the previous exact-recipe-version association;
the JSON labels this `historical_recipe_version`. This is not inferred authorship.
Removed recipe links remain visible through project history. Notebook failures and
retries are never replaced by a later successful attempt.

## Configure the existing Agent environment

Install a quant-agent version containing `research_workflow.federation` in the
already supported `quant-studio.runtimes/v1` `python_by_repo["quant-agent"]`
environment. Studio invokes it with `-I -X utf8` in a separate process. It does not
require Agent dependencies in its own environment, call an online model, or run user
notebook code when viewing evidence. A missing/old environment displays unavailable.

Optional administrator-owned `QUANT_STUDIO_RESEARCH_LINKS` JSON:

```json
{
  "schema": "quant-studio.research-links/v1",
  "projects": {
    "PROJECT_ID": [{"database": "/absolute/path/trials.sqlite", "study_ids": ["study"]}]
  }
}
```

Database/file paths are not accepted from browser forms. Lab definitions, all planned
candidates and event history are read without changing Lab data. Project/recipe saves
are identified as exploratory; this interface does not backdate a preregistration.

## Review and lifecycle

The form requires a named reviewer, explicit interpretation, reason/scope and target
research state. Supports/rejects must cite a completed, currently byte-verified
attempt. Technical failure cannot reject a hypothesis. Unscoped cross-asset results
are not combined or ranked. Local actor names are assertions, not multi-user approval.

Review evidence is written beneath `.projects/PROJECT_ID/research-review/`, in
immutable review files and an independent Agent lifecycle SQLite journal. The page
rechecks original evidence and marks prior conclusions for review after project,
source, input or attempt inventory changes. A stale browser form or competing review
is rejected. Original claims and failed attempts remain inspectable.

**Research states never activate an account, cancel a job or authorize execution.**
Byte identity is not statistical validity. Notebook evidence uses original submission
and output hashes. Native runs record the current byte snapshot plus original sealed
checks where supplied; a legacy unsealed run is not retroactively certified. Source
tracking covers registered inputs, Notebook drafts and a probed native entry point,
not every imported source file or hidden external dependency.

CLI uses the same collector and Agent process:

```sh
python -m quant_studio.project_evidence --runs-root /path/to/runs --project PROJECT_ID
python -m quant_studio.project_evidence --runs-root /path/to/runs --project PROJECT_ID --review review.json
```

Set `QUANT_TEST_RESEARCH_AGENT_PYTHON` to an installed Agent test environment and
`QUANT_TEST_NOTEBOOK_PYTHON` to the separate Notebook environment (or omit the latter
when intentionally testing a combined environment). Run `pytest tests/test_project_evidence.py`.
The research-workspace CI pins the Agent commit and runs these paths with separate
locked interpreters. Integration exercises real Notebook
kernels (success and failure), the shipped native synthetic recipe, actual Agent and
Studio CLI processes, HTTP/CSRF, stale reviews and registered input mutation. Synthetic
fixtures demonstrate software behavior only, not financial effectiveness. Keep local
Windows test directories short enough for the host's supported path length.
