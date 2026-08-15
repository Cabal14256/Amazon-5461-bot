"""Stage-6 Codex read-only triage client.

Subprocess wrapper around the Codex CLI (read-only sandbox) plus the web-side
auto-triage scanner.  Everything degrades cleanly — an unavailable CLI, a
timeout, an exhausted daily quota or an invalid result never touches the main
automation and never overwrites the detector's classification.
"""

from src.codex_client.auto_triage import AutoTriageRunner, run_auto_triage_scan
from src.codex_client.availability import CodexAvailability, check_availability
from src.codex_client.triage import run_triage, validate_triage_result

__all__ = [
    "AutoTriageRunner",
    "CodexAvailability",
    "check_availability",
    "run_auto_triage_scan",
    "run_triage",
    "validate_triage_result",
]
