# "Uncertain" failure at Description page (2026-07-04)

A brand can reach the Description page and fail with an error like
"到达 Description 页面...请人工复核该品牌是否仍需授权" when the script can't
find a clear 5461/Apply-to-sell entry point.

The script correctly leaves that brand's tab open (does not close it) since
there's no Case ID yet — this matches the general tab-closing rule (only close
a tab once you have a confirmed Case ID).

The `page_text.txt` snapshot's `Detected States` field can list contradictory
states simultaneously, e.g. both `already_approved` and `declined_case_shown`
appearing together. This is just the heuristic detector matching multiple
text/DOM patterns at once — it is NOT evidence of either outcome. Do not
conclude success or failure from `Detected States` alone.

Resolution steps:
1. Check Manage Your Brands / View Selling Applications in Seller Central for
   the brand's real status (Approved / Declined / no record).
2. Or inspect the retained live browser tab (via AdsPower) directly.
3. Only after confirming the real status, decide whether to re-run that brand
   through the batch script.

429 seen in the monitor summary around the same run is not automatically the
cause of this failure mode — verify from the log whether the failure text is
about missing the 5461 entry point (this case) vs. an explicit 410001/429
block (the 410001/429 retry pattern, which is a separate, already-documented
pitfall).
