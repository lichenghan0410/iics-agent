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
| `/saas/api/v2/dsstask/name/<name>` | ✅ 200（DSS 详情，~3s；DSS 默认路径） |

`build_v2_url()` 逻辑（`/saas` 前缀、不重复）与 ADR D-3 一致，**实测无需修改**。

### 7.0 MTT ID 体系与 FRS 详情端点（Amendment 2 依据）

V3 objects 枚举返回的 id 是 **federated / FRS ID**；V2 另有 **classic ID**，二者是不同体系：

| ID 类型 | 示例 |
|--------|------|
| federated / FRS | `fABfm4RCYqVfuVEi9e8BGb` |
| classic V2 | `000LVU0I0000000004TZ` |

| 端点 | federated ID | classic ID | 说明 |
|------|:---:|:---:|------|
| `GET /saas/api/v2/mttask/frs/{ID}` | ✅ 成功 | ❌ 失败 | **官方文档明确支持 federated ID**；项目 MTT 默认路径 |
| `GET /saas/api/v2/mttask/{ID}`（裸） | ✅ 实测成功 | ✅ 实测成功 | 属**实测兼容行为**，官方未明确保证；**不作为项目默认契约** |

**ID 类型约束**：`/mttask/frs/{id}` **严格要求 federated ID**，classic ID **不得**传入该端点。
代码依赖显式 `task_id_type="federated"` 契约，不做 `startswith("000")` 之类字符串启发式判断。

**MTT 是类型特例，不得推广到 DSS / WORKFLOW**——实测：

```
DSS:      V3 federated ID → GET /saas/api/v2/dsstask/{id}   → 500
WORKFLOW: V3 federated ID → GET /saas/api/v2/workflow/{id}  → 500
```

因此 DSS / WORKFLOW 继续走 V2 by-name（`dsstask/name/<name>` / `workflow/name/<name>`），
federated → V2 by-id 仅对 MTT（且必须经 `mttask/frs/`）成立。

### 7.1 DSS（dsstask）详情字段（实测）

包含连接字段：

```
sourceConnectionId, targetConnectionId   # ✅ 可稳定抽取
```

连接 ID → 名称解析实测成功，例：
`000LVU0B000000000015 → SCRM_ORA_STDB`、`000LVU0B00000000009O → mysql_scrm_hnn_api`。

**⚠️ 数据限制**：DSS 详情**不返回 runtime environment / agent**字段。runtime 相关仅有
`srcRuntimeAttrs` / `tgtRuntimeAttrs`，实测为空 `{"attrs": {}}`。

**结论**：`get_task_detail()` 的 `runtime_environment.name`（Amendment 3 前称 `runtime_agent`）
对 DSS **恒为空**（best-effort，不报错）。

**DSS `sourceObjects`（Amendment 7 展示规则）**：DSS raw 中 `sourceObjects` 为**数组**。本项目
DSS 11 列导出的 `Source Object` **只取 `sourceObjects[0].name`**（单值）。这是**项目展示决策**
（DSS 业务模型按单值处理），**不是**声称该 API 永远只存在一个 source object；如出现第二个及后续
元素，展示层忽略、不告警、不展开（未扩大实测结论）。这是 API 数据可得性限制，非缺陷。

### 7.2 MTT enrichment 端点（Amendment 3）

**Mapping**：`GET /saas/api/v2/mapping/{mappingId}`

- 可获取：`id`、`name`、`updateTime`。
- **不包含可靠的** `path` / `location` / `folder` / `folderId`。
- 因此 **Mapping Location = unavailable**（归一化为 `null`，**不得**等于 Task Location）。
- 未来或验证 `mapping.assetFrsGuid ↔ V3 MAPPING object id`，本次不实现、不猜测。

**Runtime Environment**：`GET /saas/api/v2/runtimeEnvironment/{runtimeEnvironmentId}`

- 用于 `runtimeEnvironmentId → Runtime Environment Name`。
- 明确：`runtimeEnvironmentId != agentId`；**不得**用 `agentId` 冒充 Runtime Environment。

### 7.3 parameters 顺序（transformation 权威来源）

实测：MTT 与 Mapping 的 `parameters[]` 顺序**可以不同**，例：

