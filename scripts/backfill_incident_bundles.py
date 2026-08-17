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
from src.incidents.evidence_bundle import assess_evidence_bundle, build_evidence_bundle  # noqa: E402
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


def _iter_success_items(payload: dict):
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
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            status = str(result.get("status") or item.get("business_status") or "")
            if status in {"success", "under_review", "dry_run"}:
                yield item, result


def _scan_files(root: Path):
    seen: set[Path] = set()
    for pattern in (
        "data/batch_*.json",
        "runtime/state/*.json",
        "runtime/state/reapplications/**/*.json",
    ):
        for path in sorted(root.glob(pattern)):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield path


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


def _collect_success_contracts(root: Path) -> list[tuple[dict, dict]]:
    successes: list[tuple[dict, dict]] = []
    for path in _scan_files(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for item, result in _iter_success_items(payload):
            evidence = result.get("success_page_evidence")
            contract = evidence.get("dom_contract") if isinstance(evidence, dict) else None
            if isinstance(contract, dict) and contract.get("nodes"):
                successes.append((item, result))
    return successes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="build bundles and update the DB")
    parser.add_argument("--incident-id", type=int, default=None, help="only this incident")
    parser.add_argument("--root", type=Path, default=project_root, help=argparse.SUPPRESS)
    args = parser.parse_args()

    settings = load_settings()
    db_path = str(settings.db_path)
    evidence_root = Path(settings.evidence_root)

    conn = get_conn(db_path)
    conn.row_factory = conn.row_factory  # keep default tuples via raw SQL below
    sql = """SELECT id, account_id, marketplace, brand_name, classification,
                    occurrence_count, evidence_bundle_path
             FROM repair_incidents WHERE evidence_status<>'ready'"""
    params: tuple = ()
    if args.incident_id is not None:
        sql += " AND id=?"
        params = (int(args.incident_id),)
    rows = conn.execute(sql + " ORDER BY id", params).fetchall()
    conn.close()

    scan_root = args.root.resolve()
    failures = _collect_failures(scan_root)
    success_contracts = _collect_success_contracts(scan_root)
    matched, skipped = [], []
    for inc_id, account_id, marketplace, brand_name, classification, occ, bundle_path in rows:
        entries = failures.get((str(account_id), str(marketplace), str(brand_name))) or []
        if not entries:
            skipped.append((inc_id, bundle_path))
            continue
        matched.append(
            (inc_id, account_id, marketplace, brand_name, classification, occ, entries)
        )

    print(
        f"incidents needing bundle evaluation: {len(rows)}; matched: {len(matched)}; "
        f"no history: {len(skipped)}; exact success contracts: {len(success_contracts)}"
    )
    if not args.write:
        print("dry-run; pass --write to register fixtures, build bundles and re-evaluate gates")
        return 0


    from src.incidents.success_contracts import (
        infer_evidence_node,
        infer_page_family,
        load_previous_success,
        register_known_good_contract,
    )

    registered = 0
    for item, result in success_contracts:
        evidence = result["success_page_evidence"]
        contract = evidence["dom_contract"]
        family = infer_page_family(evidence, contract)
        node = infer_evidence_node(family, evidence)
        row = register_known_good_contract(
            db_path,
            evidence_root,
            flow_type="5461",
            marketplace=str(item.get("site") or ""),
            page_family=family,
            evidence_node=node,
            source_status=str(result.get("status") or ""),
            contract=contract,
            run_context={
                "account_id": item.get("account_id"),
                "brand_name": item.get("brand_name"),
            },
        )
        registered += int(row is not None)

    reevaluated = 0
    for inc_id, bundle_path in skipped:
        path = Path(str(bundle_path or ""))
        if not path.is_dir():
            continue
        gate = assess_evidence_bundle(path)
        set_incident_evidence_bundle(
            db_path,
            int(inc_id),
            str(path),
            evidence_status=gate["status"],
            missing_evidence=gate["missing"],
        )
        reevaluated += 1

    built = 0
    for inc_id, account_id, marketplace, brand_name, _classification, occ, entries in matched:
        latest = entries[-1]
        result = latest["result"]
        captured = result.get("failure_page_evidence")
        page_evidence = dict(captured) if isinstance(captured, dict) else {}
        dom_contract = page_evidence.pop("dom_contract", None)
        page_evidence.update({
            "source": "batch_state_backfill",
            "status": result.get("status"),
            "note": result.get("note") or result.get("error"),
            "state_trace": result.get("steps") or [],
            "history": [
                {"source": e["source"], "created_at": e["created_at"],
                 "note": e["result"].get("note") or e["result"].get("error")}
                for e in entries
            ],
        })
        run_context = {
            "account_id": account_id,
            "marketplace": marketplace,
            "site": marketplace,
            "brand_name": brand_name,
            "flow_type": "5461",
            "case_id": result.get("case_id"),
            "sku": result.get("synced_sku"),
            "detector_type": "replay_backfill",
            "occurrence_count": occ,
            "source_file": latest["source"],
            "batch_created_at": latest["created_at"],
        }
        probes = page_evidence.get("selector_probes") or {}
        selectors = {
            "declared_candidates": ["kat-button", "kat-panel-wrapper", "kat-input", "input[type=file]"],
            "probes": probes,
            "retrigger_candidates": result.get("retrigger_results") or [],
        }
        family = infer_page_family(page_evidence, dom_contract)
        node = infer_evidence_node(family, page_evidence)
        previous_success = load_previous_success(
            db_path,
            flow_type="5461",
            marketplace=str(marketplace),
            page_family=family,
            evidence_node=node,
        )
        bundle_dir = build_evidence_bundle(
            int(inc_id), evidence_root,
            page_evidence=page_evidence, run_context=run_context,
            selectors=selectors,
            dom_contract=dom_contract,
            previous_success=previous_success,
        )
        gate = assess_evidence_bundle(bundle_dir)
        set_incident_evidence_bundle(
            db_path,
            int(inc_id),
            str(bundle_dir),
            evidence_status=gate["status"],
            missing_evidence=gate["missing"],
        )
        built += 1
    print(
        f"registered {registered} success contract(s); built {built} and re-evaluated "
        f"{reevaluated} existing bundle(s) "
        f"under {evidence_root / 'incidents'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
