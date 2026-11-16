# IICS Agent v0.1 — 冒烟测试运行手册（Mode A / Claude Code CLI；含 Mode C 手动执行）

本手册定义 IICS Agent v0.1 Read-only MVP 的**验收级冒烟测试**：在 Windows 上选定环境
（`--env` / `IICS_ENV`）并由工具自动 Login，通过 Claude Code CLI 驱动，验证 `iics_cli.py`
的业务 / 数据子命令（对 IICS 严格 GET-only）与认证生命周期子命令
（`auth-check` / `auth-pin`），以及 `smoke_test.py` 的 Test 1–6 全部通过。

> 认证方式：Amendment 10 起为多环境 username/password 自动 Login + environment pin
> （见 §3）。手动 `IICS_SESSION_TOKEN` 已降级为需显式开启的 legacy 调试模式（§3.3）。

> 适用范围：`use4.dm-us.informaticacloud.com`（Takeda pod，DEV / TEST / PROD org 共用同一 host），
> 三个文件夹 `JAPAN_COMMERCIAL_DWH` / `JAPAN_SCRM_BAU` / `JAPAN_SCRM_DI`，
> task 类型 DSS / MTT / WORKFLOW。

---

## 1. 测试策略概览（测试金字塔定位）

v0.1 的自动化测试分两层，本手册覆盖**集成层（真实 API 冒烟）**：

```
        /  真实冒烟  \      本手册：少量、慢、对真实 IICS Prod 的端到端只读验证
       / 函数单元(mock) \    已完成：Step 2–8 的 monkeypatch / fake client 测试（CI 可跑）
      /   纯函数单元      \   已完成：URL factory、normalize_task_type、缓存命中
```

- **单元层（已绿）**：URL factory、`normalize_task_type`、`list_tasks` 分页、`get_task_detail`
  抽取、`ConnectionCache` 命中、`export_results` 单 sheet——全部用 fake client，无网络，
  Step 2–8 已逐步验证通过。
- **集成层（本手册）**：`smoke_test.py` 的 Test 1–6，对真实 IICS 发只读 GET，验证认证、
  枚举、详情、连接解析缓存、Excel 导出端到端打通。
- **不在 v0.1 范围**：写操作、负载/混沌测试、UI 测试（无 UI）。

### 测试目标与覆盖矩阵

| 测试 | 覆盖点 | 类型 | 关键断言 |
|------|--------|------|----------|
| Test 1 | `SessionManager` + URL factory | 纯函数 | server_url 正确、`/saas` 不重复 |
| Test 2 | `list_tasks`（全部类型，含分页） | 集成-只读 | 返回 `tasks`，`count >= 0` |
| Test 3 | `list_tasks`（按 type 过滤） | 集成-只读 | 返回 `tasks`，type 归一化生效 |
| Test 4 | `get_task_detail`（DSS/WORKFLOW: V2 by-name；MTT: V3→FRS object ID→`mttask/frs`） | 集成-只读 | 无 error、含 `runtime_agent`；MTT 另断言 `detail_route`/`id_type` |
| Test 5 | `resolve_connection` + 缓存命中 | 集成-只读 | 两次解析结果一致（第二次走缓存） |
| Test 6 | `export_results`（openpyxl 单 sheet Task Summary） | 集成-本地 IO | 生成 `.xlsx` 且文件存在 |

> Test 4/5 在目标文件夹无对应类型 task 时会 SKIP。正式验收前必须用
> `IICS_TEST_TASK_NAME` 指定一个真实 task，确保 Test 4/5 实跑通过（见 §6）。

---

## 2. 前置条件

1. Windows + PowerShell，已 `cd "D:\IICS Agent"`。
2. Python ≥ 3.10（项目用 `X | Y` 类型注解与 `match`/现代语法）。
3. 已安装 Mode A 依赖：

   ```powershell
   Set-Location -LiteralPath "D:\IICS Agent"
   pip install -r requirements.txt
   ```

4. 已安装并登录 Claude Code CLI（`claude`）。
5. 能访问 IICS Web UI（企业 SSO / Okta）。

---

## 3. 认证准备（Amendment 10：多环境自动 Login）

### 3.0 mock 单元测试（不需要任何真实账号，先跑这个）

```powershell
Set-Location -LiteralPath "D:\IICS Agent"
python -m unittest discover -s tests -t .
```

全部通过才继续。该套件完全 mock，不触网、不使用真实账号密码，覆盖环境解析、
credential config、Login、environment pin、401 refresh、403 不 refresh，
以及 **GET-only 安全回归**；Amendment 11 起另含业务输出特征测试
（`tests/test_export_characterization.py`：DSS 12 / WORKFLOW 9 / MTT 26 列）与
MTT 字段测试（`tests/test_mtt_fields.py`）。随后执行凭据扫描：

```powershell
python scripts/check_no_real_credentials.py
```

### 3.1 填写 credential 配置（只能由使用者本人完成）

