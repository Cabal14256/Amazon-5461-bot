# MX GTIN Exemption: False Failure Pattern (2026-06-21, 620US)

## Batch: 620US → MX, 5 brands

| Brand | Script status | Actual outcome | Case ID |
|-------|--------------|----------------|---------|
| mocodi | completed | Approved | <CASE_ID_REDACTED> |
| VASG | completed | Approved (nav_failed normal) | <CASE_ID_REDACTED> |
| uShield | **failed** | Under review (GTIN Exemption) | <CASE_ID_REDACTED> |
| OUNNE | **failed** | Approved | <CASE_ID_REDACTED> |
| MP-MALL | **failed** | Under review (new application) | <CASE_ID_REDACTED> |

## Why uShield/OUNNE/MP-MALL showed as failed

**uShield:** Hit the GTIN Exemption path (not Catalog Auth). Confirmation page text:
> "Thank you for submitting your application. The estimated decision date is on or before Jun 24, 2026."

No Case ID visible on page → script's Case ID extractor found nothing → marked `failed` with note "提交后未检测到成功提示". The submit was 100% successful.

**OUNNE:** Similar "提交后未检测到成功提示" — actual Dashboard status was Approved.

**MP-MALL:** Hit "到达 Description 页面，Product Identity 触发后仍未检测到 5461 入口" — Amazon redirected to Description step, flow lost the Apply to Sell entry. But the brand had already been submitted in a prior attempt (Dashboard showed Under review with new Case ID).

## Recovery workflow used

1. `check_case_log.py us_store_620 uShield --site MX` → screenshot saved, script returned "未找到"
2. `vision_analyze` on screenshot → read all rows from Dashboard table in one pass
3. Dashboard showed: uShield-Electrónicos (GTIN Exemption, Under review), OUNNE (Approved), MP-MALL (Under review)
4. Patched `batch_state.json` with `_tmp_patch_case.py` for each brand

**Key insight:** one `vision_analyze` on a single Dashboard screenshot recovered Case IDs for all failed brands simultaneously — no need to run `check_case_log.py` per brand.

## Dashboard table columns on MX

`Application name / Case ID / Application type / Changed / Status`

GTIN Exemption rows show as `<BRAND>-Electrónicos` in the Application name column. The `check_case_log.py` script searches for the brand name as a substring — it won't find `uShield` if the row is named `uShield-Electrónicos` (depends on search logic). Always verify with screenshot when exit code 1.

## Lesson for script improvement

The confirmation page for GTIN Exemption does not contain a Case ID. The script should detect the "Thank you for submitting" text as a success signal (not a failure) and trigger a delayed `check_case_log.py` call instead of marking the brand failed.
