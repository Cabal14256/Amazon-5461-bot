"""Redacted evidence bundles for repair incidents.

Each incident gets a directory ``<evidence_root>/incidents/<id>/`` with a
fixed set of artifacts. Everything written here is redacted through
``src.capture.redact`` and raw screenshots are never copied; the marker keeps
only a boolean that a private source existed. Page-visible text is wrapped in
untrusted-data markers so downstream consumers (including LLM-based triage in
later stages) treat it as data, never as instructions.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from src.capture.redact import redact_text

UNTRUSTED_BEGIN = "<<<UNTRUSTED_PAGE_DATA>>>"
UNTRUSTED_END = "<<<END_UNTRUSTED_PAGE_DATA>>>"
_MAX_VISIBLE_TEXT_CHARS = 8192

# Identity/business fields are not needed to diagnose shared code and must not
# enter Codex-readable evidence snapshots.
_PRIVATE_KEY_MARKERS = (
    "account",
    "brand",
    "profile",
    "email",
    "username",
    "password",
    "cookie",
    "token",
    "case_id",
    "sku",
    "asin",
    "original_private_path",
)
_TEXT_EVIDENCE_SUFFIXES = {".json", ".txt", ".html", ".htm", ".md", ".csv"}

_VISIBLE_TEXT_KEYS = ("visible_text", "body_text", "text", "page_text")
REQUIRED_EVIDENCE = (
    "page_summary",
    "selectors_and_probes",
    "dom_shadow_contract",
    "previous_success",
)


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


def _identifier_patterns(run_context) -> list[tuple[str, str]]:
    if not isinstance(run_context, dict):
        return []
    patterns = []
    for key, replacement in (
        ("account_id", "[REDACTED_ACCOUNT]"),
        ("brand_name", "[REDACTED_BRAND]"),
        ("adspower_profile_id", "[REDACTED_PROFILE]"),
    ):
        value = str(run_context.get(key) or "").strip()
        if value:
            patterns.append((re.escape(value), replacement))
    return patterns


def _scrub_private_fields(obj, extra_patterns: list[tuple[str, str]]):
    if isinstance(obj, dict):
        return {
            key: _scrub_private_fields(value, extra_patterns)
            for key, value in obj.items()
            if not any(marker in str(key).lower() for marker in _PRIVATE_KEY_MARKERS)
        }
    if isinstance(obj, list):
        return [_scrub_private_fields(value, extra_patterns) for value in obj]
    if isinstance(obj, str):
        return redact_text(obj, extra_patterns)
    return obj


def _clean_run_context(run_context, extra_patterns: list[tuple[str, str]]) -> dict:
    if not isinstance(run_context, dict):
        return {}
    return _scrub_private_fields(run_context, extra_patterns)


def sanitize_payload(payload, *, run_context=None):
    """Return a de-identified payload suitable for repair fixtures/Codex."""
    return _scrub_private_fields(payload, _identifier_patterns(run_context))


def _selectors_ready(selectors) -> bool:
    if not isinstance(selectors, dict):
        return False
    candidates = selectors.get("declared_candidates") or selectors.get("candidates")
    probes = selectors.get("probes") or selectors.get("selector_probes")
    return bool(candidates) and isinstance(probes, dict) and bool(probes)


def _dom_contract_ready(dom_contract) -> bool:
    return (
        isinstance(dom_contract, dict)
        and int(dom_contract.get("schema_version") or 0) >= 1
        and isinstance(dom_contract.get("nodes"), list)
        and bool(dom_contract.get("nodes"))
    )


def _previous_success_ready(previous_success) -> bool:
    return (
        isinstance(previous_success, dict)
        and bool(previous_success.get("contract_hash"))
        and isinstance(previous_success.get("contract"), dict)
        and bool(previous_success.get("contract"))
    )


def assess_evidence_payloads(*, page_evidence, selectors, dom_contract, previous_success) -> dict:
    """Evaluate the strict pre-Codex evidence contract."""
    missing: list[str] = []
    if not isinstance(page_evidence, dict) or not page_evidence:
        missing.append("page_summary")
    if not _selectors_ready(selectors):
        missing.append("selectors_and_probes")
    if not _dom_contract_ready(dom_contract):
        missing.append("dom_shadow_contract")
    if not _previous_success_ready(previous_success):
        missing.append("previous_success")
    return {
        "status": "ready" if not missing else "incomplete",
        "missing": missing,
        "required": list(REQUIRED_EVIDENCE),
    }


def assess_evidence_bundle(bundle_dir: Path) -> dict:
    """Read a bundle manifest without trusting caller-provided gate state."""
    manifest_path = Path(bundle_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "incomplete", "missing": list(REQUIRED_EVIDENCE)}
    gate = manifest.get("evidence_gate") if isinstance(manifest, dict) else None
    if not isinstance(gate, dict):
        return {"status": "incomplete", "missing": list(REQUIRED_EVIDENCE)}
    missing = gate.get("missing") if isinstance(gate.get("missing"), list) else list(REQUIRED_EVIDENCE)
    return {
        "status": "ready" if gate.get("status") == "ready" and not missing else "incomplete",
        "missing": [str(item) for item in missing],
    }


def copy_sanitized_evidence_bundle(
    source_dir: Path,
    destination_dir: Path,
    *,
    incident: dict,
) -> list[str]:
    """Create a text-only, de-identified evidence snapshot for Codex."""
    source = Path(source_dir)
    destination = Path(destination_dir)
    if not source.is_dir():
        return []
    patterns = _identifier_patterns({
        "account_id": incident.get("account_id"),
        "brand_name": incident.get("brand_name"),
        "adspower_profile_id": incident.get("adspower_profile_id"),
    })
    copied: list[str] = []
    for child in sorted(source.iterdir()):
        if (
            not child.is_file()
            or child.is_symlink()
            or child.suffix.lower() not in _TEXT_EVIDENCE_SUFFIXES
        ):
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / child.name
        if child.name == "screenshot-withheld.json":
            try:
                raw_marker = json.loads(child.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw_marker = {}
            marker = {
                "withheld": True,
                "reason": "privacy_policy",
                "original_private_path_recorded": bool(
                    isinstance(raw_marker, dict)
                    and raw_marker.get("original_private_path")
                ),
            }
            _write_json(target, marker)
        elif child.suffix.lower() == ".json":
            try:
                payload = json.loads(child.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                target.write_text("{}\n", encoding="utf-8")
            else:
                _write_json(target, _scrub_private_fields(payload, patterns))
        else:
            text = child.read_text(encoding="utf-8", errors="replace")
            target.write_text(redact_text(text, patterns), encoding="utf-8")
        copied.append(child.name)
    return copied


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
    identifier_patterns = _identifier_patterns(run_context)

    files: dict[str, dict] = {}

    def _mark(name: str, absent: bool = False, reason: str = "") -> None:
        entry: dict = {"name": name, "absent": absent}
        if reason:
            entry["reason"] = reason
        files[name] = entry

    # page-summary.redacted.json — structured page evidence, redacted.
    _write_json(
        bundle_dir / "page-summary.redacted.json",
        _scrub_private_fields(
            page_evidence if isinstance(page_evidence, (dict, list)) else {},
            identifier_patterns,
        ),
    )
    _mark("page-summary.redacted.json", absent=not page_evidence,
          reason="" if page_evidence else "no page evidence captured")

    # visible-text.redacted.txt — capped, redacted, wrapped as untrusted data.
    visible = _extract_visible_text(page_evidence)
    truncated = redact_text(visible[:_MAX_VISIBLE_TEXT_CHARS], identifier_patterns)
    (bundle_dir / "visible-text.redacted.txt").write_text(
        f"{UNTRUSTED_BEGIN}\n{truncated}\n{UNTRUSTED_END}\n", encoding="utf-8"
    )
    _mark("visible-text.redacted.txt", absent=not bool(visible.strip()),
          reason="" if visible.strip() else "no visible text captured")

    # relevant-selectors.json — selector candidates involved in the failure.
    _write_json(
        bundle_dir / "relevant-selectors.json",
        _scrub_private_fields(
            selectors if isinstance(selectors, (dict, list)) else {},
            identifier_patterns,
        ),
    )
    _mark("relevant-selectors.json", absent=not selectors,
          reason="" if selectors else "no selector candidates recorded")

    # run-context.redacted.json — site/detector context only; identity and
    # credential fields are dropped before redaction.
    _write_json(
        bundle_dir / "run-context.redacted.json",
        _clean_run_context(run_context, identifier_patterns),
    )
    _mark("run-context.redacted.json", absent=not run_context,
          reason="" if run_context else "no run context recorded")

    # screenshot-withheld.json — raw screenshots stay in their private
    # location; the bundle only records a withheld marker.
    _write_json(
        bundle_dir / "screenshot-withheld.json",
        {
            "withheld": True,
            "reason": "privacy_policy",
            "original_private_path_recorded": bool(screenshot_path),
        },
    )
    _mark("screenshot-withheld.json", absent=not screenshot_path,
          reason="" if screenshot_path else "no screenshot captured")

    # Optional artifacts — absent when not provided.
    if dom_contract is not None:
        _write_json(
            bundle_dir / "dom-contract.json",
            _scrub_private_fields(dom_contract, identifier_patterns),
        )
        _mark("dom-contract.json")
    else:
        _mark("dom-contract.json", absent=True, reason="no DOM contract recorded")

    if previous_success is not None:
        _write_json(
            bundle_dir / "previous-success.json",
            _scrub_private_fields(previous_success, identifier_patterns),
        )
        _mark("previous-success.json")
    else:
        _mark("previous-success.json", absent=True, reason="no previous success reference")

    gate = assess_evidence_payloads(
        page_evidence=page_evidence,
        selectors=selectors,
        dom_contract=dom_contract,
        previous_success=previous_success,
    )
    _write_json(
        bundle_dir / "manifest.json",
        {
            "incident_id": int(incident_id),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
            "evidence_gate": gate,
        },
    )
    return bundle_dir
