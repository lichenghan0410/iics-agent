# ADR-001: IICS Agent · v0.1 Read-only MVP

**状态**: Accepted（含 Amendment 1–11；Amendment 10 认证架构修订已 ACCEPTED；Amendment 11 离线实施完成、真实环境待验证）
**日期**: 2026-06-27
**决策人**: Li（运维负责人）
**参考文档**: `IICS_Agent_System_Design.md` v1.4（已通过三轮 review，评分 9/10）

**修订记录**：

| 日期 | 事件 |
|------|------|
| 2026-06-27 | Accepted — v0.1 Mode A（Claude Code CLI，主）+ Mode B（Cowork，计划辅模式）；经一轮 ADR review（评分 8.6/10）修正 V2 path 契约、Smoke Test 断言阻断项，采纳 V3 params 规范、connection 抽取 best-effort、export sheet best-effort 标注等改进；D-1 扩展为双模式设计，新增 `iics_cli.py` / `CLAUDE.md` 契约 |
| 2026-06-29 | v0.1 Mode A 真实 `USER_SESSION` token 联调通过（Step 1–9 完成，Smoke Test 1–6 全部通过）。联调中发现 6 项契约与实测不符（V3 路径、分页机制、`q` 语法大小写、按名消歧可行性等），记录于文末「Post-ADR 实测修正」 |
| 2026-09-11 | **Amendment 10 / 10.1 — ACCEPTED**：D-2 认证架构由「手动 `USER_SESSION`」修订为 DEV/TEST/PROD 多环境非交互式自动 Login + TOFU `orgUuid` environment pin + process-local session + 业务 401 自动 refresh once；Amendment 10.1 修复 `auth-check` stdout contract 与重复 `--env` 的隐式优先级。实现、mock 测试与真实环境验收均已完成，详见文末三节 |
| 2026-11-16 | **Amendment 11**：MTT 业务 schema 23 → 26 列（新增 `Last Run`、`Source Connection Parameter`、`Target Connection Parameter`）；连接展示「解析名称优先、`connDefault` 兜底」；MTT compact 将 enrichment warning 写入 stderr。DSS 12 / WORKFLOW 9 列不变。离线实施与 mock 测试完成，真实环境验证待用户指定，详见文末 |
| 2026-06-30 | **Amendment 1**：① 移除 Mode B（`iics_agent.py` Cowork Interactive Agent），新增 Mode C（手动直接调用 `iics_cli.py`，零新增代码）作为辅模式，详见 D-1；② 将 2026-06-29 发现的 6 项实测偏差反向同步进 D-3 / D-4 / `tools/query.py` 契约正文及 Smoke Test 断言，消除正文与文末「Post-ADR 实测修正」附录不一致的问题 |

> 说明：本次 Amendment 1 是对 v0.1 运行模式与契约准确性的修订，**不等同于进入 v0.2（Dry-run Update）**。v0.2 的范围（写操作 dry-run、`validate_update_preflight` 等）仍未启动，升级条件见文末。

---

## Context

### 业务背景

io-tec server 迁移项目需要批量查询和变更三个 IICS 文件夹（`JAPAN_COMMERCIAL_DWH`、`JAPAN_SCRM_BAU`、`JAPAN_SCRM_DI`）下 DSS / MTT / Workflow 类型 task 的 runtime environment 和 connection 配置。当前无工具支持，运维人员只能在 IICS Web UI 逐一操作。

### 当前约束

- 认证：企业 SSO（Okta），无专用服务账号，只能手动从浏览器提取 `USER_SESSION` Cookie
- 写操作：所有 endpoint 路径已 Postman 验证，但**真实写入从未在生产执行**，PARTIAL UPDATE 行为未确认
- 目标环境：`use4.dm-us.informaticacloud.com`（Takeda Prod Org）

### 为什么分阶段

写操作风险不对称：错误批量更新影响 47+ tasks，回滚没有现成工具。因此先建立只读基础（v0.1），验证 API 行为和工具框架后，再分阶段引入写能力。

v0.1 的唯一目标是：**让运维人员能用自然语言查询 IICS task 信息并导出 Excel，代码层完全不存在写路径。**

---

## 本 ADR 覆盖的 5 个架构决策

| # | 决策点 | 选择 |
|---|--------|------|
| D-1 | 运行模式 | 双模式：Claude Code CLI（主）+ 手动 CLI 执行（辅，Amendment 1 起替代 Cowork）|
| D-2 | 认证架构 | `SessionManager` 双模式封装，v0.1 只启用 SSO Cookie 模式 |
| D-3 | URL 构建 | 工厂函数模式，防止 `/saas/saas/` 路径重复 |
| D-4 | API 版本混用策略 | V3 枚举 + V2 详情，分别注入不同 Header |
| D-5 | v0.1 CLI 暴露边界 | IICS 业务 API 只读，write 代码不存在。原始 4 个子命令，Amendment 6 起 5 个，**Amendment 10 起共 7 个**（5 个业务 / 数据命令 + `auth-check` / `auth-pin` 两个认证生命周期命令）——见 D-5「当前命令契约」 |

---

## D-1: 运行模式（双模式设计，Amendment 1 更新）

### 背景

Claude Code CLI 本身即 AI 推理层——在其中启动一个独立的 Anthropic API 对话循环会产生"嵌套 Claude"问题：额外 API 成本、`input()` 循环与 CLI I/O 管理冲突。因此两种运行场景需要不同的入口文件，但共享全部底层工具代码。

### 选项对比

| 选项 | 入口文件 | AI 层来源 | 适用场景 | 状态 |
|------|---------|---------|---------|------|
| **A（主）** | `iics_cli.py` | Claude Code CLI 自身 | 在 `claude` CLI 会话中使用 | **已实现，v0.1 验收通过** |
| B | `iics_agent.py` | 内部 Anthropic API 调用 | 没有 Claude Code CLI 时 | **已移除**（Amendment 1，见下方"取舍"） |
| **C（辅）** | `iics_cli.py`（与 Mode A 同一入口） | 无 AI | Claude Code CLI 不可用、脚本化批处理、CI/定时任务、受限网络环境 | **新增，零新增代码** |
| D | — | 后端 API | 需额外部署，违反轻量约束 | 未选 |

### 两种模式的调用链（Amendment 1：Mode A + Mode C）

**Mode A（Claude Code CLI）**
```
运维人员 → claude CLI → bash: python iics_cli.py <subcommand> → IICSClient → IICS API
```
Claude Code CLI 读取 `CLAUDE.md` 获取项目上下文，根据自然语言意图直接决定调用哪条 `iics_cli.py` 子命令，解析 JSON 输出后以自然语言呈现。**无第二层应用内 Anthropic API 调用**（Claude Code CLI 本身仍使用 Claude Code 运行环境与配额）。

**Mode C（手动 CLI 执行）**
```
运维人员 → 终端（PowerShell/Bash）→ python iics_cli.py <subcommand> → IICSClient → IICS API
```
运维人员直接在终端输入完整子命令，参数格式参照 `CLAUDE.md`（同时充当命令速查参考）或下方 `iics_cli.py` 契约表。无任何 AI 层介入，stdout 仍为纯 JSON、stderr 输出日志，与 Mode A 调用 `iics_cli.py` 时的行为完全一致。适合：Claude Code CLI 不可用、网络隔离环境、需要写 shell 脚本批量调用、或接入 cron 定时导出任务。

### 共享层

`iics_client.py` / `tools/` / `cache/` 全部共享。Mode A 与 Mode C 进一步共享**同一入口文件** `iics_cli.py`——两者代码路径完全相同，唯一区别是"谁在输入命令"：Claude Code CLI 自动生成 vs. 运维人员手动输入。

### 取舍

**v0.1 原决策**：双模式共存（Mode A 主 + Mode B 辅），主辅分明，切换只换入口文件，工具函数层无感知。

**Amendment 1（移除 Mode B，新增 Mode C）**：v0.1 Step 1–9 完成、Mode A 在真实环境验收通过后判断，保留 Mode B（`iics_agent.py` + 内部 Anthropic API 对话循环）的收益有限：

- Claude Code CLI 已覆盖日常自然语言交互需求，Mode B 的"嵌套 Claude"在实际场景中很少被触发使用
- Mode B 需要独立维护一套 system prompt、`TOOLS` schema、`stop_reason` 处理逻辑、tool_result 截断规则，长期维护成本和 Mode A 重复
- 保留两条独立的 Agent 循环增加了测试面（需要分别验证两套对话逻辑），但实际只有一条路径在用

改为 Mode C：直接复用 Mode A 已验收的 `iics_cli.py` 入口，**零新增代码**——运维人员在终端手动输入与 Claude Code CLI 生成的命令完全相同的子命令即可。仅需要在 `CLAUDE.md` 中已有的"可用命令"章节作为手动调用时的速查参考，无需额外文档。

Mode A 为主、Mode C 为辅的格局：日常通过 Claude Code CLI 自然语言操作；`claude` 不可用、需要脚本化/定时任务、或在受限网络环境手动排查时，直接调用 `iics_cli.py`。

### Mode A 安全运行建议

Claude Code CLI 支持通过 `--allowedTools` / `--disallowedTools` 限制可用工具，也支持 `settings.json` 精细权限配置。由于 `IICS_SESSION_TOKEN` 存于环境变量，建议明确声明 v0.1 只读阶段的权限边界：

**推荐启动方式**：

```bash
# 只允许调用 iics_cli.py、smoke_test.py 和文件读取
claude --allowedTools "Bash(python iics_cli.py *),Bash(python smoke_test.py),Read"
```

**禁止事项**：
- 不使用 `--dangerously-skip-permissions`
- 不允许 Bash 直接调用 curl 或构造 POST/PUT/PATCH/DELETE HTTP 请求
- 不允许创建或修改 `tools/update.py`、`tools/batch.py`、`tools/safety.py`（v0.2 前不存在）
- 不允许输出、保存或以任何形式泄露 `IICS_SESSION_TOKEN`

> **注意**：`CLAUDE.md` 内容在 Claude Code 中作为用户消息注入，不是系统提示，不能作为唯一安全边界。必须强制执行的约束（写操作禁止）由 Python 代码无写路径保证；`CLAUDE.md` 仅作操作规范声明与行为引导。

### Mode C 安全说明

Mode C 无 AI 层介入，不存在 prompt injection 或模型误调用风险，主要风险点是人工输入错误（如文件夹名拼错）。安全边界与 Mode A 完全一致，因为两者共享同一个 `iics_cli.py`：

- Token 处理规则相同：仅设置进当前终端会话的环境变量，不写入文件、不 echo、不提交 git
- 写操作限制相同：`iics_cli.py` v0.1 本身没有写路径，无论谁调用都不可能触发写操作
- 不需要额外的 `--allowedTools` 类权限配置，因为没有 AI agent 在自主决策调用哪个命令

---

## D-2: 认证架构

### 选项

**A（选定）: `SessionManager` 双模式封装**

- `from_sso_token(token)`: 当前阶段，手动粘贴 Cookie，仅只读 + dry-run
- `from_env()`: 后续阶段（专用账号到位后），自动调用 Login API

两种模式共享 `headers_v2()` / `headers_v3()` / `handle_401()` 接口，上层工具代码零感知。

**B: 只实现 SSO 模式**

简单，但需要后续重构。

**C: 只等专用账号再开始**

账号创建周期不确定，迟滞项目。

### 取舍

选 A 的核心理由：`from_env()` 只需要搭框架（v0.1 不调用），切换时上层代码无需修改。SSO Cookie 在 Takeda 环境 Postman 已验证可直接作为 `icSessionId` / `INFA-SESSION-ID` 的 header value。这是 Takeda 环境特定行为，非 IICS 通用规范。

> **v0.1 限制**: `from_sso_token()` 仅允许只读操作。`from_env()` 搭框架但 v0.1 不启用，不做写操作验证。

> **⚠️ 已被 Amendment 10 supersede（2026-09-11）**：本节「无专用服务账号、只能手动粘贴
> `USER_SESSION`」的前提已被实测推翻——存在可用于本工具的 non-interactive
> `username` / `password` 凭据，`POST /ma/api/v2/user/login` 返回的同一个 `icSessionId`
> 可同时用于 V2（`icSessionId`）与 V3（`INFA-SESSION-ID`）。当前默认认证路径为
> **多环境自动 Login + environment identity pinning + process-local session**；
> 手动 `USER_SESSION`（`IICS_SESSION_TOKEN`）已降级为需显式开启的 legacy 调试模式。
> 完整决策、调用链、安全边界与验收结论见文末 **Amendment 10 / 10.1 / 最终验收结论**。
> 以下 D-2 原文保留为历史决策记录。

---

## D-3: URL 构建

### 问题

IICS Login API 响应的 `serverUrl` 在不同 POD/账号下可能已含 `/saas` 后缀（如 `https://apne2.dm-apne.informaticacloud.com/saas`）。如果工具层直接拼 `{serverUrl}/saas/api/v2/...`，会产生 `/saas/saas/api/v2/...` 路径错误，静默失败难以排查。

### 选项

**A（选定）: URL 工厂函数，集中处理**

```python
def build_v2_url(server_url: str, path: str) -> str:
    base = server_url.rstrip("/")
    if base.endswith("/saas"):
        return f"{base}/api/v2/{path.lstrip('/')}"
    return f"{base}/saas/api/v2/{path.lstrip('/')}"

def build_v3_url(server_url: str, path: str) -> str:
    """实测确认：V3 public API 与 V2 一样位于 /saas 下，需确保 /saas 前缀（不剥离）。"""
    base = server_url.rstrip("/")
    clean_path = path.lstrip("/")
    if base.endswith("/saas"):
        return f"{base}/{clean_path}"
    return f"{base}/saas/{clean_path}"
```

**B: 每个工具函数自己拼 URL**

复用性差，出现 `/saas/saas/` 时多处修复。

### 取舍

选 A。工厂函数单测一次，所有工具受益。`IICSClient._request(path, use_v3)` 接收 path 参数，URL 构建由 `_request` 内部调用工厂函数完成，工具层完全不接触 URL 拼接逻辑。

**path 参数规范（强制，已按实测更新）**：

| API 版本 | path 传入规则 | 示例 |
|---------|-------------|------|
| V2（`use_v3=False`）| **只传业务资源路径，不含 `saas/api/v2` 前缀** | `dsstask/name/MY_TASK` |
| V3（`use_v3=True`）| 传 V3 资源路径，不含 `?` 及以后（查询参数通过 `params` 传入）；**实测确认完整路径为 `/saas/public/core/v3/objects`**，故 path 传 `public/core/v3/objects` | `public/core/v3/objects` |

违反此规则会导致路径重复（如 `/saas/api/v2/saas/api/v2/...`）或路径缺失 `/saas`（导致 404），静默失败难以排查。所有工具函数中**禁止**在 path 里拼入 `saas/api/v2` 或 `?q=...`。

---

## D-4: API 版本混用策略

### 背景

IICS 有两套 API，行为差异显著：

| 维度 | V3 (`/saas/public/core/v3/`) | V2 (`/saas/api/v2/`) |
|------|----------------|----------------------|
| 认证 Header | `INFA-SESSION-ID` | `icSessionId` |
| 用途 | 对象枚举、分页 | Task 详情、Connection 详情、写操作 |
| 分页机制 | 无 `nextPageToken`，用 `skip` 偏移 | 无（一次返回） |
| 已验证路径 | `/saas/public/core/v3/objects` | `/saas/api/v2/dsstask|mttask|workflow|connection` |

### 决策

**v0.1 实现中使用规则：枚举用 V3，详情用 V2。**

- `list_tasks` → `_request("GET", "public/core/v3/objects", use_v3=True, params={"q": f'location=="{folder}"', "pageSize": n, "skip": skip})` (V3)
- `get_task_detail` → 按 `task_type` 路由（`use_v3=False`，path 不含 `saas/api/v2` 前缀）：
  - **DSS / WORKFLOW**：V2 by-name（`dsstask/name/<name>` / `workflow/name/<name>`），行为不变。
  - **MTT（Amendment 2）**：先 V3 folder 枚举取 **FRS object ID**，再 `mttask/frs/{frs_id}`
    （官方文档明确支持、只接受 FRS object ID）。彻底解决 MTT 跨 folder 同名歧义。详见文末 Amendment 2。
- `resolve_connection` → `_request("GET", f"connection/{id}", use_v3=False)` (V2)

这两个 Header 由 `_request(use_v3=True/False)` 自动注入，工具层无需手动指定。

> **路径已实测确认**: V3 枚举正确路径为 `/saas/public/core/v3/objects`（与官方 KB 文档的 `/public/core/v3/objects` 一致，但**位于 `/saas` 下**）。`api/v3/objects` 在 Takeda Prod SSO 账号下返回 **404**，已废弃。详见文末「Post-ADR 实测修正」。

---

## D-5: v0.1 CLI 暴露边界

### 决策（原始，2026-06-27；命令集合已由 Amendment 6 / 10 扩展，见下方「当前命令契约」）

