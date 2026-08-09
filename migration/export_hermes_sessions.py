"""Export Amazon-5461 Hermes sessions into a private, portable archive.

The exporter opens the Hermes SQLite database read-only and intentionally omits
stored reasoning/system prompts. Tool output is retained because it contains the
operational evidence needed to reconstruct past work. The output directory is
ignored by Git and must remain local.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HERMES_STATE_DB = Path(r"C:\Users\Admin\AppData\Local\hermes\state.db")
OUTPUT_DIR = Path(__file__).resolve().parent / "private" / "hermes-sessions"
PROJECT_NEEDLE = "%amazon-5461-bot%"


def connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def write_jsonl(path: Path, rows) -> tuple[int, int]:
    count = 0
    characters = 0
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            record = dict(row)
            line = json.dumps(record, ensure_ascii=False, default=str)
            handle.write(line + "\n")
            count += 1
            characters += len(line)
    temporary.replace(path)
    return count, characters


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sessions_path = OUTPUT_DIR / "sessions.jsonl"
    messages_path = OUTPUT_DIR / "messages.jsonl"

    with connect_readonly(HERMES_STATE_DB) as database:
        session_rows = list(
            database.execute(
                """
                SELECT id, source, model, parent_session_id, started_at, ended_at,
                       end_reason, message_count, tool_call_count, cwd, title,
                       archived, git_branch, git_repo_root, profile_name
                  FROM sessions
                 WHERE lower(cwd) LIKE ? OR lower(git_repo_root) LIKE ?
                 ORDER BY started_at, id
                """,
                (PROJECT_NEEDLE, PROJECT_NEEDLE),
            )
        )
        session_ids = [row["id"] for row in session_rows]
        if not session_ids:
            raise RuntimeError("No Amazon-5461 Hermes sessions were found")

        placeholders = ",".join("?" for _ in session_ids)
        message_query = f"""
            SELECT id, session_id, role, content, tool_call_id, tool_calls,
                   tool_name, timestamp, finish_reason, active, compacted,
                   display_kind
              FROM messages
             WHERE session_id IN ({placeholders})
             ORDER BY timestamp, id
        """
        message_rows = database.execute(message_query, session_ids)
        session_count, session_characters = write_jsonl(sessions_path, session_rows)
        message_count, message_characters = write_jsonl(messages_path, message_rows)

        role_counts = {
            row["role"]: row["count"]
            for row in database.execute(
                f"""
                SELECT role, count(*) AS count
                  FROM messages
                 WHERE session_id IN ({placeholders})
                 GROUP BY role
                """,
                session_ids,
            )
        }
        source_counts = Counter((row["source"] or "unknown") for row in session_rows)

    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(HERMES_STATE_DB),
        "project_filter": PROJECT_NEEDLE,
        "sessions": session_count,
        "messages": message_count,
        "session_sources": dict(sorted(source_counts.items())),
        "message_roles": dict(sorted(role_counts.items())),
        "files": {
            sessions_path.name: {
                "characters": session_characters,
                "bytes": sessions_path.stat().st_size,
                "sha256": sha256(sessions_path),
            },
            messages_path.name: {
                "characters": message_characters,
                "bytes": messages_path.stat().st_size,
                "sha256": sha256(messages_path),
            },
        },
        "omitted_fields": [
            "system_prompt",
            "model_config",
            "reasoning",
            "reasoning_content",
            "reasoning_details",
            "codex_reasoning_items",
        ],
        "handling": "Local private archive; do not commit, upload, or use as an unfiltered prompt.",
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(manifest_path)

    print(
        json.dumps(
            {
                "sessions": session_count,
                "messages": message_count,
                "archive_mib": round(
                    (sessions_path.stat().st_size + messages_path.stat().st_size)
                    / 1024
                    / 1024,
                    2,
                ),
                "output": str(OUTPUT_DIR),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
