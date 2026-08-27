[日本語](README.md) | [简体中文](README.zh-CN.md)

# IICS Agent 使用手册

Takeda Japan IICS 运维工具：用自然语言（Claude Code CLI）或手动命令查询并修改三个 `JAPAN_*` 文件夹下的 task 配置，并导出 Excel。

**当前版本：v0.1.1 Read-only MVP**——只读查询，不支持任何写操作。

本手册以 **Windows + PowerShell** 为主路径（这是实际使用环境）；如果你在 Git Bash / WSL / macOS 上操作，文中会用提示框单独标注差异。

---

## 这是什么

IICS（Informatica Intelligent Cloud Services）上有数千个 task，散落在三个项目文件夹里，逐一在 Web UI 里点开查看配置非常低效。这个工具把"枚举 task / 看详情 / 解析连接 ID / 导出 Excel"这四件事封装成命令，可以用自然语言驱动（Mode A），也可以手动调用（Mode C）。之后将会支持task改修。

适用范围：

| 项 | 取值 |
|----|------|
| IICS Org | `use4.dm-us.informaticacloud.com`（Takeda Prod） |
| 文件夹 | `JAPAN_COMMERCIAL_DWH`、`JAPAN_SCRM_BAU`、`JAPAN_SCRM_DI` |
| Task 类型 | `DSS`（Sync Task）、`MTT`（Mapping Task）、`WORKFLOW`（Linear Taskflow） |

## 你能做什么 / 不能做什么

| 能 | 不能 |
|----|------|
| 查询某文件夹下的 task 列表 | 修改 task 的 runtime / connection（v0.1 没有写路径，不是被拦截，是代码里压根不存在） |
| 看某个 task 的完整配置详情 | 批量变更 |
| 把 connection ID 解析成可读名称 | 自动续期 token（过期需要手动重新获取） |
| 导出 Excel（单 sheet Task Summary，含源/目标对象列） | 跨文件夹可靠区分同名 task（IICS API 限制，见下文） |

---

## 第一次使用：从零开始

### 1. 检查本机环境

打开 PowerShell（开始菜单搜索 "PowerShell"），依次执行：

```powershell
py --version
py -m pip --version
```

如果两条都正常打印版本号（比如 `Python 3.11.x`），跳到第 2 步。

