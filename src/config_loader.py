import json
import os
import yaml
from pathlib import Path


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_accounts_path(default_path: str | Path = "config/accounts.json") -> Path:
    """Resolve the active account config path.

    Priority:
    1. AMAZON5461_ACCOUNTS_PATH, when explicitly set.
    2. Local private config at runtime/private/accounts.json, when present.
    3. The supplied default path, usually config/accounts.json.

    Relative env/default paths are resolved from the current working directory.
    """
    env_path = os.getenv("AMAZON5461_ACCOUNTS_PATH")
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate

    project_root = Path(__file__).resolve().parent.parent
    private_candidates = [
        project_root / "runtime" / "private" / "accounts.json",
        Path.cwd() / "runtime" / "private" / "accounts.json",
    ]
    for candidate in private_candidates:
        if candidate.exists():
            return candidate

    fallback = Path(default_path)
    if not fallback.is_absolute():
        fallback = Path.cwd() / fallback
    return fallback


def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_account(accounts_path: str, account_id: str):
    data = load_json(resolve_accounts_path(accounts_path))
    for acc in data.get("accounts", []):
        if acc.get("account_id") == account_id:
            return acc
    raise ValueError(f"Account not found: {account_id}")


def load_brand_manifest(brand_packs_root: str, brand_name: str):
    path = Path(brand_packs_root) / brand_name / "manifest.json"
    return load_json(str(path))
