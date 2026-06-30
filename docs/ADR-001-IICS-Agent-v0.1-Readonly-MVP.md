# ADR-001: IICS Agent · v0.1 Read-only MVP

**状态**: Accepted  
**日期**: 2026-06-27  
**决策人**: Li（运维负责人）  
**参考文档**: `IICS_Agent_System_Design.md` v1.4（已通过三轮 review，评分 9/10）  
**Review 说明**: 经一轮 ADR review（评分 8.6/10），修正阻断项（V2 path 契约 + Smoke Test 断言），并采纳 V3 params 规范、connection 抽取 best-effort extractor、export sheet best-effort 标注、异常处理包装点、环境变量化 Smoke Test 等改进。后续更新：D-1 扩展为双模式设计，新增 `iics_cli.py` 契约（Claude Code CLI 主模式）与 `CLAUDE.md` 契约，实现步骤调整为 Step 9 完成 Mode A、Step 10 完成 Mode B。

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
| D-1 | 运行模式 | 双模式：Claude Code CLI（主）+ Cowork Interactive Agent（辅） |
| D-2 | 认证架构 | `SessionManager` 双模式封装，v0.1 只启用 SSO Cookie 模式 |
| D-3 | URL 构建 | 工厂函数模式，防止 `/saas/saas/` 路径重复 |
| D-4 | API 版本混用策略 | V3 枚举 + V2 详情，分别注入不同 Header |
| D-5 | v0.1 工具注册边界 | 只注册 4 个只读工具，write tools 代码不出现 |

---

## D-1: 运行模式（双模式设计）

### 背景

Claude Code CLI 本身即 AI 推理层——在其中启动一个独立的 Anthropic API 对话循环会产生"嵌套 Claude"问题：额外 API 成本、`input()` 循环与 CLI I/O 管理冲突。因此两种运行场景需要不同的入口文件，但共享全部底层工具代码。

### 选项对比

| 选项 | 入口文件 | AI 层来源 | 适用场景 |
|------|---------|---------|---------|
| **A（主）：Claude Code CLI** | `iics_cli.py` | Claude Code CLI 自身 | 在 `claude` CLI 会话中使用 |
| **B（辅）：Cowork Interactive Agent** | `iics_agent.py` | 内部 Anthropic API 调用 | 没有 Claude Code CLI 时 |
| C（未选）：直接 Python 脚本 | — | 无 AI | 运维需记忆参数，门槛高 |
| D（未选）：Web UI | — | 后端 API | 需额外部署，违反轻量约束 |

### 两种模式的调用链

**Mode A（Claude Code CLI）**
```
运维人员 → claude CLI → bash: python iics_cli.py <subcommand> → IICSClient → IICS API
```
Claude Code CLI 读取 `CLAUDE.md` 获取项目上下文，根据自然语言意图直接决定调用哪条 `iics_cli.py` 子命令，解析 JSON 输出后以自然语言呈现。**无第二层应用内 Anthropic API 调用**（Claude Code CLI 本身仍使用 Claude Code 运行环境与配额）。

**Mode B（Cowork）**
```
运维人员 → iics_agent.py → Anthropic API（Claude）→ Python 工具函数 → IICSClient → IICS API
```
`iics_agent.py` 内部管理多轮对话循环，适合没有 Claude Code CLI 时在 Cowork 中直接运行。

### 共享层（两种模式完全相同）

`iics_client.py` / `tools/` / `cache/` 全部共享，入口层以外代码零重复。

### 取舍

双模式共存，主辅分明：v0.1 实现优先 Mode A（`iics_cli.py` + `CLAUDE.md`），Step 9 完成后 Mode A 即可用；Mode B（`iics_agent.py`）在 Step 10 完成，为辅。切换只换入口文件，工具函数层无感知。

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
    base = server_url.rstrip("/")
    if base.endswith("/saas"):
        base = base[:-5]
    return f"{base}/{path.lstrip('/')}"
