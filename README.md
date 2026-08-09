# amazon-5461-bot

Codex-ready automation helper for Amazon Seller Central 5461 / GTIN / brand-authorization workflows.

## Quick start

```powershell
cd C:\Users\Admin\Documents\Amazon-5461-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install pytest ruff
python -m playwright install chromium
```

Prepare local private account config:

```powershell
Copy-Item config\accounts.example.json runtime\private\accounts.json
$env:AMAZON5461_ACCOUNTS_PATH="runtime/private/accounts.json"  # optional override
```

Validate the project:

```powershell
python -m compileall -q src scripts tests cli
pytest -q
```

Run through the unified wrapper:

```powershell
python -m cli.amazon5461 diagnose --account us_store_000 --brand OUNNE --site US
python -m cli.amazon5461 dry-run --account us_store_000 --brand OUNNE --site US
python -m cli.amazon5461 run --account us_store_000 --brand OUNNE --site US --submit
```

Successful real runs with a Case ID automatically create a delayed Case-detail
follow-up. The delay is configured at `case_followup.delay_hours` in
`config/settings.yaml` and can be overridden for one run:

```powershell
python -m cli.amazon5461 run --account us_store_000 --brand OUNNE --site US --submit --case-followup-delay-hours 24
python -m cli.amazon5461 case-check --account us_store_000 --brand OUNNE --site US --case-id 12345678901
python -m cli.amazon5461 followups --watch
```

`case-check` does not update local/Feishu records unless `--register` is
explicitly supplied. Case follow-ups treat `Answered` only as a reply indicator.
When the newest Amazon reply explicitly approves the request, the check also
opens Manage Your Brands and fills Add Product, then clicks only the Product
Identity Continue/Next validation action. It never clicks Apply to sell or
submits a 5461 application. Final `approved` requires both checks to pass;
otherwise the result is `false_approved`, `verification_pending`, or `blocked`.

## Important safety defaults

- Diagnosis and dry-run first.
- Real submit must be explicit.
- CAPTCHA, 2FA, login expiry, rate limits, and unknown page states require human review.
- Runtime outputs stay under `runtime/`.
- Real account configuration stays under `runtime/private/` and is ignored by Git.

When the state-loop executor exhausts deterministic recovery, it writes a local
diagnosis handoff instead of calling a one-minute model watchdog:

```powershell
python scripts\check_codex_signal.py
python scripts\check_codex_signal.py --mark-handled --note "diagnosis complete"
```

LLM page analysis is optional and reads only the repo-local `LLM_*` settings in
`.env`; active code no longer reads a Hermes provider configuration.

## Main directories

```text
src/         core automation code
scripts/     existing operational scripts
cli/         stable wrapper entrypoints for humans and Codex
.agents/     repository-scoped Codex skills
config/      templates and non-secret config
knowledge/   page knowledge and analysis prompts
docs/        project plans, runbooks, and status rules
tests/       regression tests
runtime/     local run outputs, ignored by Git
legacy/      ignored one-off scripts and historical material
```

The original Hermes workspace remains at `C:\Users\Admin\projects\amazon-5461-bot`
as a rollback archive. Do not operate both copies concurrently.