```powershell
Copy-Item .\iics_credentials.template.json .\iics_credentials.json   # 若尚不存在
notepad .\iics_credentials.json                                      # 手工填入 username / password
```

要求：

- `iics_credentials.template.json` 进 git，`username` / `password` 永远是空字符串
- `iics_credentials.json` 不进 git（`.gitignore` 精确排除 `/iics_credentials.json`）
- 不得把账号密码写进源码、README、测试、fixture、commit message
- 可选逃生舱：`$env:IICS_CONFIG_FILE = "<repo 外路径>"`

git 安全自检（每次改动认证相关文件后都应跑一遍）：

```powershell
git ls-files --error-unmatch iics_credentials.json   # 期望：失败（未被追踪）
git status --short -- iics_credentials.json          # 期望：无输出
git check-ignore -v iics_credentials.json            # 期望：命中 .gitignore 的 /iics_credentials.json
git check-ignore -v iics_credentials.template.json   # 期望：无输出（模板未被忽略）
python scripts/check_no_real_credentials.py          # 期望：exit 0
```

若第一条**成功**（说明真实文件已被 git 追踪）：**立即停止**，向负责人报告，
不要自行 `git rm --cached` 或重写历史。

### 3.2 首次环境绑定：auth-check → 人工确认 → auth-pin

三个环境各做一次。**Agent 不得代替使用者确认。**

```powershell
python .\iics_cli.py auth-check --env TEST
```

期望 stdout（**恰好** 5 个顶层字段；不含 session ID、账号密码、账号 `id`
或 Login 原始响应体）：

```json
{
  "environment": "TEST",
  "status": "pin_required",
  "org_id": "000LVT",
  "org_uuid": "<ORG_UUID>",
  "server_host": "use4.dm-us.informaticacloud.com"
}
```

人工操作提示走 **stderr**，不属于 stdout JSON contract：

```text
[IICS CLI] 环境 TEST 尚未绑定：请人工核对上面输出的 org_id / org_uuid ...
```

其余状态的 stdout key 集合：

| status | stdout 顶层 key |
|---|---|
| `pin_required` | `environment` `status` `org_id` `org_uuid` `server_host` |
| `pinned` | 上述 5 个 + `pinned_at` |
| `pin_mismatch` | 上述 5 个 + `pinned_org_uuid` `error_type` `error`（exit 1） |

人工核对 `org_id`：

```text
000LA8 → DEV
000LVT → TEST
000LVU → PROD
```

对不上 → **不要 pin**，先改正 `iics_credentials.json` 里的环境账号。

核对无误后：

```powershell
python .\iics_cli.py auth-pin --env TEST
```

`auth-pin` 的 stdout key 集合：

| status | stdout 顶层 key |
|---|---|
| `pinned` / `already_pinned` | `environment` `status` `org_id` `org_uuid` `server_host` `pinned_at` `pin_file` |
| org 不一致且未 `--force` | `error` `error_type`（`EnvironmentMismatchError`，exit 1，pin 不被覆盖） |

对 DEV / PROD 重复同样三步。

绑定后验证 `.env_pins.json` 只含 org 身份：

```powershell
Get-Content .\.env_pins.json      # 应只有 schema_version / pins / org_uuid / org_id / pinned_at
Select-String -Path .\.env_pins.json -Pattern "icSessionId|password|username|serverUrl"
# 期望：无匹配
```

### 3.3 Legacy token 模式（仅排障，可跳过）

```powershell
$env:IICS_AUTH_MODE = "legacy_token"
$env:IICS_SESSION_TOKEN = "<USER_SESSION 值>"
```

legacy 模式不解析环境、不受 environment pin 保护、无法自动 refresh。
排障结束后应 `Remove-Item Env:IICS_AUTH_MODE` 还原主路径。

> 安全要求：账号密码只存在于 `iics_credentials.json`（不进 git）；session 只存在于
> CLI 进程内存，不落盘、不打印。Login 原始响应体（`roles` / `usergroups` /
> `emails` / `securityQuestion` / `securityAnswer` / `password`）不得被保留或输出。

---

## 4.0 直接 PowerShell 预检（先于 Claude Code）

进入 Claude Code CLI 之前，**先在纯 PowerShell 里直接跑一遍冒烟测试**，把
「代码 / API / token 问题」与「Claude Code 工具权限 / 自然语言调度问题」分开定位：

```powershell
Set-Location -LiteralPath "D:\IICS Agent"
git status --short          # 测试前：应只在末尾可能出现导出 .xlsx（被忽略，不入跟踪）
$env:IICS_ENV = "TEST"      # 不设置则 smoke_test 直接失败（不默认 PROD）
python smoke_test.py
git status --short          # 测试后：不应出现任何源码改动，且不应出现 iics_credentials.json
```

若 `python smoke_test.py` 在此步失败，**先不要进入 Claude Code**，按第 7 节自行排查
token、路径、权限、V3 path、`pageSize` 等问题。只有这一步通过，才继续 §4。

---

## 4. 用 Claude Code CLI 运行（Mode A 推荐启动方式）