```
MTT     parameters 示例顺序：Source → Target
Mapping parameters 示例顺序：Target → Source
```

因此 **Transformation（顺序 / 运行时覆盖值 / Source-Target 信息）只能来自
`mttask/frs/{federated_id}.parameters[]`**，不得用 `mapping.parameters[]` 生成。

#### 7.3.1 Transformation Name（真实样本 `s_IFA_V_PV_CAE_S_01`，Amendment 4）

```
SOURCE parameter：name = $SQ_001$                     label = DBConnection_awsoracle
TARGET parameter：name = $T_BI_CALL2_DETAIL_VOD_STGDB$ label = DBConnection_awsoracle
```

**结论**：`parameter.label` 可能只是 connection parameter 的 display label（此例两侧 label 都是
`DBConnection_awsoracle`），**不能作为 Transformation Name 第一优先级**。

正式提取规则（Amendment 4）：**`parameter.name` 去首尾 `$` 优先 → 缺失/空时 fallback
`parameter.label`**。据此得 `SQ_001` / `T_BI_CALL2_DETAIL_VOD_STGDB`。

#### 7.3.2 Mapping Location

`mapping/{id}` 无可靠 `path` / `location` / `folder` / `folderId`，故 **Mapping Location =
unavailable / not resolved**（未解决）。Amendment 4 起从业务 Excel / compact 输出删除该列；
normalized `task_mapping.location` 仍为 `null`。`mapping.assetFrsGuid ↔ V3 MAPPING → path` 链
仍属**待验证**，未实现。

### 7.4 Multi-object（防御性，未真实验证）

`extendedObject.objects[]` 代码按数组安全解析（保序去重、支持多值、Excel 同格换行）。
但**当前真实样本均为** `singleMode = true`、`extendedObject.objects.length = 1`；
多对象场景属防御性实现，**尚未真实验证**。

### 7.5 WORKFLOW 详情与 child tasks（Amendment 5）

`GET /saas/api/v2/workflow/name/{name}` 顶层实测字段：`@type` / `id`（classic V2）/ `orgId` /
`name` / `createTime` / `updateTime` / `tasks[]` 等。真实样本
`TF_DI_JPN_GDM_BI_DWH_AYU_M_JGI_MST`（`JAPAN_SCRM_DI`）：

```
id = 000LVU0N0000000002JV, orgId = 000LVU, updateTime = 2025-12-16T01:12:02.000Z
tasks = [
  {taskId: 000LVU0Z000000006V4F, type: MTT, name: mt_..._WK,  stopOnError, stopOnWarning},
  {taskId: 000LVU0Z00000000087Z, type: MTT, name: mt_...},
]
```

- `tasks[]` 是 WORKFLOW child row 展开的**权威来源**；每个 child = 1 Excel 行。
- export 只使用 child 的 `taskId` / `type` / `name`（`stopOnError` / `stopOnWarning` 存在但本次
  **不导出**）。
- child `taskId` 按 V2 response 原样保留，**不做 ID 类型推断**（不得当作 V3 federated ID）。
- WORKFLOW export **不查询** child 的 MTT/DSS 详情（不新增请求）。

### 7.6 Selected Task Export 复用的现有边界（Amendment 6，无新增 API research）

`export-selected` 只是编排层，复用已实测的路径，**未引入新 API 结论**：

- DSS 详情：`dsstask/name/{name}`（by-name）。
- MTT 详情：V3 folder 枚举取 federated id → `mttask/frs/{federated_id}`。
- WORKFLOW 详情：`workflow/name/{name}`（by-name）。

**本 feature 未解决 DSS / WORKFLOW 的 organization 级同名 ambiguity**：by-name 端点无 folder 约束，
组织内其它 folder 若存在同名 DSS/WORKFLOW，仍无法保证取到 selected folder 的那个（summary
warning 提示，属固有风险，未扫描全组织）。MTT 经 folder discovery + federated id 无此问题。

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
- 跨文件夹同名 task：**MTT 已解决**（V3 federated id → `mttask/frs/{id}`，见 §7.0）；
  **DSS / WORKFLOW 仍未解决**（federated → V2 by-id 返回 500，继续 by-name）。
- 写操作（update/batch）的 PARTIAL UPDATE 行为——v0.1 严格只读，未触碰。
