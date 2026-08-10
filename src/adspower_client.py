import json
import os
import socket
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests


class AdsPowerAPIError(Exception):
    pass


class AdsPowerClient:
    def __init__(
        self,
        api_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 25,
        min_interval_sec: float = 1.05,
        verify_ssl: bool = False,
    ):
        self.base_url_candidates = self._build_base_url_candidates(api_base_url)
        self.api_key = api_key or os.getenv("ADSPOWER_API_KEY") or ""
        self.timeout = timeout
        self.min_interval_sec = min_interval_sec
        self.verify_ssl = verify_ssl
        self._last_request_ts = 0.0

    def start_profile(self, profile_id: Optional[str] = None, serial_number: Optional[str] = None,
                      open_tabs: Optional[int] = None, headless: Optional[int] = None,
                      launch_args: Optional[List[str]] = None, proxy_detection: Optional[int] = None,
                      last_opened_tabs: Optional[int] = None, cdp_mask: Optional[int] = None,
                      delete_cache: Optional[int] = None, **extra_kwargs) -> Dict[str, Any]:
        self._require_profile_identifier(profile_id, serial_number)
        v2_body: Dict[str, Any] = {}
        if profile_id:
            v2_body["profile_id"] = str(profile_id)
        if serial_number:
            v2_body["profile_no"] = str(serial_number)
        if launch_args is not None:
            v2_body["launch_args"] = launch_args
        for k, v in {
            "headless": headless,
            "last_opened_tabs": last_opened_tabs,
            "proxy_detection": proxy_detection,
            "cdp_mask": cdp_mask,
            "delete_cache": delete_cache,
        }.items():
            if v is not None:
                v2_body[k] = str(int(v))
        for k, v in extra_kwargs.items():
            if v is not None:
                v2_body[k] = v if isinstance(v, (list, dict)) else str(v)

        v1_params: Dict[str, Any] = {}
        if profile_id:
            v1_params["user_id"] = str(profile_id)
        if serial_number:
            v1_params["serial_number"] = str(serial_number)
        if open_tabs is not None:
            v1_params["open_tabs"] = str(int(open_tabs))
        if headless is not None:
            v1_params["headless"] = str(int(headless))
        if launch_args is not None:
            v1_params["launch_args"] = json.dumps(launch_args) if isinstance(launch_args, list) else str(launch_args)
        if cdp_mask is not None:
            v1_params["cdp_mask"] = str(int(cdp_mask))
        if delete_cache is not None:
            v1_params["clear_cache_after_closing"] = str(int(delete_cache))
        for k, v in extra_kwargs.items():
            if v is not None and k not in v1_params:
                v1_params[k] = json.dumps(v) if isinstance(v, (list, dict)) else str(v)

        errors = []
        try:
            raw = self._request_json("POST", "/api/v2/browser-profile/start", json_body=v2_body)
            result = self._normalize_start_response(raw, "v2", profile_id)
            if result.get("ws_endpoint"):
                return result
            errors.append("v2 start returned success but no ws endpoint")
        except Exception as e:
            errors.append(f"v2 start failed: {e}")
        try:
            raw = self._request_json("GET", "/api/v1/browser/start", params=v1_params)
            result = self._normalize_start_response(raw, "v1", profile_id)
            if result.get("ws_endpoint"):
                return result
            errors.append("v1 start returned success but no ws endpoint")
        except Exception as e:
            errors.append(f"v1 start failed: {e}")
        raise AdsPowerAPIError(" ; ".join(errors))

    def stop_profile(self, profile_id: Optional[str] = None, serial_number: Optional[str] = None) -> Dict[str, Any]:
        self._require_profile_identifier(profile_id, serial_number)
        v2_body = {}
        if profile_id:
            v2_body["profile_id"] = str(profile_id)
        if serial_number:
            v2_body["profile_no"] = str(serial_number)
        v1_params = {}
        if profile_id:
            v1_params["user_id"] = str(profile_id)
        if serial_number:
            v1_params["serial_number"] = str(serial_number)
        errors = []
        try:
            raw = self._request_json("POST", "/api/v2/browser-profile/stop", json_body=v2_body)
            return {"ok": True, "version_used": "v2", "raw": raw}
        except Exception as e:
            errors.append(f"v2 stop failed: {e}")
        try:
            raw = self._request_json("GET", "/api/v1/browser/stop", params=v1_params)
            return {"ok": True, "version_used": "v1", "raw": raw}
        except Exception as e:
            errors.append(f"v1 stop failed: {e}")
        raise AdsPowerAPIError(" ; ".join(errors))

    def check_profile_active(self, profile_id: Optional[str] = None, serial_number: Optional[str] = None) -> Dict[str, Any]:
        self._require_profile_identifier(profile_id, serial_number)
        v2_params, v1_params = {}, {}
        if profile_id:
            v2_params["profile_id"] = str(profile_id)
            v1_params["user_id"] = str(profile_id)
        if serial_number:
            v2_params["profile_no"] = str(serial_number)
            v1_params["serial_number"] = str(serial_number)
        errors = []
        try:
            raw = self._request_json("GET", "/api/v2/browser-profile/active", params=v2_params)
            return self._normalize_active_response(raw, "v2", profile_id)
        except Exception as e:
            errors.append(f"v2 active failed: {e}")
        try:
            raw = self._request_json("GET", "/api/v1/browser/active", params=v1_params)
            return self._normalize_active_response(raw, "v1", profile_id)
        except Exception as e:
            errors.append(f"v1 active failed: {e}")
        raise AdsPowerAPIError(" ; ".join(errors))

    def ensure_profile_started(self, profile_id: str, serial_number: Optional[str] = None, **start_kwargs) -> Dict[str, Any]:
        health_timeout_sec = float(start_kwargs.pop("health_timeout_sec", 5.0))
        ready_timeout_sec = float(start_kwargs.pop("ready_timeout_sec", 30.0))
        restart_if_unhealthy = bool(start_kwargs.pop("restart_if_unhealthy", True))
        restart_wait_sec = float(start_kwargs.pop("restart_wait_sec", 5.0))
        try:
            active = self.check_profile_active(profile_id=profile_id, serial_number=serial_number)
        except AdsPowerAPIError:
            active = {"is_active": False}
        if active.get("is_active") and active.get("ws_endpoint"):
            health = self.check_cdp_health(str(active["ws_endpoint"]), timeout_sec=health_timeout_sec)
            if health.get("ok"):
                active["started_now"] = False
                active["cdp_health"] = health
                return active
            if not restart_if_unhealthy:
                raise AdsPowerAPIError(
                    "AdsPower profile is active but its CDP endpoint is unhealthy "
                    f"({health.get('error_class') or 'unknown'})"
                )
            print("[AdsPower] CDP 健康检查失败，自动重启浏览器")
            return self.restart_profile(
                profile_id=profile_id,
                serial_number=serial_number,
                health_timeout_sec=health_timeout_sec,
                ready_timeout_sec=ready_timeout_sec,
                restart_wait_sec=restart_wait_sec,
                **start_kwargs,
            )

        # 添加 --remote-allow-origins=* 以允许 Playwright 连接 CDP
        launch_args = list(start_kwargs.pop("launch_args", []) or [])
        if "--remote-allow-origins=*" not in launch_args:
            launch_args.append("--remote-allow-origins=*")
        started = self.start_profile(
            profile_id=profile_id,
            serial_number=serial_number,
            launch_args=launch_args,
            **start_kwargs,
        )
        health = self.wait_for_cdp_ready(
            str(started.get("ws_endpoint") or ""),
            timeout_sec=ready_timeout_sec,
            health_timeout_sec=health_timeout_sec,
        )
        if not health.get("ok"):
            raise AdsPowerAPIError(
                "AdsPower profile started but CDP did not become ready "
                f"({health.get('error_class') or 'unknown'})"
            )
        started["started_now"] = True
        started["cdp_health"] = health
        return started

    def restart_profile(
        self,
        profile_id: str,
        serial_number: Optional[str] = None,
        *,
        health_timeout_sec: float = 5.0,
        ready_timeout_sec: float = 30.0,
        restart_wait_sec: float = 5.0,
        **start_kwargs,
    ) -> Dict[str, Any]:
        """Fully restart one profile and wait for a usable DevTools endpoint."""
        self.stop_profile(profile_id=profile_id, serial_number=serial_number)
        time.sleep(max(0.0, restart_wait_sec))
        launch_args = list(start_kwargs.pop("launch_args", []) or [])
        if "--remote-allow-origins=*" not in launch_args:
            launch_args.append("--remote-allow-origins=*")
        started = self.start_profile(
            profile_id=profile_id,
            serial_number=serial_number,
            launch_args=launch_args,
            **start_kwargs,
        )
        health = self.wait_for_cdp_ready(
            str(started.get("ws_endpoint") or ""),
            timeout_sec=ready_timeout_sec,
            health_timeout_sec=health_timeout_sec,
        )
        if not health.get("ok"):
            raise AdsPowerAPIError(
                "AdsPower profile restarted but CDP did not become ready "
                f"({health.get('error_class') or 'unknown'})"
            )
        started["started_now"] = True
        started["restarted"] = True
        started["cdp_health"] = health
        return started

    def check_cdp_health(self, ws_url: str, timeout_sec: float = 5.0) -> Dict[str, Any]:
        """Check TCP and ``/json/version`` without exposing the CDP endpoint."""
        try:
            parsed = urlparse(str(ws_url or ""))
            if not parsed.hostname:
                raise ValueError("missing CDP hostname")
            secure = parsed.scheme in {"wss", "https"}
            port = parsed.port or (443 if secure else 80)
            timeout = max(0.1, float(timeout_sec))
            with socket.create_connection((parsed.hostname, port), timeout=timeout):
                pass
            scheme = "https" if secure else "http"
            response = requests.get(
                f"{scheme}://{parsed.hostname}:{port}/json/version",
                timeout=timeout,
                verify=self.verify_ssl,
            )
            if response.status_code != 200:
                raise AdsPowerAPIError(f"DevTools HTTP status {response.status_code}")
            payload = response.json()
            if not isinstance(payload, dict) or not (
                payload.get("webSocketDebuggerUrl") or payload.get("Browser")
            ):
                raise AdsPowerAPIError("DevTools version response was incomplete")
            return {"ok": True, "tcp_open": True, "devtools_http_ok": True}
        except Exception as exc:
            return {
                "ok": False,
                "tcp_open": False,
                "devtools_http_ok": False,
                "error_class": exc.__class__.__name__,
            }

    def wait_for_cdp_ready(
        self,
        ws_url: str,
        *,
        timeout_sec: float = 30.0,
        health_timeout_sec: float = 5.0,
        poll_interval_sec: float = 1.0,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        last = {"ok": False, "error_class": "cdp_not_ready"}
        while True:
            last = self.check_cdp_health(ws_url, timeout_sec=health_timeout_sec)
            if last.get("ok") or time.monotonic() >= deadline:
                return last
            time.sleep(max(0.05, min(float(poll_interval_sec), deadline - time.monotonic())))

    def _build_base_url_candidates(self, api_base_url: Optional[str]) -> List[str]:
        candidates = []
        for u in [api_base_url, os.getenv("ADSPOWER_API_BASE_URL"), "http://local.adspower.net:50325", "http://localhost:50325"]:
            if not u:
                continue
            u = u.strip().rstrip("/")
            if u not in candidates:
                candidates.append(u)
        return candidates

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _throttle(self):
        now = time.time()
        elapsed = now - self._last_request_ts
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self._last_request_ts = time.time()

    def _request_json(self, method: str, path: str, params=None, json_body=None) -> Dict[str, Any]:
        last_error = None
        for base in self.base_url_candidates:
            url = f"{base}{path}"
            try:
                self._throttle()
                resp = requests.request(method=method.upper(), url=url, params=params, json=json_body,
                                        headers=self._headers(), timeout=self.timeout, verify=self.verify_ssl)
                try:
                    payload = resp.json()
                except Exception as exc:
                    raise AdsPowerAPIError(
                        f"Non-JSON response from AdsPower API (HTTP {resp.status_code})"
                    ) from exc
                if resp.status_code >= 400:
                    raise AdsPowerAPIError(f"HTTP {resp.status_code}: {json.dumps(payload, ensure_ascii=False)}")
                if not self._is_success_payload(payload):
                    raise AdsPowerAPIError(f"API business error: {json.dumps(payload, ensure_ascii=False)}")
                return payload
            except Exception as e:
                last_error = e
                continue
        raise AdsPowerAPIError(str(last_error) if last_error else "Unknown API request error")

    def _is_success_payload(self, payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if payload.get("code") == 0 or payload.get("success") is True:
            return True
        if str(payload.get("status", "")).lower() in ("success", "ok"):
            return True
        msg = str(payload.get("msg", "")).lower()
        if payload.get("code") is None and msg in ("success", "ok", ""):
            return True
        return False

    def _require_profile_identifier(self, profile_id: Optional[str], serial_number: Optional[str]):
        if not profile_id and not serial_number:
            raise ValueError("必须传 profile_id 或 serial_number 至少一个")

    def _extract_ws_info(self, data_node: Dict[str, Any]) -> Dict[str, Optional[str]]:
        data_node = data_node or {}
        ws = data_node.get("ws") if isinstance(data_node.get("ws"), dict) else {}
        puppeteer_ws = (
            ws.get("puppeteer") or ws.get("cdp") or ws.get("playwright") or
            data_node.get("puppeteer") or data_node.get("puppeteer_ws") or
            data_node.get("ws_endpoint") or data_node.get("cdp_ws")
        )
        selenium_ep = ws.get("selenium") or data_node.get("selenium") or data_node.get("selenium_endpoint")
        return {"puppeteer": puppeteer_ws, "selenium": selenium_ep}

    def _normalize_start_response(self, raw: Dict[str, Any], version_used: str, profile_id: Optional[str]):
        data = raw.get("data") or {}
        ws_info = self._extract_ws_info(data)
        return {
            "ok": True, "version_used": version_used, "raw": raw,
            "ws_endpoint": ws_info.get("puppeteer"),
            "selenium_endpoint": ws_info.get("selenium"),
            "webdriver": data.get("webdriver"), "debug_port": data.get("debug_port"),
            "profile_id": profile_id, "status": data.get("status"),
        }

    def _normalize_active_response(self, raw: Dict[str, Any], version_version_used: str, profile_id: Optional[str]):
        data = raw.get("data") or {}
        ws_info = self._extract_ws_info(data)
        status_text = str(data.get("status", "")).strip()
        return {
            "ok": True, "version_used": version_version_used, "raw": raw, "profile_id": profile_id,
            "is_active": status_text.lower() == "active",
            "status": status_text or None,
            "ws_endpoint": ws_info.get("puppeteer"),
            "selenium_endpoint": ws_info.get("selenium"),
            "webdriver": data.get("webdriver"),
            "debug_port": data.get("debug_port"),
        }

    def get_profile_detail(self, profile_id: str) -> Dict[str, Any]:
        """
        获取单个 profile 的详细信息
        
        Args:
            profile_id: Profile ID
        
        Returns:
            Profile 详细信息，包括 username、cookie 等
        """
        params = {"profile_id": profile_id}
        
        errors = []
        try:
            raw = self._request_json("GET", "/api/v2/browser-profile/detail", params=params)
            data = raw.get("data", {})
            return {
                "ok": True,
                "profile": data,
                "profile_id": data.get("profile_id") or data.get("user_id"),
                "profile_name": data.get("profile_name") or data.get("name"),
                "username": data.get("username") or data.get("login") or data.get("email"),
                "remark": data.get("remark") or data.get("note"),
                "group_id": data.get("group_id"),
                "cookie": data.get("cookie"),
            }
        except Exception as e:
            errors.append(f"v2 detail failed: {e}")
        
        # 尝试 v1 API
        try:
            raw = self._request_json("GET", "/api/v1/user/detail", params={"user_id": profile_id})
            data = raw.get("data", {})
            return {
                "ok": True,
                "profile": data,
                "profile_id": data.get("user_id") or data.get("profile_id"),
                "profile_name": data.get("name") or data.get("profile_name"),
                "username": data.get("username") or data.get("login") or data.get("email"),
                "remark": data.get("remark") or data.get("note"),
                "group_id": data.get("group_id"),
                "cookie": data.get("cookie"),
            }
        except Exception as e:
            errors.append(f"v1 detail failed: {e}")
        
        raise AdsPowerAPIError(" ; ".join(errors))

    def query_profiles(self, group_id: Optional[str] = None, 
                       search_value: Optional[str] = None,
                       page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """
        查询 AdsPower 中的浏览器配置文件列表
        
        Args:
            group_id: 分组 ID
            search_value: 搜索关键词（支持 profile 名称、备注等）
            page: 页码
            page_size: 每页数量
        
        Returns:
            {
                "profiles": [...],
                "total": int,
                "page": int
            }
        """
        params = {
            "page": str(page),
            "page_size": str(page_size),
        }
        if group_id:
            params["group_id"] = group_id
        if search_value:
            params["search_value"] = search_value
        
        errors = []
        try:
            raw = self._request_json("GET", "/api/v2/browser-profile/list", params=params)
            data = raw.get("data", {})
            return {
                "ok": True,
                "profiles": data.get("list", []),
                "total": data.get("total", 0),
                "page": data.get("page", page),
                "page_size": data.get("page_size", page_size),
            }
        except Exception as e:
            errors.append(f"v2 list failed: {e}")
        
        # 尝试 v1 API
        try:
            raw = self._request_json("GET", "/api/v1/user/list", params=params)
            data = raw.get("data", {})
            profiles = data.get("list", [])
            # v1 API 返回格式可能不同，做适配。
            # Keep login/email fields too: account auto-bootstrap uses these
            # to populate config/accounts.json -> username/email.
            normalized = []
            for p in profiles:
                username = p.get("username") or p.get("login") or p.get("email")
                email = p.get("email") or p.get("username") or p.get("login")
                normalized.append({
                    "profile_id": p.get("user_id") or p.get("profile_id") or p.get("id"),
                    "profile_name": p.get("name") or p.get("profile_name"),
                    "remark": p.get("remark") or p.get("note"),
                    "group_id": p.get("group_id"),
                    "username": username,
                    "email": email,
                    "serial_number": p.get("serial_number"),
                    "name": p.get("name") or p.get("profile_name"),
                })
            return {
                "ok": True,
                "profiles": normalized,
                "total": data.get("total", len(normalized)),
                "page": data.get("page", page),
                "page_size": data.get("page_size", page_size),
            }
        except Exception as e:
            errors.append(f"v1 list failed: {e}")
        
        raise AdsPowerAPIError(" ; ".join(errors))