**v0.1 `iics_cli.py` 只实现只读子命令（4 个 + Amendment 6 的 `export-selected`，共 5 个），不出现任何写操作子命令或其底层 import。**

> Amendment 1 后此例已更新：原决策写于 `iics_agent.py`（Mode B，已移除）尚存在时，示例为 Anthropic tool-use 的 `TOOLS`/`TOOL_MAP` 注册表；Mode B 移除后，v0.1 唯一入口是 `iics_cli.py`（Mode A + Mode C 共用），边界改为子命令级别：

```python
# iics_cli.py — v0.1 只读子命令，对应 tools/query.py / exporter.py / selected_exporter.py
SUBCOMMANDS = {
    "list-tasks":          query.list_tasks,
    "get-task":             query.get_task_detail,
    "resolve-connection":   query.resolve_connection,
    "export":               exporter.export_results,      # 内部调用 list_tasks + get_task_detail
    "export-selected":      selected_exporter.export_selected_tasks,  # Amendment 6，只读编排
}
# 不存在 "update-runtime" / "update-connections" / "batch-update" 等子命令
```

`tools/update.py`、`tools/batch.py`、`tools/safety.py` 在 v0.1 **不创建**，`iics_cli.py` 也不 import 它们。

### 当前命令契约（Amendment 10 更新）

> 上方「决策」是 2026-06-27 的原始 snapshot（当时 4 个命令，Amendment 6 起 5 个）。
> Amendment 10 引入认证生命周期命令后，**当前 `iics_cli.py` 共 7 个 subcommands**，
> 按对 IICS 的作用分为两类：

**业务 / 数据操作命令（5 个）**

```text
list-tasks
get-task
resolve-connection
export
export-selected
```

对 IICS 严格 **GET-only**：不得发出 `POST` / `PUT` / `PATCH` / `DELETE` 业务请求。
`IICSClient._request()` 中 `if method != "GET": raise ValueError` 是这条边界的可执行强制点。

**认证生命周期管理命令（2 个，Amendment 10 新增）**

| 命令 | 允许的写行为 | 对 IICS 的边界 |
|---|---|---|
| `auth-check` | Login `POST`（仅认证）。不写任何本地文件 | 不执行任何 IICS business write |
| `auth-pin` | Login `POST`（仅认证）+ 本地写 `.env_pins.json` | 不执行任何 IICS business write |

### 两类"写"必须区分

D-5 的核心安全意图未变，但需要明确它约束的是**哪一侧**的写：

```text
IICS-side business boundary          ← D-5 的约束对象，保持 GET-only
    禁止：POST / PUT / PATCH / DELETE 业务 API
    禁止：update-runtime / update-connections / batch-update 等写子命令
    禁止：tools/update.py / batch.py / safety.py

authentication lifecycle exception   ← Amendment 10 引入，不改变上一条
    允许：POST /ma/api/v2/user/login（认证层唯一 POST，只在 auth/login.py）

local trust metadata write           ← Amendment 10 引入，不改变第一条
    允许：auth-pin 写本地 .env_pins.json（org_uuid / org_id / pinned_at）
```

因此「v0.1 Read-only MVP」的准确含义是
**「对 IICS 业务 API 只读」**，而**不是**「程序绝对不写任何本地文件」——
`export` 早已写出 Excel / JSON 文件，`auth-pin` 写本地环境身份 metadata，
两者都不代表放开 IICS business write。

Login POST 与本地 pin write 的完整设计、异常语义与安全论证见 **Amendment 10**；
D-5 只保留边界分类，不重复实现细节。

### 取舍

把"写子命令已存在但被 guard 拦截"改为"写子命令代码根本不存在"，消除实现者在 v0.1 引入写操作的可能性。这个边界与运行模式无关——无论 Mode A（Claude Code CLI 调用）还是 Mode C（人工手动调用），`iics_cli.py` 本身都没有写路径。v0.2 再创建这些文件和对应子命令。

---

## 实现契约

### 目录结构（v0.1 交付物）

```
iics_agent/
├── CLAUDE.md              # Mode A 项目上下文（Claude Code CLI 会话启动时自动读取）；同时作为 Mode C 手动调用的命令速查参考
├── iics_cli.py            # Mode A + Mode C 共享入口：Claude Code CLI 或人工终端均可直接调用
├── iics_client.py         # SessionManager + URL factory + IICSClient
├── tools/
│   ├── __init__.py
│   ├── query.py           # list_tasks, get_task_detail, resolve_connection
│   └── exporter.py        # export_results（openpyxl）
├── cache/
│   ├── __init__.py
│   └── connection_cache.py  # ConnectionCache：内存 dict，session 级别
├── requirements.txt       # 唯一依赖文件（requests, openpyxl），无 Anthropic SDK 依赖
└── smoke_test.py          # 只读冒烟测试
```

> **v0.1 不创建的文件**: `tools/update.py`、`tools/batch.py`、`tools/safety.py`、`audit_log.jsonl`

---

### iics_client.py 契约

```python
# ── 模块级常量 ──────────────────────────────────────────────────────────────
LOGIN_URL: str  # 从 os.environ.get("IICS_LOGIN_URL", "https://dm-us.../ma/api/v2/user/login")

# ── URL 工厂（模块级函数）────────────────────────────────────────────────────
def build_v2_url(server_url: str, path: str) -> str: ...
def build_v3_url(server_url: str, path: str) -> str: ...

# ── SessionManager ───────────────────────────────────────────────────────────
@dataclass
class SessionManager:
    sso_token: Optional[str]
    username:  Optional[str]
    password:  Optional[str]

    # 只读属性
    @property
    def server_url(self) -> str: ...

    # 构造器（v0.1 只用 from_sso_token）
    @classmethod
    def from_sso_token(cls, token: str) -> "SessionManager": ...
    @classmethod
    def from_env(cls) -> "SessionManager": ...  # v0.1 搭框架，不调用

    # Header 注入
    def headers_v2(self) -> dict: ...  # {"icSessionId": ..., "Content-Type": "application/json"}
    def headers_v3(self) -> dict: ...  # {"INFA-SESSION-ID": ..., "Content-Type": "application/json"}

    # 401 处理
    def handle_401(self) -> None: ...

# ── IICSClient ───────────────────────────────────────────────────────────────
class IICSClient:
    session: SessionManager

    def __init__(self, session: SessionManager): ...

    def _request(
        self,
        method: str,          # "GET" | "POST"
        path: str,            # 相对路径，不含 query string，如 "public/core/v3/objects" 或 "dsstask/name/X"
        use_v3: bool = False, # True → build_v3_url + INFA-SESSION-ID header
        **kwargs,             # 传递给 requests.request
    ) -> requests.Response:
        # 内部：build_v2/v3_url → request → 401 时 handle_401() → 重试一次
        ...
```

**`_request` 行为约定**:
- 最多重试 1 次（attempt 0 → 401 → handle_401() → attempt 1）
- 非 401 错误：直接 `raise_for_status()`
- 成功返回 `requests.Response`，调用方负责 `.json()`

---

### tools/query.py 契约

所有工具函数必须：
- 接收 `client: IICSClient` 作为第一个参数
- 返回 JSON 可序列化的 `dict`（不抛异常，错误封装进返回值）
- 异常统一格式：`{"error": str(e), "error_type": type(e).__name__}`

**异常处理强制包装点**：工具函数内所有 `client._request()` 调用（`_request` 内部会 `raise_for_status()`）必须被 try/except 包裹，异常在工具函数层拦截并返回结构化错误，不向上层 Agent 循环抛出：

```python
def list_tasks(client, folder_path, ...):
    try:
        resp = client._request("GET", "public/core/v3/objects", use_v3=True, params={...})
        data = resp.json()
        # ... 处理逻辑
        return {"folder": folder_path, "count": ..., "tasks": [...]}
    except Exception as e:
        return {"error": str(e), "error_type": type(e).__name__}
```

```python
def list_tasks(
    client: IICSClient,
    folder_path: str,
    task_type: str = "ALL",    # "DSS" | "MTT" | "WORKFLOW" | "ALL"
    page_size: int = 200,
) -> dict:
    """
    返回格式:
    {
        "folder": "JAPAN_COMMERCIAL_DWH",
        "count": 83,
        "tasks": [
            {"name": "...", "type": "DSS", "id": "...", "status": "..."},
            ...
        ]
    }
    实现要点:
    - 调用 _request("GET", "public/core/v3/objects", use_v3=True, params={"q": f'location=="{folder_path}"', "pageSize": min(page_size, 200), "skip": skip})
    - V3 q 查询必须通过 requests params 传入，不允许手拼 query string（含特殊字符时 URL encoding 不稳定）
    - task_type != "ALL" 时 q 追加 **小写** and type=="<task_type>"（大写 AND 会 400 parse error，已实测确认）
    - 分页：响应无 nextPageToken，用 skip 偏移；初始 skip=0，每页后 skip += len(objects)，objects 为空或 skip >= count 时停止；pageSize 上限 200
    - V3 对象**无 name/status 字段**，task 名取自 path 末段（_object_name(path) = path.split("/")[-1]）
    - V3 响应中 type 字段的实际值可能与工具枚举不同（如 SYNC_TASK、MTTask 等）
      → 若发现不匹配，在 query.py 中增加 normalize_task_type() 映射层，不修改架构
    """

def get_task_detail(
    client: IICSClient,
    task_name: str = "",
    task_type: str = "",       # "DSS" | "MTT" | "WORKFLOW"
    folder_path: str = "",     # MTT 必需；DSS/WORKFLOW 记录用
    task_id: str = "",         # 仅 MTT：FRS object ID 直取（供 export 内部复用）
    task_id_type: str = "",    # 仅 MTT：必须显式 "frs"
    resolve_connections: bool = True,
) -> dict:
    """
    返回格式（Amendment 3 修正；MTT 归一化 detail）:
    {
        "@type": "mtTask",
        "id": "000LVU0Z000000006IMR",     # MTT: classic V2 id（来自 FRS payload）
        "orgId": "000LVU",
        "frs_id": "fABfm4RCYqVfuVEi9e8BGb",  # V3/FRS id（用于 FRS 请求）
        "detail_route": "v2-mttask-frs",
        "task_name": "...", "task_type": "MTT", "task_location": "...",
        "task_updated_on": "...",
        "runtime_environment": {"id": "<rtId>", "name": "<resolved>"},
        "task_mapping": {"id": "...", "name": "...", "location": null, "updated_on": "..."},
        "transformations": [ ... ],       # 仅来自 MTT parameters[]
        "parameter_file": {"directory": null, "name": null},
        "advanced_options": {"pre_processing_commands": null, "post_processing_commands": null},
        "enrichment_warnings": [],
        "raw": { <完整 API 响应> }
    }
    # DSS / WORKFLOW 仍为旧结构（name/type/folder/runtime_environment/source_connection/…/raw）
    # 注：已删除误导性的 id_type；顶层 id 即 classic id，frs_id 单独字段。
    实现要点（Amendment 2 / 3）:
    - **MTT**：官方 FRS 路径 `mttask/frs/{frs_id}`（只接受 FRS object ID）。
        方式 A：name + folder → list_tasks(folder, MTT) 精确匹配取 FRS object ID → FRS。
        方式 B：task_id + task_id_type="frs"（跳过枚举，供 export 复用）。
        缺 folder → MissingFolderError；0 匹配 → TaskNotFoundError；多匹配 → AmbiguousTaskError
        (candidates 仅含 id/path/type/name)；classic/缺失/非 FRS 的 task_id_type → InvalidIdTypeError。
        **不使用** `mttask/name/<name>`，也**不使用**裸 `mttask/{id}`。
    - **DSS / WORKFLOW**：保持 V2 by-name（`dsstask/name/<name>` / `workflow/name/<name>`），行为不变；
      不支持 task_id / task_id_type（传入 → UnsupportedArgumentError）。
    - **参数冲突**：task_id 与 task_name/folder_path 并存 → ConflictingArgumentsError（不发 HTTP、不枚举）。
    - connection ID 抽取规则（best-effort extractor，extract_connection_ids(raw, task_type)）：
        DSS      → 优先提取 sourceConnectionId / targetConnectionId 字段
        MTT      → 扫描 parameters / runtime 配置中的 connectionId 字段
        WORKFLOW → v0.1 先返回 raw，不强制解析所有 step 内 connection
        未识别字段 → 不报错，记入 unresolved_connection_fields，继续执行
    - resolve_connections=True 时：对已抽取的 connection ID 列表调用 resolve_connection
    """

def resolve_connection(
    client: IICSClient,
    connection_ids: list[str],
) -> dict:
    """
    返回格式:
    {
        "resolved": {
            "000LVU0B000000000015": "REDSHIFT_JAPAN_PROD",
            "000LVU0B000000000022": "ORACLE_DWH_PROD"
        },
        "failed": ["000FAIL..."]
    }
    实现要点:
    - 先查 ConnectionCache，命中则跳过 API 调用
    - 未命中：_request("GET", f"connection/{id}", use_v3=False)（build_v2_url 自动添加 saas/api/v2 前缀）
    - 成功后写入 ConnectionCache
    - 单个 ID 失败不影响其他 ID（继续，记入 failed 列表）
    """
```

---

### cache/connection_cache.py 契约

```python
class ConnectionCache:
    """内存缓存，session 级别，进程退出即清空。"""

    def __init__(self): ...

    def get(self, connection_id: str) -> str | None:
        """返回连接名称，未命中返回 None。"""

    def set(self, connection_id: str, name: str) -> None:
        """写入缓存。"""

    def get_or_fetch(
        self,
        connection_id: str,
        fetch_fn: Callable[[str], str],
    ) -> str:
        """
        命中缓存则直接返回；未命中则调用 fetch_fn(connection_id) 并写入缓存。
        fetch_fn 应封装 IICSClient._request 调用，失败时抛异常。
        """
```

`IICSClient` 持有一个 `ConnectionCache` 实例，`resolve_connection` 通过它进行缓存读写。

---

### tools/exporter.py 契约

```python
def export_results(
    client: IICSClient,
    data: dict,
    format: str = "excel",     # "excel" | "json"
    filename: str = "",        # 空则按时间戳生成
) -> dict:
    """
    返回格式（Amendment 3：task_count 与 rows 分离）:
    {
        "file_path": "/path/to/output.xlsx",
        "format": "excel",
        "task_count": 10,     # == list_tasks.count（逻辑 task 数）
        "rows": 24,           # Task Summary 实际物理数据行数（MTT 展开后可 > task_count）
        "failed_details_count": 0, "failed_details": [],
        "enrichment_warnings_count": 0, "enrichment_warnings": [],
        "summary": "..."
    }
    Excel 输出：业务数据主 sheet `Task Summary`（Amendment 4 起 22 列；已删除始终为空的
    Mapping Location）+
    错误/警告 sheet `Export Errors`。MTT 每个 Source/Target transformation = 1 行；
    DSS/WORKFLOW 仍 1 task = 1 行。原 Field Mapping / Truncation Risk 已移除。
    完整成功条件：task_count == list_tasks.count 且 failed_details_count == 0
    （不再用 rows == task_count 判断）。
    """
```

---

### iics_cli.py 契约（Mode A + Mode C 共享入口）

```python
#!/usr/bin/env python3
"""
IICS Agent CLI — Mode A（Claude Code CLI 通过 bash 调用）与 Mode C（人工终端直接调用）共享入口。
两种模式调用方式完全相同，均无需 Anthropic API 调用。

Session token:
  export IICS_SESSION_TOKEN="<USER_SESSION 值>"

使用示例（Mode A 下由 Claude Code CLI 自动生成；Mode C 下由运维人员手动输入，命令完全一致）:
  python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
  python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI   # Amendment 9：三类型均需 --folder
  python iics_cli.py resolve-connection 000LVU0B000000000015
  python iics_cli.py export JAPAN_COMMERCIAL_DWH --type DSS
"""
```

**子命令规范**：

| 子命令 | 必填参数 | 可选参数 | 说明 |
|--------|---------|---------|------|
| `list-tasks <folder>` | `folder` | `--type DSS\|MTT\|WORKFLOW\|ALL`，`--page-size N` | 枚举文件夹 task |
| `get-task <name> <type>` | `task_name`，`task_type` | `--folder PATH`，`--no-resolve` | 获取单 task 详情 |
| `resolve-connection <id>...` | 至少一个 id | （支持多个 ID 空格分隔）| 解析 connection ID |
| `export <folder>` | `folder` | `--type ALL`，`--details`（默认）/`--summary-only`，`--format excel\|json`，`--filename NAME` | 导出 Excel |

**`export` 内部执行流程**：

