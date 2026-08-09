# Amazon 5461 自动化工具

> **内部项目声明**
>
> 本项目是针对特定内部业务流程、账号配置和运行环境开发的自动化工具，仅供内部研究、维护和技术参考。项目中的流程、选择器、配置方式和业务判断具有较强的环境依赖性，**不建议直接复制、照搬或用于生产环境**。如需参考实现，请先完成独立的安全审查、合规评估、测试验证和环境适配，并自行承担使用风险。

本项目用于辅助处理 Amazon Seller Central 的目录授权、品牌验证、5461 和 GTIN 豁免等工作流，主要使用 Python、Playwright 和 AdsPower。

## 使用边界

- 默认仅执行诊断或 dry-run，不进行真实提交。
- 真实提交必须由操作人员明确指定账号、站点、品牌并显式启用提交参数。
- 不绕过 CAPTCHA、2FA、登录检查、平台风控、速率限制或其他安全控制。
- 不伪造品牌声明、账号身份、文件或证据。
- `Draft`、`Submitted no Case ID`、`Under Review`、`Approved`、`Declined`、`Already Approved`、`Not Found` 和技术故障必须分别记录。
- 未取得 Case ID 不代表流程必然失败，应结合 Dashboard、Selling Applications 和最终证据判断。

详细边界请阅读 [`PROJECT.md`](PROJECT.md) 和 [`AGENTS.md`](AGENTS.md)。

## 环境要求

- Windows 与 PowerShell
- Python 3.11 或兼容版本
- Playwright
- AdsPower（仅在需要连接对应浏览器环境时）

## 安装

```powershell
cd C:\Users\Admin\Documents\Amazon-5461-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install pytest ruff
python -m playwright install chromium
```

## 本地配置

从公开模板创建本地账号配置：

```powershell
Copy-Item config\accounts.example.json runtime\private\accounts.json
$env:AMAZON5461_ACCOUNTS_PATH="runtime/private/accounts.json"  # 可选覆盖项
```

真实账号配置必须保存在 `runtime/private/accounts.json`，不得写入 `config/accounts.json`，也不得提交到 Git。

本地运行配置可从示例文件创建：

```powershell
Copy-Item config\settings.example.yaml config\settings.yaml
```

`.env`、`runtime/private/`、运行日志、证据、数据库、品牌包和业务工作簿均属于本地材料，不应上传到代码仓库。

## 项目验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q src scripts tests cli
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check .
```

## 命令入口

统一命令行入口示例：

```powershell
python -m cli.amazon5461 diagnose --account us_store_000 --brand EXAMPLE --site US
python -m cli.amazon5461 dry-run --account us_store_000 --brand EXAMPLE --site US
python -m cli.amazon5461 run --account us_store_000 --brand EXAMPLE --site US --submit
```

前两个命令用于诊断和 dry-run。最后一个命令包含 `--submit`，可能触发真实操作，只能在得到明确授权并完成前置检查后使用。

## Case 后续检查

真实 5461 流程取得可靠 Case ID 后，可创建延迟检查任务。延迟时间由 `config/settings.yaml` 中的 `case_followup.delay_hours` 控制，也可以在单次运行时覆盖：

```powershell
python -m cli.amazon5461 run --account us_store_000 --brand EXAMPLE --site US --submit --case-followup-delay-hours 24
python -m cli.amazon5461 case-check --account us_store_000 --brand EXAMPLE --site US --case-id 12345678901
python -m cli.amazon5461 followups --watch
```

`case-check` 默认不会更新本地登记或飞书记录，只有显式传入 `--register` 才会登记结果。`Answered` 只表示 Amazon 已回复，不等同于批准。最终状态必须结合 Case 内容、Manage Your Brands 和 Add Product 验证结果判断。

## 本地诊断信号

当状态循环耗尽确定性恢复方式时，程序会写入本地诊断信号，而不是持续调用模型：

```powershell
python scripts\check_codex_signal.py
python scripts\check_codex_signal.py --mark-handled --note "诊断已完成"
```

可选的页面分析功能只读取项目根目录 `.env` 中的 `LLM_*` 配置，并且仅在程序明确请求分析时调用模型。

## 目录结构

```text
src/         核心自动化代码
scripts/     运维、诊断和批处理脚本
cli/         统一命令行入口
.agents/     仓库级 Codex 技能
config/      非敏感配置模板和选择器
knowledge/   页面知识、状态和分析提示
docs/        方案、运行手册和状态规则
tests/       回归测试
runtime/     本地运行状态和输出，默认不进入 Git
legacy/      本地历史脚本和一次性材料，默认不进入 Git
```

## 免责声明

本仓库不构成 Amazon 官方工具、合规意见或可直接部署的通用解决方案。Amazon 页面、政策、审核标准和接口可能随时变化。任何参考、修改或运行本项目的行为，都应由使用者自行验证合法性、准确性、安全性和平台合规性。