Mode A 的核心是让 `claude` CLI 读取项目 `CLAUDE.md`，用自然语言驱动 `iics_cli.py`。
按 ADR D-1 的只读权限边界启动。**推荐使用 `--tools` + `--allowedTools` + `--disallowedTools` 三段式权限模型。**：

```powershell
Set-Location -LiteralPath "D:\IICS Agent"

claude `
  --tools "Bash,Read" `
  --allowedTools "Bash(python iics_cli.py *)" "Bash(python smoke_test.py)" "Read" `
  --disallowedTools "Edit" "Write" "Bash(curl *)"
```

- `--tools`：限制 Claude Code 可见的工具集合为 `Bash` 与 `Read`，`Edit`/`Write` 等不在其中。
- `--allowedTools`：让 `python iics_cli.py *` 与 `python smoke_test.py` 免确认自动执行（不是可见性限制，是自动批准规则）。
- `--disallowedTools`：对 `Bash` 内的危险调用模式做 scoped 拒绝（如 `curl`），即使 `Bash` 整体可见也会被拦截。
- **不要**使用 `--dangerously-skip-permissions`。

> 权限 ≠ 沙箱：禁用 `Edit`/`Write` 不代表 Bash 完全无法改文件（重定向、生成产物仍可能）。
> 权限控制与 §6 的 `git status` 检查是互补的，不能互相替代。

进入 `claude` 会话后，可用自然语言触发（CLI 会自动生成对应 bash 调用）：

| 你说 | claude 实际执行 |
|------|----------------|
| 「列出 JAPAN_SCRM_DI 下的 DSS task」 | `python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS` |
| 「看 DI_FACT_SALES_DAILY 这个 DSS 的详情」 | `python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI`（三类型均需 --folder，Amendment 9） |
| 「把这个 connection id 解析成名字」 | `python iics_cli.py resolve-connection 000LVU...` |
| 「导出 JAPAN_COMMERCIAL_DWH 的所有 task 到 Excel」 | `python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL` |
| 「跑一遍冒烟测试」 | `python smoke_test.py` |

---

## 5. 冒烟测试执行顺序（建议在 claude 会话中逐条让其运行）

### 5.1 烟雾测试主脚本（一次跑完 Test 1–6）

```powershell
python smoke_test.py
```

预期 stdout 结尾：

```
✅ Test 1 passed: SessionManager + URL factory
✅ Test 2 passed: list_tasks(JAPAN_SCRM_DI) → N tasks
✅ Test 3 passed: list_tasks type=DSS → M tasks
✅ Test 4 passed: get_task_detail(<task>)
✅ Test 5 passed: resolve_connection + cache hit
✅ Test 6 passed: export_results → D:\IICS Agent\iics_export_YYYYMMDD_HHMMSS.xlsx

✅ All smoke tests passed — v0.1 完成
```

### 5.2 子命令逐个手动验证（交叉确认 CLI 协议）

所有业务子命令都必须带环境（或已设置 `$env:IICS_ENV`）。

```powershell
$env:IICS_ENV = "TEST"

# A0. 反例（Amendment 10）：不指定环境 → MissingEnvironmentError、exit 1、无 HTTP
Remove-Item Env:IICS_ENV -ErrorAction SilentlyContinue
python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
echo "exit=$LASTEXITCODE"   # 期望 1，且不得默认落到 PROD
$env:IICS_ENV = "TEST"

# A. 枚举（验证 stdout 纯 JSON、分页、type 过滤）
python iics_cli.py --env TEST list-tasks JAPAN_SCRM_DI --type DSS
echo "exit=$LASTEXITCODE"   # 期望 0；有 error 时为 1

# B. 详情（三类型均需 --folder，Amendment 9：folder → FRS discovery 取 frs_id）
python iics_cli.py get-task <真实TASK名> DSS --folder JAPAN_SCRM_DI
echo "exit=$LASTEXITCODE"
# B2. 反例（Amendment 9）：DSS 缺 --folder → MissingFolderError、exit 1、无 HTTP
python iics_cli.py get-task <真实TASK名> DSS
echo "exit=$LASTEXITCODE"   # 期望 1

# C. 连接解析（验证缓存与 failed 列表）
python iics_cli.py resolve-connection <真实CONN_ID>
echo "exit=$LASTEXITCODE"

