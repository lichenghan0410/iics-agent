# IICS Agent 使用手册

Takeda Japan IICS 运维工具：通过自然语言（Claude Code CLI）或手动命令查询、查看并导出文件夹下的 IICS task 配置。

**当前版本：v0.1 Read-only MVP**——只读查询，不支持任何写操作。

本手册以 **Windows + PowerShell** 为主路径。

---

## 这是什么

IICS（Informatica Intelligent Cloud Services）上有大量 task，逐一在 Web UI 中打开查看配置效率很低。本工具把以下只读操作封装成统一 CLI：

- 枚举 task
- 查看 task 详情
- 解析 connection ID
- 导出整个 folder
- 导出用户指定的 task 清单

可以用两种方式操作，两者调用完全相同的 Python 实现，结果一致：

- **Mode A — Claude Code**：用自然语言描述需求，由 Claude Code 自动选择并调用 `iics_cli.py`
- **Mode C — Manual CLI**：人工直接执行 `iics_cli.py`

### 适用范围

| 项 | 取值 |
|---|---|
| IICS 平台 host | `use4.dm-us.informaticacloud.com`（Informatica IICS 美区 pod 的统一 API 入口，Takeda 的 dev/test/prod 各个 org 共用同一个 host；实际访问的是哪个 org，完全由当前使用的 `IICS_SESSION_TOKEN` 决定，与 host 本身无关） |
| 已验证 folder | `JAPAN_COMMERCIAL_DWH`、`JAPAN_SCRM_BAU`、`JAPAN_SCRM_DI`等 |
| Task 类型 | `DSS`（Sync Task）、`MTT`（Mapping Task）、`WORKFLOW`（Linear Taskflow） |

工具本身不限制 folder，任何 IICS 用户账号有权限访问的 folder 都可以查询和导出；上面三个是目前实际验证过的 folder，其它 folder 理论上同样适用，实际使用时以当前账号的访问权限为准。

## 能做什么 / 不能做什么

| 能 | 不能 |
|---|---|
| 查询某 folder 下的 task 列表 | 修改 task runtime / connection |
| 查看某个 task 的配置详情 | 批量变更 IICS 配置 |
| 把 connection ID 解析成可读名称 | 自动续期浏览器 SSO token |
| 导出一个 folder 的 Excel / JSON | 通过 CLI 调用 POST / PUT / PATCH / DELETE |
| 只导出用户指定的 task 清单 | 让 DSS / WORKFLOW 的 V2 by-name detail 按 folder 强制消歧 |

v0.1 的写操作不是"有代码但被权限拦截"——Python 实现本身只允许发出 GET 请求，尝试其它方法会直接报错。

---

## 项目结构与 Claude Code 配置

```text
IICS-Agent/
├── CLAUDE.md
├── iics_cli.py
├── iics_client.py
├── tools/
├── cache/
└── .claude/
    ├── settings.json
    ├── settings.local.json
    └── skills/
        └── iics-agent/
            └── SKILL.md
```

各部分职责：

| 文件 / 层 | 职责 |
|---|---|
| Python（`iics_cli.py` / `iics_client.py` / `tools/`） | IICS API 调用、数据解析、导出、GET-only 强制 |
| `.claude/settings.json` | 项目共享的 Claude Code 运行权限；纳入 Git，团队共用 |
| `.claude/settings.local.json` | 当前用户本机的个人权限覆盖；不纳入 Git |
| `CLAUDE.md` | Agent 的项目级行为准则：认证方式、安全边界、工作目录规则 |
| `.claude/skills/iics-agent/SKILL.md` | IICS 操作的具体 procedure：命令怎么选、参数怎么填、结果怎么解释 |

### Claude Code 权限配置