```
1. list_tasks(folder, task_type)               → 每个 task 含 id / name / type
2. 对每个 task 取详情（--summary-only 时跳过此步）：
   - DSS / WORKFLOW: get_task_detail(name, type, folder)             （V2 by-name）
   - MTT:            get_task_detail(task_type="MTT", task_id=task["id"],
                                     task_id_type="frs")        （复用 FRS object ID → FRS，
                     不传 name/folder 以免 ConflictingArgumentsError；返回后回填 name/folder）
3. 聚合为 data = {"tasks": [...], "details": [...], "failed_details": [...]}
4. export_results(data, format, filename)       → Excel / JSON 文件
5. stdout 输出 {"file_path": ..., "rows": ..., "summary": ...}
```

> **默认行为为 `--details`**（拉取每个 task 详情，Excel 含完整 runtime/connection 信息）。`--summary-only` 仅用于快速预览 task 列表，跳过逐 task 详情拉取。

**行为约定**：

- **stdout 只输出 JSON**（`print(json.dumps(..., ensure_ascii=False, indent=2))`），退出码 `0`/`1`
- **stderr 输出人类可读日志**（进度、`[Auth]` 信息、警告），避免污染 stdout JSON
- Session token 未设置：stdout `{"error": "IICS_SESSION_TOKEN 未设置"}` + `sys.exit(1)`
- 工具函数返回 `{"error": ...}`（非抛异常）时，也必须 `sys.exit(1)`：
  ```python
  result = list_tasks(...)
  print(json.dumps(result, ensure_ascii=False, indent=2))
  sys.exit(1 if "error" in result else 0)
  ```
- 捕获异常时：stdout `{"error": str(e), "error_type": type(e).__name__}` + `sys.exit(1)`
- 工具函数直接复用 `tools/query.py` / `tools/exporter.py`，不重复实现逻辑

---

### CLAUDE.md 契约（Mode A 项目上下文；同时作为 Mode C 命令速查参考）

放置于 `iics_agent/` 项目根目录，Claude Code CLI 会话启动时自动读取（Mode A）；Mode C 下运维人员手动调用时可直接查阅本文件的"可用命令"章节作为参数格式参考。**必须包含以下内容**：

````markdown
# IICS Agent

Takeda Japan IICS 运维工具，查询三个 JAPAN_* 文件夹下的 task 配置并导出 Excel。
当前阶段 v0.1：只读模式，写操作不可用。

## 环境准备

```bash
# 1. SSO 登录 IICS Web UI（Okta 弹窗验证）
# 2. 浏览器 DevTools → Network 面板 → 任意 IICS 请求头中找 Cookie: USER_SESSION=<value>
export IICS_SESSION_TOKEN="<USER_SESSION 值>"
# Token 过期后 API 返回 401，需重新从浏览器获取并重新 export
```

## 可用命令

```bash
python iics_cli.py list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
python iics_cli.py get-task <task_name> <task_type> [--folder PATH] [--no-resolve]
python iics_cli.py resolve-connection <id> [<id>...]
python iics_cli.py export <folder> [--type ALL] [--format excel|json] [--filename NAME]
```

## 覆盖范围

- 文件夹：`JAPAN_COMMERCIAL_DWH` · `JAPAN_SCRM_BAU` · `JAPAN_SCRM_DI`
- Task 类型：`DSS`（Sync Task）· `MTT`（Mapping Task）· `WORKFLOW`（Linear Taskflow）
- IICS Org：`use4.dm-us.informaticacloud.com`（Takeda Prod）

## 所有命令输出均为 JSON，可直接解析；日志和警告输出至 stderr

## 安全边界

- v0.1 只读模式，以下操作不可用且不得实现：update、batch、write 相关子命令
- 不得通过任何方式（curl、直接 requests POST 等）调用 IICS 写 API
- 不得创建或修改 `tools/update.py`、`tools/batch.py`、`tools/safety.py`
- 不得输出、打印、写入文件或以任何形式泄露 `IICS_SESSION_TOKEN`
- 如果用户要求修改 runtime / connection，回答：当前 v0.1 不支持写操作，v0.2 版本提供

## 运行目录

所有命令必须在包含 `iics_cli.py` 的项目根目录执行：

```bash
cd /path/to/iics_agent   # 若当前不在此目录，先 cd
python iics_cli.py list-tasks ...
```
````

---

### requirements.txt

```
# Python >= 3.10 required（使用 match/case 和 X | Y 类型注解）
# Mode A + Mode C 共享依赖；无 Anthropic SDK 依赖（CLI 子命令本身不调用 Anthropic API）
requests>=2.31.0
openpyxl>=3.1.0
```

安装说明：

```bash
pip install -r requirements.txt    # Mode A 与 Mode C 通用，无需额外安装
```

> v0.1 进入实际 repo 后建议生成 `requirements-lock.txt`（pip-tools）固定完整依赖树。

> **v0.1 不要求 `IICS_LOGIN_URL`、`IICS_USER`、`IICS_PASS` 环境变量存在**。`from_env()` 仅搭框架，不在 v0.1 调用或测试。Smoke Test 只需要 `IICS_SESSION_TOKEN`。

---

## Smoke Test 定义（v0.1 完成标准）

`smoke_test.py` 不启动完整 Agent，直接测试客户端和工具函数。运行命令：

```bash
IICS_SESSION_TOKEN="<USER_SESSION值>" python smoke_test.py
# 可选覆盖（默认值如下）
IICS_TEST_FOLDER="JAPAN_SCRM_DI"
IICS_TEST_TASK_TYPE="DSS"
IICS_TEST_TASK_NAME=""   # 空则取 list_tasks 第一个结果
```

**必须全部通过才算 v0.1 完成**；若目标文件夹内无指定类型 task，Test 4/5 可 SKIP，但正式验收前须通过 `IICS_TEST_TASK_NAME` 指定一个真实 task 再完整跑一遍，确保 Test 4/5 也通过：

```python
import os, json
from iics_client import SessionManager, IICSClient, build_v2_url, build_v3_url
from tools.query import list_tasks, get_task_detail, resolve_connection
from tools.exporter import export_results

# ── 测试参数（可通过环境变量覆盖）────────────────────────────────────────────
TOKEN       = os.environ["IICS_SESSION_TOKEN"]
TEST_FOLDER = os.environ.get("IICS_TEST_FOLDER", "JAPAN_SCRM_DI")
TEST_TYPE   = os.environ.get("IICS_TEST_TASK_TYPE", "DSS")
TEST_TASK   = os.environ.get("IICS_TEST_TASK_NAME", "")   # 空则动态取第一个

# Test 1: SessionManager + URL factory
session = SessionManager.from_sso_token(TOKEN)
assert session.server_url == "https://use4.dm-us.informaticacloud.com"
# V2 path 不含 saas/api/v2 前缀，由 build_v2_url 添加
assert build_v2_url(session.server_url, "dsstask/name/X") \
       == "https://use4.dm-us.informaticacloud.com/saas/api/v2/dsstask/name/X"
# V3 实测确认位于 /saas 下：build_v3_url 应确保 /saas 前缀（不剥离）
assert build_v3_url(session.server_url, "public/core/v3/objects") \
       == "https://use4.dm-us.informaticacloud.com/saas/public/core/v3/objects"
# 防 /saas/saas/ 重复：build_v2_url 遇到 serverUrl 含 /saas 后缀时应正确处理
assert build_v2_url("https://use4.dm-us.informaticacloud.com/saas", "connection/123") \
       == "https://use4.dm-us.informaticacloud.com/saas/api/v2/connection/123"
# build_v3_url 同样需要处理 serverUrl 已含 /saas 后缀的情况，不应重复拼接
assert build_v3_url("https://use4.dm-us.informaticacloud.com/saas", "public/core/v3/objects") \
       == "https://use4.dm-us.informaticacloud.com/saas/public/core/v3/objects"
print("✅ Test 1 passed: SessionManager + URL factory")

# Test 2: list_tasks — all types
client = IICSClient(session)
result = list_tasks(client, folder_path=TEST_FOLDER)
assert "tasks" in result, f"list_tasks returned error: {result}"
assert result["count"] >= 0   # 允许 0，不强制非空
print(f"✅ Test 2 passed: list_tasks({TEST_FOLDER}) → {result['count']} tasks")

# Test 3: list_tasks — filtered by type
result_typed = list_tasks(client, folder_path=TEST_FOLDER, task_type=TEST_TYPE)
assert "tasks" in result_typed
# type 字段验证（若实际值与枚举不同，normalize_task_type() 层应处理）
print(f"✅ Test 3 passed: list_tasks type={TEST_TYPE} → {result_typed['count']} tasks")

# Test 4 & 5: 依赖有效 task 存在，count=0 时跳过
if result_typed["count"] == 0:
    print(f"⏭️  Test 4 & 5 SKIPPED: {TEST_FOLDER} has no {TEST_TYPE} task")
else:
    task_name = TEST_TASK or result_typed["tasks"][0]["name"]

    # Test 4: get_task_detail（MTT 经 V3→FRS object ID→FRS；DSS/WORKFLOW by-name）
    detail = get_task_detail(
        client, task_name=task_name, task_type=TEST_TYPE, folder_path=TEST_FOLDER,
    )
    assert "error" not in detail, f"get_task_detail error: {detail}"
    assert "runtime_environment" in detail
    if TEST_TYPE == "MTT":
        assert detail["detail_route"] == "v2-mttask-frs"
        assert detail["frs_id"] and "id_type" not in detail
        assert detail["task_mapping"]["location"] is None
    # connection 断言按 task_type 区分（DSS 有稳定 source/target；MTT/WORKFLOW v0.1 返回 raw）
    if TEST_TYPE == "DSS":
        assert "source_connection" in detail
    else:
        assert "raw" in detail
    print(f"✅ Test 4 passed: get_task_detail({task_name})")

    # Test 5: resolve_connection + cache hit（仅 DSS 有稳定 source_connection）
    conn_id = (detail.get("source_connection") or {}).get("id")
    if TEST_TYPE == "DSS" and conn_id:
        r1 = resolve_connection(client, connection_ids=[conn_id])
        r2 = resolve_connection(client, connection_ids=[conn_id])   # 应命中缓存
        assert r1["resolved"] == r2["resolved"]
        print(f"✅ Test 5 passed: resolve_connection + cache hit")
    else:
        print(f"⏭️  Test 5 SKIPPED: no stable source_connection for {TEST_TYPE}")

# Test 6: export_results
export_result = export_results(client, data=result_typed, format="excel")
assert "error" not in export_result, f"export_results error: {export_result}"
assert os.path.exists(export_result["file_path"])
assert export_result["file_path"].endswith(".xlsx")
print(f"✅ Test 6 passed: export_results → {export_result['file_path']}")

print("\n✅ All smoke tests passed — v0.1 完成")
```

---

## 实现步骤（Amendment 1 更新：Step 1–9 完成 Mode A，Mode A 完成即 Mode C 同步可用）

| Step | 工作项 | 完成标志 |
|------|--------|---------|
| 1 | 创建 `iics_agent/` 目录结构，写 `requirements.txt`，写 `CLAUDE.md` 初稿 | 目录树与契约一致，`CLAUDE.md` 包含全部必需内容 |
| 2 | 实现 `SessionManager.from_sso_token()` + `headers_v2/v3` + `handle_401` | `SessionManager` 单元可实例化 |
| 3 | 实现 `build_v2_url` / `build_v3_url` | URL factory 单元测试通过（含 `/saas` 后缀变体） |
| 4 | 实现 `IICSClient._request()`（含 401 重试）| 可发出真实 GET 请求，401 触发 `handle_401` |
| 5 | 实现 `list_tasks`（含自动分页）| Smoke Test 2 & 3 通过 |
| 6 | 实现 `get_task_detail`（含同名消歧 + V2 路由）| Smoke Test 4 通过 |
| 7 | 实现 `ConnectionCache` + `resolve_connection` | Smoke Test 5 通过（含缓存命中验证）|
| 8 | 实现 `export_results`（openpyxl；v0.1.1 起单 sheet Task Summary）| Smoke Test 6 通过，Excel 可用 Excel 打开 |
| 9 | 实现 `iics_cli.py`（4 个子命令），写 `smoke_test.py` | **Mode A 完成** ✅（已通过 v0.1 真实环境验收）；**Mode C 同步可用**（同一入口，无需额外步骤） |

> **已移除（Amendment 1）**：原 Step 10「实现 `iics_agent.py` 主循环」（Mode B / Cowork Interactive Agent）。理由见 D-1「取舍」一节。

---

## 已确认不实现（v0.1 边界）

| 不实现项 | 原因 |
|---------|------|
| `tools/update.py` | v0.2 才建 |
| `tools/batch.py` | v0.4 才建 |
| `tools/safety.py` | v0.2 才建（assert_auth_mode_allows_write 等 guard 在 v0.2 引入） |
| `audit_log.jsonl` | 只读操作无需审计日志 |
| `validate_update_preflight` 工具 | v0.2 才注册 |
| `VERIFIED_WRITE_COMBINATIONS` | v0.3 才引入并解锁 |
| `SessionManager.from_env()` | 搭框架即可，v0.1 不调用，不测 |
| `build_v2_url` 写操作路径（POST） | v0.2 才需要 |
| `iics_agent.py` / `requirements-agent.txt` | Mode B 已移除（Amendment 1），改用 Mode C 复用 `iics_cli.py`，不再实现 |

---

## Consequences

**变得更容易**:
- 运维人员不需要记 API、不需要写脚本，自然语言即可导出 Excel（Mode A）
- Claude Code CLI（Mode A）直接 bash 调用工具函数，无第二层 Anthropic API 调用成本
- Mode C 零新增代码：与 Mode A 共享同一 `iics_cli.py` 入口，Claude Code CLI 不可用时可直接手动调用，无需额外维护一套交互式 Agent
- URL factory 集中处理 `/saas` 变体，后续写操作工具直接复用
- ConnectionCache 让批量查询时连接 ID 解析不重复请求

**需要接受的限制**:
- v0.1 无写能力；如需变更 task 配置，仍需手动在 IICS Web UI 操作
- SSO Token 过期后必须手动刷新（无法自动续期）
- `server_url` 默认写死为 `https://use4.dm-us.informaticacloud.com`，专用账号到位后由 Login API 动态返回

**已在当前 SSO 账号下实测确认**（不再是"待确认"）:
- V3 objects endpoint: `/saas/public/core/v3/objects`（`api/v3/objects` 已废弃，404）
- V3 分页: `pageSize` + `skip` 偏移，`pageSize` 上限 200，无 `nextPageToken`
- V3 `q` 查询逻辑符须用小写 `and`，仅支持 `location`/`type` 字段（`name`/`path` 会 400）
- SSO Cookie 值（`USER_SESSION`）可直接作为 V2 `icSessionId` / V3 `INFA-SESSION-ID` header value；用 `Cookie:` 头方式则 401

**专用账号到位后仍需复核**:
- Login API 返回的 `serverUrl` 形态（是否含 `/saas` 后缀）
- `from_env()` 的完整 login 流程
- 专用账号下 V3 endpoint 是否与当前 SSO 账号一致

---

## 升级至 v0.2 的前提条件

- [ ] v0.1 Smoke Test 全部通过
- [ ] 能从自然语言查询三个文件夹的 task，并成功导出 Excel
- [ ] 专用服务账号已创建，`from_env()` 可实测
- [ ] V3 路径、Login URL 用新账号重新验证

---

*本 ADR 基于 `IICS_Agent_System_Design.md` v1.4 生成。实现过程中如发现 API 行为与契约不符，优先以实测结果为准，并在 System Design 文档中标注差异。*

---

## Post-ADR 实测修正（v0.1 联调，2026-06-29）

v0.1 Step 1–9 实现完成后，用真实 `USER_SESSION` token 对 `use4.dm-us.informaticacloud.com`（Takeda Prod）的 `JAPAN_SCRM_DI` 做了端到端只读联调，Smoke Test 1–6 全部通过（Test 4/5 实跑非 SKIP）。过程中本 ADR 标注的多处「待确认项」被实测推翻或确认，已据此修正实现。**完整探测过程、响应样例与字段级证据见 `docs/IICS_API_NOTES.md`**；本节只列结论与对应代码改动。

> 原则不变：API 行为与契约不符时以实测为准（见上文结尾说明）。

### 与本 ADR 契约的偏差及修正

| 本 ADR 原契约 | 实测结论 | 修正 |
|---------------|---------|------|
| D-4：V3 枚举路径 `api/v3/objects`（"已验证路径"） | `/api/v3/objects` → **404**；正确为 **`/saas/public/core/v3/objects`** | `_V3_OBJECTS_PATH = "public/core/v3/objects"` |
| D-3：`build_v3_url` 遇 `/saas` 后缀时**剥离** | V3 public API 与 V2 一样**位于 `/saas` 下** | `build_v3_url` 改为**确保** `/saas` 前缀（不再剥离） |
| query.py：`list_tasks` 用 `nextPageToken` 分页 | 响应**无 `nextPageToken`**；仅 `{count, objects}`，`pageSize` 上限 200 | 改用 **`skip` 偏移**分页，按 `count` 判停 |
| query.py：`q` 追加 `AND type=="<t>"` | **大写 `AND` → 400**（parse error） | 改为**小写 `and`** |
| query.py：`get_task_detail` 先 V3 `name==... AND location==...` 消歧 | `q` 仅支持 `location`/`type`；**`name==`/`path==` → 400** | **取消 V3 按名消歧**，直接走 V2 by-name；`AmbiguousTaskError` 路径 v0.1 不再触发 |
| query.py：task 取 `item.get("name")` | V3 对象**无 `name`/`status` 字段**，名在 `path` 末段 | 新增 `_object_name()` 从 `path` 取名；`status` 取不到留空 |
| list_tasks `task_type="ALL"` 单查 `location==` | 文件夹含 1641 个 `MAPPING` 等**非任务对象**（总 4599） | `ALL` 按 **DSS/MTT/WORKFLOW 三类分别查询合并**（847+1873+37=2757） |

