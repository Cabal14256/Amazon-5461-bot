# Runbook: Dashboard / Selling Applications status check

Use this when a run has no Case ID, a draft state, a declined state, or uncertain final status.

## Rules

- Case ID with `Under Review` or `Approved` is success.
- `Draft` is not failure.
- `Declined` is not retryable without understanding the reason.
- `Submitted no Case ID` must be checked against Dashboard / Selling Applications.
- `Not Found` should be recorded separately from failure.

## Suggested command

```powershell
python scripts/check_case_log.py --help
```
