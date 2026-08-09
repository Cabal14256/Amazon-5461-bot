# Batch State File False Negatives

## Problem

The batch state file (e.g. `data/batch_641_us_20250712.json`) can mark a brand as `status: "failed"` even when the 5461 form was successfully submitted and a Case ID was issued by Amazon.

## Root Cause

After submit, the script tries to:
1. Detect a success message on the page (Case ID string)
2. Navigate to the dashboard to confirm

If either step encounters a network error — e.g.:
- `getRestrictionForm` returns HTTP 400
- CloudFront JS chunks fail with `net::ERR_FAILED`
- Dashboard navigation hits `net::ERR_ABORTED`

…the script logs `failed` and sets `case_id: null`, **even if the form submission already succeeded** on Amazon's backend.

## How to Verify

**Always check the evidence screenshot first**, not just the state file status.

```
runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png
runtime/evidence/<date>/<account>/<brand>/submit_full/04_after_submit.png
```

The `final_state.png` typically captures the "Apply to sell" modal — if it shows:
- `Case ID - XXXXXXXXXXX` (blue hyperlink at the bottom)
- `Under review - Decision expected by <date>`

…then the submission succeeded. The `failed` in the state file is a false negative.

## State File Structure

```json
{
  "created_at": "...",
  "config": {...},
  "batches": [
    {
      "batch_no": 1,
      "items": [
        {
          "brand_name": "JavoYion",
          "status": "failed",          // <-- may be wrong
          "result": {
            "status": "failed",
            "case_id": null,           // <-- may be wrong
            "evidence_files": [...],   // check these
            "health_classification": {
              "severity": "rate_limited",
              "tags": ["cloudfront_chunk_failed", "dashboard_navigation_failed"]
            }
          }
        }
      ]
    }
  ]
}
```

`batches[0].items[]` — not top-level keys.

## Decision Rule

| state file status | evidence screenshot shows Case ID | Actual result |
|---|---|---|
| `completed` | yes | ✅ Success |
| `failed` | yes | ✅ Success (false negative) |
| `failed` | no (error/blank) | ❌ Genuine failure, resubmit |
| `uncertain` | no Case ID, reached Description | ⚠️ Brand may not need authorization |

## Known Trigger: `getRestrictionForm` HTTP 400

When `POST /hz/getRestrictionForm` returns 400 + CloudFront chunk failures, the page React state breaks mid-flight after submit. Amazon received the form but the page can't re-render the success modal cleanly. The script's Case ID regex scan finds nothing.

**Implication**: `rate_limited` health_classification tags with no 429s is a signal to check screenshots rather than resubmit.

## Entry Point Reminder

The correct batch runner for account 641 is the wrapper at project root:

```
_run_641_batch_wrapper.py
```

This calls `scripts/run_full_5461_batch.py` via subprocess with the pre-configured brand list and state file paths. Do NOT call `run_full_5461_batch.py` directly unless building a custom brand subset.
