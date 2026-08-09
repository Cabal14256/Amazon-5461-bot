#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AdsPower backend factory.

Default backend is the existing direct Local API client. Set
`adspower.backend: cli` in config/settings.yaml to opt into the adspower-browser
CLI wrapper.
"""

from __future__ import annotations

from typing import Any

from src.adspower_client import AdsPowerClient
from src.adspower_cli_client import AdsPowerCliClient


def create_adspower_client(settings: dict[str, Any] | None = None):
    settings = settings or {}
    cfg = settings.get("adspower", {}) or {}
    backend = str(cfg.get("backend") or "api").strip().lower()

    if backend == "cli":
        return AdsPowerCliClient(
            cli_command=cfg.get("cli_command") or "npx adspower-browser",
            timeout=int(cfg.get("start_profile_timeout_sec") or 60),
            api_key=cfg.get("api_key") or None,
        )

    if backend not in {"api", "local_api", "local-api"}:
        raise ValueError(f"Unsupported AdsPower backend: {backend!r}; expected 'api' or 'cli'")

    return AdsPowerClient(
        api_base_url=cfg.get("api_base_url"),
        api_key=cfg.get("api_key") or None,
        timeout=int(cfg.get("start_profile_timeout_sec") or 25),
    )
