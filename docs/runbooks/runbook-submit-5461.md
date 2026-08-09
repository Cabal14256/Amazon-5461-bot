# Runbook: 5461 workflow

## 1. Preflight

```powershell
python -m compileall -q src scripts tests cli
pytest -q
python -m cli.amazon5461 diagnose --account us_store_000 --brand OUNNE --site US
```

Check:

- AdsPower local API is reachable.
- Account has valid `adspower_profile_id`.
- Marketplace config exists.
- Brand pack exists.
- No current 429 / 410001 / login-expired state.

## 2. Dry-run

```powershell
python -m cli.amazon5461 dry-run --account us_store_000 --brand OUNNE --site US
```

Dry-run must not submit forms. Use it to verify page state, selectors, brand pack content, and evidence capture.

## 3. Real run

```powershell
python -m cli.amazon5461 run --account us_store_000 --brand OUNNE --site US --submit
```

Stop for human review when CAPTCHA, 2FA, login expiry, unknown page state, duplicate application risk, 429, or 410001 appears.

## 4. Post-run status

Dashboard / Selling Applications result takes precedence over local batch state.

When a reliable Case ID is captured, the batch also:

1. Records the pending application in SQLite and the Excel registry.
2. Schedules a Case-detail follow-up after `case_followup.delay_hours`.
3. Starts one hidden follow-up worker after the batch releases its browser connection.
4. Switches the AdsPower profile back to the recorded site before opening the Case.
5. Reads the newest Amazon-authored message and registers only an explicit outcome.

See [runbook-case-followup.md](runbook-case-followup.md).
