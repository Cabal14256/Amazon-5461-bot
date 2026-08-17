"""Versioned, redacted and independently verifiable incident evidence bundles."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from src.capture.redact import redact_text
from src.incidents.contracts import (
    EVIDENCE_MANIFEST_VERSION,
    REQUIRED_EVIDENCE,
    SUCCESS_CONTRACT_STATUSES,
    canonical_json_hash,
)
from src.state_files import atomic_write_json

UNTRUSTED_BEGIN = "<<<UNTRUSTED_PAGE_DATA>>>"
UNTRUSTED_END = "<<<END_UNTRUSTED_PAGE_DATA>>>"
_MAX_VISIBLE_TEXT_CHARS = 8192
_TEXT_EVIDENCE_SUFFIXES = {".json", ".txt", ".html", ".htm", ".md", ".csv"}
_VISIBLE_TEXT_KEYS = ("visible_text", "body_text", "text", "page_text")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

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
_PUBLIC_DIMENSION_KEYS = {
    "flow_type",
    "marketplace",
    "page_family",
    "evidence_node",
    "source_status",
    "schema_version",
    "contract_hash",
}


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _write_json(path: Path, payload) -> None:
    atomic_write_json(path, payload)


def _file_hash(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_one_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def strip_url_queries(value: str) -> str:
    """Remove query strings/fragments from URL-shaped evidence text."""
    if not value:
        return value
    if _URL_RE.fullmatch(value):
        return _strip_one_url(value)
    return _URL_RE.sub(lambda match: _strip_one_url(match.group(0)), value)


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
        ("case_id", "[REDACTED_CASE]"),
        ("sku", "[REDACTED_SKU]"),
        ("synced_sku", "[REDACTED_SKU]"),
        ("asin", "[REDACTED_ASIN]"),
    ):
        value = str(run_context.get(key) or "").strip()
        if value:
            patterns.append((re.escape(value), replacement))
    return patterns


def _scrub_private_fields(obj, extra_patterns: list[tuple[str, str]]):
    if isinstance(obj, dict):
        scrubbed = {}
        for key, value in obj.items():
            normalized_key = str(key).lower()
            if any(marker in normalized_key for marker in _PRIVATE_KEY_MARKERS):
                continue
            if normalized_key in _PUBLIC_DIMENSION_KEYS and isinstance(value, str):
                scrubbed[key] = strip_url_queries(value)
            else:
                scrubbed[key] = _scrub_private_fields(value, extra_patterns)
        return scrubbed
    if isinstance(obj, list):
        return [_scrub_private_fields(value, extra_patterns) for value in obj]
    if isinstance(obj, str):
        return redact_text(strip_url_queries(obj), extra_patterns)
    return obj


def _clean_run_context(run_context, extra_patterns: list[tuple[str, str]]) -> dict:
    if not isinstance(run_context, dict):
        return {}
    return _scrub_private_fields(run_context, extra_patterns)


def sanitize_payload(payload, *, run_context=None):
    """Return a de-identified payload suitable for repair fixtures/Codex."""
    return _scrub_private_fields(payload, _identifier_patterns(run_context))


def evidence_dimensions(*, page_evidence=None, dom_contract=None, run_context=None) -> dict:
    context = run_context if isinstance(run_context, dict) else {}
    contract = dom_contract if isinstance(dom_contract, dict) else {}
    page = page_evidence if isinstance(page_evidence, dict) else {}
    state = page.get("recognized_state") if isinstance(page.get("recognized_state"), dict) else {}
    page_family = str(contract.get("page_family") or state.get("page_type") or "unknown").lower()
    evidence_node = str(page.get("evidence_node") or page_family or "unknown").lower()
    return {
        "flow_type": str(context.get("flow_type") or "5461"),
        "marketplace": str(context.get("marketplace") or context.get("site") or "").upper(),
        "page_family": page_family,
        "evidence_node": evidence_node,
    }


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


def _previous_success_ready(previous_success, dimensions: dict | None = None) -> bool:
    if not isinstance(previous_success, dict):
        return False
    contract = previous_success.get("contract")
    stored_hash = str(previous_success.get("contract_hash") or "")
    if not isinstance(contract, dict) or not contract or canonical_json_hash(contract) != stored_hash:
        return False
    if str(previous_success.get("source_status") or "") not in SUCCESS_CONTRACT_STATUSES:
        return False
    if dimensions is None:
        return True
    for key in ("flow_type", "marketplace", "page_family", "evidence_node"):
        expected = str(dimensions.get(key) or "")
        actual = str(previous_success.get(key) or "")
        if key == "marketplace":
            expected, actual = expected.upper(), actual.upper()
        else:
            expected, actual = expected.lower(), actual.lower()
        if not expected or expected in {"unknown", "none"} or expected != actual:
            return False
    return True


def assess_evidence_payloads(
    *, page_evidence, selectors, dom_contract, previous_success, dimensions=None
) -> dict:
    """Evaluate the strict pre-Codex evidence contract."""
    missing: list[str] = []
    if not isinstance(page_evidence, dict) or not page_evidence:
        missing.append("page_summary")
    if not _selectors_ready(selectors):
        missing.append("selectors_and_probes")
    if not _dom_contract_ready(dom_contract):
        missing.append("dom_shadow_contract")
    if not _previous_success_ready(previous_success, dimensions):
        missing.append("previous_success")
    return {
        "status": "ready" if not missing else "incomplete",
        "missing": missing,
        "required": list(REQUIRED_EVIDENCE),
    }


def _read_verified_json(bundle_dir: Path, manifest: dict, name: str):
    entry = (manifest.get("files") or {}).get(name)
    if not isinstance(entry, dict) or entry.get("absent") is not False:
        return None
    path = bundle_dir / name
    if not path.is_file() or path.is_symlink():
        return None
    expected_hash = str(entry.get("sha256") or "")
    if not expected_hash or _file_hash(path) != expected_hash:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_evidence_dimensions(bundle_dir: Path) -> dict | None:
    try:
        manifest = json.loads((Path(bundle_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != EVIDENCE_MANIFEST_VERSION:
        return None
    dimensions = manifest.get("match_dimensions")
    return dict(dimensions) if isinstance(dimensions, dict) else None


def assess_evidence_bundle(bundle_dir: Path) -> dict:
    """Recompute the gate from v2 files and hashes, never stored gate state."""
    bundle = Path(bundle_dir)
    try:
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "incomplete", "missing": list(REQUIRED_EVIDENCE)}
    if not isinstance(manifest, dict) or manifest.get("schema_version") != EVIDENCE_MANIFEST_VERSION:
        return {"status": "incomplete", "missing": list(REQUIRED_EVIDENCE)}
    dimensions = manifest.get("match_dimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}
    gate = assess_evidence_payloads(
        page_evidence=_read_verified_json(bundle, manifest, "page-summary.redacted.json"),
        selectors=_read_verified_json(bundle, manifest, "relevant-selectors.json"),
        dom_contract=_read_verified_json(bundle, manifest, "dom-contract.json"),
        previous_success=_read_verified_json(bundle, manifest, "previous-success.json"),
        dimensions=dimensions,
    )
    return {"status": gate["status"], "missing": gate["missing"]}


def refresh_incident_evidence_gate(db_path: str, incident_id: int) -> dict | None:
    """Reassess files and atomically synchronize one incident's DB gate."""
    from src.db import get_incident, set_incident_evidence_bundle

    incident = get_incident(db_path, int(incident_id))
    if incident is None:
        return None
    bundle_path = str(incident.get("evidence_bundle_path") or "")
    gate = assess_evidence_bundle(Path(bundle_path)) if bundle_path else {
        "status": "incomplete",
        "missing": list(REQUIRED_EVIDENCE),
    }
    return set_incident_evidence_bundle(
        db_path,
        int(incident_id),
        bundle_path,
        evidence_status=gate["status"],
        missing_evidence=gate["missing"],
    )


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
    patterns = _identifier_patterns(
        {
            "account_id": incident.get("account_id"),
            "brand_name": incident.get("brand_name"),
            "adspower_profile_id": incident.get("adspower_profile_id"),
        }
    )
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
                    and raw_marker.get("original_private_path_recorded")
                ),
            }
            _write_json(target, marker)
        elif child.suffix.lower() == ".json":
            try:
                payload = json.loads(child.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _atomic_write_text(target, "{}\n")
            else:
                _write_json(target, _scrub_private_fields(payload, patterns))
        else:
            text = child.read_text(encoding="utf-8", errors="replace")
            _atomic_write_text(target, redact_text(strip_url_queries(text), patterns))
        copied.append(child.name)
    return copied


def _mark_file(files: dict[str, dict], path: Path, *, absent: bool, reason: str = "") -> None:
    entry: dict = {"name": path.name, "absent": absent}
    if path.is_file():
        entry["sha256"] = _file_hash(path)
    if reason:
        entry["reason"] = reason
    files[path.name] = entry


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
    """Build a redacted v2 bundle and write its manifest last."""
    bundle_dir = Path(evidence_root) / "incidents" / str(int(incident_id))
    bundle_dir.mkdir(parents=True, exist_ok=True)
    identifier_patterns = _identifier_patterns(run_context)
    sanitized_page = _scrub_private_fields(
        page_evidence if isinstance(page_evidence, (dict, list)) else {}, identifier_patterns
    )
    sanitized_selectors = _scrub_private_fields(
        selectors if isinstance(selectors, (dict, list)) else {}, identifier_patterns
    )
    sanitized_dom = (
        _scrub_private_fields(dom_contract, identifier_patterns)
        if dom_contract is not None
        else None
    )
    sanitized_previous = (
        _scrub_private_fields(previous_success, identifier_patterns)
        if previous_success is not None
        else None
    )
    dimensions = evidence_dimensions(
        page_evidence=sanitized_page,
        dom_contract=sanitized_dom,
        run_context=run_context,
    )
    files: dict[str, dict] = {}

    page_path = bundle_dir / "page-summary.redacted.json"
    _write_json(page_path, sanitized_page)
    _mark_file(
        files,
        page_path,
        absent=not bool(page_evidence),
        reason="" if page_evidence else "no page evidence captured",
    )

    visible = _extract_visible_text(page_evidence)
    visible_path = bundle_dir / "visible-text.redacted.txt"
    visible_text = redact_text(
        strip_url_queries(visible[:_MAX_VISIBLE_TEXT_CHARS]), identifier_patterns
    )
    _atomic_write_text(
        visible_path,
        f"{UNTRUSTED_BEGIN}\n{visible_text}\n{UNTRUSTED_END}\n",
    )
    _mark_file(
        files,
        visible_path,
        absent=not bool(visible.strip()),
        reason="" if visible.strip() else "no visible text captured",
    )

    selectors_path = bundle_dir / "relevant-selectors.json"
    _write_json(selectors_path, sanitized_selectors)
    _mark_file(
        files,
        selectors_path,
        absent=not bool(selectors),
        reason="" if selectors else "no selector candidates recorded",
    )

    context_path = bundle_dir / "run-context.redacted.json"
    _write_json(context_path, _clean_run_context(run_context, identifier_patterns))
    _mark_file(
        files,
        context_path,
        absent=not bool(run_context),
        reason="" if run_context else "no run context recorded",
    )

    screenshot_marker = bundle_dir / "screenshot-withheld.json"
    _write_json(
        screenshot_marker,
        {
            "withheld": True,
            "reason": "privacy_policy",
            "original_private_path_recorded": bool(screenshot_path),
        },
    )
    _mark_file(
        files,
        screenshot_marker,
        absent=not bool(screenshot_path),
        reason="" if screenshot_path else "no screenshot captured",
    )

    for name, payload, reason in (
        ("dom-contract.json", sanitized_dom, "no DOM contract recorded"),
        ("previous-success.json", sanitized_previous, "no previous success reference"),
    ):
        path = bundle_dir / name
        if payload is None:
            path.unlink(missing_ok=True)
            _mark_file(files, path, absent=True, reason=reason)
        else:
            _write_json(path, payload)
            _mark_file(files, path, absent=False)

    gate = assess_evidence_payloads(
        page_evidence=sanitized_page,
        selectors=sanitized_selectors,
        dom_contract=sanitized_dom,
        previous_success=sanitized_previous,
        dimensions=dimensions,
    )
    _write_json(
        bundle_dir / "manifest.json",
        {
            "schema_version": EVIDENCE_MANIFEST_VERSION,
            "incident_id": int(incident_id),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "match_dimensions": dimensions,
            "files": files,
            "evidence_gate": gate,
        },
    )
    return bundle_dir


def rebuild_bundle_with_previous_success(bundle_dir: Path, previous_success: dict) -> Path:
    """Rebuild a v2 bundle from sanitized files with a new exact fixture."""
    bundle = Path(bundle_dir)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    incident_id = int(manifest["incident_id"])

    def read_json(name: str, default):
        try:
            return json.loads((bundle / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    marker = read_json("screenshot-withheld.json", {})
    return build_evidence_bundle(
        incident_id,
        bundle.parent.parent,
        page_evidence=read_json("page-summary.redacted.json", {}),
        run_context=read_json("run-context.redacted.json", {}),
        screenshot_path=(
            "withheld" if marker.get("original_private_path_recorded") else None
        ),
        selectors=read_json("relevant-selectors.json", {}),
        dom_contract=read_json("dom-contract.json", None),
        previous_success=previous_success,
    )
