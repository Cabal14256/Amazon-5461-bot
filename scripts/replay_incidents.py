#!/usr/bin/env python3
"""Replay historical batch failures through the stage-5 incident detector.

Scans ``data/batch_*.json`` and ``runtime/state/*.json`` under ``--root``
(default: project root), classifies every failed item, and prints a table
plus a per-classification summary.  Default is read-only; ``--write``
persists the incidents into the ledger DB.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.incidents import (  # noqa: E402
    REPAIR_CLASSES,
    classify_failure,
    compute_signature,
    initial_confidence,
)


def _iter_failed_items(payload: dict):
    """Yield (item, result) for failed items across known batch-state shapes."""
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


def _classify_item(item: dict, result: dict) -> dict:
    status_text = str(result.get("status") or result.get("submit_result") or "")
    error_text = str(result.get("error") or result.get("note") or item.get("error") or "")
    classification = classify_failure(status=status_text, error_text=error_text)
    signature = compute_signature(
        flow_type="5461",
        error_class=f"{status_text} {error_text}".strip(),
    )
    return {
        "brand": str(item.get("brand_name") or ""),
        "account_id": str(item.get("account_id") or ""),
        "site": str(item.get("site") or ""),
        "classification": classification,
        "signature": signature,
        "repair_candidate": classification in REPAIR_CLASSES,
        "confidence": initial_confidence(classification),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(project_root), help="scan root (default: project root)")
    parser.add_argument("--write", action="store_true", help="persist incidents into the ledger DB")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    db_path = str(root / "runtime" / "state" / "ledger.db")
    if args.write:
        from src.db import init_db, record_incident
        init_db(db_path)

    rows = []
    for path in _scan_files(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item, result in _iter_failed_items(payload):
            entry = _classify_item(item, result)
            entry["source"] = str(path.relative_to(root))
            rows.append(entry)
            if args.write:
                try:
                    record_incident(
                        db_path,
                        signature=entry["signature"],
                        scope_type="account",
                        flow_type="5461",
                        account_id=entry["account_id"],
                        marketplace=entry["site"],
                        brand_name=entry["brand"],
                        detector_type="replay",
                        classification=entry["classification"],
                        confidence=entry["confidence"],
                    )
                except Exception as exc:
                    print(f"[WARN] 写库失败 {path.name}/{entry['brand']}: {exc}")

    header = f"{'source':<44} {'brand':<20} {'classification':<24} {'signature':<16} {'repair':<6} {'conf':<5}"
    print(header)
    print("-" * len(header))
    for entry in rows:
        print(
            f"{entry['source']:<44} {entry['brand']:<20} {entry['classification']:<24} "
            f"{entry['signature']:<16} {str(entry['repair_candidate']):<6} {entry['confidence']:<5.2f}"
        )

    counts = Counter(entry["classification"] for entry in rows)
    print()
    print(f"共 {len(rows)} 条失败记录；classification 分布：")
    for classification, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {classification:<24} {count}")
    repair = [entry for entry in rows if entry["repair_candidate"]]
    print(f"修复候选 {len(repair)} 条：")
    for entry in repair:
        print(f"  - {entry['brand']} ({entry['source']}): {entry['classification']} sig={entry['signature']}")
    if args.write:
        print(f"已写入 {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