`.claude/settings.json` 当前内容：

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(python iics_cli.py *)",
      "Bash(python smoke_test.py)",
      "Read"
    ],
    "deny": [
      "Edit",
      "Write",
      "Bash(curl *)"
    ]
  }
}
```

含义：

- `allow`：执行 `iics_cli.py`、`smoke_test.py`、读取项目文件无需每次手动确认
- `deny`：Claude Code 不会修改文件（`Edit`/`Write`），也不会用 `curl` 绕过正式 CLI 直接访问 IICS

> **重要**：这份配置控制的是 **Claude Code 能调用哪些工具**，不是 IICS 本身的写保护。v0.1 真正的只读保证来自 Python 实现——`IICSClient._request()` 只接受 `GET` 方法，其它方法会直接抛出异常。即使权限配置被误改，Python 也不会真的发出写请求。
>
> 如果后续需要进一步收紧 Claude Code 可见的工具范围（例如禁用网络访问类工具），可以在 `deny` 中按工具名追加。

`.claude/settings.local.json` 用于个人在本机的额外授权（例如某条特定命令的免确认记录），不会影响团队其他成员，也不提交到 Git。

---

## 第一次使用：从零开始

### 1. 检查本机环境

打开 PowerShell，执行：

```powershell
py --version
py -m pip --version
```

如果正常打印版本号（比如 `Python 3.11.x`），跳到下一步。

如果提示 `py 不是内部或外部命令`，说明本机没装 Python：前往 [python.org/downloads](https://www.python.org/downloads/) 下载 3.11 或 3.12 版本安装，安装时**务必勾选 "Add python.exe to PATH"**，装完重新打开 PowerShell 再试一次。

### 2. 安装项目依赖

```powershell
Set-Location -LiteralPath "改成你实际的项目路径"
py -m pip install -r requirements.txt
```

#### 可选：使用虚拟环境

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果因 PowerShell execution policy 无法激活虚拟环境，也可以直接调用虚拟环境里的 Python，不用激活：

```powershell
.\.venv\Scripts\python.exe .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

### 3. 跑一次"无 token 预检"

暂时不设置 token，先确认程序本身能跑起来：

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

预期返回类似：

```json
{
  "error": "IICS_SESSION_TOKEN 未设置",
  "error_type": "ValueError"
}
```

看到这个结果说明 Python、依赖和 CLI 入口都正常，只缺 token，可以继续下一步。

### 4. 获取 Session Token

当前使用企业 SSO（Okta），没有独立的服务账号，token 需要每次手动从浏览器获取：

1. 使用 Chrome / Edge 登录 IICS Web UI 并完成 SSO 验证。
2. 按 `F12` 打开开发者工具，切到 **Network** 标签页。
3. 刷新一下 IICS 页面。
4. 点开任意一个正常的 IICS API 请求。
5. 在 **Request Headers → Cookie** 里找到 `USER_SESSION=<一长串值>`。
6. 只复制 `USER_SESSION=` 后面、到下一个分号 `;` 之前的那一段。

### 5. 设置 Token

```powershell
$env:IICS_SESSION_TOKEN = "<刚才复制的 USER_SESSION 值>"
```

这个值只应该存在于**当前这个终端窗口**的环境变量里。

**不要**：

- 写入 `.env` 或任何配置文件
- 打印、echo 出来
- 提交到 Git
- 粘贴到 Claude Code 对话、邮件、聊天工具或工单里（即使是为了报错求助，也请用截图打码或文字描述代替）

Token 过期后（表现为命令返回 401 或 session expired），需要重新从浏览器获取并重新执行上面这条命令。关掉终端窗口后，token 也需要重新设置。

### 6. 跑通第一条真实命令

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
echo $LASTEXITCODE
```

| `$LASTEXITCODE` | 含义 |
|---|---|
| `0` | 命令正常完成，查看 stdout 里的 JSON 结果 |
| `1` | 出现顶层 fatal error，查看 JSON 里的 `error` 字段 |

看到正常的 JSON 输出（哪怕 `count` 是 0），说明环境已经跑通，可以进入日常使用。

---

## 日常使用：Mode A / Mode C

| | Mode C：手动执行 | Mode A：Claude Code |
|---|---|---|
| 怎么用 | 直接执行 `python iics_cli.py ...` | 在 `claude` 会话里说自然语言 |
| 谁选择命令 | 你自己 | Claude Code 根据意图自动选择 |
| 需要安装 Claude Code | 否 | 是 |
| 适合 | 已经熟悉命令、脚本化批处理、排障 | 日常查询、不想记命令格式、整理 selected task 清单 |

两种模式随时可以切换，不冲突。

### Mode C：手动执行

```powershell
$env:IICS_SESSION_TOKEN = "<USER_SESSION 值>"

