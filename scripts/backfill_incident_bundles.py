#!/usr/bin/env python3
"""Backfill stage-5 evidence bundles for incidents that have none.

Incidents created by ``scripts/replay_incidents.py`` (or any path that did
not capture live page evidence) have an empty ``evidence_bundle_path``, so
Codex triage can only answer ``insufficient_evidence``.  This script scans
the same historical batch-state files, matches failed items to open
incidents by (account_id, marketplace, brand_name), and builds a redacted
bundle from the recorded state trace — the manifest honestly marks every
artifact that history simply does not contain (DOM contract, screenshot,
selectors) as absent.

Default is read-only; ``--write`` builds the bundles and updates the DB.
"""
import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.db import get_conn, set_incident_evidence_bundle  # noqa: E402
from src.incidents.evidence_bundle import build_evidence_bundle  # noqa: E402
from src.web.config import load_settings  # noqa: E402


def _iter_failed_items(payload: dict):
    """Same batch-state shapes as scripts/replay_incidents.py."""
    if not isinstance(payload, dict):
        return
    item_lists = []
    for key in ("items", "results"):
        if isinstance(payload.get(key), list):
            item_lists.append(payload[key])
    for batch in payload.get("batches") or []:
        if isinstance(batch, dict) and isinstance(batch.get("items"), list):
            item_lists.append(batch["items"])
    for items in item_lists:
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") != "failed":
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            yield item, result


def _scan_files(root: Path):
    for pattern in ("data/batch_*.json", "runtime/state/*.json"):
        yield from sorted(root.glob(pattern))


def _collect_failures(root: Path) -> dict:
    """Map (account_id, site, brand) -> list of {source, created_at, result}."""
    failures: dict[tuple, list] = {}
    for path in _scan_files(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        created_at = str(payload.get("created_at") or "")
        for item, result in _iter_failed_items(payload):
            key = (
                str(item.get("account_id") or ""),
                str(item.get("site") or ""),
                str(item.get("brand_name") or ""),
            )
            if not all(key):
                continue
            failures.setdefault(key, []).append(
                {"source": str(path.relative_to(root)), "created_at": created_at, "result": result}
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="build bundles and update the DB")
    parser.add_argument("--incident-id", type=int, default=None, help="only this incident")
    args = parser.parse_args()

    settings = load_settings()
    db_path = str(settings.db_path)
    evidence_root = Path(settings.evidence_root)

    conn = get_conn(db_path)
    conn.row_factory = conn.row_factory  # keep default tuples via raw SQL below
    rows = conn.execute(
        """SELECT id, account_id, marketplace, brand_name, classification, occurrence_count
           FROM repair_incidents
           WHERE COALESCE(evidence_bundle_path, '') = ''"""
    ).fetchall()
    conn.close()
    if args.incident_id is not None:
        rows = [r for r in rows if int(r[0]) == int(args.incident_id)]

    failures = _collect_failures(project_root)
    matched, skipped = [], []
    for inc_id, account_id, marketplace, brand_name, classification, occ in rows:
        entries = failures.get((str(account_id), str(marketplace), str(brand_name))) or []
        if not entries:
            skipped.append((inc_id, account_id, marketplace, brand_name, classification))
            continue
        matched.append((inc_id, account_id, marketplace, brand_name, classification, occ, entries))

    print(f"incidents without bundle: {len(rows)}; matched: {len(matched)}; no history: {len(skipped)}")
    for inc_id, account_id, marketplace, brand_name, classification, _occ, entries in matched:
        print(f"  #{inc_id} {account_id}/{marketplace}/{brand_name} [{classification}] "
              f"<- {len(entries)} historical failure(s), e.g. {entries[-1]['source']}")

    if not args.write:
        print("dry-run; pass --write to build bundles")
        return 0

    built = 0
    for inc_id, account_id, marketplace, brand_name, _classification, occ, entries in matched:
        latest = entries[-1]
        result = latest["result"]
        page_evidence = {
            "source": "batch_state_backfill",
            "status": result.get("status"),
            "note": result.get("note") or result.get("error"),
            "state_trace": result.get("steps") or [],
            "history": [
                {"source": e["source"], "created_at": e["created_at"],
                 "note": e["result"].get("note") or e["result"].get("error")}
                for e in entries
            ],
        }
        run_context = {
            "account_id": account_id,
            "marketplace": marketplace,
            "brand_name": brand_name,
            "flow_type": "5461",
            "detector_type": "replay_backfill",
            "occurrence_count": occ,
            "source_file": latest["source"],
            "batch_created_at": latest["created_at"],
        }
        bundle_dir = build_evidence_bundle(
            int(inc_id), evidence_root,
            page_evidence=page_evidence, run_context=run_context,
        )
        set_incident_evidence_bundle(db_path, int(inc_id), str(bundle_dir))
        built += 1
    print(f"built {built} bundle(s) under {evidence_root / 'incidents'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