### 实测确认（与本 ADR 一致，无需改动）

- D-2 认证：`USER_SESSION` 值可直接作 `icSessionId`（V2）/`INFA-SESSION-ID`（V3）；用 `Cookie:` 头方式则 **401**。
- D-3 `build_v2_url`：`/saas` 前缀、不重复，行为正确。
- V2 详情端点 `dsstask/name/<name>`、`connection/<id>` 均 200；connection ID→名称解析成功。
- 任务类型枚举串 `DSS`/`MTT`/`WORKFLOW` 与 v3 type 串一一对应。

### 新增数据可得性限制（非缺陷）

- **DSS 详情不返回 runtime environment / agent**（仅空的 `srcRuntimeAttrs`/`tgtRuntimeAttrs`），故 `get_task_detail` 的 `runtime_environment.name`（Amendment 3 前称 `runtime_agent`）对 DSS 恒为空（best-effort，不报错）。
- MTT / WORKFLOW 的 runtime / connection 字段结构尚未深度实测，列入 v0.2 待确认。

### 健壮性补强

- `smoke_test.py` / `iics_cli.py` 启动时 `sys.stdout.reconfigure(encoding="utf-8")`，避免在 GBK/cp936 控制台输出 `✅`、中文时 `UnicodeEncodeError`。

### 对「升级至 v0.2 的前提条件」的影响

- 「V3 路径、Login URL 用新账号重新验证」中的 **V3 路径已在当前 SSO 账号下确认**为 `/saas/public/core/v3/objects`；专用服务账号到位后仍需复核是否一致。
- 其余前提（专用账号、`from_env()` 实测、Login URL）保持未决。

*相关提交：`c5ba5d7`（client 实测对齐）、`6759e1b`（IICS_API_NOTES.md）。*

---

## Post-ADR 补充（v0.1.1，只读稳定性补强）

v0.1.1 是 **v0.1 只读边界内的稳定性补强，不是 v0.2**：不新增任何写操作 / 子命令，不改
API 契约（V3 端点、`skip` 分页、小写 `and`、V2 by-name 均不变），仅提升长时间大 folder
`export --details` 的网络容错。

- **导出列精简**：`Task Summary` 增加 `source_objects` / `target_object` 两列；移除
  `Field Mapping` / `Truncation Risk` 两个 sheet。
- **`_request()` 容错**：新增默认 timeout `(10, 60)`（调用方显式传入则尊重）；对
  `Timeout` / `ConnectionError` / HTTP `429/502/503/504` 最多 3 次尝试、backoff 1s→2s；
  `400/403/404` 等业务错误不重试；`401` 仍走 `handle_401()`。重试日志仅 stderr，不含 token。
- **`export --details` 单点容错**：单个 `get_task_detail` 失败不再中断整批，记入
  `failed_details`（stdout JSON 与 JSON 文件均含），Excel 增加 `Export Errors` sheet；
  `Task Summary` 保留失败 task 行；`rows` 恒等于 task 总数（便于与 `list-tasks count` 对比）。

*相关提交：`3be0e63`（导出列精简）、`4cf2809`（`fix: harden readonly export against transient failures`）。*

---

## Amendment 2（v0.1.x，MTT 跨 folder 同名消歧改进）

**性质**：v0.1 只读边界内的改进，**不是 v0.2**，不新增写操作 / 顶级子命令 / 依赖。仅针对
**MTT** 引入官方 FRS 详情路径；DSS / WORKFLOW 行为完全不变。

### 实测边界

1. V3 objects 枚举返回的 id 是 **FRS ID**（示例 `fABfm4RCYqVfuVEi9e8BGb`）。
2. V2 classic ID（示例 `000LVU0I0000000004TZ`）与 FRS ID 是**不同体系**。
3. `GET /saas/api/v2/mttask/frs/{id}` **只接受 FRS ID**（classic 传入失败）。
4. 裸 `GET /saas/api/v2/mttask/{id}` 实测 FRS 与 classic **均可返回**，但这是**未在官方文档
   明确保证**的兼容行为，**不作为项目默认路径**。
5. 项目强制采用：**V3 folder 枚举 → FRS ID → V2 `mttask/frs/{frs_id}`**。

### 契约变更

- `get_task_detail` 签名新增 `task_id` / `task_id_type`（**仅 MTT 使用**）：
  `get_task_detail(client, task_name="", task_type="", folder_path="", task_id="", task_id_type="", resolve_connections=True)`。
- **MTT name 查询必须提供 folder**（缺 folder → `MissingFolderError`）；用户 CLI **不支持 MTT `--id`**。
- **内部 task_id 调用必须显式** `task_id_type="frs"`（缺失/`classic`/其它 → `InvalidIdTypeError`，不发 HTTP）。
  **不做** `task_id.startswith("000")` 之类字符串启发式判断，严格依赖显式契约。
- **task_id 与 task_name/folder 不得同时出现**（→ `ConflictingArgumentsError`，不枚举、不发 HTTP）。
- **DSS / WORKFLOW 不支持** `task_id` / `task_id_type`（传入 → `UnsupportedArgumentError`）；行为保持不变。
- MTT 成功返回顶层 `id`（FRS）/ `detail_route: "v2-mttask-frs"`。
  **（注：此描述已被 Amendment 3 修正——顶层 `id` 实为 classic V2 ID，FRS 拆为独立
  `frs_id` 字段，`id_type` 已删除；详见 Amendment 3。）**
- **export**：MTT 复用 list_tasks 已返回的 FRS object ID，按 `task_id + task_id_type="frs"` 调 FRS，
  每 MTT 一次 `mttask/frs/{id}`、不重复枚举、不传 name/folder（返回后回填）；单条失败不中断整批。

### 已知限制（更新）

- **MTT 跨 folder 同名歧义已解决**（FRS + folder 内 FRS object ID 唯一）。
- **DSS / WORKFLOW 跨 folder 同名仍是已知限制**（继续走 by-name，MTT 特例不推广到这两类）。

### 已验证 / 待确认

- MTT `mttask/frs/{frs_id}`：运维方实测报告可用（FRS 成功 / classic 失败）。
- DSS / WORKFLOW：实测将 V3 FRS object ID 传入 `dsstask/{id}` / `workflow/{id}` 返回 **500**——
  证明 FRS→V2 by-id 是 **MTT 类型特例**，不得推广。详见 `docs/IICS_API_NOTES.md`。
- 本仓库对 MTT FRS 路由有 fake 测试（Test A–K）覆盖；真实字段级验证建议按 Runbook MTT 专项跑一次。

*相关提交：`1de1f94`（`fix: resolve MTT details through FRS endpoint`）。*

---

## Amendment 3 — MTT Metadata Enrichment & Export Row Model

**性质**：v0.1 只读边界内的信息增强，**不是 v0.2**，不新增写操作 / 顶级子命令 / 依赖。仅
增强 MTT 查询与导出；DSS / WORKFLOW 详情查询路径完全不变。

### 变更点

1. **MTT 查询字段增强**：归一化 detail 增补 task_name/task_type/task_location/task_updated_on、
   runtime_environment、task_mapping、transformations、parameter_file、advanced_options、
   enrichment_warnings。
2. **Mapping by-ID enrichment**：`MTT.mappingId → GET mapping/{id}` 取 name / updated_on；
   Mapping Location 当前不可得（见 §13）。
3. **Runtime Environment by-ID enrichment**：`MTT.runtimeEnvironmentId → GET runtimeEnvironment/{id}`
   取 name。**不使用 agentId** 冒充 Runtime Environment。
4. **`runtime_agent` → `runtime_environment`**：公开 schema/export/测试/文档统一更名；结构为
   `{"id": <rtId or null>, "name": <resolved or null>}`。内部抽取函数改名 `extract_runtime_env_name`。
5. **classic `id` 与 `frs_id` 分离**：MTT FRS payload 顶层 `id` 是 **classic V2 ID**；
   V3/FRS 请求用的 id 记为 `frs_id`。**删除误导性的 `id_type`**。
6. **transformation 只来自 MTT `parameters[]`**：不使用 Mapping parameters；不取
   `parameters[0]`/`[-1]`/first_source/last_target；不按 Connection ID 去重。
7. **MTT parameters 内联展开到 Task Summary**：每个有效 Source/Target parameter = 1 Excel 行，
   任务级字段每行重复，**不 merge cells**。
8. **不新增 MTT 专属 sheet**：Excel 只有 `Task Summary`（业务数据主 sheet）+ `Export Errors`
   （错误/警告 sheet）。
9. **`task_count` 与 `rows` 分离**：task_count == list_tasks.count；rows == 物理数据行数
   （MTT 展开后可 > task_count）。完整成功 = task_count 一致 **且** failed_details_count == 0。
   **不再用 `rows == task_count` / `rows == list_tasks.count` 判断完整性**。
10. **Mapping / Runtime / Connection process-local cache**：一次 export 内同一 id 只真实请求一次
    （复用 ConnectionCache，新增 ObjectCache 供 mapping/runtime）；不做磁盘持久化。
11. **main-detail failure 与 enrichment warning 分级**：`mttask/frs/{id}` 失败 → failed_details
    （进 Export Errors、failed_details_count +1）；`connection/mapping/runtimeEnvironment` 失败
    → enrichment warning（字段留空、记 warning、**不**增加 failed_details_count、不终止批量）。
12. **Mapping Location 当前不可得**：`task_mapping.location = null`，Excel 空白；**禁止**等于
    Task Location。未来或经 `mapping.assetFrsGuid ↔ V3 MAPPING id` 验证，本次不实现、不猜测。
13. **multi-object parser 已做数组安全处理**：`extendedObject.objects[]` 保序去重、支持多值
    （Excel 同格换行 + wrap text）；但真实样本均为 `singleMode=true`、长度 1，**多对象场景尚未
    真实验证**。

### 对正文旧契约的反向修正

已在正文同步修正：`get_task_detail` 契约（id/frs_id/去除 id_type、runtime_environment、
transformations 等）、`export_results` 契约（task_count/rows 分离、双 sheet）、Smoke Test 4、
D-4/export 流程、Amendment 2 中 `id（FRS）/id_type` 的描述均标注被本 Amendment 修正。

### Task Summary 列（22 列；**Amendment 7 起为 MTT 专属 `MTT_HEADERS`**）

> Amendment 4 曾为 DSS/MTT 共用；**Amendment 7 起 DSS 拆出独立 11 列 `DSS_HEADERS`**，此 22 列
> 仅用于 MTT。原 23 列含 `Mapping Location`，已被 Amendment 4 删除（始终为空、无业务价值）。

`@type, id, orgId, Task Name, Task Type, Task Location, Runtime Environment, Task Updated On,
Mapping Name, Mapping Updated On, Transformation Name, Source Connection,
Source Object, Custom Query, Target Connection, Operation, Target Object,
Parameter File Directory, Parameter File Name, Pre-Processing Commands, Post-Processing Commands, Status`

### 已验证 / 待确认

- MTT `id`/`frs_id` 分离、runtime/mapping enrichment、parameters 展开、缓存、失败分级：
  本仓库 fake 测试 A–N 覆盖。
- 真实字段级验证（`runtime_environment.id == raw.runtimeEnvironmentId`、Mapping Name 非空等）
  建议按 Runbook §6.3 用真实 MTT task 跑一次。
- 多对象 `extendedObject.objects[]`（length > 1）：防御性实现，**尚无真实样本验证**。

*相关提交：`af2bcec`（`feat: enrich MTT metadata and export rows`）。*

---

## Amendment 4 — MTT Transformation Name & Compact Query Output & Mapping Location Cleanup

**性质**：v0.1 只读边界内的小范围改进，不新增写操作 / 顶级子命令 / 依赖。DSS / WORKFLOW
detail route 与行为完全不变。

### 变更点

1. **Transformation Name 优先级修正**：真实样本 `s_IFA_V_PV_CAE_S_01` 证明 `parameter.label`
   可能只是 connection parameter 的 display label（如 `DBConnection_awsoracle`）。**新规则**：
   `parameter.name`（去首尾 `$`）**优先**，仅在 name 缺失/空时 fallback `parameter.label`。
   例：`$SQ_001$ → SQ_001`、`$T_BI_CALL2_DETAIL_VOD_STGDB$ → T_BI_CALL2_DETAIL_VOD_STGDB`，
   **不再**得到 `DBConnection_awsoracle`。
   **（反向修正 Amendment 3 中「label → name」的旧优先级——正文 `_transformation_name` 契约以此为准。）**
2. **`get-task MTT` 默认 compact 输出**：默认返回 `{task_count, rows, records[]}`，`records` 每条
   为与 Excel `Task Summary` 完全相同的业务字段（一个 transformation = 一条 record）；无有效
   transformation 时 `rows=1`、一条 task-level 基础行。**compact 不含** raw / frs_id /
   detail_route / enrichment_warnings / connection·mapping·runtime ID。
3. **新增 `--full`**：MTT 下 `--full` 返回 Amendment 3 的完整 normalized detail（诊断/API 分析用，
   只读）。仅 `task_type == MTT` 时按 `--full` 分支；DSS / WORKFLOW 保持现状（扁平 dict）。
4. **共享 row builder**：`tools/exporter.build_task_summary_rows(detail)` 为 Excel export 与 CLI
   compact **共用**，`get-task` 看到的字段/值 == Excel。`get_task_detail()` 返回值**不变**
   （compact/full 区分只在 CLI 输出层；smoke_test Test 4 仍直接断言完整 detail）。
5. **输出结构不对称（显式文档化）**：`get-task` 顶层结构因 task_type 而异——
   MTT → `{task_count, rows, records[]}`；DSS / WORKFLOW → 单个扁平 dict。脚本调用方须先判 task_type。
6. **删除 Excel / compact 的 `Mapping Location`**：Mapping V2 无可靠 path/location/folder，该列始终
   为空、无业务价值。Task Summary 由 23 列 → **22 列**；compact records 同样不含。
7. **normalized `task_mapping.location = null` 保留**（供 `--full` 与未来 enrichment）；本次仅删
   业务展示层，不删 schema 字段。**禁止**推测 Mapping Location（不得 = Task Location、不据 name/
   folder/mappingId 猜测、不实现 `assetFrsGuid → V3 MAPPING → path`——仍属待验证）。
8. **空值规则（compact JSON）**：可选字段键缺失（parameterFileDir/preProcessingCmd/enrichment
   失败的 Mapping/Runtime 等）→ `null`；transformation 对侧不适用字段（Source 行的 Target 侧、
   Target 行的 Source 侧）→ `""`。Excel 中两者均显示空白。

### 不变项

MTT FRS 路由、runtime/mapping/connection enrichment、缓存、`task_count`/`rows` 语义、
DSS/WORKFLOW route、workbook 结构（`Task Summary` + `Export Errors`，不新增 sheet、不恢复
`Field Mapping`/`Truncation Risk`、不实现 merge cells）——全部保持 Amendment 3。

### 已验证 / 待确认

- Transformation Name 新规则、compact/full 分离、共享 row builder、22 列、Mapping Location 删除：
  fake 测试 A–L 覆盖。
- Mapping Location 仍 **unavailable**，未解决、不推测。

*相关提交：`cfb0332`（`fix: refine MTT transformation and query output`）。*

---

## Amendment 5 — WORKFLOW Child Task Export

> **⚠️ 本节的 WORKFLOW 导出列结构已被后续 Amendment supersede**：本节步骤 3/4 描述的
> WORKFLOW 列（含取自 export `folder_path` 的 `folder` 列）先由 Amendment 8 去掉 `folder`
> 列（`build_workflow_summary_rows(rec)` 去 `folder_path` 参数与父 `folder` 字段），
> 随后又被 **Amendment 9** 再次调整。
> **当前 authoritative 的 WORKFLOW 导出 schema 以 Amendment 9 为准**——本横幅不重复维护列数，
> 以免 schema 再变时多处失同步。
> WORKFLOW child 展开 / count / detail route / get-task 行为自本节起未变。

**性质**：v0.1 只读边界内的改进，仅针对 `export <FOLDER> --type WORKFLOW --format excel`。
MTT / DSS 的查询与导出契约、WORKFLOW 的 `get-task` 与 detail route 均不变。

