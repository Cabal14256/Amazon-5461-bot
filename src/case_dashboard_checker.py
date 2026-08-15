"""
Case Dashboard checker for Amazon 5461 automation.

This module is intentionally conservative: it only inspects Seller Central
Case Dashboard pages and records evidence. It never clicks submit/resume
buttons and never mutates Excel/SQLite ledgers.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

CASE_DASHBOARD_URLS = {
    "amazon.co.uk": "https://sellercentral.amazon.co.uk/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.com": "https://sellercentral.amazon.com/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.de": "https://sellercentral.amazon.de/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.fr": "https://sellercentral.amazon.fr/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.es": "https://sellercentral.amazon.es/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.it": "https://sellercentral.amazon.it/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.com.mx": "https://sellercentral.amazon.com/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.nl": "https://sellercentral.amazon.nl/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.se": "https://sellercentral.amazon.se/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.pl": "https://sellercentral.amazon.pl/hz/myqdashboard/ref=xx_myqd_favb_xx",
}

EU_SITES = {"UK", "BE", "NL", "SE", "DE", "FR", "ES", "IT", "PL"}
SITE_DOMAINS = {
    "US": "amazon.com",
    "CA": "amazon.ca",
    "MX": "amazon.com.mx",
    "UK": "amazon.co.uk",
    "BE": "amazon.co.uk",
    # Seller Central's EU session is shared through amazon.co.uk.  The
    # marketplace switcher selects Germany/France/etc. inside that session;
    # navigating to the retail-country Seller Central domain can instead land
    # on /ap/signin even though the EU portal is already authenticated.
    "NL": "amazon.co.uk",
    "SE": "amazon.co.uk",
    "DE": "amazon.co.uk",
    "FR": "amazon.co.uk",
    "ES": "amazon.co.uk",
    "IT": "amazon.co.uk",
    "PL": "amazon.co.uk",
}

STATUS_PATTERNS = [
    ("under_review", ["under review", "in progress", "pending amazon action", "submitted", "application submitted"]),
    ("draft", ["draft", "go to application", "continue application", "resume application"]),
    ("declined", ["declined", "rejected", "denied"]),
    ("approved", ["approved", "granted", "accepted"]),
]


def _site_to_domain(marketplace: str, current_url: str = "") -> str:
    site = (marketplace or "").upper()
    if site in SITE_DOMAINS:
        return SITE_DOMAINS[site]
    m = re.search(r"sellercentral\.amazon\.([a-z.]+)", current_url or "")
    if m:
        suffix = m.group(1)
        return f"amazon.{suffix}"
    return "amazon.co.uk" if site in EU_SITES else "amazon.com"


def get_case_dashboard_url(marketplace: str, current_url: str = "") -> str:
    domain = _site_to_domain(marketplace, current_url)
    if domain in CASE_DASHBOARD_URLS:
        return CASE_DASHBOARD_URLS[domain]
    return f"https://sellercentral.{domain}/hz/myqdashboard/ref=xx_myqd_favb_xx"


def _navigation_landed(final_url: str, target_url: str) -> bool:
    """Return True only when Seller Central really left the previous page for the requested area."""
    final = (final_url or "").lower()
    target = (target_url or "").lower()
    if not final or final == "about:blank":
        return False
    if "/hz/myqdashboard" in target:
        return "/hz/myqdashboard" in final or "case-dashboard" in final or "/cu/case" in final
    return final.split("?", 1)[0].rstrip("/") == target.split("?", 1)[0].rstrip("/")


def _safe_text(page) -> str:
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


def _safe_screenshot(page, path: Path) -> str | None:
    try:
        page.screenshot(path=str(path), full_page=False, timeout=15000)
        return str(path)
    except Exception:
        try:
            page.screenshot(
                path=str(path),
                full_page=False,
                clip={"x": 0, "y": 0, "width": 1280, "height": 900},
                timeout=10000,
            )
            return str(path)
        except Exception:
            return None


def _navigate_resilient(page, url: str, timeout_ms: int = 60000) -> tuple[bool, str]:
    """Navigate conservatively across Seller Central redirects/account-switcher quirks."""
    errors: list[str] = []
    attempts = [
        ("domcontentloaded", False),
        ("commit", True),
        ("load", True),
    ]
    for wait_until, reset_blank in attempts:
        try:
            if reset_blank:
                try:
                    page.goto("about:blank", wait_until="commit", timeout=15000)
                    time.sleep(1)
                except Exception as reset_err:
                    errors.append(f"reset:{reset_err}")
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            if wait_until == "commit":
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 30000))
                except Exception as load_err:
                    errors.append(f"post_commit_dom:{load_err}")
            time.sleep(4)
            final_url = getattr(page, "url", "")
            if _navigation_landed(final_url, url):
                return True, wait_until
            errors.append(f"{wait_until}:landed_wrong_url:{final_url}")
        except Exception as nav_err:
            errors.append(f"{wait_until}:{nav_err}")
            # net::ERR_ABORTED is common during Seller Central redirects; if a page body exists, salvage it.
            try:
                if page.url and page.url != "about:blank" and _navigation_landed(page.url, url) and _safe_text(page).strip():
                    return True, f"salvaged_after_{wait_until}_error"
            except Exception:
                pass
    return False, " | ".join(errors)[-2000:]


def _brand_windows(text: str, brand_name: str, radius: int = 1800) -> list[str]:
    windows = []
    if not text:
        return windows
    brand_lower = (brand_name or "").lower()
    text_lower = text.lower()
    if brand_lower:
        start = 0
        while True:
            idx = text_lower.find(brand_lower, start)
            if idx < 0:
                break
            windows.append(text[max(0, idx - radius): idx + radius])
            start = idx + len(brand_lower)
    return windows


def _extract_application_id(text: str) -> str | None:
    patterns = [
        r"applicationId[=:]\s*([A-Za-z0-9_-]+)",
        r"application(?:\s+)?id\s*[:#]?\s*([A-Za-z0-9_-]{6,})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _parse_dashboard_rows(text: str) -> list[dict[str, str]]:
    """
    Parse structured rows from the View Selling Applications table.

    Dashboard text layout (innerText) per row:
        <Application name>\\t<Application type>\\t<Changed>\\t<Status>

    Returns list of dicts with keys: name, type, status, case_ids
    """
    rows = []
    # Split on tab-separated chunks; rows are separated by blank lines
    # Typical pattern after "Application name\tCase ID\t..." header:
    #   MP-MALL\n\tCatalog Authorization\t\nJun 13, 2026\n\t\nUnder review\n...
    # We look for lines that are pure brand-name-like strings followed by
    # an application type on the next non-empty token.
    app_types = {
        "catalog authorization",
        "catalogue authorisation",
        "gtin exemption",
        "brand registry",
        "selling application",
    }
    status_map = {
        "under review": "under_review",
        "approved": "approved",
        "draft": "draft",
        "declined": "declined",
        "closed": "closed",
        "pending": "pending",
    }

    lines = [line.strip() for line in text.replace("\t", "\n").splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        # Detect application type line
        line_lower = line.lower()
        if line_lower in app_types:
            # Brand name is the last non-empty line before this
            name = ""
            for j in range(i - 1, max(i - 5, -1), -1):
                if lines[j].strip():
                    name = lines[j].strip()
                    break
            # Status is somewhere in the next ~6 lines
            status_raw = "unknown"
            case_ids = []
            for j in range(i + 1, min(i + 8, len(lines))):
                tok = lines[j].strip().lower()
                if tok in app_types:
                    break
                for raw, mapped in status_map.items():
                    if raw in tok:
                        status_raw = mapped
                        break
                for cid in re.findall(r"(?<!\d)(\d{10,12})(?!\d)", lines[j]):
                    if cid not in case_ids:
                        case_ids.append(cid)
            rows.append({
                "name": name,
                "type": line_lower,
                "status": status_raw,
                "case_ids": case_ids,
            })
        i += 1
    return rows


def _extract_dashboard_dom_rows(page) -> list[dict[str, Any]]:
    """Read Case IDs hidden in ``kat-link`` attributes on dashboard rows.

    Amazon's data table paints the Case ID from a ``kat-link[label]`` custom
    element.  The number is visible in screenshots but is absent from
    ``document.body.innerText``, so text-only parsing cannot recover it.
    """

    try:
        rows = page.evaluate(
            r"""() => Array.from(document.querySelectorAll('tr')).map(row => {
              const caseIds = [];
              for (const link of row.querySelectorAll('kat-link, a')) {
                const values = [
                  link.getAttribute('label') || '',
                  link.textContent || '',
                  link.getAttribute('href') || ''
                ];
                for (const value of values) {
                  for (const match of value.matchAll(/(?<!\d)(\d{10,12})(?!\d)/g)) {
                    if (!caseIds.includes(match[1])) caseIds.push(match[1]);
                  }
                }
              }
              if (!caseIds.length) return null;
              const nameCell = row.querySelector('.application-name-cell, .application-name-column');
              const name = (nameCell && (nameCell.innerText || nameCell.textContent) || '')
                .trim().replace(/\s+/g, ' ');
              const text = (row.innerText || row.textContent || '').trim().replace(/\s+/g, ' ');
              return {name, text, case_ids: caseIds};
            }).filter(Boolean)"""
        )
    except Exception:
        return []
    return [row for row in (rows or []) if isinstance(row, dict)]


def analyze_dashboard_dom_rows(
    rows: list[dict[str, Any]],
    brand_name: str,
    submitted_at: str | None = None,
) -> dict[str, Any] | None:
    """Return an exact-brand result from structured dashboard DOM rows."""

    target = (brand_name or "").strip().casefold()
    exact_rows = []
    for row in rows or []:
        name = str(row.get("name") or "").strip()
        if name.casefold() != target:
            continue
        text = str(row.get("text") or "")
        text_lower = text.casefold()
        status = "unknown"
        for candidate, keywords in STATUS_PATTERNS:
            if any(keyword in text_lower for keyword in keywords):
                status = candidate
                break
        case_ids = [
            str(value)
            for value in (row.get("case_ids") or [])
            if re.fullmatch(r"\d{10,12}", str(value))
        ]
        exact_rows.append({"text": text, "status": status, "case_ids": case_ids})
    if not exact_rows:
        return None

    catalog_rows = [
        row
        for row in exact_rows
        if "catalog authorization" in row["text"].casefold()
        or "catalogue authorisation" in row["text"].casefold()
    ]
    candidates = catalog_rows or exact_rows
    submitted_date = None
    if submitted_at:
        try:
            submitted_date = datetime.fromisoformat(
                str(submitted_at).replace("Z", "+00:00")
            ).date()
        except ValueError:
            submitted_date = None
    if submitted_date:
        dated_candidates = []
        for row in candidates:
            row_dates = []
            for value, date_format in [
                *[
                    (match, "%b %d, %Y")
                    for match in re.findall(r"\b[A-Z][a-z]{2} \d{1,2}, \d{4}\b", row["text"])
                ],
                *[
                    (match, "%d %b %Y")
                    for match in re.findall(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}\b", row["text"])
                ],
            ]:
                try:
                    row_dates.append(datetime.strptime(value, date_format).date())
                except ValueError:
                    pass
            if any(abs((row_date - submitted_date).days) <= 1 for row_date in row_dates):
                dated_candidates.append(row)
        if dated_candidates:
            candidates = dated_candidates
    priority = {"under_review": 0, "approved": 1, "draft": 2, "declined": 3, "unknown": 9}
    candidates.sort(key=lambda row: priority.get(row["status"], 8))
    case_ids: list[str] = []
    for row in candidates:
        for case_id in row["case_ids"]:
            if case_id not in case_ids:
                case_ids.append(case_id)
    matched_text = " ; ".join(row["text"] for row in candidates)
    return {
        "status": candidates[0]["status"],
        "case_id": case_ids[0] if len(case_ids) == 1 else None,
        "case_ids": case_ids,
        "application_id": _extract_application_id(matched_text),
        "matched_text": matched_text[:1200],
        "brand_mentions": len(candidates),
    }


def analyze_dashboard_text(text: str, brand_name: str) -> dict[str, Any]:
    """Return best-effort status/case information from dashboard text.

    Fixes: when a brand has both a Catalog Authorization row and a GTIN Exemption
    row (named '<brand>-Cell Phones & Accessories'), the old substring-window
    approach would sometimes match the GTIN Draft row and return 'draft' even
    though the Catalog Authorization was already Under Review.

    New approach:
    1. Parse the structured table rows first.
    2. Exact-match on brand name (case-insensitive, full token) for
       'catalog authorization' rows — these are authoritative for sell status.
    3. Fall back to any row that exact-matches the brand name.
    4. Only fall back to the old substring-window path if no rows are found
       (e.g. non-standard page layout).

    Priority: Catalog Authorization > other types; Under Review > Draft.
    """
    brand_lower = (brand_name or "").lower().strip()

    rows = _parse_dashboard_rows(text)

    # Filter to rows whose name exactly matches the brand (not prefix-matches)
    exact_rows = [r for r in rows if r["name"].lower() == brand_lower]

    if exact_rows:
        # Prefer Catalog Authorization rows
        cat_rows = [
            r
            for r in exact_rows
            if r["type"] in {"catalog authorization", "catalogue authorisation"}
        ]
        best_rows = cat_rows if cat_rows else exact_rows

        # Within best_rows, prefer Under Review > Approved > Draft > others
        priority = {"under_review": 0, "approved": 1, "draft": 2, "declined": 3, "closed": 4, "pending": 5, "unknown": 6}
        best_rows.sort(key=lambda r: priority.get(r["status"], 99))
        best = best_rows[0]

        all_case_ids = []
        for r in best_rows:
            for cid in r["case_ids"]:
                if cid not in all_case_ids:
                    all_case_ids.append(cid)

        matched_summary = "; ".join(
            f"{r['name']} [{r['type']}] → {r['status']}" + (f" {r['case_ids']}" if r['case_ids'] else "")
            for r in exact_rows
        )
        return {
            "status": best["status"],
            "case_id": (best["case_ids"] or all_case_ids or [None])[0],
            "case_ids": all_case_ids,
            "application_id": _extract_application_id(text),
            "matched_text": matched_summary[:1200],
            "brand_mentions": len(exact_rows),
        }

    # Fallback: old substring-window path (non-standard layout)
    windows = _brand_windows(text, brand_name)
    scope = "\n---\n".join(windows) if windows else text[:4000]
    scope_lower = scope.lower()

    case_ids = []
    for cid in re.findall(r"(?<!\d)(\d{10,12})(?!\d)", scope):
        if cid not in case_ids:
            case_ids.append(cid)

    status = "unknown"
    for candidate, keywords in STATUS_PATTERNS:
        if any(k in scope_lower for k in keywords):
            status = candidate
            break
    if not windows and not case_ids and brand_name and brand_name.lower() not in text.lower():
        status = "not_found"
    elif windows and not case_ids and status == "unknown":
        status = "unknown"

    return {
        "status": status,
        "case_id": case_ids[0] if case_ids else None,
        "case_ids": case_ids,
        "application_id": _extract_application_id(scope),
        "matched_text": scope[:1200],
        "brand_mentions": len(windows),
    }


def check_case_dashboard_for_brand(
    page,
    account_id: str,
    marketplace: str,
    brand_name: str,
    evidence_dir: Path,
    timeout_sec: int = 60,
    submitted_at: str | None = None,
) -> dict[str, Any]:
    """
    Inspect Case Dashboard for a brand.

    Returns a stable shape suitable for embedding in submit/batch results.
    """
    out_dir = Path(evidence_dir) / "dashboard_check"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_files: list[str] = []
    started = datetime.now().isoformat()

    result: dict[str, Any] = {
        "checked": False,
        "status": "unknown",
        "case_id": None,
        "case_ids": [],
        "application_id": None,
        "matched_text": "",
        "dashboard_url": "",
        "selling_applications_url": "",
        "evidence_files": evidence_files,
        "error": "",
        "started_at": started,
        "completed_at": None,
    }

    try:
        current_url = ""
        try:
            current_url = page.url
        except Exception:
            pass
        dashboard_url = get_case_dashboard_url(marketplace, current_url)
        result["dashboard_url"] = dashboard_url

        print(f"[DashboardCheck] 打开 Case Dashboard: {dashboard_url}")
        nav_ok, nav_note = _navigate_resilient(page, dashboard_url, timeout_ms=max(30000, timeout_sec * 1000))
        final_url = getattr(page, "url", "")
        result["dashboard_navigation"] = {"ok": nav_ok, "note": nav_note, "final_url": final_url}
        if not nav_ok:
            # Navigation failed — the page content is NOT the dashboard.
            # Analysing it would produce a false status (e.g. 'draft' from the
            # Add Product form that was left open after submit).  Return
            # navigation_failed so the caller knows to treat the result as
            # unknown rather than acting on a spurious draft/error status.
            result["error"] = f"dashboard_navigation_failed: {nav_note}"
            result["status"] = "navigation_failed"
            result["checked"] = True
            result["completed_at"] = datetime.now().isoformat()
            shot = _safe_screenshot(page, out_dir / "dashboard.png")
            if shot:
                evidence_files.append(shot)
            summary_path = out_dir / "dashboard_result.json"
            summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            evidence_files.append(str(summary_path))
            print(f"[DashboardCheck] 导航失败，跳过文本分析: status=navigation_failed url={final_url}")
            return result

        try:
            page.evaluate("window.scrollTo(0, 300)")
            time.sleep(1)
        except Exception:
            pass

        shot = _safe_screenshot(page, out_dir / "dashboard.png")
        if shot:
            evidence_files.append(shot)
        text = _safe_text(page)
        text_path = out_dir / "dashboard_text.txt"
        text_path.write_text(text, encoding="utf-8", errors="ignore")
        evidence_files.append(str(text_path))

        analysis = analyze_dashboard_text(text, brand_name)
        dom_rows = _extract_dashboard_dom_rows(page)
        dom_analysis = analyze_dashboard_dom_rows(
            dom_rows,
            brand_name,
            submitted_at=submitted_at,
        )
        if dom_analysis is not None:
            analysis = dom_analysis
            dom_path = out_dir / "dashboard_dom_rows.json"
            dom_path.write_text(json.dumps(dom_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            evidence_files.append(str(dom_path))
        result.update(analysis)
        result["checked"] = True

        if result["status"] in {"not_found", "unknown"} and not result.get("case_id"):
            # Do not fallback to /abis/approval/search: it is not the correct
            # Selling Applications/Draft review URL for this workflow. Draft
            # resume must use a real "Go to application" / application URL
            # discovered from the current page or Case Dashboard.
            result["note"] = "Case Dashboard did not expose a matching Case ID; skipped invalid /abis/approval/search fallback."

        summary_path = out_dir / "dashboard_result.json"
        result["completed_at"] = datetime.now().isoformat()
        summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence_files.append(str(summary_path))
        print(f"[DashboardCheck] 结果: status={result['status']} case_id={result.get('case_id')}")
        return result

    except Exception as e:
        result["checked"] = False
        result["status"] = "error"
        result["error"] = str(e)
        result["completed_at"] = datetime.now().isoformat()
        try:
            error_path = out_dir / "dashboard_result.json"
            error_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            evidence_files.append(str(error_path))
        except Exception:
            pass
        print(f"[DashboardCheck] 异常: {e}")
        return result
