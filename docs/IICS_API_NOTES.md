# IICS API 实测笔记（v0.1 联调结论）

本文件记录 IICS Agent v0.1 真实 token 联调中**实测确认**的 API 行为，作为 ADR-001
「需要在 v0.2 前重新确认」各项的落地结论，供 v0.2 写操作开发与后续维护参考。

- 环境：`use4.dm-us.informaticacloud.com`（Takeda Prod Org）
- 认证：企业 SSO（Okta），手动从浏览器提取 `USER_SESSION` Cookie
- 联调日期：2026-06-29
- 验证文件夹：`JAPAN_SCRM_DI`
- 结论以**实测为准**，与 ADR / 官方 KB 的差异已在下文标注。

> 所有实测均为只读 GET。文中不含任何 token / cookie 真值。

---

## 1. 认证（Header 注入）

| API | 认证 Header | 实测结果 |
|-----|------------|---------|
| V2 | `icSessionId: <USER_SESSION>` | ✅ 200（`/saas/api/v2/connection` 返回连接列表） |
| V3 | `INFA-SESSION-ID: <USER_SESSION>` | ✅ 200（`/saas/public/core/v3/objects`） |
| V3（误用）| `Cookie: USER_SESSION=<...>` | ❌ 401 |

**结论**：Takeda 环境下 `USER_SESSION` Cookie 值可直接作为 `icSessionId`（V2）/
`INFA-SESSION-ID`（V3）的 header value，与 ADR D-2 假设一致。**不能**用 `Cookie:`
头方式传（401）。这是 Takeda 环境特定行为，非 IICS 通用规范。

---

## 2. V3 对象枚举端点（重要修正）

ADR 原假设路径 `/api/v3/objects`，实测 **404**。逐一探测结果：

| 候选 URL | Header | 结果 |
|----------|--------|------|
| `/api/v3/objects` | INFA-SESSION-ID | ❌ 404 |
| `/saas/api/v3/objects` | INFA-SESSION-ID | ⚠️ 200 但返回 **identity-service 登录 HTML**（假阳性，非 API） |
| `/public/core/v3/objects` | INFA-SESSION-ID | ❌ 404（缺 `/saas`） |
| **`/saas/public/core/v3/objects`** | **INFA-SESSION-ID** | ✅ **200，返回 JSON** |

**结论**：正确端点为 **`/saas/public/core/v3/objects`**（与官方 KB 的
`/public/core/v3/objects` 一致，但**位于 `/saas` 下**）。

**代码影响**：
- `build_v3_url()`：V3 public API 与 V2 同样位于 `/saas` 下，**不再剥离** `/saas`，
  改为确保 `/saas` 前缀。
- `tools/query.py`：V3 path 常量 `_V3_OBJECTS_PATH = "public/core/v3/objects"`。

---

## 3. V3 查询语法（`q` 参数）

通过 `params={"q": ...}` 传入（不手拼 query string）。实测：

| 查询 | 结果 |
|------|------|
| `location=="JAPAN_SCRM_DI"` | ✅ 200 |
| `location=="..." AND type=="DSS"`（大写 AND） | ❌ 400 `V3API_ObjectsListError_04 Query parse error` |
| `location=="..." and type=="DSS"`（小写 and） | ✅ 200 |
| `type=="DSS"`（单独） | ✅ 200 |
| `name=="<task>"` | ❌ 400 `Invalid query parameter` |
| `path=="JAPAN_SCRM_DI/<task>"` | ❌ 400 `Invalid query parameter` |

**结论**：
- 逻辑连接符**必须小写** `and` / `or`（大写报 parse error）。
- `q` 可过滤字段实测仅 `location`、`type`（另据 KB 还有 `tag`、`updateTime`，未验证）。
- **`name` / `path` 不可作为查询字段**（400）。这导致**无法在 V3 按名直接定位/消歧**。

**代码影响**：
- `list_tasks()`：`q` 用小写 `and`；类型过滤用服务端 `type=="..."`。
- `get_task_detail()`：**取消 V3 按名消歧**（无可用字段），改为直接走 V2 by-name。
  跨文件夹同名消歧待后续有可查询标识时再实现。

---

## 4. V3 分页机制（重要修正）

ADR 原假设 `nextPageToken`，实测**不存在**。响应仅含 `count` + `objects`：

```json
{ "count": 4599, "objects": [ { "id": "...", "path": "...", "type": "DSS", ... } ] }
```

| 探测 | 结果 |
|------|------|
| `pageSize=200 / 1000 / 5000` | 返回数恒为 **200**（pageSize **上限 200**） |
| `skip=200` | ✅ 返回**下一段**（首/末 id 不同）→ **skip 是有效偏移** |
| `offset=200` / `page=2` | ❌ 被忽略（与第一页相同） |
| 响应中 `nextPageToken` | ❌ 不存在 |