```

**B: 每个工具函数自己拼 URL**

复用性差，出现 `/saas/saas/` 时多处修复。

### 取舍

选 A。工厂函数单测一次，所有工具受益。`IICSClient._request(path, use_v3)` 接收 path 参数，URL 构建由 `_request` 内部调用工厂函数完成，工具层完全不接触 URL 拼接逻辑。

**path 参数规范（强制）**：

| API 版本 | path 传入规则 | 示例 |
|---------|-------------|------|
| V2（`use_v3=False`）| **只传业务资源路径，不含 `saas/api/v2` 前缀** | `dsstask/name/MY_TASK` |
| V3（`use_v3=True`）| 传 `api/v3/objects` 等 V3 路径，不含 `?` 及以后（查询参数通过 `params` 传入） | `api/v3/objects` |

违反此规则会导致路径重复（如 `/saas/api/v2/saas/api/v2/...`），静默 404 难以排查。所有工具函数中**禁止**在 path 里拼入 `saas/api/v2` 或 `?q=...`。

---

## D-4: API 版本混用策略

### 背景

IICS 有两套 API，行为差异显著：

| 维度 | V3 (`/api/v3/`) | V2 (`/saas/api/v2/`) |
|------|----------------|----------------------|
| 认证 Header | `INFA-SESSION-ID` | `icSessionId` |
| 用途 | 对象枚举、分页 | Task 详情、Connection 详情、写操作 |
| 分页机制 | `nextPageToken` | 无（一次返回） |
| 已验证路径 | `/api/v3/objects` | `/saas/api/v2/dsstask|mttask|workflow|connection` |

### 决策

**v0.1 实现中使用规则：枚举用 V3，详情用 V2。**

- `list_tasks` → `_request("GET", "api/v3/objects", use_v3=True, params={"q": f'location=="{folder}"', "pageSize": n})` (V3)
- `get_task_detail` → 根据 `task_type` 路由到对应 V2 path（`use_v3=False`，path 不含 `saas/api/v2` 前缀）
- `resolve_connection` → `_request("GET", f"connection/{id}", use_v3=False)` (V2)

这两个 Header 由 `_request(use_v3=True/False)` 自动注入，工具层无需手动指定。

> **路径备注**: 官方 KB 文档 V3 路径为 `/public/core/v3/objects`，与当前已验证路径 `/api/v3/objects` 存在差异。v0.1 使用已验证路径。专用账号到位后重新确认。

---

## D-5: v0.1 工具注册边界

### 决策

**v0.1 TOOLS 列表只包含 4 个只读工具，`iics_agent.py` 中不出现任何写工具的 import 或注册。**

```python
# v0.1 iics_agent.py — 只注册这 4 个
TOOLS = [
    LIST_TASKS_DEF,
    GET_TASK_DETAIL_DEF,
    RESOLVE_CONNECTION_DEF,
    EXPORT_RESULTS_DEF,
]

