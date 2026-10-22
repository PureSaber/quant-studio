# Research workbench usability

The workbench uses **project → saved plan → immutable run**. Template previews remain available; executions launched there belong to a Quick Trial project. A project can also contain Notebook runs and parameter batches. The run index includes runs, jobs, batches and saved project versions; comparing configurations and failure diagnoses does not require a valid return curve.

This checklist follows the supplied usability review, with the accepted corrections: notes are always available; formal decisions retain their evidence requirements; unsupported metrics say unavailable; progress lists only applicable work; interrupted, incomplete and unconfirmed cancellation outcomes stay distinct.

| Review item | Implementation / acceptance |
|---|---|
| Error recovery | One-sentence summary, useful action, collapsed diagnostics; interrupted runs require an explicit confirmation before a new attempt. |
| Status and time | Shared status meanings and readable timestamps. Native form values and evidence text retain their original representation. |
| Tool setup | One home setup notice; names describe the tool; scan existing environments inside the selected workspace, then review before saving. |
| Current parameters | One save-and-run action, dirty state, fixed version, inline progress and elapsed time; terminal success opens its result. |
| Navigation | Five destinations and five project tabs; legacy history/jobs/results/backtest routes lead to the unified run index. |
| Forms | Grouped fields, explicit units, basis-point conversion, live summary, field validation, technical-name toggle and native-config drawer. |
| Results | Applicable metrics, non-positive drawdown, same-observation benchmark, normalized/amount display, round axes, hover/keyboard readout, interval brush and eligible log scale. |
| Project workflow | Notes and failure conclusions available before successful runs; one primary save per tab; advanced tools remain contextual. |
| Run index | Search/filter/paginate existing plan runs, jobs, Notebook executions, native/Notebook/durable batches and project history. |
| Visual system | Shared color tokens, persistent return-color preference, distinct information/action/error notices, text and symbols with statuses, desktop/mobile layouts. |

## Route mapping

Home and onboarding → Home. Projects and saved plans → Research projects. Catalog/import/maintenance → Data. Runs, jobs, results, history and batches → Run records. Today/accounts/reconciliation → Simulation tracking. Templates → Template library. Runtime, delivery and service health → Settings.

## Verification

Local acceptance on 2026-10-22:

- Python 3.12 full suite: **638 passed, 32 skipped**. The skipped cases require optional platform/runtime integrations; they are not counted as passes. Ruff check and format, JavaScript syntax checks, and `git diff --check` passed.
- A real synthetic Quick Trial launched with an edited initial capital of 12,345, opened its successful result automatically, and retained the originating project and saved plan. Retry and preflight execution also retain the frozen project version; regression coverage checks that association.
- Browser checks covered parameter editing and dirty state, basis-point echoes, labeled factor selections, saved return-color preference across reload, normalized/amount charts and interval controls. Interval changes preserve the full-run KPI basis; absent baselines stay absent.
- At 1366×900 with the sample project, the five tabs measured 900 / 900 / 900 / 900 / 1,491 CSS pixels in total page height. At 375 pixels, the settings, run index, result, job and A-share editor had no page-level horizontal overflow. The conclusions view's sampled text contrast check found no values below 4.5:1.
- Failure runs retain raw diagnostics inside collapsed details, and remain available for configuration/diagnostic comparison. Curve eligibility, financial evidence checks, cancellation distinctions and formal-decision evidence requirements remain enforced by regression tests.

Complete durable history uses the cursor capability added in [quant-pipeline PR 35](https://github.com/PureSaber/quant-pipeline/pull/35). The optional client pin includes that capability. External worker environments need the corresponding upgrade: older clients remain usable, with an explicit notice that their history is limited to the newest 30 records. Cursor tests cover tied timestamps, more than 30 records and new submissions between pages; workbench tests cover a full page containing only non-research jobs before older research records.

Browser observations are sample-based, not a claim that every upstream strategy, screen reader or external worker was exercised live. The final stock-pool summary, diagnostic fallback and history changes were checked by automated regression tests after the browser-control tool became unavailable. Full repository CI and upstream integration results are attached to the implementation pull request.

These source changes do not update an already installed local application or its worker environments automatically.