**结论**：分页用 **`skip` 偏移**，`pageSize` 上限 **200**，无翻页 token；用响应
`count` 字段判断是否取完。

**代码影响**：`list_tasks()` 循环 `skip += len(items)`，至空页或 `skip >= count` 停止。

---

## 5. 对象类型字符串（JAPAN_SCRM_DI 实测计数）

`location=="JAPAN_SCRM_DI" and type=="<T>"` 的 `count`：

| type 串 | count | 是否任务类型 |
|---------|------:|------------|
| `DSS` | 847 | ✅ Synchronization Task |
| `MTT` | 1873 | ✅ Mapping Task |
| `WORKFLOW` | 37 | ✅ Linear Taskflow |
| `MAPPING` | 1641 | ❌ Mapping 设计（非任务） |
| `TASKFLOW` | 26 | ❌ 高级 Taskflow（与 WORKFLOW 不同） |
| `MTTASK` / `LINEAR_TASKFLOW` | 0 | —（非该 API 的 type 串） |
| 文件夹总对象（不限 type） | 4599 | 含上述各类 |

**结论**：v0.1 三种任务类型与 V3 type 串**一一对应**（`DSS` / `MTT` / `WORKFLOW`）。
`ALL` 模式按这三类分别查询合并（共 847+1873+37 = 2757），**排除** `MAPPING` 等非任务
对象。`normalize_task_type()` 的别名映射仍保留以防其它环境返回 `SYNC_TASK` 等变体。

---

## 6. V3 对象响应结构

`objects[]` 每项实测字段：

```
id, path, type, description, updatedBy, updateTime, tags, sourceControl, customAttributes
```

**注意**：**无 `name` 字段**——任务名 = `path` 的**末段**（`path.split("/")[-1]`）。
**无 `status` 字段**。

**代码影响**：`list_tasks()` 用 `_object_name()` 从 `path` 取名；`status` 取不到留空。

---

## 7. V2 详情端点

| 端点 | 结果 |
|------|------|
| `/saas/api/v2/connection` | ✅ 200（连接列表） |
| `/saas/api/v2/connection/<id>` | ✅ 200（用于 `resolve_connection`） |
| `/saas/api/v2/dsstask/name/<name>` | ✅ 200（DSS 详情，~3s） |

`build_v2_url()` 逻辑（`/saas` 前缀、不重复）与 ADR D-3 一致，**实测无需修改**。

### 7.1 DSS（dsstask）详情字段（实测）

包含连接字段：

```
sourceConnectionId, targetConnectionId   # ✅ 可稳定抽取
```

连接 ID → 名称解析实测成功，例：
`000LVU0B000000000015 → SCRM_ORA_STDB`、`000LVU0B00000000009O → mysql_scrm_hnn_api`。

**⚠️ 数据限制**：DSS 详情**不返回 runtime environment / agent**字段。runtime 相关仅有
`srcRuntimeAttrs` / `tgtRuntimeAttrs`，实测为空 `{"attrs": {}}`。

**结论**：`get_task_detail()` 的 `runtime_agent` 对 DSS **恒为空字符串**（best-effort，
不报错）。这是 API 数据可得性限制，非缺陷。MTT / WORKFLOW 的 runtime 字段尚未实测，
v0.2 需补充确认（可能在 `runtimeEnvironmentId` 或 mapping 参数中）。

---

## 8. 对 ADR-001「v2 前需重新确认项」的结论

| ADR 待确认项 | 实测结论 |
|--------------|---------|
| V3 路径 `/api/v3/objects` vs `/public/core/v3/objects` | ✅ 确认为 **`/saas/public/core/v3/objects`** |
| SSO Cookie = icSessionId / INFA-SESSION-ID 等价性 | ✅ 成立（Cookie 头方式则 401） |
| 分页参数 `pageSize` vs `limit` | `pageSize`（上限 200）**有效**，但翻页须用 **`skip`**，无 token |
| （新增）V3 `q` 大小写敏感 | 逻辑符**必须小写** `and`/`or` |
| （新增）V3 `q` 可查询字段 | 仅 `location` / `type`；`name` / `path` 不可查（400） |
| （新增）DSS runtime 字段 | 详情**不返回** runtime/agent |

---

## 9. 待 v0.2 / 后续确认

- MTT / WORKFLOW 详情的 runtime 与 connection 字段结构（v0.1 仅深度验证 DSS）。
- V3 `q` 是否支持 `tag` / `updateTime` 过滤（KB 提及，未验证）。
- 跨文件夹同名 task 的可靠区分标识（V3 `id` 或 V2 by-id 路由）。
- 写操作（update/batch）的 PARTIAL UPDATE 行为——v0.1 严格只读，未触碰。