TOOL_MAP = {
    "list_tasks":       query.list_tasks,
    "get_task_detail":  query.get_task_detail,
    "resolve_connection": query.resolve_connection,
    "export_results":   exporter.export_results,
}
```

`tools/update.py`、`tools/batch.py`、`tools/safety.py` 在 v0.1 **不创建**。

### 取舍

把"写工具已存在但被 guard 拦截"改为"写工具代码根本不存在"，消除实现者在 v0.1 引入写操作的可能性。v0.2 再创建这些文件。

---

## 实现契约

### 目录结构（v0.1 交付物）

```
iics_agent/
├── CLAUDE.md              # Mode A：Claude Code CLI 项目上下文（会话启动时自动读取）
├── iics_cli.py            # Mode A 入口：bash 可调用的 CLI 子命令
├── iics_agent.py          # Mode B 入口：Cowork 交互式 Agent 对话循环
├── iics_client.py         # 两种模式共享：SessionManager + URL factory + IICSClient
├── tools/
│   ├── __init__.py
│   ├── query.py           # list_tasks, get_task_detail, resolve_connection
│   └── exporter.py        # export_results（openpyxl）
├── cache/
│   ├── __init__.py
│   └── connection_cache.py  # ConnectionCache：内存 dict，session 级别
├── requirements.txt       # Mode A 依赖（requests, openpyxl）
├── requirements-agent.txt # Mode B 追加依赖（+anthropic SDK）
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
        path: str,            # 相对路径，不含 query string，如 "api/v3/objects" 或 "dsstask/name/X"
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
        resp = client._request("GET", "api/v3/objects", use_v3=True, params={...})
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
    - 调用 _request("GET", "api/v3/objects", use_v3=True, params={"q": f'location=="{folder_path}"', "pageSize": page_size})
    - V3 q 查询必须通过 requests params 传入，不允许手拼 query string（含特殊字符时 URL encoding 不稳定）
    - task_type != "ALL" 时 q 追加 AND type=="<task_type>" 到 params["q"]
    - 分页：循环直到响应中无 nextPageToken 字段（pageToken 参数传入下一页 token）
    - V3 响应中 type 字段的实际值可能与工具枚举不同（如 SYNC_TASK、MTTask 等）
      → 若发现不匹配，在 query.py 中增加 normalize_task_type() 映射层，不修改架构
    """

def get_task_detail(
    client: IICSClient,
    task_name: str,
    task_type: str,            # "DSS" | "MTT" | "WORKFLOW"
    folder_path: str = "",     # 强烈推荐；空则跳过消歧
    resolve_connections: bool = True,
) -> dict:
    """
    返回格式:
    {
        "name": "...",
        "type": "DSS",
        "folder": "...",
        "runtime_agent": "JPTKXICST001",
        "source_connection": {"id": "000LVU...", "name": "REDSHIFT_JAPAN_PROD"},
        "target_connection": {"id": "000LVU...", "name": "ORACLE_DWH_PROD"},
        "raw": { <完整 API 响应> }
    }
    实现要点:
    - folder_path 非空时：先 V3 lookup，use_v3=True，params={"q": f'name=="{task_name}" AND location=="{folder_path}"'}
      - 结果 > 1 → 返回 {"error": "同名 task 歧义", "candidates": [...]}
      - 结果 == 1 → 继续
    - 按 task_type 路由到对应 V2 path（use_v3=False，path 不含 saas/api/v2 前缀）：
        DSS      → dsstask/name/<name>
        MTT      → mttask/name/<name>
        WORKFLOW → workflow/name/<name>
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
    返回格式:
    {
        "file_path": "/path/to/output.xlsx",
        "format": "excel",
        "rows": 83,
        "summary": "已导出 83 条记录至 .../output.xlsx"
    }
    Excel 输出包含 3 个 sheet:
    - Task Summary    : name, type, folder, runtime_agent, source_conn, target_conn, status（必填）
    - Field Mapping   : task_name, source_field, target_field, type, length（仅 DSS）
                        best-effort：无法解析时输出空 sheet + 注释行"字段映射不可用"
    - Truncation Risk : task_name, field, source_len, target_len（source_len > target_len 的字段）
                        best-effort：无法解析时输出空 sheet + 注释行"截断风险分析不可用"
    """
```

---

### iics_agent.py 主循环契约

```python
# v0.1 系统提示（写操作功能不提及）
SYSTEM_PROMPT = """你是 Takeda Japan IICS 运维 Assistant。
当前阶段：只读查询模式（v0.1）。
你可以帮运维人员查询 IICS 平台上的 task 配置，并导出为 Excel。
覆盖范围：JAPAN_COMMERCIAL_DWH、JAPAN_SCRM_BAU、JAPAN_SCRM_DI 三个文件夹。
支持的任务类型：DSS（Sync Task）、MTT（Mapping Task）、Workflow（Linear Taskflow）。
如果用户询问写操作或变更，告知当前版本不支持，后续版本会提供。"""

# 工具循环终止条件
STOP_REASONS_TERMINAL = {"end_turn", "stop_sequence"}

# 工具结果大小限制
TOOL_RESULT_MAX_BYTES = 8000
```

**stop_reason 处理**:

| stop_reason | 处理方式 |
|-------------|---------|
| `end_turn` / `stop_sequence` | 打印文本回复，退出内层循环 |
| `tool_use` | 执行工具，追加 tool_result，继续内层循环 |
| `max_tokens` | 打印截断警告，退出内层循环 |
| 其他 | 打印 `[警告] 未知 stop_reason: {reason}`，退出内层循环 |

**tool_result 格式**（必须 JSON 字符串，不是 `str(dict)`）:

```python
content = json.dumps(result, ensure_ascii=False)
if len(content.encode("utf-8")) > TOOL_RESULT_MAX_BYTES:
    content = json.dumps({
        "summary": result.get("summary", f"结果共 {result.get('count', '?')} 条"),
        "file_path": result.get("file_path", ""),
        "truncated": True
    }, ensure_ascii=False)
```

---

### iics_cli.py 契约（Mode A：Claude Code CLI 入口）

```python
#!/usr/bin/env python3
"""
IICS Agent CLI — Claude Code CLI 调用入口
Claude Code CLI 通过 bash 调用此文件，无需二次 Anthropic API 调用。

Session token:
  export IICS_SESSION_TOKEN="<USER_SESSION 值>"

使用示例（Claude Code CLI 会自动生成类似调用）:
  python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
  python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
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
1. list_tasks(folder, task_type)               → task 名称列表
2. 对每个 task: get_task_detail(name, type, folder)  → 含 runtime / connection 字段
   （--summary-only 时跳过此步，Task Summary 中 runtime/connection 列为空）
3. 聚合为 data = {"tasks": [...], "details": [...]}
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

### CLAUDE.md 契约（Mode A：项目上下文文件）

放置于 `iics_agent/` 项目根目录，Claude Code CLI 会话启动时自动读取。**必须包含以下内容**：

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

### requirements.txt（Mode A）

```
# Python >= 3.10 required（使用 match/case 和 X | Y 类型注解）
# Mode A（Claude Code CLI）只需要此文件，无 Anthropic SDK 依赖
requests>=2.31.0
openpyxl>=3.1.0
```

### requirements-agent.txt（Mode B）

```
# Mode B（Cowork Interactive Agent）在 Mode A 基础上追加 Anthropic SDK
-r requirements.txt
anthropic>=0.30.0
```

安装说明：

