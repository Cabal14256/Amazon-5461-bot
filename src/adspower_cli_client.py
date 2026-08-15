#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional AdsPower CLI backend.

This wraps `npx adspower-browser` behind a small interface compatible with the
parts of AdsPowerClient used by the 5461 helper scripts. The default production
backend remains the direct Local API client; this class is only used when
config/settings.yaml sets adspower.backend: cli.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from src.windows_subprocess import no_window_kwargs


class AdsPowerCliError(Exception):
    pass


class AdsPowerCliClient:
    def __init__(self, cli_command: str = "npx adspower-browser", timeout: int = 60, api_key: Optional[str] = None):
        self.cli_command = cli_command
        self.timeout = timeout
        self.api_key = api_key or ""

    def check_status(self) -> Dict[str, Any]:
        res = self._run(["check-status"], timeout=min(self.timeout, 30), allow_failure=True)
        text = f"{res.get('stdout', '')}\n{res.get('stderr', '')}".lower()
        ok = bool(res.get("ok")) and "not running" not in text
        return {"ok": ok, "version_used": "cli", "raw": res, "status": "running" if ok else "not_running"}

    def start_profile(
        self,
        profile_id: Optional[str] = None,
        serial_number: Optional[str] = None,
        open_tabs: Optional[int] = None,
        headless: Optional[int] = None,
        launch_args: Optional[List[str]] = None,
        proxy_detection: Optional[int] = None,
        last_opened_tabs: Optional[int] = None,
        cdp_mask: Optional[int] = None,
        delete_cache: Optional[int] = None,
        **extra_kwargs: Any,
    ) -> Dict[str, Any]:
        self._require_profile_identifier(profile_id, serial_number)
        body: Dict[str, Any] = {}
        if profile_id:
            body["profile_id"] = str(profile_id)
        if serial_number:
            body["profile_no"] = str(serial_number)
        if launch_args is not None:
            body["launch_args"] = launch_args
        if open_tabs is not None:
            # CLI uses last_opened_tabs/ip_tab rather than v1 open_tabs; keep this for compatibility only.
            body["last_opened_tabs"] = str(int(open_tabs))
        for k, v in {
            "headless": headless,
            "last_opened_tabs": last_opened_tabs,
            "proxy_detection": proxy_detection,
            "cdp_mask": cdp_mask,
            "delete_cache": delete_cache,
        }.items():
            if v is not None:
                body[k] = str(int(v))
        for k, v in extra_kwargs.items():
            if v is not None:
                body[k] = v

        arg = json.dumps(body, ensure_ascii=False)
        raw = self._run(["open-browser", arg], timeout=self.timeout)
        result = self._normalize_start_response(raw, profile_id)
        if not result.get("ws_endpoint"):
            raise AdsPowerCliError(f"open-browser returned no ws endpoint: {self._safe(raw)}")
        return result

    def stop_profile(self, profile_id: Optional[str] = None, serial_number: Optional[str] = None) -> Dict[str, Any]:
        self._require_profile_identifier(profile_id, serial_number)
        ident = str(profile_id or serial_number)
        raw = self._run(["close-browser", ident], timeout=self.timeout)
        return {"ok": True, "version_used": "cli", "raw": raw}

    def check_profile_active(self, profile_id: Optional[str] = None, serial_number: Optional[str] = None) -> Dict[str, Any]:
        self._require_profile_identifier(profile_id, serial_number)
        ident = str(profile_id or serial_number)
        raw = self._run(["get-browser-active", ident], timeout=self.timeout, allow_failure=True)
        parsed = raw.get("json") if isinstance(raw.get("json"), dict) else {}
        data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        ws_endpoint = self._extract_ws_endpoint(data) or self._extract_ws_endpoint(parsed) or self._extract_ws_endpoint(raw)
        text = f"{raw.get('stdout', '')}\n{raw.get('stderr', '')}".lower()
        status_text = str(data.get("status") or data.get("state") or "").strip()
        is_active = bool(ws_endpoint) or status_text.lower() in {"active", "open", "opened", "running", "1", "true"}
        if "not running" in text or "not active" in text or "closed" in text:
            is_active = False
        return {
            "ok": bool(raw.get("ok")),
            "version_used": "cli",
            "raw": raw,
            "profile_id": profile_id,
            "is_active": is_active,
            "status": status_text or ("active" if is_active else None),
            "ws_endpoint": ws_endpoint,
            "selenium_endpoint": self._extract_selenium_endpoint(data) or self._extract_selenium_endpoint(parsed),
            "webdriver": data.get("webdriver") if isinstance(data, dict) else None,
            "debug_port": data.get("debug_port") if isinstance(data, dict) else None,
        }

    def ensure_profile_started(self, profile_id: str, serial_number: Optional[str] = None, **start_kwargs: Any) -> Dict[str, Any]:
        active = self.check_profile_active(profile_id=profile_id, serial_number=serial_number)
        if active.get("is_active") and active.get("ws_endpoint"):
            active["started_now"] = False
            return active
        launch_args = list(start_kwargs.pop("launch_args", []) or [])
        if "--remote-allow-origins=*" not in launch_args:
            launch_args.append("--remote-allow-origins=*")
        started = self.start_profile(profile_id=profile_id, serial_number=serial_number, launch_args=launch_args, **start_kwargs)
        started["started_now"] = True
        return started

    def query_profiles(
        self,
        group_id: Optional[str] = None,
        search_value: Optional[str] = None,
        page: int = 1,
        page_size: int = 200,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"page": int(page), "limit": int(page_size)}
        if group_id:
            params["group_id"] = str(group_id)
        if search_value:
            params["name"] = str(search_value)
        raw = self._run(["get-browser-list", json.dumps(params, ensure_ascii=False)], timeout=self.timeout)
        parsed = raw.get("json") if isinstance(raw.get("json"), dict) else {}
        data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        profiles = []
        if isinstance(data, dict):
            for key in ("list", "profiles", "items"):
                if isinstance(data.get(key), list):
                    profiles = data.get(key) or []
                    break
        normalized = [self._normalize_profile(p) for p in profiles if isinstance(p, dict)]
        total = data.get("total_count") or data.get("total") or len(normalized) if isinstance(data, dict) else len(normalized)
        return {"ok": True, "version_used": "cli", "profiles": normalized, "total": total, "page": page, "page_size": page_size, "raw": raw}

    def _run(self, args: List[str], timeout: Optional[int] = None, allow_failure: bool = False) -> Dict[str, Any]:
        cmd = self._command_parts() + args
        if self.api_key and "-k" not in cmd and "--api-key" not in cmd:
            cmd.extend(["--api-key", self.api_key])
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=timeout or self.timeout,
                check=False,
                **no_window_kwargs(),
            )
        except FileNotFoundError as exc:
            raise AdsPowerCliError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdsPowerCliError(f"command timed out after {timeout or self.timeout}s: {' '.join(cmd)}") from exc
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        parsed = self._parse_json_from_output(stdout)
        result = {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": stdout.strip(), "stderr": stderr.strip(), "json": parsed}
        if proc.returncode != 0 and not allow_failure:
            raise AdsPowerCliError(f"CLI failed ({proc.returncode}): {self._safe(result)}")
        return result

    def _command_parts(self) -> List[str]:
        import shlex

        parts = shlex.split(self.cli_command, posix=False)
        if parts:
            resolved = shutil.which(parts[0])
            if resolved:
                parts[0] = resolved
        return parts

    def _parse_json_from_output(self, text: str) -> Any | None:
        text = (text or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        starts = [idx for idx in (text.find("{"), text.find("[")) if idx >= 0]
        for start in sorted(starts):
            try:
                return json.loads(text[start:])
            except Exception:
                continue
        return None

    def _normalize_profile(self, p: Dict[str, Any]) -> Dict[str, Any]:
        username = p.get("username") or p.get("login") or p.get("email")
        return {
            "profile_id": p.get("profile_id") or p.get("user_id") or p.get("id"),
            "profile_no": p.get("profile_no") or p.get("serial_number"),
            "serial_number": p.get("serial_number") or p.get("profile_no"),
            "profile_name": p.get("name") or p.get("profile_name"),
            "name": p.get("name") or p.get("profile_name"),
            "remark": p.get("remark") or p.get("note"),
            "group_id": p.get("group_id"),
            "username": username,
            "email": p.get("email") or username,
            "raw": p,
        }

    def _normalize_start_response(self, raw: Dict[str, Any], profile_id: Optional[str]) -> Dict[str, Any]:
        parsed = raw.get("json") if isinstance(raw.get("json"), dict) else {}
        data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        return {
            "ok": True,
            "version_used": "cli",
            "raw": raw,
            "ws_endpoint": self._extract_ws_endpoint(data) or self._extract_ws_endpoint(parsed) or self._extract_ws_endpoint(raw),
            "selenium_endpoint": self._extract_selenium_endpoint(data) or self._extract_selenium_endpoint(parsed),
            "webdriver": data.get("webdriver") if isinstance(data, dict) else None,
            "debug_port": data.get("debug_port") if isinstance(data, dict) else None,
            "profile_id": profile_id,
            "status": data.get("status") if isinstance(data, dict) else None,
        }

    def _extract_ws_endpoint(self, node: Any) -> Optional[str]:
        if not isinstance(node, dict):
            return None
        ws = node.get("ws") if isinstance(node.get("ws"), dict) else {}
        return (
            ws.get("puppeteer")
            or ws.get("cdp")
            or ws.get("playwright")
            or node.get("puppeteer")
            or node.get("puppeteer_ws")
            or node.get("ws_endpoint")
            or node.get("cdp_ws")
        )

    def _extract_selenium_endpoint(self, node: Any) -> Optional[str]:
        if not isinstance(node, dict):
            return None
        ws = node.get("ws") if isinstance(node.get("ws"), dict) else {}
        return ws.get("selenium") or node.get("selenium") or node.get("selenium_endpoint")

    def _require_profile_identifier(self, profile_id: Optional[str], serial_number: Optional[str]) -> None:
        if not profile_id and not serial_number:
            raise ValueError("必须传 profile_id 或 serial_number 至少一个")

    def _safe(self, value: Any) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
        text = re.sub(r"(?i)(api[-_ ]?key|authorization|bearer)(['\"=: ]+)[^\s,'\"]+", r"\1\2***", text)
        return text[:1000]