python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
python iics_cli.py resolve-connection 000LVU0B000000000015
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL
```

Mode C 不经过 Claude Code，因此不受 `.claude/settings.json` 权限配置影响，也不需要安装 Claude Code。

### Mode A：Claude Code

进入项目根目录后启动：

```powershell
Set-Location -LiteralPath "改成你实际的项目路径"
claude
```

项目的共享权限（`.claude/settings.json`）和操作规则（`.claude/skills/iics-agent/SKILL.md`）会随项目自动加载，不需要额外配置。

首次在一台新机器上打开这个项目时，Claude Code 可能会提示确认 workspace 信任（trust）——请按公司安全规定和本项目实际情况确认。项目共享的 `allow` 规则需要在信任确认之后才会作为免确认规则生效；`deny` 规则不受此影响，始终立即生效。

进入会话后可以直接用自然语言描述需求，例如：

| 你说 | 对应操作 |
|---|---|
| "列出 JAPAN_SCRM_DI 下的 DSS task" | 枚举 task |
| "看 DI_FACT_SALES_DAILY 这个 DSS 的详情，folder 是 JAPAN_SCRM_DI" | 查看单个 task 详情 |
| "把这个 connection ID 解析成名字" | 解析 connection |
| "导出 JAPAN_COMMERCIAL_DWH 的所有 task" | 导出整个 folder |
| "只导出下面这几条 task"（可以直接贴 Excel/表格） | 导出指定清单 |

**不要使用 `--dangerously-skip-permissions` 启动。**

---

## 功能详解（Mode A / Mode C 通用）

v0.1 公开 5 个只读子命令，以下命令语法在两种模式下完全一致。

### 1. `list-tasks` — 枚举 folder 下的 task

```powershell
python iics_cli.py list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `<folder>` | 是 | 当前账号有权限访问的 IICS folder（已验证 `JAPAN_*` 三个 folder） |
| `--type` | 否 | 默认 `ALL`，即 DSS + MTT + WORKFLOW 三种（不包含其它 IICS 对象类型） |
| `--page-size` | 否 | 单页查询数量，一般不需要调整 |

示例：

```powershell
python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

返回的 `count` 和 `tasks` 由 Python 统计好，直接使用即可，不需要自己再数一遍。

---

### 2. `get-task` — 查看单个 task 详情

```powershell
python iics_cli.py get-task <task_name> <task_type> --folder <folder> [--full] [--no-resolve]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `<task_name>` | 是 | task 名称 |
| `<task_type>` | 是 | `DSS` / `MTT` / `WORKFLOW` |
| `--folder` | **是** | 三种类型都必需，用于精确定位对应的 task 对象 |
| `--full` | 否 | 返回完整详情，用于诊断；日常查询不需要 |
| `--no-resolve` | 否 | 不把 connection ID 解析成名称，只看原始 ID |

#### DSS

```powershell
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
```

默认返回精简的业务字段，和 Excel 导出的列一致：

```json
{
  "task_count": 1,
  "rows": 1,
  "records": [
    {"Task Name": "..."}
  ]
}
```

需要更完整的诊断信息时加 `--full`：

```powershell
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI --full
```

**注意**：如果组织内其它 folder 也存在同名的 DSS task，`--folder` 只能保证系统内部能准确定位到你指定 folder 的这个对象，但底层查询这个 task 详情时用的是"按名称查询"的方式，无法百分之百保证一定拿到你指定 folder 下的那一份配置。如果结果里出现相关提示（warning），请按提示内容确认。

#### MTT

```powershell
python iics_cli.py get-task TASK_NAME MTT --folder JAPAN_SCRM_BAU
```

默认同样返回精简业务字段，**一个 Source 或 Target transformation 对应一条记录**，一个 task 可能返回多条记录。

需要完整详情时加 `--full`。完整详情里的 `transformations` 字段是权威结果，具备以下特性：

- 只来自该 MTT 任务自己的原始参数，不会用其所属 Mapping 的参数补充
- 保留原始顺序，不去重，不会只截取第一条或最后一条

MTT 的详情查询会精确定位到具体对象，因此**可以可靠区分不同 folder 中的同名 MTT**，不存在 DSS 那种"同名歧义"的问题。

#### WORKFLOW

```powershell
python iics_cli.py get-task WORKFLOW_NAME WORKFLOW --folder JAPAN_SCRM_DI
```

返回的是完整详情（不像 DSS/MTT 那样默认返回精简记录）。folder 的作用和 DSS 类似——能帮助系统内部精确定位对象，但不能保证在组织内其它 folder 存在同名 WORKFLOW 时，查到的详情一定来自你指定的 folder。

---

### 3. `resolve-connection` — 解析 connection ID

```powershell
python iics_cli.py resolve-connection <id> [<id>...]
```

支持一次传多个 ID：

```powershell
python iics_cli.py resolve-connection 000LVU0B000000000015 000LVU0B00000000009O
```