### 变更点

1. **WORKFLOW detail route 保持 `workflow/name/{name}`**（不改；不得用 V3 FRS object ID 调 by-id）。
2. **`raw.tasks[]` 是 WORKFLOW child row 的权威来源**；**一个 child task = 一个 Excel 行**。
3. **父 workflow 字段逐行重复**：`Task Type`（恒 `WORKFLOW`）/ `folder`（来自 export 的
   `folder_path`，非 detail）/ `orgId` / `id`（workflow V2 classic id）/ `Task Name` / `updateTime`。
4. **WORKFLOW Excel 使用独立 9 列 schema**（有意与 MTT/DSS 通用列命名风格不同、不对齐）：
   `Task Type, folder, orgId, id, Task Name, updateTime, tasks.taskId, tasks.type, tasks.name`。
5. **不查询 child MTT/DSS 详情**（每个 WORKFLOW 仅 1 个 detail 请求；child 字段仅取自 `raw.tasks[]`；
   缺失 → `""`，非 failed / 非 enrichment failure）。
6. **zero-child / 缺 tasks / detail 失败**：仍保留至少 1 行（child 三列空）；detail 失败还计入
   `failed_details` + `Export Errors`，fallback 行 Task Name 取 list_tasks 已知 name，其余留空。
7. **count 语义**：`rows = Σ max(1, len(workflow.tasks))`（含 fallback 行按 1 计入）；`rows` 可
   `> task_count`。完整成功仍为 `task_count == list_tasks.count` 且 `failed_details_count == 0`；
   **不得**用 `rows == task_count`。
8. **workbook 结构不变**：`Task Summary` + `Export Errors`，不新增 WORKFLOW 专属 sheet。

### 对正文旧契约的反向修正

Amendment 3/4 正文「DSS / WORKFLOW：1 task = 1 行」仅适用于**通用列 schema**（DSS / MTT /
`--type ALL`）。`--type WORKFLOW` 单类型导出改用上述 9 列 child-expanded schema，
**WORKFLOW 不再是 1 task = 1 行**（1 workflow → N 行，N = max(1, child 数)）。

### `--type ALL` 决策（Li 确认：WORKFLOW 单独 sheet）

`ALL` 原实现为「list_tasks(ALL) 合并三类 → 单张通用列 Task Summary」。WORKFLOW 的 9 列 child
展开与通用列**字段集合不兼容**，无法在同一张表无损共存。经 Li 确认采用**分表**方案：

- **`Task Summary`**（通用 22 列）：DSS + MTT 行；**ALL 下 WORKFLOW 不进此表**。
- **`Workflow Tasks`**（WORKFLOW 专用 9 列，child 展开）：ALL 中的 WORKFLOW 记录；无 WORKFLOW 时不生成。

> **⚠️ 已被 Amendment 7 / 8 supersede**：ALL 现改为三张独立业务 sheet
> `MTT Task`(22) → `DSS Tasks`(11) → `Workflow Tasks`(8，Amendment 8 去 folder) → `Export Errors`
> （DSS 不再与 MTT 同表）。
- **`Export Errors`**：不变。
- `rows` = 两张业务表物理数据行数之和；`task_count == list_tasks.count`。

`--type WORKFLOW` 单类型导出仍是**单张 9 列 `Task Summary`**（不新增 sheet），与 ALL 下的
`Workflow Tasks` 内容一致、仅所处 sheet 不同。

### 已验证 / 待确认

- WORKFLOW child 展开、9 列 schema、zero-child、缺失字段、无 child API、counts、fallback、
  MTT/DSS/WORKFLOW-get-task 回归：fake 测试 A–O 覆盖。
- 真实字段级验证建议按 Runbook 用 `TF_DI_JPN_GDM_BI_DWH_AYU_M_JGI_MST` 跑一次。

*相关提交：`17f0333`（`feat: expand workflow child tasks in export`），含 ALL 分表实现（Li 已确认）。*

---

## Amendment 6 — Selected Task List Export

> **⚠️ 部分已被 Amendment 8 supersede**：本节步骤 2/3/4 的「所有类型输入 `{name,type,folder}`」、
> exact-tuple 去重键 `(type,folder,name)`、DSS/WORKFLOW `ambiguous_input` 分支——均已由
> Amendment 8 修正：folder 仅 MTT；去重键按类型（DSS/WORKFLOW=`(type,name)`）；`ambiguous_input` /
> `detect_input_ambiguities` 已删除；by-name 风险 warning 改新措辞。MTT discovery / 缓存 / 三 workbook /
> summary 语义不变。以 Amendment 8 为准。

**性质**：v0.1 只读边界内的 integration feature。新增确定性 selected-task **orchestration**
层，复用现有 DSS/MTT/WORKFLOW 详情、exporter、缓存；不新增写操作、不改现有子命令语义、
不重新实现任何 parser/exporter/resolver、不改 `--type ALL`。

### 变更点

1. **新增第 5 个只读子命令 `export-selected`**：`--input <json> --output-dir <dir>`。
   `export <folder> --type X` 仍是「folder 全量」；`export-selected` 是「只导出 list 中指定 task」。
2. **正式输入只接受 JSON**：top-level array，每项 `{name, type, folder}`（string）。标准化：
   name/folder `strip()`（**不改大小写**）、type `strip().upper()`。任何 schema/JSON 错误 →
   **fatal** `SelectedTaskInputError`（**先于任何 IICS 请求**、不生成部分文件、exit 1）。
3. **exact-tuple 去重**：key = `(type, folder, name)`，保序；报告 `input_count / unique_count /
   duplicates_removed`。
4. **DSS / WORKFLOW input-level ambiguity**：list 内同 type+同 name 但不同 folder（by-name 无法
   消歧）→ 请求前标 `ambiguous_input`、**不调用** by-name detail、两条都计入该 type failed。
   本版**不做 org 级同名扫描**；只要 list 含 DSS/WORKFLOW，summary warnings 各追加 by-name 风险提示。
5. **MTT 每 folder 一次 V3 discovery**：按 folder 分组，每 unique folder 只调 `list_tasks(folder, MTT)`
   **一次**（函数级；folder 内 >200 时内部按 `skip` 分页属正常，见 IICS_API_NOTES §4），建
   `name → [candidates]` index；0→`selected_task_not_found`、>1→`ambiguous_mtt`（candidates
   含 id/path/name/type）、1→取 FRS object ID → 现有 FRS detail（`task_id_type="frs"`，
   **不再** name+folder 二次 discovery）。MTT 跨 folder 同名由此正确区分（非 ambiguous_input）。
6. **整个 batch 共用一个 `IICSClient`**：Connection/Mapping/Runtime process-local 缓存跨类型复用。
7. **三个独立 workbook**：`selected_{DSS,MTT,WORKFLOW}_<同一 batch timestamp>.xlsx`，各调对应现有
   exporter（DSS/MTT/WORKFLOW 各自 schema、行模型不变）。某 type `requested==0` → 不生成文件、
   `file_path=null`。**不生成 ALL 单一 workbook**。
8. **计数语义**：per-type `requested`（去重后该类型数，含 ambiguous/not_found/detail 失败）、
   `success`（成功进入 exporter 的 task 数）、`failed = requested - success`（`success+failed==requested`）、
   `rows`（物理数据行，MTT/WORKFLOW 可展开多行）。全局 `Σ requested == unique_count`。
9. **失败分级**：主详情失败 → failed + Export Errors；MTT enrichment 失败 → enrichment warning，
   **不**计 failed。`failures[]` 保留真实 `error_type`（不压成笼统 detail_error）；连续 ≥5 次同一
   session 相关（`RuntimeError`）→ summary warnings 追加 token 过期提示（不 abort）。
10. **单 task 失败不中断整批**；唯一 fail-fast = 整个输入无效。**退出码只看 summary 顶层 `error`**
    key（`failures[].error` 属嵌套、不触发 exit 1）。**stdout summary 不含** raw/transformations/
    mapping·runtime·connection response 等完整 detail。
11. **Mode A / Mode C 边界**：Agent 只做输入理解 + 生成临时 JSON + 调用 + 转述 summary；
    **不**逐条 get-task 搬 detail、不自造 Excel、不自行统计（以 Python summary 为准）。

### 对正文旧契约的反向修正

正文「4 个只读子命令」已更新为「5 个（含 `export-selected`）」；`export-selected` 只读、无写路径。

### 已验证 / 待确认

- 校验/标准化/去重、DSS·WORKFLOW ambiguous_input、MTT 每 folder 一次 discovery + FRS 直取、
  只导出 selected（不含 folder 其它对象）、三独立文件、counts、跨类型共享缓存（DSS↔MTT 两种顺序）、
  现有 export/get-task/ALL 无回归：fake 测试 A–X + U-1 + CLL 覆盖。
- 本 feature **未解决** DSS/WORKFLOW org 级同名 ambiguity（by-name 固有风险，summary warning 提示）。

*相关提交：`1aaff25`（`feat: add selected task list export`）。*

---

## Amendment 7 — DSS Dedicated Export Schema

> **⚠️ 本节引用的 WORKFLOW 列数已被后续 Amendment supersede**：本节写作时 WORKFLOW 为
> 9 列（含 `folder`），随后由 Amendment 8 去掉 `folder` 列、再由 **Amendment 9** 再次调整。
> **当前 authoritative 的三张业务 sheet schema（含 ALL 的 sheet 顺序与各自列数）以
> Amendment 9 为准**——本横幅不重复维护列数。
> 本节确立的「DSS 脱离 MTT schema、使用独立 DSS_HEADERS」这一决策本身未被推翻。

**性质**：export/schema + CLI presentation change，**不改查询层**。不新增 API、不新增写操作、
不改 DSS detail route。

### 变更点

1. **DSS 脱离 MTT 22 列 schema，使用独立 11 列**（顺序固定）：
   `@type, id, orgId, Task Name, Task Type, Task Updated On, Source Connection,
   Source Object, Operation, Target Connection, Target Object`。
   来源：`raw["@type"/"id"/"orgId"/"updateTime"/"operation"/"targetObject"]`、`Task Name`=detail
   name、`Task Type`=固定 `"DSS"`、Source/Target Connection=现有 `detail["source_connection"/
   "target_connection"]`（已解析 name）、`Source Object`=`raw["sourceObjects"][0]["name"]`。
   **不显示** Task Location / Runtime Environment / Mapping* / Transformation Name / Custom Query /
   Parameter File* / Pre-/Post-Processing / Status。
2. **MTT 22 列正式收窄为 MTT 专属**：常量 `_COLS` 更名 `MTT_HEADERS`（`_COLS` 保留为内部私有别名）。
3. **DSS `1 task = 1 row`**：`build_dss_summary_rows()`，`len==1`；不做 MTT 式 transformation 展开。
4. **`sourceObjects[]` 只取第一项** `sourceObjects[0].name`（产品决策）；有第二项及后续 → 忽略、
   不告警、不展开、不多值。缺失/空/非 dict/无 name → `""`。
5. **`export --type DSS`**：`Task Summary` 恰 11 列（`1 task = 1 row`，DSS-only 时 `rows==task_count`
   为自然结果，**不**作为通用完整性契约）。
6. **`export --type ALL` 改为三个独立业务 sheet（固定顺序）**：`MTT Task`(22) → `DSS Tasks`(11) →
   `Workflow Tasks`(9) → `Export Errors`。DSS 不再与 MTT 同表；无 union、无空列膨胀。
   `rows` = 三张业务表物理行数之和。**（supersede Amendment 5 的「Task Summary(DSS+MTT) + Workflow
   Tasks」ALL 结构。）**
7. **`get-task <name> DSS` 默认 compact**：`{task_count:1, rows:1, records:[{…11 字段}]}`，record keys
   恰 == `DSS_HEADERS`；`--full` 输出完整 normalized detail（不变）。CLI 与 Excel 共用
   `build_dss_summary_rows()`；`compact_record(row, columns=…)` 泛化（MTT 默认，DSS 传 DSS_HEADERS）。
   WORKFLOW get-task 保持不变。返回结构因 task type 而异（MTT/DSS→records、WORKFLOW→扁平 dict）。
8. **`export-selected` 自动继承**：`selected_exporter` 仍 `export_results(export_type="DSS")`，
   `selected_DSS_*.xlsx` 自动用 11 列；MTT/WORKFLOW schema 与 selection/dedup/ambiguity/discovery/
   summary/cache 契约**不变**（selected_exporter.py 无业务改动）。

### 对正文旧契约的反向修正

- 「22-column Task Summary」不再是 DSS/MTT 共用；**DSS = 11 列（Amendment 7）、MTT = 22 列
  (MTT_HEADERS)**。正文相应处已标注 supersede。
- Amendment 4「DSS / WORKFLOW：1 task = 1 行 … 通用列」中的 DSS 部分被本 Amendment 修正为独立 11 列
  （仍 1 task = 1 row）；MTT 22 列部分不变。
- Amendment 5 ALL 结构（`Task Summary`(DSS+MTT) + `Workflow Tasks`）被本 Amendment 的三表结构替代。

### 未改动（回归保证）

DSS route `dsstask/name/{name}`、MTT FRS 路由、WORKFLOW `workflow/name/{name}`、
`query.py` normalized contract、`selected_exporter.py` 编排、MTT 22 列 / WORKFLOW 9 列、
MTT/WORKFLOW 行展开与 count 语义、单类型 MTT/WORKFLOW 的 sheet 名 `Task Summary`——全部不变。

### 已验证

fake 测试 A–R：DSS 11 列/顺序、正常行、sourceObjects 空/缺失/多值(取首)、可选字段、connection
dict 复用、DSS-only workbook、MTT/WORKFLOW 单类型回归、ALL 三表结构+schema、ALL rows 求和、
DSS compact/full、MTT/WORKFLOW get-task 回归、selected DSS 11 列、connection 失败保留行。

*相关提交：本次（`feat: add dedicated DSS export schema`）。*

## Amendment 8 — DSS / WORKFLOW Folder Semantics Cleanup

> **⚠️ 大部分已被 Amendment 9 supersede**：Amendment 8 的核心结论「DSS/WORKFLOW get-task /
> selected 不接受 folder、folder 不属于任何 detail identity、Export Errors DSS/WF folder 为空、
> WORKFLOW 8 列」均被 Amendment 9 逆转——三类型重新要求 folder（用于 FRS discovery 取 frs_id），
> WORKFLOW 恢复 9 列（新增 FRS ID，非旧 folder 列）。以 Amendment 9 为准。以下保留历史记录。

**性质**：CLI / 输入契约清理 + schema 修正。**不新增 API、不改 detail route、不重写 exporter、
不涉及写操作。**

**核心原则**：**「folder 作为枚举范围」≠「folder 作为详情身份」**。
- **MTT**：folder 是真实身份 / discovery 数据（V3 folder 枚举取 FRS object ID → FRS）——**不变**。
- **DSS / WORKFLOW**：detail 按 name 解析（`dsstask/name/{name}` / `workflow/name/{name}`），
  detail 层**无可靠 folder identity**，不得从用户输入伪造或模拟。

### 变更点

1. **`get-task DSS` / `get-task WORKFLOW` 不再接受 `--folder`**：传入非空 `--folder` → 结构化
   `UnsupportedArgumentError`（**先于任何 HTTP**，exit 1）。DSS 文案：
   `--folder is not supported for DSS get-task because DSS details are resolved by name only.`
   （WORKFLOW 同构。）**MTT `--folder` 仍必需**，缺失 → `MissingFolderError`（不变）。
2. **`get_task_detail()` 亦拒绝**：`task_type in ("DSS","WORKFLOW")` 且 `folder_path` trim 后非空
   → `UnsupportedArgumentError`、不发 HTTP。`folder_path == "" / None / 纯空白` = 视为未提供。
   MTT folder 行为完全不变。
3. **DSS / WORKFLOW normalized detail 不含合成 `folder` 键**（默认 `"folder" not in detail`；
   不用 `folder=""` / `folder=null` 代替）。MTT 保留 `task_location` / `frs_id` 等。
4. **普通 `export <folder> --type DSS|WORKFLOW`**：folder 仍作 `list_tasks()` 枚举范围，但**不再**
   把 `folder_path=args.folder` 传给 `get_task_detail()`（只传 name/type）。MTT 保留 folder /
   FRS object ID 流程。
5. **`export-selected` 输入 schema 按类型区分**：MTT `{name,type,folder}`（folder 键必须存在、
   字符串、trim 后非空，否则 `SelectedTaskInputError`）；DSS / WORKFLOW `{name,type}`。
6. **DSS / WORKFLOW 兼容**：`folder:""` 或纯空白 → 归一化丢弃 key；`folder:"非空"` → **快速失败**
   `SelectedTaskInputError`（`folder is only supported for MTT selected tasks`），0 次 API、0 workbook、
   exit 1。校验全部先于任何 IICS API。
