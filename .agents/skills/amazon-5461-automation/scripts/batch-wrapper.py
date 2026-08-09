#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sequential wrapper: US brands first, then MX V-PORYADKU for {ACCOUNT_ID}.
Replace ACCOUNT_ID, DATE (YYYYMMDD), US_BRANDS, MX_BRANDS before running.
"""
import subprocess
import sys
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
ACCOUNT_ID = "us_store_XXX"           # e.g. us_store_644
DATE       = "20260716"               # today in YYYYMMDD
US_BRANDS  = "WILLONE,JZG,MP-MALL"   # comma-separated, no spaces
MX_BRANDS  = "V-PORYADKU"            # comma-separated (empty string = skip MX)
# ──────────────────────────────────────────────────────────────────────────────

project_root = Path(__file__).parent
python = project_root / ".venv" / "Scripts" / "python.exe"

exit_codes = []

if US_BRANDS:
    us_cmd = [
        str(python), "scripts/run_full_5461_batch.py",
        "--accounts", ACCOUNT_ID,
        "--brands",   US_BRANDS,
        "--site",     "US",
        "--state-file", f"data/batch_{ACCOUNT_ID.split('_')[-1]}_us_{DATE}.json",
        "--yes",
    ]
    print("=" * 70)
    print(f"[WRAPPER] Starting US batch for {ACCOUNT_ID}")
    print("=" * 70)
    r = subprocess.run(us_cmd, cwd=str(project_root))
    print(f"[WRAPPER] US batch exited with code {r.returncode}")
    exit_codes.append(r.returncode)

if MX_BRANDS:
    mx_cmd = [
        str(python), "scripts/run_full_5461_batch.py",
        "--accounts", ACCOUNT_ID,
        "--brands",   MX_BRANDS,
        "--site",     "MX",
        "--state-file", f"data/batch_{ACCOUNT_ID.split('_')[-1]}_mx_{DATE}.json",
        "--yes",
    ]
    print("=" * 70)
    print(f"[WRAPPER] Starting MX batch for {ACCOUNT_ID}")
    print("=" * 70)
    r = subprocess.run(mx_cmd, cwd=str(project_root))
    print(f"[WRAPPER] MX batch exited with code {r.returncode}")
    exit_codes.append(r.returncode)

print("=" * 70)
print("[WRAPPER] All batches completed")
print("=" * 70)
sys.exit(max(exit_codes) if exit_codes else 0)