返回结果分为 `resolved`（成功解析）和 `failed`（解析失败）两部分。同一次运行中重复出现的 connection ID 会自动复用已经查过的结果，不会重复请求。

---

### 4. `export` — 导出整个 folder

```powershell
python iics_cli.py export <folder> [--type DSS|MTT|WORKFLOW|ALL] [--details|--summary-only] [--format excel|json] [--filename NAME]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `<folder>` | 是 | 当前账号有权限访问的 IICS folder（已验证 `JAPAN_*` 三个 folder） |
| `--type` | 否 | 默认 `ALL` |
| `--details` | 否 | 默认行为，查询完整详情后导出 |
| `--summary-only` | 否 | 只导出列表级信息，不查详情，速度更快 |
| `--format` | 否 | `excel`（默认）或 `json` |
| `--filename` | 否 | 自定义输出文件名 |

不同 task 类型使用不同的 Excel 列结构：

| 类型 | 列数 | 一行代表什么 |
|---|---:|---|
| DSS | 12 | 1 个 task = 1 行 |
| MTT | 23 | 1 个 Source 或 Target transformation = 1 行 |
| WORKFLOW | 9 | 1 个子任务 = 1 行；没有子任务时仍保留 1 行 |

`--type ALL` 会生成固定顺序的多个业务 sheet：`MTT Task → DSS Tasks → Workflow Tasks → Export Errors`。

#### 怎么判断导出是否完整成功

命令返回的 JSON 里会包含：

| 字段 | 含义 |
|---|---|
| `task_count` | 逻辑上一共有多少个 task |
| `rows` | Excel 里实际写了多少行 |
| `failed_details_count` | 有多少个 task 的详情查询失败了 |
| `enrichment_warnings_count` | 有多少条非致命的补充信息警告（不影响导出成功与否） |
| `file_path` | 生成的文件路径 |

**重要**：因为一个 MTT 或 WORKFLOW 可能展开成多行，`rows` 经常会比 `task_count` 大，**这是正常现象，不代表出错**。请不要用"`rows` 是否等于 `task_count`"来判断导出是否完整，正确的判断方式是：

1. `failed_details_count` 是否为 0
2. 如果不为 0，查看具体是哪些 task 失败，其余成功的部分不受影响
3. `enrichment_warnings_count` 只是提醒，不是失败

---

### 5. `export-selected` — 只导出指定的 task 清单

当你手上已经有一份明确的 task 清单，而不是想导出整个 folder 时用这个：

```powershell
python iics_cli.py export-selected --input .\selected_tasks.json --output-dir .\exports
```

Python 只接受这样格式的 JSON 输入：

```json
[
  {"name": "Task_A", "type": "DSS", "folder": "JAPAN_SCRM_DI"},
  {"name": "Task_B", "type": "MTT", "folder": "JAPAN_SCRM_BAU"},
  {"name": "Workflow_A", "type": "WORKFLOW", "folder": "JAPAN_COMMERCIAL_DWH"}
]
```

每一条都必须提供 `name`、`type`、`folder` 三个字段（三种类型的要求相同）。

> 在 Mode A 下，你可以直接把 Excel 表格、CSV、Markdown 表格或纯文本粘贴给 Claude Code，让它帮你整理成上面这种 JSON 格式，不需要自己手写。

**关于重复项**：判断两条记录是否重复，看的是 `type` + `folder` + `name` 这三项是否完全一致。如果两个 task 名字相同、类型相同，但分属不同 folder，会被当成两个不同的 task，**都会被处理，不会互相顶替**。

导出结果会按类型分别生成独立的 Excel 文件，例如 `selected_DSS_20260909.xlsx`、`selected_MTT_20260909.xlsx`；某个类型如果本次没有涉及，就不会生成对应文件。

结果 JSON 里可以看到：

| 字段 | 含义 |
|---|---|
| `input_count` | 原始输入了多少条 |
| `unique_count` | 去重后剩下多少条 |
| `duplicates_removed` | 去掉了多少条完全重复的 |
| `requested` / `success` / `failed` | 按类型统计的请求数、成功数、失败数 |
| `rows` | 每种类型实际写了多少行 |
| `file_path` | 每种类型对应的文件路径 |
| `failures` / `warnings` | 具体的失败明细和警告明细 |

单个 task 失败不代表整个命令失败——其余成功的 task 仍然会正常生成文件。

---

## 环境变量参考

| 变量 | 是否必需 | 说明 |
|---|---|---|
| `IICS_SESSION_TOKEN` | **是** | 浏览器 `USER_SESSION` Cookie 的值，见"获取 Session Token"一节 |

以下变量属于程序框架预留，v0.1 当前的 SSO 使用方式下不需要设置：

| 变量 | 说明 |
|---|---|
| `IICS_LOGIN_URL` | 有默认值，日常不需要覆盖 |
| `IICS_USER` / `IICS_PASS` | 当前版本不使用 |

---

## 命令输出格式

所有命令遵循统一的输出约定：

- **stdout**：机器可读的 JSON 结果——这是你应该解析和使用的内容
- **stderr**：过程日志，人类阅读用，不包含业务数据
- 命令正常完成：退出码 `0`
- 出现顶层 `{"error": ...}`：退出码 `1`，代表这次命令整体失败

批量操作（`export`/`export-selected`）里，即使命令整体退出码是 `0`，返回结果中仍然可能包含个别 task 的失败或警告信息，因此不能只看"有没有生成文件"来判断是否全部成功，需要看上面提到的 `failed_details_count`（或 `failed`）字段。

---

## 已知限制（v0.1）

- **严格只读**：底层只允许发出 GET 请求，不支持任何形式的修改操作。
- **Token 需要手动获取，不会自动续期**：过期后需要重新从浏览器复制。
- **DSS / WORKFLOW 在跨 folder 同名时可能存在歧义**：查询详情时按名称匹配，无法百分之百保证拿到你指定 folder 下的那一份，如有提示会以 warning 形式告知。
- **MTT 不受此限制**：可以在跨 folder 同名的情况下准确区分。
- **Mapping Location 字段当前没有可靠数据来源**，不会显示推测值。
- **`rows` 和 `task_count` 是两个不同的统计口径**，`rows` 更大是正常现象。

---

## 故障排查

| 现象 | 可能原因 | 处理方法 |
|---|---|---|
| `py` / `python` 提示找不到命令 | Python 未安装，或安装时没勾选加入 PATH | 重新安装 Python 并重启 PowerShell |
| `ModuleNotFoundError` | 依赖没有装到当前使用的 Python 环境 | 重新执行 `py -m pip install -r requirements.txt` |
| 返回 `IICS_SESSION_TOKEN 未设置` | 当前终端窗口没有设置 token | 重新执行 `$env:IICS_SESSION_TOKEN = "..."` |
| 返回 401 或 session expired | Token 已过期 | 回到浏览器重新获取 `USER_SESSION` 并重新设置 |
| `get-task` 报错缺少 folder | 三种 task 类型查详情都必须提供 folder | 补上 `--folder <folder名称>` |
| DSS / WORKFLOW 结果里出现同名相关提示 | 组织内其它 folder 存在同名 task | 按提示内容判断，必要时用其它信息进一步确认 |
| Claude Code 对已允许的命令仍然弹出确认框 | 项目 workspace 还没有被信任，或本地环境差异 | 确认是否已经完成 workspace 信任确认 |
| Claude Code 没有按预期理解 IICS 相关请求 | Skill 未正确加载 | 确认 `.claude/skills/iics-agent/SKILL.md` 文件存在且路径正确 |
| Excel 文件没有生成 | 依赖缺失或输出目录写入失败 | 检查 `openpyxl` 是否安装成功、输出目录是否可写，并查看返回的 JSON 中是否有 `error` |

---

## 安全须知

- `IICS_SESSION_TOKEN` 只应该存在于当前终端窗口的环境变量里，关闭窗口后即失效。
- 不要把 token 写进文件、打印出来、提交到 Git，或粘贴到任何对话、邮件、工单里。
- 不要用 `curl` 等方式绕开正式 CLI 直接访问 IICS 接口。
- 不要使用 `--dangerously-skip-permissions` 启动 Claude Code。
- `.claude/settings.json` 控制的是 Claude Code 能调用哪些工具，不是 IICS 本身的写保护；真正的只读保证由 Python 实现本身提供。
- `.claude/settings.local.json` 是个人在本机的配置，不会提交到项目 Git 仓库。

---

## 参考文档

日常使用只需要本手册和以下两个文件：

```text
CLAUDE.md                              — Agent 行为准则
.claude/skills/iics-agent/SKILL.md     — IICS 操作 procedure
```

如需了解设计背景或历史决策依据，可查阅项目内的架构决策记录文档；日常操作不需要阅读它。