7. **归一化 selected item**：MTT 保留 folder；DSS / WORKFLOW 完全丢弃 folder（非 `folder=""`）。
8. **去重键按类型**：MTT `(type, folder, name)`；DSS / WORKFLOW `(type, name)`（helper
   `_selection_identity(item)`）。保序、保 first-occurrence；`input_count` / `unique_count` /
   `duplicates_removed` 语义不变。MTT 跨 folder 同名**永不**被去重。
9. **删除 `detect_input_ambiguities()`** 及全部 `ambiguous_input` 逻辑 / 分支 / 文案 / 测试（无死
   兼容路径）。
10. **保留 org 级重名警告，但换新措辞**：DSS →
    `DSS details are resolved by name only; folder identity is unavailable and duplicate names
    elsewhere in the organization cannot be disambiguated.`（WORKFLOW 同构）。旧措辞
    "folder identity cannot be guaranteed" 已废弃；亦不使用 "folder identity may be inaccurate"。
11. **selected DSS / WORKFLOW 查询**：`get_task_detail(client, task_name=…, task_type="DSS"|"WORKFLOW")`
    —— 无 `folder_path`。失败对象：DSS / WORKFLOW `{type, name, error_type, error}`（**无 folder**）；
    MTT 保留 folder。
12. **`WORKFLOW_HEADERS` 9 → 8 列**（去 `folder`）：`Task Type, orgId, id, Task Name, updateTime,
    tasks.taskId, tasks.type, tasks.name`。`build_workflow_summary_rows(rec)`（去 `folder_path` 参数
    与父 `folder` 字段）；child 展开 / 零 child / fallback 逻辑不变。WORKFLOW workbook 处处 8 列
    （单类型、ALL 的 Workflow Tasks、selected_WORKFLOW）。ALL sheet 顺序不变：
    `MTT Task`(22) → `DSS Tasks`(11) → `Workflow Tasks`(8) → `Export Errors`。
13. **`Export Errors.folder` 表头保留**，但**值按类型**：MTT error folder = MTT 身份 / discovery
    folder；DSS / WORKFLOW error folder = `""`。修正发生在**源头**（`iics_cli._cmd_export`：
    `"folder": args.folder if ttype == "MTT" else ""`），非 exporter fallback。普通
    `export --type WORKFLOW|DSS` 的 detail 失败行 `Export Errors.folder` 仍为 `""`（即使枚举用了
    folder）。`_EXPORT_ERROR_HEADERS` 的 `folder` 列保留（MTT 仍需要）。

> **关于 `exporter.py` 第 348 行（`_collect_enrichment_warnings` 的 folder 归属逻辑
> `ew.setdefault("folder", rec.get("task_location") or rec.get("folder", ""))`）：未修改**，其符合本
> Amendment 要求，是实现要求 3（DSS/WORKFLOW detail 不再含合成 folder）完成后的**自动结果**，而非遗漏项——
> DSS/WORKFLOW rec 既无 `task_location` 亦无 `folder`，故自然归为 `""`；MTT rec 有 `task_location`。

### 对正文旧契约的反向修正（supersede）

- **Amendment 2 / 3「DSS / WORKFLOW 保持 by-name；跨 folder 同名是已知限制」**：本 Amendment 明确
  DSS / WORKFLOW detail 层 folder identity **不可用**，`--folder` / selected folder 非空即拒；不再以
  "已知限制" 形式默默接受用户 folder。
- **Amendment 5「WORKFLOW Excel 独立 9 列（含 folder，取自 export 的 `<FOLDER>`）」** →
  **8 列、无 folder**（`build_workflow_summary_rows` 去 `folder_path`）。
- **Amendment 6「selected 输入 `{name,type,folder}`（所有类型）、`(type,folder,name)` 去重、
  ambiguous_input 分支、"folder identity cannot be guaranteed" 警告」** → folder 仅 MTT；
  去重键 `_selection_identity` 按类型；删 `detect_input_ambiguities`；警告改新措辞。
- **Amendment 7 ALL 顺序 `Workflow Tasks(9)`** → `Workflow Tasks(8)`。

### 未改动（回归保证）

DSS route `dsstask/name/{name}`、WORKFLOW route `workflow/name/{name}`、MTT FRS 路由与
folder/FRS 流程、MTT `MTT_HEADERS`(22)、`DSS_HEADERS`(11)、DSS `1 task=1 row`、WORKFLOW child
展开 / 零 child / fallback 与 `max(1,len)` count、`compact_record` / `build_dss_summary_rows` /
`build_task_summary_rows`、enrichment warning 分级、缓存契约、`get-task WORKFLOW` 扁平输出、
DSS get-task compact/full——全部不变。

### 已验证

fake 测试 A–Z（37 断言，全 fake、无真实 IICS）：DSS/WF 非空 folder 拒绝（CLI 与 Python 层、不发 HTTP、
文案）、DSS/WF `folder=""`/空白视为未提供、detail 无合成 folder、MTT folder identity 保留、
`WORKFLOW_HEADERS` 8 列、`build_workflow_summary_rows(rec)` 单参无 folder、零 child、selected 校验
type-conditional（MTT 必需 folder / DSS/WF 缺省 / 空白丢弃 / 非空拒绝）、`_selection_identity`、
type-dependent dedup（6→4、MTT 跨 folder 同名保留）、`detect_input_ambiguities` 已删、`_process_by_name`
不传 folder_path、warning 新措辞、DSS/WF 失败对象无 folder、WORKFLOW/ALL Excel round-trip 8 列与
sheet 顺序、`_cmd_export` Export Errors folder 按类型（DSS=""/MTT=folder）、MTT selected 失败保留 folder。

*相关提交：`fix: clarify DSS and workflow folder semantics`。*

## Amendment 9 — Unified FRS ID Exposure for DSS / MTT / WORKFLOW

**性质**：查询结果与 Excel 业务导出统一双 ID 契约 + 三类型 folder 语义统一。**不新增 API、
不改 detail route、不改 ID 值、不改业务行为、不涉及写操作。**

**核心原则**：**FRS discovery identity ≠ V2 detail identity。**
- **MTT**：folder → FRS object ID → `mttask/frs/{frs_id}`，folder 同时是 detail identity（消歧）。
- **DSS / WORKFLOW**：folder → FRS discovery only（可靠取得对应 FRS object 的 `frs_id`）；detail
  仍走 V2 by-name（`dsstask/name/{name}` / `workflow/name/{name}`），**by-name detail 无法按
  folder 消歧**（组织内其它 folder 存在同名 task 时）。这是 by-name endpoint 限制，**不是 FRS 与
  classic ID 体系映射不确定**。

### 双 ID 正式契约

三种类型 normalized detail 与 Excel 统一：`id` = classic V2 ID、`frs_id` = FRS object ID。
不得用单个 `id` 同时表示两种 ID。MTT 原有 `id + frs_id` 模型推广到 DSS / WORKFLOW。

### 变更点

1. **`list_tasks()` 输出 `id` → `frs_id`**：V3 `/objects` 返回的 id 即 FRS object ID，统一命名
   `frs_id`（无 active compatibility alias）。所有调用方（`_cmd_export`、selected discovery、
   `_discover_frs_id`）改用 `frs_id`；AmbiguousTaskError candidates 用 `frs_id` key。
2. **共享 `_discover_frs_id(client, folder_path, task_type, task_name)`**：三类型复用同一
   folder discovery + exact-name 匹配 + 0/1/>1 判定（0→TaskNotFoundError、>1→AmbiguousTaskError
   candidates 用 frs_id、1→frs_id）。MTT/DSS/WORKFLOW 的 `get_task_detail` 均经此 helper。
3. **三类型 get-task 均需 `--folder`**：缺失 → `MissingFolderError`（0 HTTP、exit 1）。逆转
   Amendment 8 的「DSS/WORKFLOW 非空 folder → UnsupportedArgumentError」。
4. **DSS / WORKFLOW normalized detail 含 `id`（by-name response 的 classic id）+ `frs_id`
   （folder-scoped discovery）**。
5. **内部 FRS 直传（编排层专用）**：`get_task_detail(task_id=<frs_id>, task_id_type="frs")` 对三
   类型一致 —— MTT 走 `mttask/frs/{frs_id}`；DSS/WORKFLOW 走 by-name（需 task_name），`frs_id`
   回填、不二次 discovery。`task_id` 与 `folder_path` 冲突 → `ConflictingArgumentsError`；
   `task_id_type` 非 `"frs"` → `InvalidIdTypeError`（不发 HTTP）。CLI / selected 输入**不暴露**
   `task_id` / `task_id_type`。
6. **普通 export 复用初始 `list_tasks()` 的 frs_id**：每 task 以 `task_id=frs_id,
   task_id_type="frs"` 直传 detail，DSS/WORKFLOW **不再二次 discovery**。
7. **export-selected 输入 schema 三类型统一 `{name, type, folder}`**：folder 对三类型均必需、
   trim 后非空，否则 `SelectedTaskInputError`（0 API、0 workbook、exit 1）。逆转 Amendment 8 的
   type-conditional schema。
8. **selected dedup 键三类型统一 `(type, folder, name)`**：跨 folder 同名（含 DSS/WORKFLOW）
   分属不同 FRS object，不去重、**不重新引入 `ambiguous_input` 阻断**。每 unique (folder, type)
   只 discovery 一次。
9. **Excel 三业务 schema 各加 `FRS ID`（紧邻 `id`）**：MTT 22→**23**、DSS 11→**12**、
   WORKFLOW 8→**9**（WORKFLOW 在 `id` 后插入 FRS ID，不重排 orgId/id）。`id` 仍表示 classic V2 ID
   （本轮不重命名为 Classic ID，避免扩大 breaking change）。ALL 顺序不变：
   `MTT Task`(23) → `DSS Tasks`(12) → `Workflow Tasks`(9) → `Export Errors`。
10. **organization-level warning 更新措辞**：DSS/WORKFLOW → "resolved by name only. The
    supplied folder is used to resolve the FRS ID, but it does not disambiguate the V2 detail
    response when duplicate names exist elsewhere in the organization."
11. **Export Errors.folder 三类型统一记录 requested / FRS-discovery folder**（逆转 Amendment 8 的
    「DSS/WF 必须为空」）。DSS/WORKFLOW 的该值不代表 V2 detail 已按 folder 验证。

### 对 Amendment 8 的 supersede（明确对照）

| 旧 Amendment 8 | Amendment 9 |
|---|---|
| DSS/WF get-task 不接受 folder | DSS/WF get-task 重新要求 folder（FRS discovery） |
| DSS/WF selected 不接受 folder | DSS/WF selected 重新要求 folder |
| DSS/WF folder 不属于任何 detail identity | folder 是 FRS discovery identity；仍非 V2 by-name detail identity |
| Export Errors DSS/WF folder 为空 | 三类型统一记录 FRS-discovery folder |
| WORKFLOW 8 列（去 folder） | WORKFLOW 9 列（id 后加 FRS ID） |

### 不做 / 未改（回归保证）

不新增 `POST /public/core/v3/lookup`、不实现 `POST /public/core/v3/export`；detail route 全不变
（`mttask/frs/{frs_id}` / `dsstask/name/{name}` / `workflow/name/{name}`）；不新增 `dsstask/{frs_id}`
/ `workflow/{frs_id}`；不依赖裸 `mttask/{id}`；V3 返回的 ID 值本身不变；MTT transformation 展开 /
DSS 单行 / WORKFLOW child 展开与 count 语义、缓存、enrichment 分级——全部不变。

### 已验证

fake 测试 A–Q（31 断言，全 fake、无真实 IICS）：list_tasks frs_id（无旧 id）、三类型 get-task
folder discovery + id=classic/frs_id=FRS + 正确 route、三类型缺 folder → MissingFolderError(0 HTTP)、
DSS/WF 非空 folder 不再 UnsupportedArgumentError、frs 直传旁路(0 discovery)、Conflicting/InvalidId、
ordinary export 复用(单次 discovery)、selected 三类型 folder required、dedup (type,folder,name)、
跨 folder 同名两条保留无 ambiguous_input、warning 新措辞、Excel 23/12/9 且 FRS ID 紧邻 id、
id=classic/FRS ID=discovered、ALL 顺序、WORKFLOW child 2→2/0→1 每行重复 id+FRS ID、无 lookup/export、
无 dsstask/{frs}·workflow/{frs}。

### 列数订正 —— 当前 authoritative Excel schema（单一权威来源）

> **历史记录（已被 Amendment 11 更新）**：下表为 Amendment 9 时点的快照。自 Amendment 11 起，
> 当前列数权威来源为 Amendment 11 的「列数订正」小节（`MTT Task` = 26；DSS / WORKFLOW 不变）。

Amendment 8 后文档基线曾滞后（WORKFLOW 8）。**自 Amendment 9 起，本小节是三张业务 sheet
列数的唯一权威来源**，Amendment 5 / 7 / 8 中的列数均为各自当时的历史快照：

| 业务 sheet | 当前列数 |
|---|---|
| `MTT Task` | **23**（22 + `FRS ID`） |
| `DSS Tasks` | **12**（11 + `FRS ID`） |
| `Workflow Tasks` | **9**（8 + `FRS ID`，在 `id` 之后插入；与 Amendment 5 时代含 `folder` 的 9 列不是同一组列） |

`--type ALL` 的 sheet 顺序：`MTT Task` → `DSS Tasks` → `Workflow Tasks` → `Export Errors`。

*相关提交：本次（`feat: expose FRS IDs across task queries and exports`）。*

---

## Amendment 10 — Multi-Environment Non-Interactive Authentication (D-2 更新)

**日期**：2026-09-11
**状态**：**ACCEPTED**（实现 + 真实环境验收均已完成；不推翻 ADR-001 正文，作为 D-2 认证决策的修订）
**范围**：仅认证 / 环境层。业务 API 仍严格 GET-only，v0.1 只读边界不变。
**最终验收结论**：见文末「Amendment 10 — 最终验收结论」。

### 原前提（ADR-001 D-2）

```text
企业 SSO（Okta）
无专用 service-style credential
只能由运维人员手动从浏览器提取 USER_SESSION
IICS_SESSION_TOKEN 为唯一认证入口
```

### 新实测事实（Postman 验证，本 Amendment 的既定前提）

```text
POST https://dm-us.informaticacloud.com/ma/api/v2/user/login
     body: {"username": ..., "password": ...}
        ↓
返回 serverUrl + icSessionId（以及 orgId / orgUuid）
        ↓
同一个 icSessionId：
  V2 → icSessionId header
  V3 → INFA-SESSION-ID header
        ↓
V2 / V3 API 均 200
```

因此：**存在可用于 non-interactive 登录的 username/password 凭据**，
「只能手动 USER_SESSION」这一前提被推翻。V2 Login 与 V3 public API 共用同一 session，
不需要两套登录。

### 新方向（D-2 修订）

```text
multi-environment credential config
+ automatic Login
+ TOFU orgUuid pin
+ process-local session（不落盘）
+ 业务 401 自动 refresh once
```

### 决策要点（索引）

每条决策的完整展开见其后对应小节，此处仅列结论，避免重复陈述。

| # | 决策 | 详见 |
|---|---|---|
| 1 | **环境参数化**：`--env` → `IICS_ENV` → `MissingEnvironmentError`，永不默认 PROD，不猜测环境 | Environment selection 契约 |
| 2 | **Credential 所有权分离**：空值模板进 git，真实值 local-only 由使用者本人维护 | Credential 边界 |
| 3 | **Login URL 三级解析**：env override → 顶层 → `DEFAULT_LOGIN_URL`，三环境不重复配置 | Credential 边界 |
| 4 | **Login POST 隔离**：认证层唯一 POST，业务 `IICSClient` 仍严格 GET-only | 最终认证调用链 |
| 5 | **Login 响应最小化持有**：只保留 `icSessionId`/`serverUrl`/`orgId`/`orgUuid`/`id`，原始 response 不落任何长期对象 | Login 响应最小化持有 |
| 6 | **session = process-local**：不落盘、无 session cache；25 分钟 idle 主动刷新只是优化，401 才是权威失效信号 | SessionManager 与 session 生命周期 |
| 7 | **Environment Pin（TOFU + 人工确认）**：`orgUuid` 为唯一强身份；未 pin 或不匹配即阻断业务 API，禁止静默覆盖 | Environment identity 与 TOFU pin |
| 8 | **401 / 403 语义分离**：业务 401 → refresh once + 重建 URL/Header + retry once；403 不 refresh | 401 / 403 与重试策略 |
| 9 | **重试计数分离**：认证 refresh ≤1，与业务 / Login 的 transient retry 各自独立计数 | 401 / 403 与重试策略 |
| 10 | **不实现**：`SessionState` enum / 状态机、跨进程 session cache、JWT `exp` 解析、per-request health-check、业务写 API、`IICSClient` 的 POST 能力 | — |

### 代码结构

