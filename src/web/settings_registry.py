"""Public configuration registry and atomic settings persistence.

Only entries declared here may cross the web API boundary.  Paths, commands,
URLs, account data, credentials and arbitrary YAML values are deliberately
excluded even when they exist in ``config/settings.yaml``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from src.jobs.options import NON_SUBMIT_BRAND_LIMIT

ApplyMode = Literal["immediate", "new_job", "restart_required"]
ValueType = Literal["integer", "number", "boolean"]
FieldStatus = Literal["active", "protected", "unwired"]


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    group: str
    label: str
    value_type: ValueType
    default: int | float | bool
    unit: str
    apply_mode: ApplyMode
    description: str
    editable: bool = True
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    status: FieldStatus = "active"


def _number(
    key: str,
    group: str,
    label: str,
    default: int | float,
    unit: str,
    minimum: int | float,
    maximum: int | float,
    step: int | float,
    apply_mode: ApplyMode,
    description: str,
    *,
    integer: bool = False,
) -> SettingDefinition:
    return SettingDefinition(
        key=key,
        group=group,
        label=label,
        value_type="integer" if integer else "number",
        default=default,
        unit=unit,
        apply_mode=apply_mode,
        description=description,
        minimum=minimum,
        maximum=maximum,
        step=step,
    )


def _protected(
    key: str,
    group: str,
    label: str,
    default: bool | int | float,
    description: str,
    *,
    status: FieldStatus = "protected",
) -> SettingDefinition:
    return SettingDefinition(
        key=key,
        group=group,
        label=label,
        value_type=(
            "boolean"
            if isinstance(default, bool)
            else "integer"
            if isinstance(default, int)
            else "number"
        ),
        default=default,
        unit="",
        apply_mode="restart_required",
        description=description,
        editable=False,
        status=status,
    )


SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    _number("adspower.start_profile_timeout_sec", "浏览器与 AdsPower", "环境启动超时", 60, "秒", 10, 300, 5, "new_job", "等待 AdsPower 环境启动完成的最长时间。", integer=True),
    _number("browser.action_timeout_ms", "浏览器与 AdsPower", "页面操作超时", 20_000, "毫秒", 1_000, 120_000, 1_000, "new_job", "点击、填写和元素等待的默认超时。", integer=True),
    _number("browser.page_timeout_ms", "浏览器与 AdsPower", "页面导航超时", 45_000, "毫秒", 5_000, 300_000, 1_000, "new_job", "页面跳转和加载的默认超时。", integer=True),
    _number("browser.cdp_connect_timeout_ms", "浏览器与 AdsPower", "CDP 连接超时", 30_000, "毫秒", 5_000, 120_000, 1_000, "new_job", "Playwright 连接 AdsPower CDP 的最长等待。", integer=True),
    _number("browser.cdp_health_timeout_sec", "浏览器与 AdsPower", "CDP 健康检查超时", 5, "秒", 1, 30, 1, "new_job", "DevTools 健康探测的单次超时。", integer=True),
    _number("browser.cdp_ready_timeout_sec", "浏览器与 AdsPower", "CDP 就绪等待", 30, "秒", 5, 180, 5, "new_job", "环境启动后等待 CDP 可用的最长时间。", integer=True),
    _number("browser.cdp_restart_attempts", "浏览器与 AdsPower", "CDP 重启次数", 1, "次", 0, 3, 1, "new_job", "CDP 恢复失败时允许的有界环境重启次数。", integer=True),
    _number("browser.profile_restart_wait_sec", "浏览器与 AdsPower", "环境重启等待", 5, "秒", 0, 60, 1, "new_job", "停止环境后再次启动前的等待时间。", integer=True),

    _number("case_followup.delay_hours", "Case 跟进", "首次跟进延迟", 2.0, "小时", 0.1, 168, 0.5, "new_job", "取得 Case ID 后首次自动检查的延迟。"),
    _number("case_followup.retry_interval_hours", "Case 跟进", "未决结果重试间隔", 1.0, "小时", 0.1, 168, 0.5, "restart_required", "Pending、假过等未决结果的再次检查间隔。"),
    _number("case_followup.error_retry_interval_hours", "Case 跟进", "技术错误重试间隔", 1.0, "小时", 0.1, 168, 0.5, "restart_required", "技术性失败后的再次检查间隔。"),
    _number("case_followup.max_attempts", "Case 跟进", "最大检查次数", 6, "次", 1, 50, 1, "restart_required", "包含首次检查；用尽后转人工复核。", integer=True),
    _number("case_followup.poll_interval_seconds", "Case 跟进", "队列轮询频率", 60, "秒", 10, 3_600, 10, "restart_required", "后台查找已到期 Case 的频率。", integer=True),
    _number("case_followup.claim_group_limit", "Case 跟进", "单组领取上限", 20, "条", 1, 200, 1, "restart_required", "同账号站点一次领取的最大 Case 数量。", integer=True),
    _number("case_followup.max_parallel_profiles", "Case 跟进", "并行环境上限", 3, "个", 1, 10, 1, "restart_required", "Case 跟进可并行使用的最大 AdsPower 环境数。", integer=True),
    _number("case_followup.parallel_backlog_threshold", "Case 跟进", "并行积压阈值", 8, "条", 1, 200, 1, "restart_required", "积压达到该值后才启用并行处理。", integer=True),
    _number("case_followup.ai_reply_classification.auto_apply_min_confidence", "Case 跟进", "AI 自动采用置信度", 0.85, "", 0, 1, 0.01, "restart_required", "低于阈值的回复保持人工复核。"),
    _number("case_followup.ai_reply_classification.timeout_sec", "Case 跟进", "AI 分类超时", 300, "秒", 10, 1_800, 10, "restart_required", "单次只读 Case 回复分类的最长时间。", integer=True),
    _number("case_followup.ai_reply_classification.max_attempts", "Case 跟进", "AI 分类最大次数", 2, "次", 1, 5, 1, "restart_required", "AI 暂时不可用时允许的有界尝试次数。", integer=True),
    _number("case_followup.ai_reply_classification.retry_interval_minutes", "Case 跟进", "AI 分类重试间隔", 5, "分钟", 1, 1_440, 1, "restart_required", "一般 AI 暂时失败后的冷却时间。", integer=True),
    _number("case_followup.ai_reply_classification.forbidden_retry_interval_minutes", "Case 跟进", "AI 403 冷却时间", 30, "分钟", 1, 10_080, 1, "restart_required", "服务拒绝访问后的较长冷却时间。", integer=True),

    _number("case_id_recovery.initial_delay_minutes", "Case ID 恢复", "首次恢复延迟", 10, "分钟", 0, 1_440, 1, "restart_required", "提交后首次检查 Selling Applications 的延迟。", integer=True),
    _number("case_id_recovery.retry_interval_minutes", "Case ID 恢复", "恢复重试间隔", 30, "分钟", 1, 10_080, 1, "restart_required", "未找到唯一 Case ID 时的再次检查间隔。", integer=True),
    _number("case_id_recovery.max_attempts", "Case ID 恢复", "最大恢复次数", 12, "次", 1, 50, 1, "restart_required", "恢复队列的有界检查次数。", integer=True),
    _number("case_id_recovery.poll_interval_seconds", "Case ID 恢复", "恢复队列轮询", 60, "秒", 10, 3_600, 10, "restart_required", "后台查找已到期恢复任务的频率。", integer=True),

    _number("reapplication.decline_delay_hours", "重新申请", "拒绝后延迟", 2.0, "小时", 0.1, 168, 0.5, "restart_required", "明确拒绝后进入有限下一站的等待时间。"),
    _number("reapplication.poll_interval_seconds", "重新申请", "重申队列轮询", 60, "秒", 10, 3_600, 10, "restart_required", "后台查找已到期重申任务的频率。", integer=True),
    _number("reapplication.busy_retry_minutes", "重新申请", "环境忙重试间隔", 10, "分钟", 1, 1_440, 1, "restart_required", "profile 被占用时的再次尝试间隔。", integer=True),
    _number("reapplication.auto_backfill_limit", "重新申请", "自动回填上限", 100, "条", 1, 1_000, 1, "restart_required", "一次启动扫描最多回填的明确拒绝记录数。", integer=True),

    _number("web.session_ttl_hours", "认证与 Web", "登录会话有效期", 12.0, "小时", 0.5, 168, 0.5, "immediate", "新签发和后续校验的 Web 会话有效期。"),
    _number("web.submit_max_brands", "认证与 Web", "真实提交品牌上限", 5, "个", 1, 20, 1, "immediate", "一个真实提交任务允许包含的最大品牌数。", integer=True),
    _number("auth_recovery.poll_interval_seconds", "认证与 Web", "登录状态检查频率", 300, "秒", 30, 86_400, 30, "restart_required", "被阻塞 profile 的被动登录状态复查频率。", integer=True),

    _number("codex.timeout_sec", "Codex 修复", "判因调用超时", 300, "秒", 30, 3_600, 30, "restart_required", "一次只读 Codex 判因的最长时间。", integer=True),
    _number("codex.auto_triage_min_confidence", "Codex 修复", "自动判因置信度", 0.60, "", 0, 1, 0.01, "restart_required", "达到阈值的异常才进入自动判因。"),
    _number("codex.daily_call_limit", "Codex 修复", "每日调用额度", 50, "次", 0, 1_000, 1, "restart_required", "本机每日允许的 Codex 调用上限；0 表示禁用自动调用。", integer=True),
    _number("codex.auto_triage_poll_seconds", "Codex 修复", "异常扫描频率", 15, "秒", 5, 3_600, 5, "restart_required", "后台扫描待判因异常的频率。", integer=True),
    _number("codex.patch_timeout_sec", "Codex 修复", "补丁生成超时", 600, "秒", 30, 7_200, 30, "restart_required", "隔离工作区生成修复补丁的最长时间。", integer=True),
    _number("codex.worktree_retention_days", "Codex 修复", "工作区保留时间", 14, "天", 1, 365, 1, "restart_required", "已发布或拒绝工作区的取证保留天数。", integer=True),
    _number("codex.validation_timeout_sec", "Codex 修复", "验证套件超时", 900, "秒", 30, 7_200, 30, "restart_required", "修复验证流程的最长运行时间。", integer=True),
    _number("codex.workflow_poll_seconds", "Codex 修复", "修复工作流频率", 2, "秒", 1, 300, 1, "restart_required", "后台修复状态机的轮询频率。", integer=True),

    _protected("run.require_human_confirm_before_submit", "未接线配置（只读）", "提交前人工确认", True, "当前主流程使用显式提交入口和确认弹窗，不读取该字段。", status="unwired"),
    _protected("run.pause_on_captcha_or_2fa", "未接线配置（只读）", "验证码/2FA 暂停", True, "当前认证守卫始终暂停，不读取该字段。", status="unwired"),
    _protected("run.save_html_snapshot", "未接线配置（只读）", "保存 HTML 快照", False, "当前证据采集不读取该字段。", status="unwired"),
    _protected("web.submit_enabled", "安全开关（只读）", "真实提交总开关", True, "高风险总开关，仅允许在本机配置文件中维护。"),
    _protected("case_followup.enabled", "安全开关（只读）", "Case 跟进总开关", True, "工作流总开关只读。"),
    _protected("case_id_recovery.enabled", "安全开关（只读）", "Case ID 恢复总开关", True, "工作流总开关只读。"),
    _protected("reapplication.enabled", "安全开关（只读）", "重新申请总开关", True, "真实业务流程总开关只读。"),
    _protected("reapplication.auto_authorize_declined_cases", "安全开关（只读）", "拒绝后自动授权", False, "会延续真实提交授权，网页不可修改。"),
    _protected("reapplication.auto_backfill_declined_cases", "安全开关（只读）", "历史拒绝自动回填", False, "可能创建真实重申活动，网页不可修改。"),
    _protected("feishu_bitable.enabled", "安全开关（只读）", "飞书集成", False, "外部系统集成开关只读。"),
    _protected("feishu_bitable.create_missing_records", "安全开关（只读）", "飞书缺行创建", False, "外部写入相关开关只读。"),
    _protected("feishu_bitable.write_enabled", "安全开关（只读）", "飞书真实写入", False, "外部真实写入门禁只读。"),
    _protected("codex.enabled", "安全开关（只读）", "Codex 判因", True, "自动工具调用总开关只读。"),
    _protected("codex.workflow_enabled", "安全开关（只读）", "Codex 修复工作流", True, "自动修复工作流总开关只读。"),
    _protected("codex.release_enabled", "安全开关（只读）", "自动发布", False, "发布总开关必须在本机显式维护。"),
    _protected("browser.headless", "未接线配置（只读）", "无头浏览器", False, "当前主流程未读取该字段。", status="unwired"),
    _protected("verification.approved_threshold", "未接线配置（只读）", "批准阈值", 70, "当前批准判定不读取该字段。", status="unwired"),
    _protected("case_followup.healthcheck_interval_minutes", "未接线配置（只读）", "Case worker 健康检查", 5, "当前 worker 安装与调度不读取该字段。", status="unwired"),
    _protected("incidents.manual_review_below", "未接线配置（只读）", "异常人工复核阈值", 0.60, "WebSettings 会加载，但当前异常分流不读取该字段。", status="unwired"),
    _protected("incidents.auto_triage_at_or_above", "未接线配置（只读）", "异常自动判因阈值", 0.80, "当前自动判因实际使用 codex.auto_triage_min_confidence。", status="unwired"),
)

DEFINITIONS_BY_KEY = {field.key: field for field in SETTING_DEFINITIONS}
_SETTINGS_WRITE_LOCK = threading.Lock()


class SettingsUpdateError(ValueError):
    def __init__(self, code: str, *, status_code: int = 422):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _nested_get(data: dict[str, Any], key: str, default: Any) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _nested_set(data: dict[str, Any], key: str, value: Any) -> None:
    current = data
    parts = key.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise SettingsUpdateError("settings_root_must_be_object")
    return parsed


def settings_revision(path: Path) -> str:
    payload = path.read_bytes() if path.exists() else b"<missing-settings>"
    return hashlib.sha256(payload).hexdigest()[:16]


def _public_field(field: SettingDefinition, raw: dict[str, Any]) -> dict[str, Any]:
    value = _nested_get(raw, field.key, field.default)
    return {
        "key": field.key,
        "group": field.group,
        "label": field.label,
        "value_type": field.value_type,
        "value": value,
        "default": field.default,
        "unit": field.unit,
        "minimum": field.minimum,
        "maximum": field.maximum,
        "step": field.step,
        "editable": field.editable,
        "apply_mode": field.apply_mode,
        "status": field.status,
        "description": field.description,
    }


def build_settings_snapshot(path: Path) -> dict[str, Any]:
    raw = _read_yaml(path)
    fields = [_public_field(field, raw) for field in SETTING_DEFINITIONS]
    values = {field["key"]: field["value"] for field in fields}
    submit_limit = int(values["web.submit_max_brands"])
    return {
        "revision": settings_revision(path),
        "fields": fields,
        "limits": {
            "diagnose": NON_SUBMIT_BRAND_LIMIT,
            "dry_run": NON_SUBMIT_BRAND_LIMIT,
            "submit": submit_limit,
        },
        "job_defaults": {
            "case_followup_delay_hours": float(values["case_followup.delay_hours"]),
            "case_followup_enabled": bool(values["case_followup.enabled"]),
        },
    }


def _validate_value(field: SettingDefinition, value: Any) -> int | float:
    if isinstance(value, bool):
        raise SettingsUpdateError(f"invalid_type:{field.key}")
    if field.value_type == "integer":
        if not isinstance(value, int):
            raise SettingsUpdateError(f"invalid_type:{field.key}")
        normalized: int | float = int(value)
    elif field.value_type == "number":
        if not isinstance(value, (int, float)):
            raise SettingsUpdateError(f"invalid_type:{field.key}")
        normalized = float(value)
    else:
        raise SettingsUpdateError(f"setting_not_editable:{field.key}")
    if field.minimum is not None and normalized < field.minimum:
        raise SettingsUpdateError(f"value_too_small:{field.key}")
    if field.maximum is not None and normalized > field.maximum:
        raise SettingsUpdateError(f"value_too_large:{field.key}")
    return normalized


def validate_settings_changes(raw: dict[str, Any], changes: Any) -> dict[str, int | float]:
    if not isinstance(changes, dict):
        raise SettingsUpdateError("changes_must_be_object")
    normalized: dict[str, int | float] = {}
    for key, value in changes.items():
        field = DEFINITIONS_BY_KEY.get(str(key))
        if field is None:
            raise SettingsUpdateError(f"unknown_setting:{key}")
        if not field.editable:
            raise SettingsUpdateError(f"setting_not_editable:{key}")
        normalized[str(key)] = _validate_value(field, value)

    candidate = {
        field.key: normalized.get(field.key, _nested_get(raw, field.key, field.default))
        for field in SETTING_DEFINITIONS
    }
    if int(candidate["case_followup.max_parallel_profiles"]) > int(candidate["case_followup.claim_group_limit"]):
        raise SettingsUpdateError("parallel_profiles_exceed_claim_group")
    return normalized


def _atomic_write_yaml(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(raw, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def update_settings_file(
    path: Path,
    backup_root: Path,
    *,
    expected_revision: str,
    changes: Any,
) -> dict[str, Any]:
    with _SETTINGS_WRITE_LOCK:
        current_revision = settings_revision(path)
        if expected_revision != current_revision:
            raise SettingsUpdateError("settings_revision_conflict", status_code=409)
        raw = _read_yaml(path)
        normalized = validate_settings_changes(raw, changes)
        changed: dict[str, dict[str, int | float]] = {}
        for key, value in normalized.items():
            field = DEFINITIONS_BY_KEY[key]
            old_value = _nested_get(raw, key, field.default)
            if old_value != value:
                changed[key] = {"old": old_value, "new": value}
                _nested_set(raw, key, value)

        if changed:
            if path.exists():
                backup_root.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                shutil.copy2(path, backup_root / f"settings-{stamp}-{current_revision}.yaml")
            _atomic_write_yaml(path, raw)

        restart_required = [
            key
            for key in changed
            if DEFINITIONS_BY_KEY[key].apply_mode == "restart_required"
        ]
        return {
            "snapshot": build_settings_snapshot(path),
            "changed": changed,
            "restart_required": restart_required,
        }


def apply_immediate_web_settings(settings: Any, snapshot: dict[str, Any]) -> None:
    values = {field["key"]: field["value"] for field in snapshot["fields"]}
    settings.session_ttl_hours = float(values["web.session_ttl_hours"])
    settings.submit_max_brands = int(values["web.submit_max_brands"])


__all__ = [
    "DEFINITIONS_BY_KEY",
    "SETTING_DEFINITIONS",
    "SettingsUpdateError",
    "apply_immediate_web_settings",
    "build_settings_snapshot",
    "settings_revision",
    "update_settings_file",
    "validate_settings_changes",
]
