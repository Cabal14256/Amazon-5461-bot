"""Evidence listing and allowlist path resolution.

Only files under the allowlist roots (``runtime/evidence``, ``runtime/logs``)
are ever served.  Paths are resolved with ``Path.resolve()`` and must remain
inside one of the roots, which rejects ``..`` segments, absolute paths and
symlinks that escape the roots.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.db import get_conn

TEXT_EXTENSIONS = {".log", ".txt", ".json", ".html", ".htm", ".md", ".csv", ".out", ".err"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_LIST_ITEMS = 500


class PathNotAllowed(Exception):
    """Raised when a requested path escapes every allowlist root."""


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return "binary"


def resolve_allowed_path(allowed_roots: Iterable[Path], rel_path: str) -> tuple[Path, Path]:
    """Resolve ``rel_path`` against the allowlist roots.

    Returns ``(resolved_path, root)``.  Raises :class:`PathNotAllowed` for
    absolute paths, ``..`` escapes and symlink escapes;
    :class:`FileNotFoundError` when nothing matches.
    """
    text = str(rel_path or "").strip()
    if not text:
        raise FileNotFoundError("empty path")
    candidate_input = Path(text)
    if candidate_input.is_absolute() or candidate_input.drive:
        raise PathNotAllowed(text)
    for root in allowed_roots:
        root_resolved = Path(root).resolve()
        resolved = (root_resolved / text).resolve()
        if resolved != root_resolved and root_resolved not in resolved.parents:
            # Escapes this root (.., symlink) — keep checking other roots.
            continue
        if resolved.is_file():
            return resolved, root_resolved
    raise FileNotFoundError(text)


def _relative_to_root(resolved: Path, root: Path) -> str:
    return resolved.relative_to(root).as_posix()


def _item_from_path(resolved: Path, root: Path, root_name: str) -> dict[str, Any]:
    stat = resolved.stat()
    return {
        "path": _relative_to_root(resolved, root),
        "root": root_name,
        "type": classify_file(resolved),
        "size": int(stat.st_size),
        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def list_evidence(
    db_path: str,
    evidence_root: Path,
    logs_root: Path,
    *,
    date: str | None = None,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """List evidence items; DB index first, filesystem scan as fallback."""
    items = _list_from_db(db_path, evidence_root, date=date, account_id=account_id)
    if items:
        return items[:MAX_LIST_ITEMS]
    return _list_from_filesystem(evidence_root, date=date, account_id=account_id)[:MAX_LIST_ITEMS]


def _list_from_db(db_path: str, evidence_root: Path, *,
                  date: str | None, account_id: str | None) -> list[dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    sql = "SELECT evidence_type, file_path, created_at FROM evidence_items WHERE 1=1"
    params: list[Any] = []
    if date:
        sql += " AND substr(created_at, 1, 10)=?"
        params.append(date)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(MAX_LIST_ITEMS)
    try:
        conn = get_conn(db_path)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    except Exception:
        return []
    root_resolved = Path(evidence_root).resolve()
    items: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row["file_path"] or "")
        if not raw:
            continue
        candidate = Path(raw)
        resolved = candidate.resolve() if candidate.is_absolute() else (root_resolved / raw).resolve()
        if resolved == root_resolved or root_resolved not in resolved.parents:
            continue  # never leak paths outside the evidence root
        if not resolved.is_file():
            continue
        if account_id and account_id not in resolved.as_posix():
            continue
        items.append(_item_from_path(resolved, root_resolved, "evidence"))
    return items


def _list_from_filesystem(evidence_root: Path, *,
                          date: str | None, account_id: str | None) -> list[dict[str, Any]]:
    root_resolved = Path(evidence_root).resolve()
    if not root_resolved.is_dir():
        return []
    if date:
        base = (root_resolved / date).resolve()
        if base == root_resolved or root_resolved not in base.parents or not base.is_dir():
            return []
        scan_roots = [base]
    else:
        scan_roots = sorted(
            [p for p in root_resolved.iterdir() if p.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )[:7]
    items: list[dict[str, Any]] = []
    for scan_root in scan_roots:
        for path in sorted(scan_root.rglob("*")):
            if len(items) >= MAX_LIST_ITEMS:
                return items
            if not path.is_file() or path.is_symlink():
                continue
            if account_id and account_id not in path.as_posix():
                continue
            try:
                items.append(_item_from_path(path, root_resolved, "evidence"))
            except OSError:
                continue
    return items