```text
auth/errors.py       认证 / 环境异常语义
auth/credentials.py  credential config loader + 环境名解析
auth/login.py        Login POST（认证层唯一 POST）+ LoginIdentity 最小对象
auth/pins.py         EnvironmentPinStore（原子写：temp → fsync → os.replace）

iics_client.py       SessionManager（environment-aware，login/ensure_valid/
                     refresh/invalidate/headers_v2/headers_v3）
                     IICSClient._request（GET-only；每 attempt 重建 URL+Header）

iics_cli.py          全局 --env；auth-check / auth-pin；create_client(args)

tools/query.py / exporter.py / selected_exporter.py
                     零认证改动（只面向 IICSClient）
```

### 最终认证调用链

```text
--env DEV / TEST / PROD            （或 IICS_ENV；都没有 → MissingEnvironmentError）
        ↓
Environment Resolver               （alias → 规范键；不默认 PROD，不猜测）
        ↓
Credential Config                  （IICS_CONFIG_FILE → <repo_root>/iics_credentials.json）
        ↓
SessionManager.from_credentials()
        ↓
Login POST                         （认证层唯一 POST：/ma/api/v2/user/login）
        ↓
serverUrl + icSessionId + orgId + orgUuid      （其余响应字段读取后即丢弃）
        ↓
Environment Pin Guard              （未 pin / orgUuid 不符 → 阻断，且不保存 session）
        ↓
IICSClient
        ↓
GET-only business APIs
        ├─ V2 → icSessionId
        └─ V3 → INFA-SESSION-ID    （同一个 Login session）
```

**Login POST ≠ 开放业务 POST。** Login 的 `requests.post` 只存在于 `auth/login.py`；
`IICSClient._request()` 中的 `if method != "GET": raise ValueError` 是必须保留的安全回归点，
业务层不存在任何写路径。

### Environment selection 契约

```text
CLI --env  →  IICS_ENV  →  MissingEnvironmentError
```

- **永不默认 PROD**；不由 folder 名 / username / serverUrl / org 名推断环境
- canonical environments：`DEV` / `TEST` / `PROD`；alias 由 credential config 的
  `aliases` 字段管理（例如 `test` / `testing` / `テスト` / `テスト環境` → `TEST`），
  不在代码中硬编码
- 未知环境名 → `UnknownEnvironmentError`
- `--env` 可放在子命令前或后，但**每条命令只允许出现一次**（Amendment 10.1，见下节）
- `IICS_ENV=DEV` + 一个 CLI `--env TEST` → 使用 `TEST`。环境变量被 CLI 覆盖是既有优先级
  设计，**不算重复**

### Credential 边界

| 文件 | 进 Git | 内容 | 维护者 |
|---|---|---|---|
| `iics_credentials.template.json` | ✅ | 仅键名与 schema；`username` / `password` **永远为空字符串** | 可由工具维护 |
| `iics_credentials.json` | ❌ local-only / git ignored | 三个环境的真实 `username` / `password` | **使用者本人** |

配置路径解析：`IICS_CONFIG_FILE`（可选 override，仓库外部署用）→
默认 `<repo_root>/iics_credentials.json`。

Login URL 解析：`environment.login_url` → 顶层 `login_url` → 代码 `DEFAULT_LOGIN_URL`。
三个环境正常共用顶层值，**不重复配置**；env 级字段仅为未来特殊环境保留。

`username` / `password` 为空字符串视为该环境**未配置** → `MissingCredentialsError`，
不会拿空串去尝试 Login。

**Agent 行为红线**（同步写入 `CLAUDE.md` 与 `SKILL.md`）：Agent 不得生成真实 credential、
不得填写或修改真实 `username` / `password`、不得读取 `iics_credentials.json` 正文、
不得输出 credential 或 session。

### Login 响应最小化持有

Login 成功后只允许提取并长期持有：

```text
icSessionId   serverUrl   orgId   orgUuid   id
```

（`id` 仅作排障时的账号标识，且不进入任何 CLI 输出。）

原始 Login response **不得**挂载到 `SessionManager`、不得打印、不得落盘、
不得进入异常信息、不得进入 CLI stdout。实测该响应还包含
`roles` / `usergroups`（含内部维护人邮箱）/ `emails` / `securityQuestion` /
`securityAnswer` / `password`（IICS 已脱敏），这些字段读取后即丢弃、一律不透传。
异常只描述结构性问题（"响应不是合法 JSON"、"缺少字段 icSessionId"），不含字段取值。

`session_id`（即 `icSessionId`）运行时必须使用，但**不得**出现在 CLI 输出、日志、
异常或任何持久化文件中。

### SessionManager 与 session 生命周期

`SessionManager` 是环境感知的进程内会话持有者，概念能力：
`login()` / `ensure_valid()` / `refresh()` / `invalidate()` / `touch()` /
`can_refresh()` / `headers_v2()` / `headers_v3()`。
未实现显式 `SessionState` enum 或状态机——单进程一次性 CLI 模型下，字段本身即状态。

```text
CLI 进程启动
    ↓
Login（一次）
    ↓
同一进程内所有业务 GET 复用同一 session
    ↓
失效时自动重新登录（见下节）
    ↓
进程退出 → session 丢弃
```

**session = process-local。** 不实现 session cache 文件、不做跨进程 token 持久化。
下一条 CLI 命令重新 Login。

proactive refresh 是**优化**而非判定依据：以 `last_activity_at` +
`IDLE_REFRESH_SECONDS = 25 * 60` 触发主动刷新。**真正权威的失效信号是业务 API 401。**
不解析 JWT `exp`（即使 `icSessionId` 形似 JWT 也不作为程序契约），
不在每次业务 GET 前发 health-check。

### Environment identity 与 TOFU pin

**为什么不能首次 Login 后静默自动 pin：**

```text
TEST 槽位里误填了 PROD 的 credential
        ↓
Login 成功（凭据本身有效）
        ↓
若程序静默 pin
        ↓
PROD 的 orgUuid 被永久绑定为 "TEST"
```

因此采用 TOFU + explicit confirmation：

```text
auth-check --env <ENV>
        ↓
展示非敏感身份（org_id / org_uuid / server_host），不写任何 pin
        ↓
【使用者本人人工确认这确实是目标环境】   ← Agent 不得代为确认
        ↓
auth-pin --env <ENV>
        ↓
原子写入 .env_pins.json
```

运行期 guard：未 pin → `EnvironmentPinRequiredError`；Login 的 `orgUuid` 与已 pin 值不符
→ `EnvironmentMismatchError`。两种情况都**阻断一切业务 API，且不保存 session**。

**身份强度**：

| 字段 | 角色 |
|---|---|
| `orgUuid` | **唯一强身份**，pin 校验以它为准 |
| `orgId` | 短码，仅供人工 sanity check（`000LA8` → DEV，`000LVT` → TEST，`000LVU` → PROD） |
| `serverUrl` / server host / `username` | **不得**作为环境身份；`serverUrl` 每次以当前 Login 响应为准，不持久化 |

实测 Login 响应中不存在 `orgName` 等 org 显示名字段。

**`.env_pins.json` 产品选择**：local-only、git ignored、**不随仓库共享**——每个使用者
在自己机器上独立完成 `auth-check` → 人工确认 → `auth-pin`。
entry 只包含 `org_uuid` / `org_id` / `pinned_at`，不含 credential、不含 `icSessionId`、
不含 `serverUrl`。

**写入安全**：原子写（temp file → flush/fsync/close → `os.replace()`），避免进程中断
留下半截 JSON。已有 pin 与本次 Login 的 `orgUuid` 不一致时**不得自动覆盖**；
重新绑定必须由使用者显式执行（`auth-pin --force`）。相同 `orgUuid` 的重复 pin 为幂等，
不重写。

### 401 / 403 与重试策略

**业务 GET 401**（权威失效信号）：

```text
GET（旧 URL / 旧 session）
    ↓ 401
invalidate
    ↓
Login
    ↓
得到新的 serverUrl + session_id
    ↓
重新 build URL（不能只换 Header 而沿用旧 URL）
    ↓
重新 build Header
    ↓
retry 原 GET 一次
    ↓ 仍 401
AuthenticationError
```

认证 refresh 最多 **1 次**，不存在 `login → retry → login → retry` 的循环。

**业务 GET 403**：session 有效但权限不足 → `PermissionDeniedError`，**不 refresh**。
看到 403 就重新登录是错误行为。

**Login 失败 ≠ 业务 session 失败**（重要排障契约）：

| 场景 | 异常 | 行为 |
|---|---|---|
| **Login POST** 返回 401 / 403 | `InvalidCredentialsError` | 账号本身无法登录，**不重复 retry**（避免锁号） |
| **业务 GET** 401 → refresh → retry → 仍 401 | `AuthenticationError` | 会话在重新登录后仍无效 |

**Login transient retry**：`Timeout` / `ConnectionError` / `429` / `502` / `503` / `504`
→ 最多 3 次总尝试，backoff 1s / 2s。
`400` / `401` / `403` 及其它非 transient 4xx **不做重复 credential Login**。

**重试计数分离**：认证 refresh（≤1）、业务 transient retry（≤3 次总尝试）、
Login 自身 transient retry（≤3 次）三者使用彼此独立的计数器，互不消耗预算。

### 异常语义

全部位于 `auth/errors.py`，共同基类 `IICSAuthError`：

| 异常 | 触发条件 |
|---|---|
| `MissingEnvironmentError` | 无 `--env` 且无 `IICS_ENV` |
| `UnknownEnvironmentError` | 环境名 / alias 不在 credential config 中 |
| `DuplicateEnvironmentArgumentError` | 同一条命令出现多个 `--env`（Amendment 10.1） |
| `CredentialConfigError` | 配置文件缺失 / 不可读 / JSON 非法 / schema 不合法 |
| `MissingCredentialsError` | 该环境 `username` 或 `password` 为空字符串（`CredentialConfigError` 子类） |
| `InvalidCredentialsError` | **Login POST** 返回 401 / 403 |
| `InvalidLoginResponseError` | 非 JSON / HTML / 缺 `icSessionId`·`serverUrl`·`orgUuid`·`orgId` / 其它 4xx |
| `EnvironmentPinRequiredError` | 该环境尚未 pin |
| `EnvironmentMismatchError` | Login `orgUuid` 与已 pin 值不一致，或试图静默覆盖 pin |
| `PermissionDeniedError` | **业务 GET** 返回 403 |
| `AuthenticationError` | 业务 GET refresh 后仍 401；或无 session 且无法 refresh |

### Git / secret 安全边界

```text
.gitignore 精确排除（不使用会误伤 template 的宽泛规则）：
    /iics_credentials.json
    /.env_pins.json
```

Accepted 时的实测状态：credential tracked = **no**，pin tracked = **no**，
`iics_credentials.template.json` 仍可被 git 跟踪。

额外 guard：`scripts/check_no_real_credentials.py`——无第三方依赖，只检查暂存区**路径**，
不读取文件内容；可选安装为本地 `pre-commit` hook，但 `.gitignore` 与提交前的 git 自检
才是主要防线。

文档与测试中一律不出现真实 credential、真实 session 或真实 `orgUuid`；
需要举例时使用 `<ORG_UUID>` 占位。

### 自动化验证覆盖

mock 单元测试全部不触网、不使用真实账号密码、不读写真实 `.env_pins.json`
（通过 `IICS_CONFIG_FILE` / `IICS_PIN_FILE` 指向临时目录隔离）。覆盖领域：

- environment resolution（`--env` 优先于 `IICS_ENV`、alias 含日文、缺失 fail closed、未知环境）
- credential validation（缺文件 / 非法 JSON / 缺环境 / 空 username / 空 password /
  顶层与 env 级 login_url / 默认 login_url / repr 脱敏）
- Login（字段提取、POST body、200+HTML、缺 `icSessionId`·`serverUrl`·`orgUuid`、异常无 secret）
- Login transient retry（`Timeout`、`ConnectionError`、`429`、`502`、`503`、`504`、
  retry exhausted、`400`/`401`/`403`/`404` 不重试且请求数为 1、transient 常量未被改动）
- environment pinning（未 pin 阻断且不保存 session、正确 pin 放行、orgUuid mismatch、
  不自动覆盖、幂等、`--force` 重绑、pin 记录字段白名单、原子写无残留 temp）
- `auth-check` / `auth-pin` stdout contract（各 status 的 exact key set、禁止字段阻断、
  未登记 status fail closed、人工提示在 stderr）
- duplicate `--env`（前置 / 后置 / 不同值 / 相同值 / `--env=` 形式 /
  `IICS_ENV` 叠加不算重复 / 重复在读取 credential 与 Login 之前就失败）
- V2 / V3 header 注入与同一 session 复用
- 业务 401 refresh once + retry（含 refresh 后必须使用新 `serverUrl`）、二次 401 →
  `AuthenticationError`、legacy 模式不 refresh、认证与瞬时计数分离、idle 主动 refresh
- 403 不 refresh
- **GET-only safety regression**：`POST` / `PUT` / `PATCH` / `DELETE` 全部被拒且不发 HTTP
- secret minimization：`SessionManager` 不持有原始 Login 响应、pin 文件无 secret/session、
  `public_identity()` 不含 `id`、仓库内无 session cache 文件

最终数量见文末「Amendment 10 — 最终验收结论」。

### 对正文旧契约的反向修正（supersede）

- 正文「`IICS_SESSION_TOKEN` 为唯一认证入口」→ 改为 legacy/debug 模式，
  需显式 `IICS_AUTH_MODE=legacy_token` 开启，不受环境 pin 保护。
- 正文 `SessionManager.from_env()` 语义「不调用 login API」→ 仅对 legacy 路径成立；
  主路径 `from_credentials()` 会调用 Login。
- `SessionManager.sso_token` 保留为 `session_id` 的向后兼容别名。
- Step 4 契约「401 → `handle_401()` 直接抛 RuntimeError」→ 主路径改为
  refresh once + retry；legacy 路径保持原 RuntimeError 语义。
- 正文「实现契约」中嵌入的 v0.1 期 `CLAUDE.md` / `iics_cli.py` 快照（`export
  IICS_SESSION_TOKEN="<USER_SESSION 值>"`、`iics_client.py` 契约里的
  `SessionManager(sso_token/username/password)`）→ 保留为历史契约记录；当前实际契约
  以本 Amendment 及仓库中的 `CLAUDE.md` 为准。
- 正文 Consequences「SSO Token 过期后必须手动刷新（无法自动续期）」与「`server_url`
  默认写死」→ 主路径下 session 由 Login API 自动获取并在 401 时自动重新登录，
  `server_url` 每次取自 Login 响应。
- **D-5 命令集合**：由 5 个扩展为 7 个（新增 `auth-check` / `auth-pin`）。这两个命令的
  Login `POST` 与 `auth-pin` 的本地 `.env_pins.json` 写入，**不改变 D-5 的 IICS business
  GET-only 边界**；D-5「当前命令契约」已同步，其分类以本 Amendment 为设计依据。

### 未改动（回归保证）

- `build_v2_url` / `build_v3_url` 行为不变
- `IICSClient._request` 仍然只接受 GET（必测安全回归）
- `tools/query.py` / `tools/exporter.py` 零改动
- `tools/selected_exporter.py` 仅把 session 失败告警的 error_type 从
  `"RuntimeError"` 扩展为 `{"RuntimeError", "AuthenticationError"}`。
  理由：Amendment 6 §9 的「连续 ≥5 次同类 session 失败 → 追加告警」原按 `RuntimeError`
  识别，而主路径的会话失效语义已变为 `AuthenticationError`；不扩展则该告警在新认证路径下
  永不触发（静默回归）。仅扩展识别集合，告警文案、abort 行为、计数与输出结构均不变
- Excel / JSON schema、列数、counts 语义（`task_count` / `rows` /
  `failed_details_count` / `enrichment_warnings_count`）全部不变

### 已验证

- mock 单元测试全通过，不触网、不使用真实账号密码（最终 87 项，见文末最终验收结论）
- `git check-ignore` 确认 `/iics_credentials.json` 被忽略，模板仍可被 git 跟踪
- 三个环境的 `auth-check` → 人工核对 org 身份 → `auth-pin` 均已由使用者本人完成
- TEST 环境完成真实业务只读回归（详见文末「Amendment 10 — 最终验收结论」）

---

## Amendment 10.1 — auth-check / auth-pin stdout contract 与重复 `--env` fail closed

**日期**：2026-09-11
**状态**：Accepted（Amendment 10 的缺陷修复，不改变其架构决策）
**触发**：真实环境 TEST `auth-check` 验收期间发现两处契约缺陷。

### 缺陷 1 — `auth-check` stdout 混入人类提示字段

`status = pin_required` 时 stdout 多出 `next_step`（固定人类操作提示，不含敏感信息），
违反 machine-readable stdout contract。

**决策**：认证 / 环境初始化命令的 stdout 按 **status-specific allowlist** 投影输出，
人类提示改走 stderr，维持 `stdout = data / stderr = diagnostics` 的 CLI contract。

