"""Stage-7 patch diff scanning (blueprint §18.1, stage-7 plan §7.4).

The candidate patch (``git diff`` of the repair worktree against its baseline
commit) is scanned immediately after generation:

- **sensitive content** — emails, long digit strings, AdsPower profiles,
  private paths, credential patterns; reuses ``src/capture/redact.py``'s
  ``SENSITIVE_PATTERNS`` on added lines;
- **allowed scope** — every changed file must live under
  ``codex.patch_allowed_paths`` (an R2-specific whitelist relaxation is a
  later stage; out-of-scope files are rejected outright for now);
- **must-reject rules** — blueprint §18.1 as a static, extensible rule table
  (``DIFF_RULES``): each rule inspects the parsed diff and returns
  violations.  Any violation rejects the whole patch
  (job -> ``validation_failed``).

Also hosts the backend risk re-classification (blueprint §17.3): the file-
path based ``backend_risk_level`` is combined with Codex's self-assessed
``risk_level`` taking the stricter of the two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.capture.redact import SENSITIVE_PATTERNS

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}

# Backend risk review by changed file path (blueprint §17.3).
# R3: submit / legal statements / authorization semantics.
_R3_PATH_RE = re.compile(r"(?i)(submit|legal|statement|declaration|authoriz|consent)")
# R2: navigation order / form steps / field semantics.
_R2_PATH_RE = re.compile(r"(?i)(navigat|form(?![a-z])|step)")

# Private locations that must never be referenced by a patch (§18.1 rule 1).
_PRIVATE_PATH_RE = re.compile(
    r"(runtime/private|migration/private|\.env\b|brand_packs/)"
)

# Sensitive content beyond redact.py's generic patterns: AdsPower profile
# ids, hardcoded account ids, Case IDs (§18.1 rule 2).
_EXTRA_SENSITIVE_RES = [
    re.compile(r"(?i)adspower[_-]?profile"),
    re.compile(r"\b[a-z]{2}_store_\d+\b"),
    re.compile(r"(?i)case[-_ ]?id\s*[:=]\s*[\"']?\d{6,}"),
]
_SENSITIVE_RES = [re.compile(pattern) for pattern, _ in SENSITIVE_PATTERNS]

# --submit gate identifiers: net removal is rejected.
_SUBMIT_GATE_KEYWORDS = (
    "--submit",
    "require_human_confirm_before_submit",
)
# CAPTCHA / 2FA / rate-limit handling turned into an automatic bypass.
_BYPASS_ACTION_RE = re.compile(r"(?i)(bypass|skip|auto[-_]?solve|disable)")
_PROTECTION_WORD_RE = re.compile(r"(?i)(captcha|2fa|\botp\b|\bmfa\b|rate[-_]?limit)")
_PAUSE_ON_CAPTCHA_KEYWORD = "pause_on_captcha_or_2fa"
# Draft / missing-Case-ID mapped straight onto a terminal outcome.
_DRAFT_WORD_RE = re.compile(r"(?i)\bdraft\b|no[_ ]?case[_ ]?id|missing[_ ]?case")
_TERMINAL_WORD_RE = re.compile(r"(?i)\b(success|succeeded|failed)\b")
# Evidence saving / Dashboard checks / human-review logic (net removal).
_EVIDENCE_KEYWORDS = (
    "save_evidence",
    "capture_evidence",
    "preserve_evidence",
    "evidence_bundle",
    "dashboard",
    "requires_human_review",
    "human_review",
)
# Unbounded retries / weakened cooldowns (§18.1 rule 7).
_INFINITE_LOOP_RE = re.compile(r"^\s*while\s+(True|1)\s*:")
_COOLDOWN_KEYWORDS = ("cooldown", "rate_limit", "retry_interval", "busy_retry")
# Tests calling the real Seller Central (§18.1 rule 8).
_LIVE_SC_RE = re.compile(r"(?i)(sellercentral|seller[-.]central|https?://[^\"'\s]*amazon\.)")


@dataclass
class FileDiff:
    path: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass
class Violation:
    rule_id: str
    detail: str
    file: str = ""


@dataclass
class ScanReport:
    passed: bool
    violations: list[Violation]
    files: list[str]


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff into per-file added/removed line lists."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    for line in str(diff_text or "").splitlines():
        if line.startswith("diff --git "):
            current = None
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current = FileDiff(path=path)
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current.added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            current.removed.append(line[1:])
    return files


def _net_removed(files: list[FileDiff], keyword: str) -> bool:
    """Keyword present in removed lines but absent from added lines."""
    removed = any(keyword in line for f in files for line in f.removed)
    added = any(keyword in line for f in files for line in f.added)
    return removed and not added


def _check_allowed_scope(files: list[FileDiff], allowed_paths: tuple[str, ...]) -> list[Violation]:
    violations = []
    for f in files:
        if not any(f.path.startswith(prefix) for prefix in allowed_paths):
            violations.append(Violation(
                "path_outside_allowed",
                f"changed file outside patch_allowed_paths: {f.path}",
                file=f.path,
            ))
    return violations


def _check_private_paths(files: list[FileDiff]) -> list[Violation]:
    violations = []
    for f in files:
        if _PRIVATE_PATH_RE.search(f.path):
            violations.append(Violation(
                "private_path_reference", f"private path in diff header: {f.path}", file=f.path,
            ))
            continue
        for line in f.added:
            if _PRIVATE_PATH_RE.search(line):
                violations.append(Violation(
                    "private_path_reference", "added line references a private path", file=f.path,
                ))
                break
    return violations


def _check_sensitive_content(files: list[FileDiff]) -> list[Violation]:
    violations = []
    for f in files:
        for line in f.added:
            hit = any(rx.search(line) for rx in _SENSITIVE_RES) or any(
                rx.search(line) for rx in _EXTRA_SENSITIVE_RES
            )
            if hit:
                violations.append(Violation(
                    "sensitive_content", "added line matches a sensitive pattern", file=f.path,
                ))
                break
    return violations


def _check_submit_gate_removed(files: list[FileDiff]) -> list[Violation]:
    for keyword in _SUBMIT_GATE_KEYWORDS:
        if _net_removed(files, keyword):
            return [Violation(
                "submit_gate_removed", f"net removal of submit gate keyword: {keyword}",
            )]
    return []


def _check_safety_bypass(files: list[FileDiff]) -> list[Violation]:
    for f in files:
        for line in f.added:
            if _BYPASS_ACTION_RE.search(line) and _PROTECTION_WORD_RE.search(line):
                return [Violation(
                    "safety_bypass_introduced",
                    "added line bypasses CAPTCHA/2FA/rate-limit handling", file=f.path,
                )]
    if _net_removed(files, _PAUSE_ON_CAPTCHA_KEYWORD):
        return [Violation(
            "safety_bypass_introduced",
            f"net removal of {_PAUSE_ON_CAPTCHA_KEYWORD}",
        )]
    return []


def _check_draft_terminal_mapping(files: list[FileDiff]) -> list[Violation]:
    for f in files:
        for line in f.added:
            if _DRAFT_WORD_RE.search(line) and _TERMINAL_WORD_RE.search(line):
                return [Violation(
                    "draft_mapped_to_terminal",
                    "added line maps Draft / missing Case ID to a terminal outcome",
                    file=f.path,
                )]
    return []


def _check_evidence_logic_removed(files: list[FileDiff]) -> list[Violation]:
    for keyword in _EVIDENCE_KEYWORDS:
        if _net_removed(files, keyword):
            return [Violation(
                "evidence_logic_removed",
                f"net removal of evidence/dashboard/review keyword: {keyword}",
            )]
    return []


def _check_retry_or_cooldown(files: list[FileDiff]) -> list[Violation]:
    for f in files:
        if f.path.startswith("src/"):
            for line in f.added:
                if _INFINITE_LOOP_RE.match(line):
                    return [Violation(
                        "retry_or_cooldown_weakened",
                        "added unbounded retry loop in production code", file=f.path,
                    )]
    for keyword in _COOLDOWN_KEYWORDS:
        if _net_removed(files, keyword):
            return [Violation(
                "retry_or_cooldown_weakened",
                f"net removal of cooldown/rate-limit keyword: {keyword}",
            )]
    return []


def _check_live_seller_central_in_tests(files: list[FileDiff]) -> list[Violation]:
    for f in files:
        if not f.path.startswith("tests/"):
            continue
        for line in f.added:
            if _LIVE_SC_RE.search(line):
                return [Violation(
                    "live_seller_central_in_tests",
                    "added line calls a live Seller Central endpoint from tests",
                    file=f.path,
                )]
    return []


# Extensible rule table (blueprint §18.1).  Each rule receives the parsed
# diff and returns its violations; any violation rejects the patch.
DIFF_RULES = [
    ("private_path_reference", "私有目录/文件引用", _check_private_paths),
    ("sensitive_content", "私密串（邮箱/长数字/凭据/AdsPower profile）", _check_sensitive_content),
    ("submit_gate_removed", "移除 --submit 安全门禁", _check_submit_gate_removed),
    ("safety_bypass_introduced", "CAPTCHA/2FA/限流处理改为自动绕过", _check_safety_bypass),
    ("draft_mapped_to_terminal", "Draft/无 Case ID 直接映射 success/failed", _check_draft_terminal_mapping),
    ("evidence_logic_removed", "删除证据保存/Dashboard 检查/人工复核逻辑", _check_evidence_logic_removed),
    ("retry_or_cooldown_weakened", "无限重试或缩短冷却", _check_retry_or_cooldown),
    ("live_seller_central_in_tests", "测试中调用真实 Seller Central", _check_live_seller_central_in_tests),
]


def scan_diff(diff_text: str, *, allowed_paths: list[str]) -> ScanReport:
    """Scan a candidate patch diff; ``passed`` is True only with zero hits."""
    files = parse_unified_diff(diff_text)
    paths = tuple(str(p) for p in allowed_paths)
    violations: list[Violation] = list(_check_allowed_scope(files, paths))
    for _rule_id, _description, check in DIFF_RULES:
        violations.extend(check(files))
    return ScanReport(
        passed=not violations,
        violations=violations,
        files=[f.path for f in files],
    )


# ---------------------------------------------------------------------------
# Backend risk re-classification (blueprint §17.3)
# ---------------------------------------------------------------------------

def classify_file_risk(path: str, allowed_paths: list[str]) -> str:
    """Deterministic per-file risk from its repo-relative path."""
    if _R3_PATH_RE.search(path):
        return "R3"
    if _R2_PATH_RE.search(path):
        return "R2"
    if not any(path.startswith(str(prefix)) for prefix in allowed_paths):
        # Out of scope (also rejected by the scan) — never below R2.
        return "R2"
    if path.startswith("src/"):
        return "R1"
    return "R0"


def stricter_risk(*levels: str) -> str:
    """Return the strictest (highest) of the given R0–R3 levels."""
    known = [level for level in levels if level in RISK_ORDER]
    if not known:
        return "R1"
    return max(known, key=lambda level: RISK_ORDER[level])


def backend_risk_level(changed_files: list[str], allowed_paths: list[str]) -> str:
    """Worst per-file risk across the patch; empty patch is R0."""
    level = "R0"
    for path in changed_files:
        level = stricter_risk(level, classify_file_risk(path, allowed_paths))
    return level
