"""Feishu Bitable access and conservative 5461 record binding.

The integration deliberately separates read-only record binding from writes.
Scheduling a Case follow-up may locate and persist an existing Feishu
``record_id``; it never creates a row and never changes a Bitable cell.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_BASE_URL = "https://open.feishu.cn/open-apis"

DEFAULT_FIELD_MAP = {
    "account": "账号",
    "country": "国家",
    "country_eu": "国家EU",
    "brand": "品牌",
    "sku": "SKU",
    "progress": "5461进度",
}

DEFAULT_COUNTRY_OPTION_SITE_MAP = {
    "比利时": "BE",
    "瑞典": "SE",
    "荷兰": "NL",
    "德国": "DE",
    "英国": "UK",
    "法国": "FR",
    "西班牙": "ES",
    "意大利": "IT",
}

DEFAULT_ACCOUNT_SUFFIX_BY_SITE = {
    "US": "US",
    "MX": "MX",
    "UK": "EU",
    "BE": "EU",
    "NL": "EU",
    "SE": "EU",
    "DE": "EU",
    "FR": "EU",
    "ES": "EU",
    "IT": "EU",
}

ALLOWED_PROGRESS_VALUES = {"申请中", "假过", "拒绝", "通过"}
CASE_RESULT_PROGRESS_MAP = {
    "approved": "通过",
    "false_approved": "假过",
    "declined": "拒绝",
}


class FeishuConfigurationError(RuntimeError):
    """Raised when the local integration configuration is incomplete."""


class FeishuApiError(RuntimeError):
    """Raised for a sanitized Feishu API failure."""

    def __init__(self, stage: str, code: str | int, message: str):
        self.stage = stage
        self.code = code
        self.message = _sanitize_message(message)
        super().__init__(f"{stage} failed: code={code}, msg={self.message}")


def _sanitize_message(value: str) -> str:
    message = re.sub(r"https?://\S+", "[URL_REDACTED]", str(value or ""))
    message = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "[REDACTED_EMAIL]",
        message,
    )
    return message[:500]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_or_config(env_name: str, configured: Mapping[str, Any], config_name: str, default: Any = "") -> Any:
    env_value = os.getenv(env_name)
    if env_value is not None:
        return env_value.strip()
    return configured.get(config_name, default)


@dataclass(frozen=True)
class FeishuBitableConfig:
    enabled: bool = False
    bind_on_schedule: bool = True
    write_enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    wiki_node_token: str = ""
    app_token: str = ""
    table_id: str = ""
    request_timeout_seconds: float = 20.0
    max_candidate_records: int = 100
    field_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FIELD_MAP))
    country_option_site_map: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_COUNTRY_OPTION_SITE_MAP)
    )
    account_suffix_by_site: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_ACCOUNT_SUFFIX_BY_SITE)
    )

    def validate(self) -> None:
        missing = []
        if not self.app_id:
            missing.append("FEISHU_APP_ID")
        if not self.app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not self.table_id:
            missing.append("FEISHU_BITABLE_TABLE_ID")
        if not (self.app_token or self.wiki_node_token):
            missing.append("FEISHU_BITABLE_APP_TOKEN or FEISHU_WIKI_NODE_TOKEN")
        if missing:
            raise FeishuConfigurationError("Missing Feishu configuration: " + ", ".join(missing))


def get_feishu_bitable_config(settings: Mapping[str, Any] | None = None) -> FeishuBitableConfig:
    """Load non-secret behavior from YAML and credentials from the local env."""

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    configured = dict((settings or {}).get("feishu_bitable") or {})
    field_map = dict(DEFAULT_FIELD_MAP)
    field_map.update(configured.get("field_map") or {})
    country_map = dict(DEFAULT_COUNTRY_OPTION_SITE_MAP)
    country_map.update(
        {str(name): str(site).upper() for name, site in (configured.get("country_option_site_map") or {}).items()}
    )
    account_suffix_map = dict(DEFAULT_ACCOUNT_SUFFIX_BY_SITE)
    account_suffix_map.update(
        {
            str(site).upper(): str(suffix).upper()
            for site, suffix in (configured.get("account_suffix_by_site") or {}).items()
        }
    )

    enabled_value = _env_or_config("FEISHU_BITABLE_ENABLED", configured, "enabled", False)
    bind_value = _env_or_config("FEISHU_BIND_ON_SCHEDULE", configured, "bind_on_schedule", True)
    write_value = _env_or_config("FEISHU_BITABLE_WRITE_ENABLED", configured, "write_enabled", False)
    return FeishuBitableConfig(
        enabled=_as_bool(enabled_value),
        bind_on_schedule=_as_bool(bind_value, True),
        write_enabled=_as_bool(write_value),
        app_id=str(_env_or_config("FEISHU_APP_ID", configured, "app_id", "") or ""),
        app_secret=str(_env_or_config("FEISHU_APP_SECRET", configured, "app_secret", "") or ""),
        wiki_node_token=str(
            _env_or_config("FEISHU_WIKI_NODE_TOKEN", configured, "wiki_node_token", "") or ""
        ),
        app_token=str(_env_or_config("FEISHU_BITABLE_APP_TOKEN", configured, "app_token", "") or ""),
        table_id=str(_env_or_config("FEISHU_BITABLE_TABLE_ID", configured, "table_id", "") or ""),
        request_timeout_seconds=float(configured.get("request_timeout_seconds", 20.0)),
        max_candidate_records=max(1, int(configured.get("max_candidate_records", 100))),
        field_map=field_map,
        country_option_site_map=country_map,
        account_suffix_by_site=account_suffix_map,
    )


def split_country_option(value: str) -> tuple[str, str]:
    """Return ``(base country label, repeat marker)`` without losing the label."""

    normalized = str(value or "").strip()
    match = re.fullmatch(r"(.+?)(\d+)?", normalized)
    if not match:
        return normalized, ""
    return match.group(1).strip(), match.group(2) or ""


def country_option_to_site(value: str, mapping: Mapping[str, str] | None = None) -> str:
    base, _marker = split_country_option(value)
    resolved = dict(DEFAULT_COUNTRY_OPTION_SITE_MAP)
    resolved.update(mapping or {})
    if base in resolved:
        return str(resolved[base]).upper()
    upper = base.upper()
    return upper if re.fullmatch(r"[A-Z]{2}", upper) else ""


def _formula_field(field_name: str) -> str:
    if not field_name or "]" in field_name or "\n" in field_name or "\r" in field_name:
        raise FeishuConfigurationError("Unsafe or empty Feishu field name in field_map")
    return f"CurrentValue.[{field_name}]"


def _formula_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and item.get("text") is not None:
                parts.append(str(item.get("text")))
        return "".join(parts).strip()
    if isinstance(value, Mapping) and value.get("text") is not None:
        return str(value.get("text")).strip()
    return str(value).strip()


def _multi_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]
    return []


def _account_digits(value: str) -> str:
    match = re.search(r"(\d{3,})", str(value or ""))
    return match.group(1) if match else ""


def _account_key(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(value or "")).casefold()


def _account_matches(
    feishu_value: str,
    account_id: str,
    site: str,
    suffix_by_site: Mapping[str, str],
) -> bool:
    actual = _account_key(feishu_value)
    supplied = _account_key(account_id)
    if actual == supplied:
        return True
    digits = _account_digits(account_id)
    if not digits:
        return False
    site_code = str(site or "").upper()
    suffix = str(suffix_by_site.get(site_code) or site_code).casefold()
    expected = {
        _account_key(digits + suffix),
        _account_key(suffix + digits),
        _account_key(digits + site_code),
        _account_key(site_code + digits),
    }
    return actual in expected


@dataclass(frozen=True)
class FeishuBindingResult:
    status: str
    reason: str
    record_id: str = ""
    country_option: str = ""
    candidate_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "record_id": self.record_id,
            "country_option": self.country_option,
            "candidate_count": self.candidate_count,
        }


class FeishuBitableClient:
    """Minimal Feishu client used by the delayed Case workflow."""

    def __init__(self, config: FeishuBitableConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self._tenant_access_token = ""
        self._tenant_access_token_expires_at = 0.0
        self._resolved_app_token = ""
        self._fields_cache: list[dict[str, Any]] | None = None

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        stage: str,
        authenticated: bool = True,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.get_tenant_access_token()}"
        try:
            response = self.session.request(
                method,
                API_BASE_URL + path,
                headers=headers,
                params=dict(params or {}),
                json=dict(json_body or {}) if json_body is not None else None,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise FeishuApiError(stage, "network_error", exc.__class__.__name__) from exc

        try:
            body = response.json()
        except (TypeError, ValueError) as exc:
            raise FeishuApiError(stage, getattr(response, "status_code", "invalid_json"), "invalid JSON response") from exc

        code = body.get("code", 0)
        status_code = int(getattr(response, "status_code", 200))
        if status_code >= 400 or int(code or 0) != 0:
            raise FeishuApiError(stage, code or status_code, str(body.get("msg") or "request failed"))
        return body

    def get_tenant_access_token(self) -> str:
        if self._tenant_access_token and time.monotonic() < self._tenant_access_token_expires_at:
            return self._tenant_access_token
        self.config.validate()
        body = self._request_json(
            "POST",
            "/auth/v3/tenant_access_token/internal/",
            stage="Feishu authentication",
            authenticated=False,
            json_body={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
        )
        token = str(body.get("tenant_access_token") or "")
        if not token:
            raise FeishuApiError("Feishu authentication", "missing_token", "response contained no token")
        expires_in = max(60, int(body.get("expire") or 7200))
        self._tenant_access_token = token
        self._tenant_access_token_expires_at = time.monotonic() + expires_in - 30
        return token

    def resolve_app_token(self) -> str:
        if self._resolved_app_token:
            return self._resolved_app_token
        if self.config.app_token:
            self._resolved_app_token = self.config.app_token
            return self._resolved_app_token
        self.config.validate()
        body = self._request_json(
            "GET",
            "/wiki/v2/spaces/get_node",
            stage="Feishu Wiki node lookup",
            params={"token": self.config.wiki_node_token},
        )
        node = (body.get("data") or {}).get("node") or {}
        if node.get("obj_type") != "bitable":
            raise FeishuApiError(
                "Feishu Wiki node lookup",
                "wrong_object_type",
                f"expected bitable, got {node.get('obj_type') or 'unknown'}",
            )
        token = str(node.get("obj_token") or "")
        if not token:
            raise FeishuApiError("Feishu Wiki node lookup", "missing_obj_token", "node contained no obj_token")
        self._resolved_app_token = token
        return token

    def _table_path(self) -> str:
        app_token = quote(self.resolve_app_token(), safe="")
        table_id = quote(self.config.table_id, safe="")
        return f"/bitable/v1/apps/{app_token}/tables/{table_id}"

    def list_fields(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._fields_cache is not None and not refresh:
            return list(self._fields_cache)
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            body = self._request_json(
                "GET",
                self._table_path() + "/fields",
                stage="Feishu field listing",
                params=params,
            )
            data = body.get("data") or {}
            items.extend(data.get("items") or [])
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more") or not page_token:
                break
        self._fields_cache = items
        return list(items)

    def validate_binding_schema(self) -> dict[str, dict[str, Any]]:
        by_name = {str(item.get("field_name") or ""): item for item in self.list_fields()}
        required_keys = ("account", "country", "country_eu", "brand", "sku", "progress")
        missing = [self.config.field_map[key] for key in required_keys if self.config.field_map.get(key) not in by_name]
        if missing:
            raise FeishuConfigurationError("Configured Feishu fields were not found: " + ", ".join(missing))
        return by_name

    def search_candidate_records(
        self,
        account_id: str,
        site: str,
        brand_name: str,
        sku: str = "",
    ) -> list[dict[str, Any]]:
        self.validate_binding_schema()
        field_map = self.config.field_map
        account_fragment = _account_digits(account_id) or account_id.strip()
        conditions = [
            f"{_formula_field(field_map['account'])}.contains({_formula_string(account_fragment)})",
            f"{_formula_field(field_map['brand'])}={_formula_string(brand_name.strip())}",
        ]
        if sku.strip():
            conditions.append(
                f"{_formula_field(field_map['sku'])}={_formula_string(sku.strip())}"
            )
        formula = "AND(" + ",".join(conditions) + ")"
        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 100, "filter": formula}
            if page_token:
                params["page_token"] = page_token
            body = self._request_json(
                "GET",
                self._table_path() + "/records",
                stage="Feishu candidate record search",
                params=params,
            )
            data = body.get("data") or {}
            records.extend(data.get("items") or [])
            if len(records) > self.config.max_candidate_records:
                break
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more") or not page_token:
                break
        return records

    def find_record_binding(
        self,
        account_id: str,
        site: str,
        brand_name: str,
        sku: str,
        country_option: str = "",
    ) -> FeishuBindingResult:
        required = (account_id.strip(), site.strip(), brand_name.strip(), sku.strip())
        if not all(required):
            return FeishuBindingResult(
                "insufficient_data",
                "account, site, brand and SKU are required for safe binding",
            )

        site_code = site.strip().upper()
        requested_option = country_option.strip()
        if requested_option:
            option_site = country_option_to_site(requested_option, self.config.country_option_site_map)
            if option_site and option_site != site_code:
                return FeishuBindingResult(
                    "insufficient_data",
                    "country option does not match the submitted marketplace",
                )

        records = self.search_candidate_records(account_id, site_code, brand_name, sku)
        used_country_fallback = False
        # Some existing EU rows intentionally reuse their UK SKU while 国家EU
        # contains the latest submitted country.  When the exact site SKU is not
        # present, an explicit country option may safely narrow account+brand rows.
        if not records and requested_option:
            records = self.search_candidate_records(account_id, site_code, brand_name)
            used_country_fallback = True
        if len(records) > self.config.max_candidate_records:
            return FeishuBindingResult(
                "ambiguous",
                "candidate limit exceeded; refusing to guess a record",
                candidate_count=len(records),
            )

        field_map = self.config.field_map
        matches: list[tuple[str, str]] = []
        ambiguous_option = False
        for record in records:
            fields = record.get("fields") or {}
            # Defend against loose server-side filter behavior.
            if not _account_matches(
                _scalar_text(fields.get(field_map["account"])),
                account_id,
                site_code,
                self.config.account_suffix_by_site,
            ):
                continue
            if _scalar_text(fields.get(field_map["brand"])).casefold() != brand_name.strip().casefold():
                continue
            if (
                not used_country_fallback
                and _scalar_text(fields.get(field_map["sku"])).casefold()
                != sku.strip().casefold()
            ):
                continue

            record_id = str(record.get("record_id") or "")
            if not record_id:
                continue
            country_value = _scalar_text(fields.get(field_map["country"]))
            eu_options = _multi_strings(fields.get(field_map["country_eu"]))

            if requested_option:
                if requested_option in eu_options:
                    matches.append((record_id, requested_option))
                elif not eu_options and country_value.casefold() in {
                    requested_option.casefold(),
                    site_code.casefold(),
                }:
                    matches.append((record_id, requested_option))
                continue

            matching_options = [
                option
                for option in eu_options
                if country_option_to_site(option, self.config.country_option_site_map) == site_code
            ]
            if len(matching_options) == 1:
                matches.append((record_id, matching_options[0]))
            elif len(matching_options) > 1:
                ambiguous_option = True
            elif not eu_options and country_value.strip().upper() == site_code:
                matches.append((record_id, country_value.strip() or site_code))

        if ambiguous_option or len(matches) > 1:
            return FeishuBindingResult(
                "ambiguous",
                "multiple records or repeat-country options matched; explicit country option required",
                candidate_count=len(records),
            )
        if not matches:
            return FeishuBindingResult(
                "not_found",
                "no unique existing record matched the submitted account, site, brand and country",
                candidate_count=len(records),
            )
        record_id, resolved_option = matches[0]
        return FeishuBindingResult(
            "bound",
            (
                "unique existing Feishu record matched by account, brand and explicit country option"
                if used_country_fallback
                else "unique existing Feishu record matched"
            ),
            record_id=record_id,
            country_option=resolved_option,
            candidate_count=len(records),
        )

    def update_progress(self, record_id: str, progress: str, *, dry_run: bool = True) -> dict[str, Any]:
        """Update only ``5461进度``; writes require two explicit gates."""

        if progress not in ALLOWED_PROGRESS_VALUES:
            raise ValueError(f"Unsupported Feishu progress value: {progress}")
        self.validate_binding_schema()
        progress_field = self.config.field_map["progress"]
        progress_meta = next(
            item for item in self.list_fields() if str(item.get("field_name") or "") == progress_field
        )
        allowed_options = {
            str(option.get("name") or "") for option in ((progress_meta.get("property") or {}).get("options") or [])
        }
        if allowed_options and progress not in allowed_options:
            raise FeishuConfigurationError("Requested progress is not an option in the existing Feishu field")
        if dry_run:
            return {"status": "dry_run", "record_id": record_id, "field": progress_field, "value": progress}
        if not self.config.write_enabled:
            raise FeishuConfigurationError("Feishu writes are disabled; set write_enabled only after dry-run review")
        body = self._request_json(
            "PUT",
            self._table_path() + "/records/" + quote(str(record_id), safe=""),
            stage="Feishu progress update",
            json_body={"fields": {progress_field: progress}},
        )
        return {"status": "updated", "record": (body.get("data") or {}).get("record") or {}}


def bind_case_to_record(
    settings: Mapping[str, Any],
    account_id: str,
    site: str,
    brand_name: str,
    sku: str,
    country_option: str = "",
    *,
    client: FeishuBitableClient | None = None,
) -> dict[str, Any]:
    """Resolve one existing row for a Case without writing to Feishu."""

    config = get_feishu_bitable_config(settings)
    if not config.enabled or not config.bind_on_schedule:
        return FeishuBindingResult("disabled", "Feishu binding is disabled").to_dict()
    try:
        config.validate()
        active_client = client or FeishuBitableClient(config)
        return active_client.find_record_binding(
            account_id=account_id,
            site=site,
            brand_name=brand_name,
            sku=sku,
            country_option=country_option,
        ).to_dict()
    except FeishuConfigurationError as exc:
        return FeishuBindingResult("not_configured", _sanitize_message(str(exc))).to_dict()
    except FeishuApiError as exc:
        return FeishuBindingResult("error", _sanitize_message(str(exc))).to_dict()


def update_bound_progress(
    settings: Mapping[str, Any],
    record_id: str,
    progress: str,
    *,
    client: FeishuBitableClient | None = None,
) -> dict[str, Any]:
    """Update one already-bound row, with config and API safety gates intact."""

    config = get_feishu_bitable_config(settings)
    if not config.enabled:
        return {"status": "disabled", "reason": "Feishu Bitable integration is disabled"}
    if not record_id:
        return {"status": "skipped", "reason": "no bound Feishu record_id"}
    try:
        config.validate()
        active_client = client or FeishuBitableClient(config)
        return active_client.update_progress(
            str(record_id),
            progress,
            dry_run=not config.write_enabled,
        )
    except (FeishuConfigurationError, FeishuApiError, ValueError) as exc:
        return {"status": "error", "reason": _sanitize_message(str(exc))}
