# Project simulation registration

The project page offers completed native candidates, immutable previews, explicit
confirmation, persisted background execution and independent account inspection.
Existing authentication and CSRF checks apply. Failed and interrupted jobs retain
their evidence; the preview page is the recovery entry point.

An administrator supplies `QUANT_STUDIO_REGISTRATION` and the dedicated runtime key
`quant-pipeline-registration`. This runtime must match the research code identity.
It is separate from `quant-pipeline-frozen`, which continues serving the existing
account/daily workflow. The optional JSON configuration is:

```json
{
  "schema": "quant.project-registration/v1",
  "runs_root": "C:/isolated/work/runs",
  "accounts_root": "C:/isolated/work/new-accounts",
  "state_root": "C:/isolated/work/registration-state",
  "preview_ttl_seconds": 900
}
```

Use separate absolute roots. Browser requests contain only validated identifiers,
dates and explicit confirmation, never executable commands or filesystem paths.
The setup allowlist and final installed runtime pins are integration-owned.

Preview uses the same native promotion validation as registration and the native
research preflight. Confirm revalidates the project, all collected artifacts, native
study registry, inputs, code identity and configuration. An expired or changed
preview cannot create an account. Repeated confirmations resolve to one account.
An interrupted, already committed registration may finish publication after expiry;
this does not change its definition or create a second registration.

New accounts remain unscheduled. No observation is appended by registration, and
no existing scheduled account is changed. Use the project's registration list to
inspect new accounts and their native observations/verification separately.

For isolated browser acceptance use `integration/generate_workflow_fixture.py` with
a clean installed integration interpreter. It creates current-date synthetic data,
real native research, compatible/incompatible comparisons and a preserved failure.
See its `--help` and generated `fixture.json`. Synthetic calendar days and any later
synthetic observations are software tests, never natural trading-day acceptance.
