"""Redacted evidence bundles for repair incidents.

Each incident gets a directory ``<evidence_root>/incidents/<id>/`` with a
fixed set of artifacts.  Everything written here is redacted through
``src.capture.redact`` and raw screenshots are never copied — only a
withheld marker referencing the original private path.  Page-visible text
is wrapped in untrusted-data markers so downstream consumers (including
LLM-based triage in later stages) treat it as data, never as instructions.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.capture.redact import redact_dict, redact_text

UNTRUSTED_BEGIN = "<<<UNTRUSTED_PAGE_DATA>>>"
UNTRUSTED_END = "<<<END_UNTRUSTED_PAGE_DATA>>>"
_MAX_VISIBLE_TEXT_CHARS = 8192

# Keys dropped entirely from run_context before redaction.
_DROP_CONTEXT_KEYS = ("email", "username", "password")

_VISIBLE_TEXT_KEYS = ("visible_text", "body_text", "text", "page_text")


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_visible_text(page_evidence) -> str:
    if not isinstance(page_evidence, dict):
        return ""
    for key in _VISIBLE_TEXT_KEYS:
        value = page_evidence.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _clean_run_context(run_context) -> dict:
    if not isinstance(run_context, dict):
        return {}
    return {
        key: value
        for key, value in run_context.items()
        if not any(marker in str(key).lower() for marker in _DROP_CONTEXT_KEYS)
    }


def build_evidence_bundle(
    incident_id: int,
    evidence_root,
    *,
    page_evidence=None,
    run_context=None,
    screenshot_path=None,
    selectors=None,
    dom_contract=None,
    previous_success=None,
) -> Path:
    """Build the redacted evidence bundle; returns the bundle directory."""
    bundle_dir = Path(evidence_root) / "incidents" / str(int(incident_id))
    bundle_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, dict] = {}

    def _mark(name: str, absent: bool = False, reason: str = "") -> None:
        entry: dict = {"name": name, "absent": absent}
        if reason:
            entry["reason"] = reason
        files[name] = entry

    # page-summary.redacted.json — structured page evidence, redacted.
    _write_json(
        bundle_dir / "page-summary.redacted.json",
        redact_dict(page_evidence if isinstance(page_evidence, (dict, list)) else {}),
    )
    _mark("page-summary.redacted.json", absent=not page_evidence,
          reason="" if page_evidence else "no page evidence captured")

    # visible-text.redacted.txt — capped, redacted, wrapped as untrusted data.
    visible = _extract_visible_text(page_evidence)
    truncated = redact_text(visible[:_MAX_VISIBLE_TEXT_CHARS])
    (bundle_dir / "visible-text.redacted.txt").write_text(
        f"{UNTRUSTED_BEGIN}\n{truncated}\n{UNTRUSTED_END}\n", encoding="utf-8"
    )
    _mark("visible-text.redacted.txt", absent=not bool(visible.strip()),
          reason="" if visible.strip() else "no visible text captured")

    # relevant-selectors.json — selector candidates involved in the failure.
    _write_json(
        bundle_dir / "relevant-selectors.json",
        redact_dict(selectors if isinstance(selectors, (dict, list)) else {}),
    )
    _mark("relevant-selectors.json", absent=not selectors,
          reason="" if selectors else "no selector candidates recorded")

    # run-context.redacted.json — account/site/brand context, credential
    # keys dropped before redaction.
    _write_json(bundle_dir / "run-context.redacted.json", redact_dict(_clean_run_context(run_context)))
    _mark("run-context.redacted.json", absent=not run_context,
          reason="" if run_context else "no run context recorded")

    # screenshot-withheld.json — raw screenshots stay in their private
    # location; the bundle only records a withheld marker.
    _write_json(
        bundle_dir / "screenshot-withheld.json",
        {
            "withheld": True,
            "reason": "stage-5 default",
            "original_private_path": str(screenshot_path or ""),
        },
    )
    _mark("screenshot-withheld.json", absent=not screenshot_path,
          reason="" if screenshot_path else "no screenshot captured")

    # Optional artifacts — absent when not provided.
    if dom_contract is not None:
        _write_json(bundle_dir / "dom-contract.json", redact_dict(dom_contract))
        _mark("dom-contract.json")
    else:
        _mark("dom-contract.json", absent=True, reason="no DOM contract recorded")

    if previous_success is not None:
        _write_json(bundle_dir / "previous-success.json", redact_dict(previous_success))
        _mark("previous-success.json")
    else:
        _mark("previous-success.json", absent=True, reason="no previous success reference")

    _write_json(
        bundle_dir / "manifest.json",
        {
            "incident_id": int(incident_id),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
        },
    )
    return bundle_dir