如果提示 `py 不是内部或外部命令`：说明本机没装 Python。去 [python.org/downloads](https://www.python.org/downloads/) 下载 3.11 或 3.12 版本，安装时**务必勾选 "Add python.exe to PATH"**，装完重新打开 PowerShell 再试一次上面两条命令。

### 2. 安装项目依赖

```powershell
Set-Location -LiteralPath "改成你实际的项目路径"   
py -m pip install -r requirements.txt
```

用 `py -m pip` 而不是裸 `pip`，可以避免企业电脑上多个 Python 版本导致装错环境的问题。

`requirements.txt` 内容很简单，只有两个包，不需要 Anthropic SDK：

```
requests>=2.31.0
openpyxl>=3.1.0
```

> **可选：用虚拟环境隔离依赖**
>
> 如果不想把这两个包装进系统 Python，可以用虚拟环境（非必需，跳过也完全能用）：
>
> ```powershell
> py -m venv .venv
> .\.venv\Scripts\Activate.ps1
> python -m pip install -r requirements.txt
> ```
>
> 如果激活时报错 `running scripts is disabled on this system`，先执行一次：
>
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
>
> 或者干脆不激活，直接用虚拟环境里的 Python 调用：
>
> ```powershell
> .\.venv\Scripts\python.exe -m pip install -r requirements.txt
> .\.venv\Scripts\python.exe .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
> ```

### 3. 跑一次"无 token 预检"

先不设 token，直接跑一条命令，确认 Python 环境和脚本本身没问题：

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

预期看到：

```json
{
  "error": "IICS_SESSION_TOKEN 未设置",
  "error_type": "ValueError"
}
```

看到这个结果说明 Python、依赖、脚本入口全部正常，只差最后一步——设置 token。如果报的是别的错误（比如 `ModuleNotFoundError`），回到第 2 步重新检查依赖安装。

### 4. 获取 Session Token

IICS 当前用企业 SSO（Okta），没有专用服务账号，token 需要手动从浏览器拿：

1. 浏览器（Chrome / Edge）SSO 登录 IICS Web UI，完成 Okta 验证。
2. 按 `F12` 打开开发者工具，点击顶部 **Network** 标签页。
3. 刷新一下当前 IICS 页面，让请求重新发出来。
4. 在请求列表里找一个名字像 `objects`、`connection`、`api` 之类的请求，点开它。
5. 右侧面板点 **Headers** → 往下滚动找 **Request Headers** → 找到 `Cookie` 这一行。
6. 在一长串 Cookie 字符串里找到 `USER_SESSION=`，只复制 **等号后面、到下一个分号 `;` 前面**的那一段值。

### 5. 设置 Token

```powershell
$env:IICS_SESSION_TOKEN = "<刚才复制的 USER_SESSION 值>"
```

这个设置只在当前 PowerShell 窗口内有效，**不要**写进文件、不要截图发出去、关闭窗口后下次要重新设置一遍。

### 6. 跑通真正的第一条命令

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
echo $LASTEXITCODE
```

判断是否成功：

| `$LASTEXITCODE` | 含义 |
|---|---|
| `0` | 成功，看上面打印的 JSON 里的 `tasks` 列表 |
| `1` | 失败，看 JSON 里的 `error` 字段，对照下方「故障排查」 |

到这一步如果能看到 task 列表，环境就完全配好了，可以进入日常使用。

---

## 日常使用：两种模式怎么选

| | Mode C：手动执行 | Mode A：Claude Code CLI |
|---|---|---|
| 怎么用 | 在 PowerShell 里直接敲 `python iics_cli.py ...` | 在 `claude` 会话里说自然语言 |
| 谁生成命令 | 你自己输入 | Claude Code CLI 自动生成 |
| 需要 Claude Code | 否 | 是 |
| 适合 | 刚才已经验证过的方式，继续用即可；脚本化批处理、cron 定时任务、`claude` 不可用时 | 日常查询、不想记命令格式、想用自然语言描述需求 |

两种模式调用的是**完全相同的入口** `iics_cli.py`，没有任何代码差异，输出也一样是 JSON。你刚才在「第一次使用」里跑通的就是 Mode C；如果想用自然语言交互，可以装好 Claude Code 后切到 Mode A，两者随时切换，不冲突。

### Mode C：手动执行（继续沿用上面的方式）

```powershell
$env:IICS_SESSION_TOKEN = "<USER_SESSION 值>"

python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
python iics_cli.py resolve-connection 000LVU0B000000000015
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL
```

没有 AI 层介入，安全边界很简单：不需要任何权限配置，token 处理规则和 Mode A 完全一样。

### Mode A：Claude Code CLI

启动方式（PowerShell，用反引号 `` ` `` 续行，不是反斜杠）：

```powershell
claude `
  --tools "Bash,Read" `
  --allowedTools "Bash(python iics_cli.py *)" "Bash(python smoke_test.py)" "Read" `
  --disallowedTools "Edit" "Write" "Bash(curl *)"
```

> 如果你在 Git Bash / WSL / macOS 终端里启动，续行符换成反斜杠 `\`：
> ```bash
> claude \
>   --tools "Bash,Read" \
>   --allowedTools "Bash(python iics_cli.py *)" "Bash(python smoke_test.py)" "Read" \
>   --disallowedTools "Edit" "Write" "Bash(curl *)"
> ```

三个 flag 的作用：

- `--tools "Bash,Read"`：限制 Claude Code 能看到的工具集合，`Edit`/`Write` 等不在其中。
- `--allowedTools`：让 `iics_cli.py`、`smoke_test.py` 这两个命令免确认自动执行——这不是"只允许调用"的可见性限制，是自动批准规则。
- `--disallowedTools`：对 Bash 内部的危险调用模式做拒绝（比如 `curl`），即使 Bash 整体可见也会被拦截。

**不要**使用 `--dangerously-skip-permissions`。

进入会话后，用自然语言说出需求即可，Claude Code CLI 会自动生成对应的命令：

| 你说 | Claude Code CLI 实际执行 |
|------|--------------------------|
| 「列出 JAPAN_SCRM_DI 下的 DSS task」 | `python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS` |
| 「看 DI_FACT_SALES_DAILY 这个 DSS 的详情」 | `python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI` |
| 「把这个 connection id 解析成名字」 | `python iics_cli.py resolve-connection 000LVU...` |
| 「导出 JAPAN_COMMERCIAL_DWH 的所有 task 到 Excel」 | `python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL` |

---

## 功能详解（Mode A / Mode C 通用）

下面四个子命令是 v0.1 的全部能力，两种模式都直接调用它们，命令格式完全相同。

### 1. `list-tasks` — 枚举文件夹下的 task

```powershell
python iics_cli.py list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `<folder>` | 是 | 三个 `JAPAN_*` 文件夹之一 |
| `--type` | 否 | 默认 `ALL`；`ALL` 会按 DSS/MTT/WORKFLOW 三类分别查询合并（自动排除 `MAPPING` 等非 task 设计对象） |
| `--page-size` | 否 | 默认 200（IICS V3 单页上限也是 200，超过会被截到 200） |

示例：

```powershell
python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

返回：

```json
{
  "folder": "JAPAN_SCRM_DI",
  "count": 847,
  "tasks": [
    {"name": "DI_FACT_SALES_DAILY", "type": "DSS", "id": "...", "status": ""},
    ...
  ]
}
```

> `status` 字段目前恒为空——V3 对象列表本身不带 status 信息，这是 API 数据限制，不是缺陷。

### 2. `get-task` — 查看单个 task 详情

```powershell
python iics_cli.py get-task <task_name> <task_type> [--folder PATH] [--no-resolve]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `<task_name>` | 是 | task 名称 |
| `<task_type>` | 是 | `DSS` / `MTT` / `WORKFLOW` |
| `--folder` | **强烈建议填** | 见下方"同名 task"说明 |
| `--no-resolve` | 否 | 跳过 connection ID → 名称解析，只看 ID |

示例：

```powershell
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
```

返回：

```json
{
  "name": "DI_FACT_SALES_DAILY",
  "type": "DSS",
  "folder": "JAPAN_SCRM_DI",
  "runtime_environment": {"id": null, "name": ""},
  "source_connection": {"id": "000LVU0B000000000015", "name": "SCRM_ORA_STDB"},
  "target_connection": {"id": "000LVU0B00000000009O", "name": "mysql_scrm_hnn_api"},
  "raw": { ... }
}
```

> **重要限制**：`runtime_environment.name` 对 DSS 类型恒为空——IICS API 的 DSS 详情接口本身不返回 runtime environment 字段，这是平台数据可得性限制，不是工具的 bug。
>
> **MTT 详情已增强**：MTT 走官方 FRS 路径，返回结构与 DSS/WORKFLOW 不同——顶层含 `id`（classic V2 ID）、`federated_id`（V3/FRS ID）、`detail_route`、`runtime_environment{id,name}`、`task_mapping{id,name,location,updated_on}`（`location` 当前为空）、`transformations[]`（仅来自 MTT parameters）、`enrichment_warnings[]`。
>
> **MTT 查询必须同时提供 task name 和 `--folder`**：系统会先在指定 folder 中定位该 task 的 federated ID，再通过官方 FRS detail 接口获取详情。这样彻底避免了 MTT 的跨文件夹同名歧义。示例：
>
> ```powershell
> python .\iics_cli.py get-task TASK_NAME MTT --folder JAPAN_SCRM_BAU
> ```
>
> MTT 不支持直接使用 `--id`。**DSS / WORKFLOW** 的 `--folder` 仍为可选，且它们跨文件夹同名时仍可能有歧义（建议也带上 `--folder`）。

**MTT `get-task` 默认输出（运维用）** 只显示与 Excel `Task Summary` 相同的业务字段，一个 transformation 一条 record：

```powershell
python .\iics_cli.py get-task TASK_NAME MTT --folder JAPAN_SCRM_BAU
# → {"task_count": 1, "rows": 2, "records": [ {业务字段…}, … ]}
```

需要完整内部结构（`raw` / `federated_id` / `transformations` 等，调试/分析用）时加 `--full`：

```powershell
python .\iics_cli.py get-task TASK_NAME MTT --folder JAPAN_SCRM_BAU --full
```

> **注意：返回结构因 task 类型而异，不能用同一套解析逻辑**——**MTT** 返回 `{task_count, rows, records[]}`；**DSS / WORKFLOW** 返回单个 task 的扁平字典（无 `records` 包装）。脚本化调用请先判断 `task_type`。

### 3. `resolve-connection` — 解析 connection ID

```powershell
python iics_cli.py resolve-connection <id> [<id>...]
```

支持一次传多个 ID（空格分隔）：

```powershell
python iics_cli.py resolve-connection 000LVU0B000000000015 000LVU0B00000000009O
```

返回：

```json
{
  "resolved": {
    "000LVU0B000000000015": "SCRM_ORA_STDB",
    "000LVU0B00000000009O": "mysql_scrm_hnn_api"
  },
  "failed": []
}
```

同一会话内重复解析同一个 ID 会命中内存缓存，不会重复发请求。

### 4. `export` — 导出 Excel / JSON

```powershell
python iics_cli.py export <folder> [--type ALL] [--details|--summary-only] [--format excel|json] [--filename NAME]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `<folder>` | 是 | 三个 `JAPAN_*` 文件夹之一 |
| `--type` | 否 | 默认 `ALL` |
| `--details` | 否 | **默认行为**，逐个 task 拉详情，Excel 含完整 runtime/connection 信息 |
| `--summary-only` | 否 | 跳过详情拉取，只导出 task 列表，速度快但 runtime/connection 列为空 |
| `--format` | 否 | 默认 `excel` |
| `--filename` | 否 | 默认按时间戳生成 |

示例：

```powershell
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type DSS
```

返回：

```json
{
  "file_path": "/path/to/iics_export_20260630_103000.xlsx",
  "format": "excel",
  "task_count": 83,
  "rows": 96,
  "failed_details_count": 0, "failed_details": [],
  "enrichment_warnings_count": 0, "enrichment_warnings": [],
  "summary": "..."
}
```

Excel 含 2 个 sheet（原 `Field Mapping` / `Truncation Risk` 已移除）：

| Sheet | 说明 |
|-------|------|
| Task Summary | 业务数据主 sheet，共 22 列（`@type, id, orgId, Task Name, Task Type, Task Location, Runtime Environment, Task Updated On, Mapping Name, Mapping Updated On, Transformation Name, Source Connection, Source Object, Custom Query, Target Connection, Operation, Target Object, Parameter File Directory, Parameter File Name, Pre-Processing Commands, Post-Processing Commands, Status`）。**DSS / WORKFLOW：1 task = 1 行；MTT：每个 Source/Target transformation = 1 行**（任务级字段每行重复），MTT 专属列对 DSS/WORKFLOW 留空。多个 source object 在同一单元格换行显示。（`Mapping Location` 因 API 无可靠来源、始终为空，已删除。） |
| Export Errors | 错误 / 警告记录：主详情失败记为 `error`，enrichment（mapping/runtime/connection）失败记为 `warning`；都没有时写单行 `No export errors`。 |

> 一个文件夹可能有上千个 task，`--details` 模式会对每个 task 单独调用一次详情接口，文件夹大时会比较慢。快速预览用 `--summary-only`。
>
> 上表为 DSS / MTT / `--type ALL` 的通用列。**`--type WORKFLOW` 使用独立的 9 列 schema，见下。**

#### `--type WORKFLOW` 导出（child task 展开）

```powershell
python .\iics_cli.py export JAPAN_SCRM_DI --type WORKFLOW --format excel
```

WORKFLOW 的 `Task Summary` 使用**专用 9 列**：`Task Type, folder, orgId, id, Task Name, updateTime, tasks.taskId, tasks.type, tasks.name`。

**每个 workflow 的 `tasks[]` child 独立占一行**；父级字段（`Task Type` / `folder` / `orgId` / `id` / `Task Name` / `updateTime`）在每行重复；child 字段（`tasks.taskId` / `tasks.type` / `tasks.name`）单独展示。例：

```text
1 个 workflow + 2 个 child task → Excel 2 行（父级字段两行相同）
```

无 child task 的 workflow 仍占 1 行（child 三列留空）。`folder` 取自命令行的 `<FOLDER>`。WORKFLOW export 不查询 child 的 MTT/DSS 详情。**WORKFLOW `get-task` 输出不变**（仍为完整扁平 detail）。

> `--type ALL` 导出时：通用 `Task Summary`（DSS + MTT）之外，额外生成一张 **`Workflow Tasks`** sheet（WORKFLOW 的 9 列 child 展开）；没有 WORKFLOW 时不生成该 sheet。

### 4b. `export-selected` — 只导出指定 Task List

`export <folder> --type X` 是「导出该 folder 下全部 X」;`export-selected` 是「**只导出你给的清单里指定的 task**」——不会因为某个 task 在某 folder 就把该 folder 下其它 task 一起导出。

```powershell
python .\iics_cli.py export-selected --input .\selected_tasks.json --output-dir .\exports
```

输入是一个 JSON 数组,每项 `{name, type, folder}`(都必须是字符串):

```json
[
  {"name": "Task_A",     "type": "DSS",      "folder": "JAPAN_SCRM_DI"},
  {"name": "Task_B",     "type": "MTT",      "folder": "JAPAN_SCRM_BAU"},
  {"name": "Workflow_A", "type": "WORKFLOW", "folder": "JAPAN_COMMERCIAL_DWH"}
]
```

行为:按类型分别生成 **3 个独立 Excel**(`selected_DSS_*.xlsx` / `selected_MTT_*.xlsx` / `selected_WORKFLOW_*.xlsx`,同一时间戳);某类型清单为空则不生成该文件。stdout 返回一个小型 summary JSON(`input_count / unique_count / duplicates_removed`、每类型 `requested/success/failed/rows/file_path`、`failures[]`、`warnings[]`),**不含**完整 detail。

重要限制:

- **DSS / WORKFLOW 按名查询**:若清单里同一 type+同一 name 出现在多个 folder,无法可靠区分,会被标记为 `ambiguous_input` 计入失败;且即使只选一个,组织内别的 folder 若有同名也无法保证(summary warning 会提示)。
- **MTT 可按 folder 可靠消歧**(V3 定位 federated id),跨 folder 同名没问题。
- 单个 task 失败不中断整批;只有整个输入 JSON 非法才会整体失败(exit 1)。

> Mode A(Claude Code):可以从 Excel / CSV / Markdown 表格 / 粘贴文本整理出上面的 JSON 再调用;但 Python 正式入口**只接受 JSON**。最终统计以 summary 为准。

### 稳定性与导出判定

- **`_request()` 带 timeout + 瞬时重试**：默认 timeout `(connect=10s, read=60s)`；对 `Timeout` / `ConnectionError` / HTTP `429/502/503/504` 最多重试 3 次（backoff 1s→2s），日志只进 stderr、不含 token。
- **`export --details` 单个 task 失败不中断**：主详情失败记入 `failed_details`（进 `Export Errors`）；`Task Summary` 仍保留该 task 一行。MTT 的 mapping/runtime/connection **enrichment 失败**只记为 `enrichment_warnings`（字段留空），**不**计入 `failed_details`、不终止导出。

#### 导出判定标准

| 计数 | 含义 |
|------|------|
| `task_count` | 本次导出的逻辑 task 数，应与同参数 `list-tasks` 的 `count` 一致 |
| `rows` | Task Summary 实际物理数据行数（MTT 展开后可能 **大于** `task_count`） |
| **完整成功** | `task_count == list-tasks count` **且** `failed_details_count == 0` |
| `enrichment_warnings_count > 0` | 部分 mapping/runtime/connection 未解析，字段留空；不影响完整性判定，可查 `Export Errors` |

> 核对流程：先 `list-tasks --type X` 记下 `count`，再 `export --type X`，确认 stdout 的 `task_count` 与 `count` 相等且 `failed_details_count == 0`。**不要**用 `rows == count` 判断完整性（MTT 会展开成多行）。

---

## 环境变量与参数取值

### 环境变量（只有一个是必需的）

| 变量 | 必需 | 说明 |
|------|------|------|
| `IICS_SESSION_TOKEN` | **是** | 浏览器 `USER_SESSION` Cookie 值，见上方「获取 Session Token」 |
| `IICS_LOGIN_URL` / `IICS_USER` / `IICS_PASS` | 否 | v0.1 不需要、不调用——为未来专用服务账号模式预留的框架，目前设不设都没有影响 |

### 命令参数（不是环境变量，每次按需传）

文件夹和 task 类型不是环境变量，是每次调用 `iics_cli.py` 时直接作为命令参数传入的，因为不同查询通常需要不同的文件夹/类型：

| 参数 | 合法取值 |
|------|---------|
| folder | `JAPAN_COMMERCIAL_DWH`、`JAPAN_SCRM_BAU`、`JAPAN_SCRM_DI` |
| task type | `DSS`、`MTT`、`WORKFLOW`、`ALL`（仅 `list-tasks` / `export` 支持 `ALL`，`get-task` 必须指定具体类型） |

### 冒烟测试专用环境变量（与日常使用无关）

下面这组只在跑 `smoke_test.py` 验收测试时使用，**日常用 `iics_cli.py` 查询不需要设置**：

```powershell
$env:IICS_TEST_FOLDER    = "JAPAN_SCRM_DI"      # 默认值
$env:IICS_TEST_TASK_TYPE = "DSS"                # 默认值
$env:IICS_TEST_TASK_NAME = "<某个真实task名>"     # 默认空，自动取第一个
python smoke_test.py
```

---

## 输出格式说明

- **stdout 只输出 JSON**，可以直接拿去脚本解析。
- **stderr 输出人类可读日志**（进度、`[Auth]` 信息、警告），不会混进 JSON。
- **退出码**：成功为 `0`；出错（包括 `IICS_SESSION_TOKEN` 未设置、API 报错、工具函数返回 `{"error": ...}`）为 `1`。PowerShell 里用 `echo $LASTEXITCODE` 查看，脚本化调用时检查这个值即可，不需要解析 JSON 内容判断成败。

---

## 已知限制（v0.1 边界）

- **只读**：没有任何写操作子命令，代码层面 `update`/`batch`/`safety` 相关文件都不存在，不是靠权限拦截。
- **DSS 的 `runtime_environment.name` 恒为空**：API 数据限制，见上方 `get-task` 说明。
- **同名 task 跨文件夹无法可靠区分**：务必带 `--folder`。
- **MTT / WORKFLOW 的 runtime / connection 字段结构未深度验证**：v0.1 主要在 DSS 上做了完整字段验证。
- **Token 不会自动刷新**：过期后需要重新从浏览器手动获取。
- **Excel 导出 `Task Summary`（22 列）+ `Export Errors` 两个 sheet**（原 `Field Mapping` / `Truncation Risk` 已移除；`Mapping Location` 因始终为空已删除）；MTT 按 transformation 展开成多行，`task_count` 与 `rows` 分离。

---

## 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `py 不是内部或外部命令` | 没装 Python，或装的时候没勾选 Add to PATH | 重新安装 Python，安装界面务必勾选 "Add python.exe to PATH" |
| `python -m pip install` 报错 / 装错位置 | 企业电脑装了多个 Python 版本 | 统一用 `py -m pip install -r requirements.txt` |
| 激活虚拟环境报 `running scripts is disabled` | PowerShell 执行策略限制 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，或不激活直接用 `.\.venv\Scripts\python.exe` |
| `{"error":"IICS_SESSION_TOKEN 未设置"}`，退出码 1 | 环境变量没设，或在新开的窗口里 | 重新 `$env:IICS_SESSION_TOKEN = "..."` |
| 报 `RuntimeError: IICS session expired...` | Token 过期（401） | 浏览器重新取 `USER_SESSION` 再设置一遍 |
| `list-tasks` 返回 `{"error":...}` | 文件夹名拼错 | 核对是否为三个 `JAPAN_*` 之一 |
| `get-task` 结果好像不是预期的那个 task | 同名 task 跨文件夹歧义 | 加上 `--folder` |
| Excel 打不开 / 缺 sheet | `openpyxl` 没装好或写盘失败 | 重新 `py -m pip install -r requirements.txt`，检查目录写权限 |
| Claude Code 启动命令报语法错误 | 在 PowerShell 里用了反斜杠 `\` 续行 | PowerShell 用反引号 `` ` ``，反斜杠续行只在 Bash/WSL 下有效 |
| stdout 里混进了非 JSON 内容 | 不应该发生，属于缺陷 | 反馈给开发者，附带完整命令和输出 |

---

## 安全须知

- `IICS_SESSION_TOKEN` 只存在于当前终端的环境变量里，不写文件、不打印、不提交 git。
- **不要把 token 粘贴到 Claude Code 对话、聊天工具、邮件或工单里**——即使是为了报错求助也不行，描述问题用截图打码或文字描述代替。
- 不要用 `curl` 或任何其他方式直接对 IICS 发 POST/PUT/PATCH/DELETE 请求——v0.1 代码本身没有写路径，强行绕过没有意义还有风险。
- Mode A 启动 Claude Code 时不要用 `--dangerously-skip-permissions`。

---

## 参考

本 README 已包含日常使用所需的全部步骤。开发者内部设计文档不作为普通运维使用前提。
