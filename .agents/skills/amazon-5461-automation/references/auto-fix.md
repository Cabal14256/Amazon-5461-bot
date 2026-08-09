# 自动修复与迭代提升（历史设计）

> 当前状态（2026-06-01）：历史设计资料。当前项目树不存在 `src/selector_auto_fix.py`、`src/auto_fix_engine.py`、`src/openclaw_integration.py`、`scripts/run_with_auto_fix.py`、`scripts/run_with_openclaw.py`。不要按本文命令直接执行。
>
> 当前实际可用的诊断/迭代路径：`scripts/diagnose_no_submit_add_product.py`、`src/capture/*`、`src/executor/state_loop.py`、`knowledge/pages/*`、`src/failure_classifier.py`。

> 位置: `projects/amazon-5461-bot/`
> 最后更新: 2026-04-14

## 概述

当 Amazon 页面结构变化导致选择器失效时，系统可以自动检测并尝试修复。

**OpenClaw 集成**：系统可以与 OpenClaw 深度集成，实现：
- 截图分析辅助修复决策
- 人工介入实时通知
- 修复方案智能确认
- 执行结果自动汇报

## OpenClaw 集成流程

```
执行流程
    ↓
遇到错误
    ↓
截图保存证据
    ↓
OpenClaw 分析截图
    ↓
决策分支:
    ├── 高置信度 → 自动修复 → 重试
    ├── 中置信度 → 请求确认 → 等待用户 → 应用/跳过
    └── 低置信度 → 人工接管 → 发送通知 → 等待处理
    ↓
报告结果
```

### OpenClaw 参与场景

| 场景 | OpenClaw 作用 |
|------|---------------|
| 选择器失效 | 分析截图，确认新选择器位置 |
| 验证码/2FA | 发送通知，请求人工输入 |
| 未知页面 | 截图分析，判断页面类型 |
| 修复确认 | 决策是否自动应用修复 |
| 执行报告 | 汇总结果，发送状态更新 |

## 核心组件（历史设计，当前不存在）

### 1. 选择器自动修复（历史设计：`src/selector_auto_fix.py`，当前不存在）

**功能**:
- 从错误消息中检测失效的选择器
- 从页面 HTML 中提取新的选择器候选
- 模糊匹配找到最可能的新选择器
- 自动更新配置文件

**修复策略**:
1. **精确匹配**: data-cy 属性完全匹配 (置信度 0.95)
2. **模糊匹配**: 部分字符串匹配 (置信度 0.6-0.7)
3. **兜底策略**: 使用同类型的第一个候选 (置信度 0.4)

**使用方式**:
```python
from selector_auto_fix import try_auto_fix_selector

try:
    page.locator('kat-input[data-cy="old-selector"]').fill("text")
except Exception as e:
    fix = try_auto_fix_selector(
        error_message=str(e),
        page_html=page.content(),
        flow_name="flow_5461",
        project_root="."
    )
    if fix and fix.verified:
        print(f"选择器已自动修复: {fix.new_selector}")
```

### 2. 自动修复引擎（历史设计：`src/auto_fix_engine.py`，当前不存在）

**功能**:
- 错误检测与分类
- 根因分析（使用 LLM 或本地启发式）
- 修复策略生成
- 自动修复执行
- 修复效果验证
- 知识沉淀

**错误类型**:
- `ELEMENT_NOT_FOUND` - 元素未找到
- `TIMEOUT` - 操作超时
- `SELECTOR_INVALID` - 选择器无效
- `FORM_VALIDATION_FAILED` - 表单验证失败
- `PAGE_CHANGED` - 页面结构变化

**使用方式**:
```python
from auto_fix_engine import auto_fix_error

try:
    # 执行自动化流程
    run_flow()
except Exception as e:
    result = auto_fix_error(e, {
        "page_url": page.url,
        "flow_step": "fill_form",
        "account_id": "us_store_530",
        "brand_name": "OUNNE"
    }, project_root)
    
    if result.fix_applied:
        print(f"修复已应用: {result.fix_suggestion.description}")
    else:
        print(f"需要人工处理: {result.fix_suggestion.description}")
```

### 3. OpenClaw 集成（历史设计：`src/openclaw_integration.py`，当前不存在）