```bash
pip install -r requirements.txt          # Mode A（Step 1–9）
pip install -r requirements-agent.txt    # Mode B（Step 10，含 Anthropic SDK）
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
assert build_v3_url(session.server_url, "api/v3/objects") \
       == "https://use4.dm-us.informaticacloud.com/api/v3/objects"
# 防 /saas/saas/ 重复：build_v2_url 遇到 serverUrl 含 /saas 后缀时应正确处理
assert build_v2_url("https://use4.dm-us.informaticacloud.com/saas", "connection/123") \
       == "https://use4.dm-us.informaticacloud.com/saas/api/v2/connection/123"
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

    # Test 4: get_task_detail
    detail = get_task_detail(
        client, task_name=task_name, task_type=TEST_TYPE, folder_path=TEST_FOLDER,
    )
    assert "error" not in detail, f"get_task_detail error: {detail}"
    assert "runtime_agent" in detail
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

## 实现步骤（Step 1–9 完成 Mode A；Step 10 完成 Mode B）

| Step | 工作项 | 完成标志 |
|------|--------|---------|
| 1 | 创建 `iics_agent/` 目录结构，写 `requirements.txt`、`requirements-agent.txt`，写 `CLAUDE.md` 初稿 | 目录树与契约一致，`CLAUDE.md` 包含全部必需内容 |
| 2 | 实现 `SessionManager.from_sso_token()` + `headers_v2/v3` + `handle_401` | `SessionManager` 单元可实例化 |
| 3 | 实现 `build_v2_url` / `build_v3_url` | URL factory 单元测试通过（含 `/saas` 后缀变体） |
| 4 | 实现 `IICSClient._request()`（含 401 重试）| 可发出真实 GET 请求，401 触发 `handle_401` |
| 5 | 实现 `list_tasks`（含自动分页）| Smoke Test 2 & 3 通过 |
| 6 | 实现 `get_task_detail`（含同名消歧 + V2 路由）| Smoke Test 4 通过 |
| 7 | 实现 `ConnectionCache` + `resolve_connection` | Smoke Test 5 通过（含缓存命中验证）|
| 8 | 实现 `export_results`（openpyxl，3 sheet）| Smoke Test 6 通过，Excel 可用 Excel 打开 |
| 9 | 实现 `iics_cli.py`（4 个子命令），写 `smoke_test.py` | **Mode A 完成**：全部 smoke test 通过，Claude Code CLI 可自然语言查询 |
| 10 | `pip install -r requirements-agent.txt`，实现 `iics_agent.py` 主循环 | **Mode B 完成**：Cowork 交互式 Agent 可用 |

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

---

## Consequences

**变得更容易**:
- 运维人员不需要记 API、不需要写脚本，自然语言即可导出 Excel
- Claude Code CLI（Mode A）直接 bash 调用工具函数，无额外 Anthropic API 调用成本
- URL factory 集中处理 `/saas` 变体，后续写操作工具直接复用
- ConnectionCache 让批量查询时连接 ID 解析不重复请求

**需要接受的限制**:
- v0.1 无写能力；如需变更 task 配置，仍需手动在 IICS Web UI 操作
- SSO Token 过期后必须手动刷新（无法自动续期）
- `server_url` 默认写死为 `https://use4.dm-us.informaticacloud.com`，专用账号到位后由 Login API 动态返回

**需要在 v0.2 前重新确认**:
- V3 路径：当前用 `/api/v3/objects`，专用账号后需核实是否为 `/public/core/v3/objects`
- SSO Cookie = icSessionId 的等价性在新账号下是否仍然成立
- 分页参数名：v0.1 用 `pageSize`，若实测失败改为 `limit` 重试

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

- **DSS 详情不返回 runtime environment / agent**（仅空的 `srcRuntimeAttrs`/`tgtRuntimeAttrs`），故 `get_task_detail` 的 `runtime_agent` 对 DSS 恒为空字符串（best-effort，不报错）。
- MTT / WORKFLOW 的 runtime / connection 字段结构尚未深度实测，列入 v0.2 待确认。

### 健壮性补强

- `smoke_test.py` / `iics_cli.py` 启动时 `sys.stdout.reconfigure(encoding="utf-8")`，避免在 GBK/cp936 控制台输出 `✅`、中文时 `UnicodeEncodeError`。

### 对「升级至 v0.2 的前提条件」的影响

- 「V3 路径、Login URL 用新账号重新验证」中的 **V3 路径已在当前 SSO 账号下确认**为 `/saas/public/core/v3/objects`；专用服务账号到位后仍需复核是否一致。
- 其余前提（专用账号、`from_env()` 实测、Login URL）保持未决。

*相关提交：`c5ba5d7`（client 实测对齐）、`6759e1b`（IICS_API_NOTES.md）。*