| 命令 / status | stdout 顶层 key |
|---|---|
| `auth-check` / `pin_required` | `environment` `status` `org_id` `org_uuid` `server_host`（恰好 5 个） |
| `auth-check` / `pinned` | 上述 5 个 + `pinned_at` |
| `auth-check` / `pin_mismatch` | 上述 5 个 + `pinned_org_uuid` `error_type` `error` |
| `auth-pin` / `pinned`、`already_pinned` | `environment` `status` `org_id` `org_uuid` `server_host` `pinned_at` `pin_file` |
| 顶层 fatal error（含 `auth-pin` mismatch 拒绝） | `error` `error_type` |

实现为 `iics_cli._project_auth_stdout()`：未登记的 status → `ValueError` fail closed；
结果 dict 中出现禁止字段（`username` / `password` / `icSessionId` / `session_id` /
`id` / `roles` / `usergroups` / `emails` / `securityQuestion` / `securityAnswer` /
`raw` / `headers` / `Authorization` 等）→ 阻断输出且只报 key 名、不回显取值。
这是 §13.1「Login 响应体最小化持有」在 CLI 输出侧的可执行兜底。

`auth-pin` 经字段级审查：原实现已符合最小化原则，无 `next_step` 类多余字段，
本次仅补上 allowlist 强制与对应测试，未修改其输出字段。

### 缺陷 2 — 重复 `--env` 静默采用后一个值

`python iics_cli.py --env DEV auth-check --env TEST` 原先静默使用 `TEST`
（argparse 子命令级参数覆盖全局级）。在 DEV/TEST/PROD 并存的多环境工具中，
这是不可接受的隐式优先级。

**决策**：一条 invocation 中 `--env` 只允许出现一次，重复即 fail closed。

- 前置（`--env TEST auth-check`）与后置（`auth-check --env TEST`）均继续支持
- 重复出现（含 `--env=VALUE` 形式、含两次取值相同）→
  `DuplicateEnvironmentArgumentError`、退出码 1
- 检查在 `parse_args` **之前**执行，因此在读取 credential / 发起 Login 之前就失败
- **不**实现 first-wins / last-wins
- `IICS_ENV` + 一个 CLI `--env` 不算重复：CLI 覆盖环境变量是 Amendment 10 的既有
  优先级设计，保持不变

行为矩阵：

| 调用形式 | 结果 |
|---|---|
| `--env TEST auth-check`（前置） | ✅ 解析为 TEST |
| `auth-check --env TEST`（后置） | ✅ 解析为 TEST |
| `--env DEV auth-check --env TEST` | ❌ `DuplicateEnvironmentArgumentError`、exit 1 |
| `--env TEST auth-check --env TEST`（同值） | ❌ 同样拒绝 |
| `--env=DEV … --env=TEST` | ❌ 同样拒绝 |
| `IICS_ENV=DEV` + `--env TEST` | ✅ 使用 TEST（既有优先级，不算重复） |
| 仅 `IICS_ENV=DEV` | ✅ 使用 DEV |

### 未改动（回归保证）

- Login transient retry policy 不变（Timeout / ConnectionError / 429 / 502 / 503 /
  504，最多 3 次总尝试，backoff 1s/2s；400/401/403 绝不重试）—— 本轮只补测试
- proactive refresh（`IDLE_REFRESH_SECONDS = 25 * 60`）、业务 401 refresh once、
  403 不 refresh、`IICSClient` GET-only、environment pin 模型、credential schema
  全部不变
- `tools/query.py` / `exporter.py` / `selected_exporter.py` 本轮零改动

### 已验证

87 项 mock 单元测试全通过（其中 Login transient retry 与 CLI contract 为本次补充），
不触网、不读取真实 credential、不写真实 `.env_pins.json`。

---

## Amendment 10 — 最终验收结论

**日期**：2026-09-11
**状态**：

```text
Amendment 10 = ACCEPTED
```

涵盖 Amendment 10 与其缺陷修复 Amendment 10.1。认证架构、安全边界与 CLI 契约自本节起
视为已收口，不再有 pending 项。

### 自动化测试

```text
87 / 87 PASS
```

Login transient retry 覆盖**已完整补充**，包含：
`Timeout` / `ConnectionError` / `429` / `502` / `503` / `504` / retry exhausted /
`400` no retry / `401` no retry / `403` no retry。
任何仍声称这些用例缺失的描述均为过期信息。

### 环境验收矩阵

| 环境 | 结论 | 已验证内容 | 未执行项 |
|---|---|---|---|
| **TEST** | `FULL REAL PATH PASS` | automatic Login；`auth-check`；人工 identity confirmation；未 pin 时 fail closed（`EnvironmentPinRequiredError`，Login 1 次、business GET 0 次）；`auth-pin`；V3 真实业务 GET（`list-tasks JAPAN_SCRM_DI DSS` → 921 tasks）；V2 真实业务 GET（`get-task` 成功）；same-session 互通（同一进程 Login 1 次，V3 用 `INFA-SESSION-ID`、V2 用 `icSessionId`，共用同一 Login session）；query/export 回归（`export-selected` 2 DSS success / 0 failure）；credential & session 泄漏扫描 PASS | — |
| **DEV** | `ENVIRONMENT ONBOARDING PASS` | Login；人工 identity confirmation；`auth-pin`；pin preservation；pin guard；credential & session 泄漏扫描 PASS | 真实 business GET 未执行 —— 无已确认存在且安全的 DEV folder |
| **PROD** | `ENVIRONMENT ONBOARDING PASS` | Login；人工 identity confirmation；`auth-pin`；pin preservation；pin guard；credential & session 泄漏扫描 PASS | 真实 business GET 未执行 —— 无已确认存在且安全的 PROD folder |

DEV / PROD 未执行 business GET 属于 **test coverage difference**，不是 Amendment 10 的
defect 或 blocker。**不得**将 DEV / PROD 记述为已完成 V2/V3 business API 验证。

### Environment Pin 最终状态

```text
TEST → pin present
DEV  → pin present
PROD → pin present
```

三个 environment identity 均由使用者**分别人工确认**后绑定。

`.env_pins.json`：local-only、not tracked、git ignored；每条 entry 只包含
`org_uuid` / `org_id` / `pinned_at`。真实 `org_uuid` 不出现在任何文档中，
需要举例时一律使用 `<ORG_UUID>` 占位。

### Credential 最终状态

`iics_credentials.json`：local-only、not tracked、git ignored；不得读取正文、
修改正文、输出正文或加入 Git。
`iics_credentials.template.json`：继续进入 Git，`username` / `password` 保持空字符串。

### Security Boundary 最终状态

```text
Business API        = GET-only
唯一允许的 POST      = IICS Login API（认证层）
session             = process-local，不落盘
credential          = local-only，不进入 Git
pin                 = local-only，不进入 Git
V2 / V3             = 共享同一个 Login session
业务 401             = refresh once → rebuild URL/Header → retry once
业务 403             = 不 refresh（PermissionDeniedError）
```

---

## Amendment 11 — MTT Last Run & Connection Parameter Columns

**日期**：2026-11-16
**状态**：离线实施完成（代码 + mock 测试 + 文档）；**真实环境验证未执行**（待用户指定环境）。
**性质**：MTT 业务输出增量扩展。**不新增 endpoint / 依赖 / 命令 / CLI 选项，不改 detail route，
不涉及写操作。** DSS 12 列、WORKFLOW 9 列及其查询 / 导出行为不变。

### 决策

1. **MTT schema 23 → 26 列**：插入 `Last Run`（`Task Updated On` 之后）、
   `Source Connection Parameter`（`Source Connection` 之后）、`Target Connection Parameter`
   （`Target Connection` 之后）。`get-task` MTT compact 与 Excel 共用同一 row builder，列名 /
   顺序 / 值完全一致。**按固定列号读取文件的调用方需要调整**（后续列号移动）。
2. **Last Run**：唯一来源 MTT `raw.lastRunTime`。实测为 epoch 毫秒（与官方「UTC ISO」约定不符，
   见 API Notes §7.7.1）；输出固定 UTC `yyyy-MM-ddTHH:mm:ss.SSSZ`。ISO 字符串只接受明确定义的
   子集 `YYYY-MM-DD(T|t|空格)HH:MM[:SS[.f{1,6}]](Z|±HH:MM|±HHMM)`（项目自带解析，3.10+ 一致）。
   缺失 / null / `""` / `0` →
   `null`（不宣称从未运行、不告警）；数值字符串 / 非法 / 布尔 / 负数 / 非有限 / 越界 / 无时区 →
   `null` + warning。不用 `updateTime` / activityLog 补值，不解释为最近成功运行时间。
3. **连接参数（兼容解析，非官方字段）**：`uiProperties.connectionParameterized` 明确 true/false
   优先（安全解析字符串）；缺失时仅以 `paramType-mapping == "Connection"` 或
   `runtimeParameterData.connectionParameterName` 为证据；参数名 `connectionParameterName` 优先、
   fallback `paramName`，不从 `name` / `label` / `paramLabel` / `logcnx` 推断。
   `isConnectionRuntimeParameter` 仅原样映射，语义未确认。
4. **Connection 列优先级**：真实解析名称 → `connDefault`（仅确认参数化）→ connection ID；
   `--no-resolve` 恒为 ID。「真实解析名称」按 resolver 记录的**名称来源**判定（响应是否含
   `name` / `connectionName`，与名称一起缓存）：请求失败或响应无名称（resolver 以 id / `""`
   兜底）视为无名称；真实名称即使恰好等于 ID 也照常展示。公开 `resolve_connection()` 输出、
   `resolve-connection` CLI 与 DSS 调用方契约不变；`iics_client.py` 未改。
   使用 MTT 专用显示函数，共用 `_conn_display()`（DSS 使用）不变。（review 修正，见下文）
5. **Parameter 列取值**：明确非参数化 → `""`；参数化 → 参数名（缺名 `null`）；状态未知 → `null`；
   对侧 → `""`。
6. **展开规则不变**：仍以 `sourceConnectionId` / `targetConnectionId` 为依据，保序、不去重；
   **未按 `type` 严格过滤**（Lookup 等同名字段存在条件性误分类风险，本次不修复）。明确参数化但缺少
   ID 的 SOURCE/TARGET 项只记 1 条 warning，不新增行；不恢复无 ID 行。
7. **warning**：Last Run 非法、参数化缺名、字段冲突、连接名称降级、参数化缺 ID 均为 enrichment
   warning（不计入 `failed_details_count`、不中止导出）。结构
   `{kind, transformation_name, field, error_type: "EnrichmentError", error}`；`error` 文本以
   `<transformation> / <field>: ...`（任务级为 `Last Run: ...`）开头，保证 Export Errors 可定位。
   同一 transformation 的同一字段最多 1 条；请求失败沿用既有 `kind: connection`，`connDefault`
   兜底时不另加；仅响应无名称（ID 兜底）时为该字段记 1 条 `connection_parameter`。
8. **MTT compact stderr（行为变化）**：MTT 默认 compact 开始把全部 `enrichment_warnings`（含既有
   runtime / mapping / connection）经 `_log()` 写入 stderr，每条一行
   `[IICS CLI] WARNING {kind}: {error}`；stdout 顶层仍只有 `task_count` / `rows` / `records`。
   `--full` 与 JSON 导出保留结构化 warning；Excel 复用 Export Errors。

### normalized detail 增量

任务级 `last_run: str | null`；适用侧 `source_connection` / `target_connection` 在既有
`id` / `name` 之外增加 `parameterized`、`parameter_name`、`configured_value`
（`uiProperties.connDefault`）、`is_connection_runtime_parameter`、`display_value`；`name` 仅保存
真实解析名称。row builder 仅消费 normalized 字段。不增加旧 normalized JSON 回读兼容路径
（仓库无该入口）。

### 入口影响

| 入口 | 结果 |
|---|---|
| `get-task` MTT 默认 | 26 列 records；warning → stderr |
| `get-task` MTT `--full` | normalized detail 增量字段 + `raw` + 结构化 warning |
| `export` MTT / ALL | MTT sheet 26 列；sheet 名称 / 顺序 / Export Errors 不变 |
| `export --format json` | 整份 data，`details` 含新增 normalized 字段 |
| `export-selected` | 复用底层，无代码改动；MTT 文件 26 列 |
| `export --summary-only` | 新增列为空，不抓取详情 |
| `get-task --no-resolve` | Connection 为 ID；Parameter 与 Last Run 照常（`export` 无此选项） |

### 列数订正 —— 当前 authoritative Excel schema

| 业务 sheet | 当前列数 |
|---|---|
| `MTT Task` | **26**（23 + `Last Run` / `Source Connection Parameter` / `Target Connection Parameter`） |
| `DSS Tasks` | **12**（不变） |
| `Workflow Tasks` | **9**（不变） |

Amendment 9 的列数表保留为历史快照。

### 不做（本次范围外）

DSS Last Run（已取消，不承诺用日志或 HTML 补值）、activityLog、HTML 抓取、参数文件读取、运行时
连接还原、废弃任务判定、Lookup 分类修复、无 ID 行恢复。输出的连接是**任务配置值**，不保证是某次
运行最终使用的连接。

### 已验证（离线）

- 改动前：基线 87/87；新增特征测试 13 项在未改动代码上全部通过（DSS 12 / WORKFLOW 9 / MTT 23）。
- 初版实施后：完整套件 165/165（Python 3.11 与 3.13）；同一版本在 Python 3.10 为 164/165（见下文
  review 修正 2）。
- review 修正后：完整套件 **187/187**（Python 3.10.20 / 3.11.18 / 3.13.18）；DSS / WORKFLOW 特征
  测试未修改；MTT 特征测试仅插入 3 列。凭据扫描 OK。
- 样本 `mt_DWH_TO_REG_V_DMF_SSA_FACT_RESULT_QUICK`（fixture 级）：Last Run
  `2026-11-12T06:58:54.000Z`（两行重复）；Source Connection `rsh_gdm_e2e_TEC`、Parameter
  `parameter_rsh_gdm_e2e`；Target Parameter `""`；Target Connection 取 resolver 结果
  （ID `000LVU0B0000000001VU`，未预填名称）。

### 待验证（真实环境）

见 Runbook §6.9：本例真实核对；Target 参数化任务与完全非参数化任务；「参数化但未勾选运行时覆盖」
样本以确认 `isConnectionRuntimeParameter` / `connectionParameterName` 含义；仅 taskflow 触发的
MTT 的 `lastRunTime` 更新行为；`JPN_COMM_DWH_DL.PARAM` 中 `parameter_rsh_gdm_e2e` 的定义与生效值
（人工确认，代码不读取参数文件）。

### Amendment 11 review 修正（2026-11-16，两个阻断项）

**修正 1 — 真实连接名称与 ID fallback 的区分。** 初版实现采用名称与 ID 相等的启发式判断
（`resolved[id]` 为空或等于 ID 本身即视为无名称）。本轮 review 将其替换为 resolver 记录名称来源，
并保留缓存来源信息；原启发式会把真实名称恰好等于 ID 的连接误判为无名称，错误地展示 `connDefault`
并告警「returned no name」。修正：

- `tools/query.py`：新增内部 `_resolve_connection_entries()`，在 `resolved` / `failed` 之外返回
  `has_name[id]`（值是否来自响应的 `name` / `connectionName`）；公开 `resolve_connection()` 改为
  包装它，输出仍只有 `{resolved, failed}`，值不变。`_real_connection_name()` 只依据 `has_name`
  与 `failed` 判定，删除「名称 == ID」比较。
- `cache/connection_cache.py`：`ConnectionCache` 增加来源记录（`get_entry` /
  `get_or_fetch_entry`；`set()` 增加可选 `has_name`）。旧 `get` / `set` / `get_or_fetch` 行为与
  返回值不变；失败仍不缓存。仅由旧 `set()` 写入、缺少来源信息的条目会补一次 fetch（项目内无此
  写入路径）。
- `resolve-connection` CLI、DSS 调用方的输出契约与缓存行为不变（测试覆盖）。

**修正 2 — Python 3.10 的 Last Run ISO 解析。** 初版依赖 `datetime.fromisoformat`：3.10 只接受
`isoformat()` 输出（拒绝 1/2/4/5 位小数秒），3.11+ 又额外接受 basic 格式等，跨版本行为不一致
（3.10 下 1 项测试失败）。修正为项目自带的显式子集解析（`_ISO_DATETIME_RE` /
`_parse_iso_datetime`，标准库 `re` + `datetime`，无新增依赖），3.10 / 3.11 / 3.13 行为一致；
支持与不支持格式见 API Notes §7.7.1。注意：3.11+ 上原本被接受的 basic 格式等，现按子集定义
返回 `null` + warning（有意统一）。`requirements.txt` 仍为 Python >= 3.10。

**测试**：修复前新增测试在 3.10 / 3.11 / 3.13 分别 16+2 / 13+2 / 12+2 项失败（复现缺陷）；
修复后全套 187/187（三版本）。原有失败测试 `test_iso_with_offset_normalized_to_utc` 未删除、未改期望。