**功能**:
- 截图分析请求
- 人工介入通知
- 修复决策确认
- 执行结果报告

**使用方式**:
```python
from openclaw_integration import (
    OpenClawIntegration,
    notify_error_with_screenshot,
    request_human_for_captcha
)

# 初始化
openclaw = OpenClawIntegration(project_root)

# 截图分析
analysis = openclaw.analyze_screenshot(
    screenshot_path="error.png",
    context={"error": "元素未找到"}
)

# 请求人工处理验证码
code = request_human_for_captcha(
    screenshot_path="captcha.png",
    account_id="us_store_530"
)

# 修复决策确认
decision = openclaw.confirm_auto_fix(
    fix_description="更新选择器",
    old_value="kat-input[data-cy='old']",
    new_value="kat-input[data-cy='new']",
    confidence=0.75
)
```

## 使用方式（历史命令示例，不可直接执行）

### 基础自动修复（本地模式）

```bash
# 历史示例，不可直接执行：当前项目树不存在 scripts/run_with_auto_fix.py
python scripts/run_with_auto_fix.py us_store_530 OUNNE --flow submit_5461  # 历史示例，不可直接执行：当前不存在
```

### OpenClaw 增强模式

```bash
# 历史示例，不可直接执行：当前项目树不存在 scripts/run_with_openclaw.py
# OpenClaw 增强执行
python scripts/run_with_openclaw.py us_store_530 OUNNE --flow submit_5461  # 历史示例，不可直接执行：当前不存在

# 禁用 OpenClaw（纯本地）
python scripts/run_with_openclaw.py us_store_530 OUNNE --no-openclaw  # 历史示例，不可直接执行：当前不存在
```

## 自动化修复流程

```
1. 执行流程
        ↓
2. 遇到错误（选择器失效 / 超时 / 页面变化）
        ↓
3. 错误分类
   - 选择器错误 -> 选择器自动修复
   - 时序错误 -> 增加等待时间
   - 页面变化 -> 提取新选择器
        ↓
4. 生成修复方案
   - 高置信度 (>0.8): 自动应用
   - 中置信度 (0.5-0.8): 请求人工确认
   - 低置信度 (<0.5): 人工接管
        ↓
5. 应用修复
   - 更新配置文件
   - 创建备份
   - 记录修复历史
        ↓
6. 重试执行
        ↓
7. 验证修复效果
   - 成功: 沉淀到知识库
   - 失败: 继续重试或人工接管
```

## 配置备份

每次自动修复选择器时，会创建备份文件：
```
config/selectors/flow_5461.yaml.bak
```

如需回滚：
```bash
cd projects/amazon-5461-bot
cp config/selectors/flow_5461.yaml.bak config/selectors/flow_5461.yaml
```

## OpenClaw 配置

启用 OpenClaw 集成需要设置环境变量：

```bash
# .env
OPENCLAW_INTEGRATION=true
OPENCLAW_GATEWAY_URL=http://localhost:8080
```

## 知识库（历史设计，当前文件不存在）

历史设计中，成功的修复方案会沉淀到：
```
data/auto_fix_knowledge.json  # 历史设计，当前文件不存在
```

当前项目树不存在该文件；当前知识库/状态样本主要在 `knowledge/pages/*` 和运行 evidence 中维护。

历史格式包含：
- 错误模式
- 修复策略
- 代码变更
- 成功次数

## 限制与注意事项

1. **高置信度才自动应用**: 默认置信度 >= 0.6 才自动修复
2. **保留人工确认环节**: 复杂错误或低置信度时通过 OpenClaw 请求确认
3. **创建配置备份**: 每次修改前自动备份
4. **记录修复历史**: 便于追溯和审计
5. **CAPTCHA/2FA 通知**: 通过 OpenClaw 发送实时通知，不自动处理
6. **网络依赖**: OpenClaw 模式需要 Gateway 可访问

## 未来增强

- [ ] 集成 OpenClaw Vision 分析页面截图
- [ ] 通过 OpenClaw 发送语音/推送通知
- [ ] 学习历史修复模式，提高匹配准确度
- [ ] 建立选择器老化预警机制
- [ ] 支持更多错误类型的自动修复
- [ ] 批量任务执行报告通过 OpenClaw 发送

