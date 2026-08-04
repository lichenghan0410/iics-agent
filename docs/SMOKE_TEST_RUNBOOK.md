# IICS Agent v0.1 — 真实 Token 冒烟测试运行手册（Mode A / Claude Code CLI；含 Mode C 手动执行）

本手册定义 IICS Agent v0.1 Read-only MVP 的**验收级冒烟测试**：在 Windows 上用真实
`IICS_SESSION_TOKEN` 通过 Claude Code CLI 驱动，验证 `iics_cli.py` 四个只读子命令与
`smoke_test.py` 的 Test 1–6 全部通过。

> 适用范围：`use4.dm-us.informaticacloud.com`（Takeda Prod Org），三个文件夹
> `JAPAN_COMMERCIAL_DWH` / `JAPAN_SCRM_BAU` / `JAPAN_SCRM_DI`，task 类型 DSS / MTT / WORKFLOW。

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
| Test 4 | `get_task_detail`（V2 by-name 路由 + 抽取，v0.1 不做 V3 消歧） | 集成-只读 | 无 error、含 `runtime_agent` |
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

## 3. 获取真实 Session Token（不落盘、不进 git）

1. 浏览器 SSO 登录 IICS Web UI，完成 Okta 验证。
2. 打开 DevTools → Network 面板，点任意 IICS 请求，在请求头 `Cookie` 中找
   `USER_SESSION=<value>`，复制 `<value>`。
3. **仅在当前 PowerShell 会话内**设置环境变量（不要写进 `.env`、不要 commit）：

   ```powershell
   $env:IICS_SESSION_TOKEN = "<USER_SESSION 值>"
   ```

> 安全要求：token 只存在于进程环境变量中。本项目代码不打印、不写文件、不入库
> （`.gitignore` 已排除 `.env`、`*.xlsx`、`*.jsonl`）。Token 过期后 API 返回 401，
> `handle_401()` 会提示重新从浏览器获取并重新 `export`。

---

## 4.0 直接 PowerShell 预检（先于 Claude Code）

进入 Claude Code CLI 之前，**先在纯 PowerShell 里直接跑一遍冒烟测试**，把
「代码 / API / token 问题」与「Claude Code 工具权限 / 自然语言调度问题」分开定位：

```powershell
Set-Location -LiteralPath "D:\IICS Agent"
git status --short          # 测试前：应只在末尾可能出现导出 .xlsx（被忽略，不入跟踪）
python smoke_test.py
git status --short          # 测试后：不应出现任何源码改动
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
| 「看 DI_FACT_SALES_DAILY 这个 DSS 的详情」 | `python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI` |
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

```powershell
# A. 枚举（验证 stdout 纯 JSON、分页、type 过滤）
python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
echo "exit=$LASTEXITCODE"   # 期望 0；有 error 时为 1

# B. 详情（验证 V2 by-name 路由 + connection name 解析；v0.1 不做 V3 按名消歧，见 ADR Amendment 1）
python iics_cli.py get-task <真实TASK名> DSS --folder JAPAN_SCRM_DI
echo "exit=$LASTEXITCODE"

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
$env:IICS_SESSION_TOKEN = "<USER_SESSION 值>"

python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
python iics_cli.py get-task <真实TASK名> DSS --folder JAPAN_SCRM_DI
python iics_cli.py resolve-connection <真实CONN_ID>
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL
```

Mode C 没有 AI 层介入，不存在 prompt injection 或模型误调用风险；安全边界与 Mode A 完全
一致（token 处理规则相同、`iics_cli.py` 本身无写路径），不需要额外的 `--allowedTools` 类
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
- [ ] 四个子命令成功时退出码 0，工具返回 `{"error":...}` 时退出码 1。
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

---

## 7. 常见失败与排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `{"error":"IICS_SESSION_TOKEN 未设置",...}` 退出码 1 | 环境变量没设或在新会话 | 在当前 PowerShell 重新 `$env:IICS_SESSION_TOKEN=...` |
| 任意请求抛 `RuntimeError: IICS session expired...` | token 过期（401） | 浏览器重新取 `USER_SESSION` 再 export |
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