# D. 导出（验证端到端聚合 + Excel 落盘）
python iics_cli.py export JAPAN_SCRM_DI --type DSS --format excel
echo "exit=$LASTEXITCODE"
```

> stdout 永远只应出现 JSON；进度/`[IICS CLI]` 日志走 stderr。若 stdout 混入非 JSON，
> 视为缺陷。

### 5.3 测试后状态检查

```powershell
git status --short
```

期望：不出现任何源码（`.py` / `.md`）改动；导出的 `.xlsx` 即便存在也应处于未跟踪且被
`.gitignore` 忽略状态（不会被 `git add .` 误纳入）。若出现源码改动，说明测试过程意外修改了
文件，需排查后回退。

### 5.4 Mode C：手动执行（无 Claude Code CLI 时的辅模式）

`iics_cli.py` 是 Mode A 与 Mode C 的共享入口，Mode C 即跳过 `claude` CLI，直接在终端手动
输入与上面 §5.2 完全相同的子命令。适用场景：`claude` 不可用、网络隔离环境、需要写 shell
脚本批量调用、或接入 cron 定时导出任务。详见 ADR `ADR-001-IICS-Agent-v0.1-Readonly-MVP.md`
的 D-1「Amendment 1」说明。

```powershell
# 与 Mode A 自然语言触发后台执行的命令完全一致，手动输入即可
Set-Location -LiteralPath "D:\IICS Agent"
$env:IICS_ENV = "TEST"

python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
python iics_cli.py get-task <真实TASK名> DSS --folder JAPAN_SCRM_DI
python iics_cli.py resolve-connection <真实CONN_ID>
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL
```

Mode C 没有 AI 层介入，不存在 prompt injection 或模型误调用风险；安全边界与 Mode A 完全
一致（credential / session 处理规则相同、`iics_cli.py` 本身无写路径），不需要额外的 `--allowedTools` 类
权限配置。

---

## 6. 验收口径（DoD）

v0.1 **完成**当且仅当全部满足：

- [ ] `python smoke_test.py` 在真实 token 下输出 `✅ All smoke tests passed — v0.1 完成`。
- [ ] Test 4 与 Test 5 **实跑**通过（非 SKIP）。若默认文件夹无 DSS task，用环境变量指定：

  ```powershell
  $env:IICS_TEST_FOLDER    = "JAPAN_SCRM_DI"
  $env:IICS_TEST_TASK_TYPE = "DSS"
  $env:IICS_TEST_TASK_NAME = "<一个真实存在的 task 名>"
  python smoke_test.py
  ```

- [ ] 三个文件夹各自 `list-tasks` 能返回非报错 JSON。
- [ ] 至少一次 `export` 生成的 `.xlsx` 能用 Excel 打开；v0.1.1 起含
      `Task Summary`（列：name, type, folder, runtime_agent, source_conn,
      target_conn, source_objects, target_object, status）与 `Export Errors` 两个 sheet。
- [ ] 退出码契约对**所有** CLI 子命令成立：成功时退出码 0，工具返回顶层
      `{"error":...}` 时退出码 1。适用范围——
      业务 / 数据命令 `list-tasks` / `get-task` / `resolve-connection` / `export` /
      `export-selected`，以及认证生命周期命令 `auth-check` / `auth-pin`
      （后两者的 status 与 stdout 字段契约另见 §6.8）。
- [ ] 全程无 token 出现在 stdout / 文件 / git。
- [ ] 测试结束后 `git status --short` 不应出现源码改动；导出的 `.xlsx` 不应被 git 跟踪。

### 6.1 v0.1.1 稳定性验收项

**导出判定标准（判断某类型导出是否完整成功）**：

- `rows` 表示本次导出的 task 总数，应与同参数 `list-tasks` 的 `count` 一致。
- `rows == list-tasks count` **只**表示 `Task Summary` 没有丢 task，**不代表**所有 task 详情都成功。
- **完整成功** = 同时满足：`rows == list-tasks count` **且** `failed_details_count == 0`。
- 若 `failed_details_count > 0`：Excel 已生成，但部分 task 详情拉取失败；打开 `Export Errors`
  sheet 查看失败 task（`error_type` / `error`），按需**重跑该类型导出**。

对照命令：

```powershell
python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS   # 记下 count
python iics_cli.py export     JAPAN_SCRM_DI --type DSS   # 比对 rows 与 failed_details_count
```

验收勾选：

- [ ] `export --details` 返回的 JSON 含 `rows` / `failed_details_count` / `failed_details`；
      `rows` 等于同参数 `list-tasks` 的 `count`（task 总数，非成功详情数）。
- [ ] 判定完整成功时确认 `rows == list-tasks count` 且 `failed_details_count == 0`。
- [ ] 若本次有详情失败：`failed_details_count > 0`，Excel `Export Errors` sheet 逐条列出
      失败 task；`Task Summary` 仍保留这些 task 的行（name/type/folder/status）。
- [ ] 若无失败：`failed_details_count = 0`，`Export Errors` sheet 写单行 `No export errors`。
- [ ] 网络波动时 stderr 出现 `[IICS retry] attempt N/3 ...` 日志，stdout 仍为纯净 JSON，
      日志中不含 token / cookie / header。

### 6.2 MTT 专项验收（Amendment 2：FRS 详情路径）

用真实 MTT task 跑一次冒烟（Test 4 非 SKIP）：

```powershell
$env:IICS_TEST_TASK_TYPE = "MTT"
$env:IICS_TEST_TASK_NAME = "<真实 MTT 名称>"
$env:IICS_TEST_FOLDER    = "<真实 folder，如 JAPAN_SCRM_BAU>"
python .\smoke_test.py
```

正例验收：

- [ ] `detail_route == "v2-mttask-frs"`；`frs_id` 非空；**`id_type` 已移除**（不存在）。
- [ ] 顶层 `id` 为 classic V2 ID（与 `frs_id` 不同体系）。
- [ ] 无 404 / 500；connection 解析正常；Test 4 实跑非 SKIP。
- [ ] 详情经 `mttask/frs/{frs_id}` 获取（非 `mttask/name/`、非裸 `mttask/{id}`）。

负例（预期结构化错误，均**不发** HTTP）：

- [ ] MTT 无 folder（`get-task NAME MTT`，缺 `--folder`）→ `MissingFolderError`。
- [ ] MTT 传 classic ID（内部 `task_id_type="classic"`）→ `InvalidIdTypeError`。
- [ ] `task_id` 与 `task_name`/`folder` 同时提供 → `ConflictingArgumentsError`。
- [ ] （Amendment 9）`task_id_type` 非 `"frs"` → `InvalidIdTypeError`（不发 HTTP）；`task_id` + `folder_path` → `ConflictingArgumentsError`；三类型一致。CLI/selected 不暴露 task_id/task_id_type。

### 6.3 MTT 信息增强与导出行模型（Amendment 3）

Schema：

- [ ] 归一化 detail 含 `runtime_environment`（**不再有 `runtime_agent`**）。
- [ ] `runtime_environment.id == raw.runtimeEnvironmentId`；Runtime Environment Name 非空。
- [ ] `task_mapping.id == raw.mappingId`；Mapping Name 非空、Mapping Updated On 可解析；
      **Mapping Location 为空**（null）。

Transformation（只来自 MTT `parameters[]`）：

- [ ] `3 Source + 1 Target → 4 transformations / 4 Excel 行`；`1 Source + 1 Target → 2 / 2`。
- [ ] `customQuery` 非空 → Source Object 为空、Custom Query 保留。
- [ ] transformation name 按 **`name`（去首尾 `$`）→ fallback `label`** 优先级（Amendment 4）。
      真实样本 `s_IFA_V_PV_CAE_S_01`：SOURCE → `SQ_001`、TARGET → `T_BI_CALL2_DETAIL_VOD_STGDB`，
      **不得**得到 `DBConnection_awsoracle`。
- [ ] source/target connection name 可解析；optional key 缺失不报错（空白单元格）。
- [ ] 多 source object 单元格开启 wrap text。

计数：

- [ ] `task_count == list-tasks count`；`rows == Task Summary 物理数据行数`（MTT 展开后可 > task_count）。
- [ ] **不**断言 `rows == task_count`。

失败分级：

- [ ] mapping/runtime/connection enrichment 失败 → `enrichment_warnings_count` 增加、
      `failed_details_count` **不**增加、task 行保留。
- [ ] 主详情（`mttask/frs`）失败 → `failed_details_count +1`、进 `Export Errors`、批量继续。

缓存：

- [ ] 一次 export 内同一 `connectionId` / `mappingId` / `runtimeEnvironmentId` 只请求一次。

### 6.4 MTT compact / --full 与 26 列（Amendment 4 / 9 / 11）

get-task：

- [ ] 默认 `get-task NAME MTT --folder F` 返回 `{task_count, rows, records[]}`；`records` 每条
      恰为 26 个 Task Summary 业务字段（一个 transformation = 一条 record；Amendment 11 起）。
- [ ] 默认 compact **不含** `raw` / `frs_id` / `detail_route` / `enrichment_warnings`。
- [ ] `--full` 返回完整 normalized detail（含 `raw` / `frs_id` / `detail_route` / `transformations`）。
- [ ] DSS / WORKFLOW `get-task` 仍为扁平 dict（无 `records`），结构与 MTT 不对称。

Excel / schema：

- [ ] `Task Summary` header 恰为 **26 列**（Amendment 9：`id` 后含 `FRS ID`；Amendment 11：
      `Last Run`、`Source Connection Parameter`、`Target Connection Parameter`），**不含 `Mapping Location`**。
- [ ] compact record 与 Excel 单元格 **字段名与值一致**（同一 row builder）。
- [ ] normalized detail 的 `task_mapping.location` 仍为 `null`（`--full` 可见）。

### 6.5 WORKFLOW export child 展开（Amendment 5）

真实样本 `TF_DI_JPN_GDM_BI_DWH_AYU_M_JGI_MST`（`JAPAN_SCRM_DI`，2 个 child MTT）：

```powershell
python .\iics_cli.py export JAPAN_SCRM_DI --type WORKFLOW --format excel
```

- [ ] 该 workflow 在 Excel 中出现 **2 行**；父字段两行完全一致。
- [ ] child rows：`000LVU0Z000000006V4F / MTT / mt_..._WK` 与 `000LVU0Z00000000087Z / MTT / mt_...`。
- [ ] `Task Summary` header 恰为 **9 列（Amendment 9：id 后含 FRS ID）**（`Task Type, orgId, id,
      FRS ID, Task Name, updateTime, tasks.taskId, tasks.type, tasks.name`）；无
      `runtime_environment` / `Mapping Name` / `Transformation Name` / `Connection` 等列。
- [ ] 每行父字段重复同一个 `id`(classic) 与 `FRS ID`(discovery)。
- [ ] 每个 WORKFLOW 仅 1 个 `workflow/name/{name}` 请求；无 `mttask/*` / `dsstask/*` /
      `workflow/{childId}` child 请求。
- [ ] zero-child workflow 仍占 1 行；detail 失败保留 1 fallback 行并计入 `rows`。
- [ ] `task_count == list-tasks count`；`rows = Σ max(1, child 数)`，可 > task_count。
- [ ] WORKFLOW `get-task` 输出不变（扁平 dict）。

### 6.6 Selected Task Export（Amendment 6 / 8）

准备一个小型 `selected_tasks.json`（例：2 DSS + 3 MTT（至少来自 2 个 folder）+ 2 WORKFLOW），
**注意 Amendment 9 输入 schema**：三类型统一 `{name,type,folder}`（folder 全类型必填、非空）。运行：

```powershell
python .\iics_cli.py export-selected --input .\selected_tasks.json --output-dir .\exports
```

验收（stdout summary）：

- [ ] `input_count` / `unique_count` / `duplicates_removed` 正确（放两条完全相同的 tuple 验证去重）。
- [ ] 每类型 `requested` / `success` / `failed` 且 `success + failed == requested`；
      `Σ requested == unique_count`。
- [ ] 三个 workbook（`selected_DSS_*` / `selected_MTT_*` / `selected_WORKFLOW_*`，同一时间戳）
      **只包含 selected task**——特别确认不出现同 folder 内未被选中的 task。
- [ ] DSS / WORKFLOW warnings 存在且为 Amendment 9 措辞（`used to resolve the FRS ID, but it
      does not disambiguate the V2 detail response ...`）；三类型跨 folder 同名分别正确导出。
- [ ] （Amendment 9）dedup 键三类型统一 `(type,folder,name)`；跨 folder 同名不去重、不 ambiguous_input 阻断。
- [ ] 任一 item 缺 `folder` / 空白 → 整体 `SelectedTaskInputError`、exit 1、0 workbook。
- [ ] 每个 (folder,type) 只 discovery 一次 list_tasks；失败对象含 `folder`（三类型）。
- [ ] MTT 同一 folder 多个 selected → discovery 只发生一次（真实 API 次数不易黑盒确认，以 fake
      regression 为主）。
- [ ] summary 不含完整 detail / raw；个别失败不中断整批；输入 JSON 非法才整体 exit 1。

### 6.7 DSS 独立 12 列 schema（Amendment 7 / 9）

DSS-only 导出：

```powershell
python .\iics_cli.py export JAPAN_SCRM_DI --type DSS --format excel
```

- [ ] `Task Summary` header 恰为 **12 列**（`@type, id, FRS ID, orgId, Task Name, Task Type,
      Task Updated On, Source Connection, Source Object, Operation, Target Connection, Target Object`）。
- [ ] `1 DSS task = 1 行`（DSS-only 时 rows == task_count 为自然结果）。
- [ ] `Source Object` 只取 `sourceObjects[0].name`（多个只显示第一个，不展开、不告警）。

DSS get-task：

```powershell
python .\iics_cli.py get-task <NAME> DSS --folder <FOLDER>   # 默认 compact（Amendment 9：三类型均需 --folder）
python .\iics_cli.py get-task <NAME> DSS --folder <FOLDER> --full   # 完整 detail
```

- [ ] 默认：`task_count==1`、`rows==1`、`len(records)==1`、record keys == 12 列 DSS_HEADERS（含 `FRS ID`）。
- [ ] `--full`：仍返回完整 normalized detail（`raw` / `source_connection` / `target_connection` 等）。

`--type ALL`：

- [ ] 业务 sheet 顺序恰为 **`MTT Task`（23）→ `DSS Tasks`（12）→ `Workflow Tasks`（9）**，再 `Export Errors`。
- [ ] DSS 行只出现在 `DSS Tasks`，不进 `MTT Task`。

`export-selected`：

- [ ] `selected_DSS_*.xlsx` 使用 12 列 DSS schema（含 FRS ID；selection/count/summary 契约不变）。

---

### 6.8 认证架构验收（Amendment 10 / 10.1）

> **最终状态：Amendment 10 = ACCEPTED。** 自动化测试 87/87 PASS；
> TEST = FULL REAL PATH PASS；DEV / PROD = ENVIRONMENT ONBOARDING PASS
> （未执行真实 business GET，因无已确认存在且安全的 folder —— 属 test coverage
> difference，不是 blocker）。三个环境的 pin 均已由使用者分别人工确认后绑定。
> 下列清单保留作为**回归复核**用途。

**A. mock 层（必须先全绿）**

```powershell
python -m unittest discover -s tests -t .
```

- [ ] 全部用例通过
- [ ] 其中 GET-only 安全回归用例通过：`IICSClient._request("POST", ...)` 仍抛
      `ValueError("v0.1 only supports GET requests")`，且**没有**发出任何 HTTP 请求
- [ ] pin 文件不含 `username` / `password` / `icSessionId` / `serverUrl`
- [ ] `SessionManager` 不持有 Login 原始响应体（`roles` / `usergroups` / `emails` /
      `securityQuestion` / `securityAnswer` 均不出现）

**B. 环境选择**

- [ ] `--env TEST` 生效；`--env テスト` 等 alias 正确 normalize 为 `TEST`
- [ ] 只设 `IICS_ENV` 也能工作；同时存在时 `--env` 优先
- [ ] 两者都不提供 → `MissingEnvironmentError`、exit 1、**不落到 PROD**
- [ ] 未知环境 → `UnknownEnvironmentError`
- [ ] `--env DEV auth-check --env TEST` → `DuplicateEnvironmentArgumentError`、exit 1，
      **且在读取 credential / 发起 Login 之前**就失败
- [ ] `--env TEST auth-check --env TEST`（取值相同）同样被拒绝
- [ ] `IICS_ENV=DEV` + `--env TEST` → 使用 TEST，**不**报 duplicate

**C. Credential**

- [ ] 配置文件缺失 / JSON 非法 → `CredentialConfigError`
- [ ] 环境 username 或 password 为空 → `MissingCredentialsError`（**不会**拿空串去 Login）
- [ ] stdout / stderr 全程无 username / password / icSessionId

**D. Environment Pin**

- [ ] 未 pin 的环境执行任何业务命令 → `EnvironmentPinRequiredError`、exit 1
- [ ] `auth-check` 不写 pin；`pin_required` 时 stdout 顶层 key **恰好**是
      `environment` / `status` / `org_id` / `org_uuid` / `server_host` 五个，
      **不含** `next_step` 等人类提示字段（提示应出现在 stderr）
- [ ] 任何 status 下 stdout 都**不含** `id` / `icSessionId` / 账号密码 /
      `roles` / `usergroups` / `emails` / `securityQuestion` / `securityAnswer`
- [ ] `auth-pin` 成功/幂等时 stdout 顶层 key 恰好为 `environment` / `status` /
      `org_id` / `org_uuid` / `server_host` / `pinned_at` / `pin_file`
- [ ] `auth-pin` 写入后，`.env_pins.json` 仅含 `org_uuid` / `org_id` / `pinned_at`
- [ ] 已 pin 环境重复 `auth-pin` → 幂等，不重写
- [ ] 故意把另一环境账号填进来 → `EnvironmentMismatchError`，且**不自动覆盖**已有 pin

**E. 会话行为（真实环境）**

- [ ] 一次 `export` 全程只登录一次（stderr 中 `[IICS auth]` 登录日志只出现一次）
- [ ] 进程结束后仓库内**不存在** `session_cache.json` / `cached_session.json` 之类文件
- [ ] 401 自动重新登录并重试一次后继续完成导出（可在 session 长时间空闲后触发观察）
- [ ] 403 场景不触发重新登录（若有权限受限的 folder 可验证）

**F. 只读回归（pin 完成后）**

- [ ] `list-tasks` / `get-task` / `resolve-connection` / `export` / `export-selected`
      输出与 Amendment 9 完全一致（列数、`task_count` / `rows` /
      `failed_details_count` / `enrichment_warnings_count` 语义不变）

**G. git 安全**

- [ ] `git ls-files --error-unmatch iics_credentials.json` 失败（未被追踪）
- [ ] `git status --short -- iics_credentials.json` 无输出
- [ ] `git check-ignore -v iics_credentials.json` 命中 `/iics_credentials.json`
- [ ] `git check-ignore -v iics_credentials.template.json` 无输出（模板可被跟踪）
- [ ] `python scripts/check_no_real_credentials.py` exit 0

### 6.9 MTT Last Run / Connection Parameter（Amendment 11）

> 离线实施已由 mock 测试覆盖；以下为**真实环境待验证项**，须由用户明确指定环境并满足认证 /
> pin 规则后，通过正式 CLI 只读执行。不读取参数文件、不启动任务。

样本 `mt_DWH_TO_REG_V_DMF_SSA_FACT_RESULT_QUICK`（Source 参数化、Target 非参数化）：

```powershell
python .\iics_cli.py --env <ENV> get-task mt_DWH_TO_REG_V_DMF_SSA_FACT_RESULT_QUICK MTT --folder <folder>
```

- [ ] 2 条 record（Source / Target），两行 `Last Run` 均为 `2026-11-12T06:58:54.000Z`
      （`raw.lastRunTime = 1794466734000`，epoch 毫秒；固定 UTC、三位毫秒、`Z`）。
- [ ] Source：`Source Connection = rsh_gdm_e2e_TEC`、`Source Connection Parameter = parameter_rsh_gdm_e2e`；
      Target 侧两列为 `""`。
- [ ] Target：`Target Connection` 为真实 resolver 对 `000LVU0B0000000001VU` 的结果（不预填名称）；
      `Target Connection Parameter = ""`；Source 侧两列为 `""`。
- [ ] Custom Query 保留；dummy Source Object 仍隐藏（`""`）。
- [ ] stdout 顶层只有 `task_count` / `rows` / `records`；enrichment warning（若有）出现在 stderr，
      每条一行 `[IICS CLI] WARNING {kind}: {error}`。
- [ ] `--no-resolve`：Connection 列为原始 ID，不出现 `connection/` 请求；Parameter 与 Last Run 照常；
      无因跳过解析产生的 warning。
- [ ] 只有响应确实缺少 `name` / `connectionName` 时才出现 `returned no name` warning；若遇到名称恰好
      等于 ID 的 connection，应照常展示该名称且不出现此 warning（判定依据名称来源，不比较名称与 ID）。
- [ ] `export <folder> --type MTT`：`Task Summary` 26 列，值与 compact 一致；`--summary-only` 新增列为空。
- [ ] Last Run / 参数名缺失 / 冲突 / 名称降级 warning 只计入 `enrichment_warnings_count`，
      `failed_details_count` 不增加；Export Errors 文本含 transformation 名与字段名。

补充样本（待用户提供）：

- [ ] 一个 Target 参数化任务、一个完全非参数化任务。
- [ ] 「连接参数化但未勾选运行时覆盖」的任务：确认 `isConnectionRuntimeParameter` 与
      `connectionParameterName` 的实际含义（mock 不能替代该结论）。
- [ ] 仅由 taskflow 触发的 MTT：比较既有运行后的 `raw.lastRunTime` 与前端 Last Run（不主动触发执行）。
- [ ] 本例 `JPN_COMM_DWH_DL.PARAM`：人工确认有效作用域中是否定义 `parameter_rsh_gdm_e2e`
      （仅「存在定义」不足以证明实际连接不同，还需允许覆盖且生效值不同；无定义时采用任务值）。

---

## 7. 常见失败与排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `{"error":"未指定 IICS 环境...","error_type":"MissingEnvironmentError"}` 退出码 1 | 没给 `--env`，`IICS_ENV` 也没设 | 加 `--env TEST`，或 `$env:IICS_ENV = "TEST"`。工具刻意不默认 PROD |
| `DuplicateEnvironmentArgumentError` 退出码 1 | 同一条命令写了两次 `--env` | 只保留一个 `--env`；没有"后一个覆盖前一个"这种行为 |
| `EnvironmentPinRequiredError` / `EnvironmentMismatchError` | 该环境未绑定，或本次 Login 的 org 与已绑定值不符 | 走 §3.2：`auth-check` → 人工核对 org → `auth-pin`；mismatch 时先查 credential 是否填错环境 |
| `InvalidCredentialsError` | Login 被拒（401/403） | 检查该环境账号；工具刻意不自动重试，避免触发锁号 |
| `AuthenticationError` | 自动重新登录后业务 GET 仍 401 | 确认该账号在目标 org 是否仍有效 |
| `PermissionDeniedError` | 业务 GET 返回 403 | 权限不足；session 有效，工具正确地**不**重新登录 |
| legacy 模式下 `RuntimeError: IICS session expired...` | `IICS_AUTH_MODE=legacy_token` 且手动 token 过期 | 浏览器重新取 `USER_SESSION` 重设，或改回默认的 `--env` 主路径 |
| `list_tasks` 返回 `{"error":...}` 非空 | 文件夹名拼写 / 权限 / 分页参数 | 核对 folder 名；ADR 备注：`pageSize` 失败可改 `limit` 重试 |
| Test 3 type 过滤为 0 但应有 task | V3 `type` 实际值与枚举不同 | 检查 `normalize_task_type` 映射，按实测值补充映射层 |
| Test 4 SKIP | 目标文件夹该类型无 task | 用 `IICS_TEST_TASK_NAME` 指定真实 task |
| Excel 打不开 / 缺 sheet | openpyxl 未装或写盘失败 | `pip install -r requirements.txt`；检查目录写权限 |
| 中文在 PowerShell 显示乱码 | 控制台代码页非 UTF-8 | 仅显示问题；可 `chcp 65001` 改善，文件内容本身为正确 UTF-8 |

---

## 8. 安全红线（测试期间同样适用）

- 只读：测试只允许 GET；不得触发 update / batch / write 子命令（v0.1 代码中不存在写路径）。
- 不得用 `curl` 或任何方式直接构造 POST/PUT/PATCH/DELETE。
- 不得 `--dangerously-skip-permissions`。
- 不得把 token / cookie 写入任何文件或提交到 git。
- 导出的 `.xlsx` 属于业务数据，按 Takeda 数据规范存放，勿提交到仓库（已被 `.gitignore` 排除）。
```
