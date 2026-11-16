# IICS API 实测笔记（v0.1 联调结论）

本文件记录 IICS Agent v0.1 真实 token 联调中**实测确认**的 API 行为，作为 ADR-001
「需要在 v0.2 前重新确认」各项的落地结论，供 v0.2 写操作开发与后续维护参考。

- 环境：`use4.dm-us.informaticacloud.com`（Takeda Prod Org）
- 认证（本文第 1 节的 2026-06-29 联调当时）：企业 SSO（Okta），手动从浏览器提取 `USER_SESSION` Cookie
- 认证（Amendment 10 起的默认路径）：多环境 username/password 自动 Login，见 §1.1。手动 `USER_SESSION` 已降级为 legacy 调试模式
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

> 本节记录的是 v0.1 初期的手动 token 用法。Amendment 10 起默认路径为 §1.1 的
> Login API；两者产出的 session 值在 header 用法上完全一致。

**结论**：Takeda 环境下 `USER_SESSION` Cookie 值可直接作为 `icSessionId`（V2）/
`INFA-SESSION-ID`（V3）的 header value，与 ADR D-2 假设一致。**不能**用 `Cookie:`
头方式传（401）。这是 Takeda 环境特定行为，非 IICS 通用规范。

---

### 1.1 V2 Login API（Amendment 10，Postman 实测）

```text
POST https://dm-us.informaticacloud.com/ma/api/v2/user/login
Content-Type: application/json
Accept: application/json

{"username": "<env account>", "password": "<env password>"}
```

响应（**只允许提取以下字段，其余读取后立即丢弃**）：

| 字段 | 用途 | 是否可展示 |
|------|------|-----------|
| `icSessionId` | V2/V3 共用的 session 值 | ❌ 绝不输出 |
| `serverUrl` | 本次会话的 pod 基址；每次以当前响应为准 | ⚠️ 仅 host 部分可展示 |
| `orgUuid` | **环境身份唯一强校验值**（TOFU pin） | ✅ |
| `orgId` | 短码，人工核对辅助 | ✅ |
| `id` | 账号标识，仅排障用 | ❌ 不在 CLI 输出中展示 |

响应体中**同时存在但必须丢弃**的字段（不写日志 / 不落盘 / 不入异常）：

```text
roles[]
usergroups[]（含内部维护人邮箱 createdBy / updatedBy）
emails
securityQuestion（安全问题原文）
password / securityAnswer（IICS 已脱敏为 "********"，非真值，但仍不保留）
```

实测确认：响应中**不存在** `orgName` 或其它 org 显示名字段。

**同一 icSessionId 可同时用于 V2 与 V3**：

| API | 认证 Header |
|-----|------------|
| V2 | `icSessionId: <icSessionId>` |
| V3 | `INFA-SESSION-ID: <icSessionId>` |

即不需要为 V2 / V3 分别登录。与第 1 节中 `USER_SESSION` Cookie 值的 header 用法一致，
区别只是 session 值的来源从「浏览器手动提取」变为「Login API 自动获取」。

`orgId` 短码与 Takeda 环境的已知对应（仅人工核对用，不作为强身份）：

```text
000LA8 → DEV
000LVT → TEST
000LVU → PROD
```

**认证状态码语义（实现契约）**：

| 场景 | 语义 | 处理 |
|------|------|------|
| Login POST 401 / 403 | 凭据本身无法登录 | `InvalidCredentialsError`，不重复 retry（防账号锁定） |
| 业务 GET 401 | session 失效 | 重新 Login → 重建 URL/Header → 重试一次；仍 401 → `AuthenticationError` |
| 业务 GET 403 | session 有效但权限不足 | `PermissionDeniedError`，**不 refresh** |

Session 为 idle timeout 模型：不解析 JWT `exp`，不做 per-request health-check；
进程内以 `last_activity_at` + 25 分钟阈值做 proactive refresh（optimization），
**401 才是权威失效判定**。

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

V3 objects 枚举返回的 id 是 **FRS ID**；V2 另有 **classic ID**，二者是不同体系：

| ID 类型 | 示例 |
|--------|------|
| FRS | `fABfm4RCYqVfuVEi9e8BGb` |
| classic V2 | `000LVU0I0000000004TZ` |

| 端点 | FRS ID | classic ID | 说明 |
|------|:---:|:---:|------|
| `GET /saas/api/v2/mttask/frs/{ID}` | ✅ 成功 | ❌ 失败 | **官方文档明确支持 FRS ID**；项目 MTT 默认路径 |
| `GET /saas/api/v2/mttask/{ID}`（裸） | ✅ 实测成功 | ✅ 实测成功 | 属**实测兼容行为**，官方未明确保证；**不作为项目默认契约** |

**ID 类型约束**：`/mttask/frs/{id}` **严格要求 FRS ID**，classic ID **不得**传入该端点。
代码依赖显式 `task_id_type="frs"` 契约，不做 `startswith("000")` 之类字符串启发式判断。

**MTT 是类型特例，不得推广到 DSS / WORKFLOW**——实测：

```
DSS:      V3 FRS ID → GET /saas/api/v2/dsstask/{id}   → 500
WORKFLOW: V3 FRS ID → GET /saas/api/v2/workflow/{id}  → 500
```

因此 DSS / WORKFLOW 继续走 V2 by-name（`dsstask/name/<name>` / `workflow/name/<name>`），
FRS → V2 by-id 仅对 MTT（且必须经 `mttask/frs/`）成立。

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
`mttask/frs/{frs_id}.parameters[]`**，不得用 `mapping.parameters[]` 生成。

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
- child `taskId` 按 V2 response 原样保留，**不做 ID 类型推断**（不得当作 V3 FRS ID）。
- WORKFLOW export **不查询** child 的 MTT/DSS 详情（不新增请求）。

### 7.6 Selected / get-task / export 复用的现有边界（Amendment 6 / 9，无新增 API research）

编排层复用已实测的路径，**未引入新 API 结论**（三类型均先 V3 folder discovery 取 frs_id）：

- V3 discovery：`GET /saas/public/core/v3/objects`（`list_tasks`），三类型共用。
- DSS 详情：`dsstask/name/{name}`（by-name）；`frs_id` 来自 discovery。
- MTT 详情：`mttask/frs/{frs_id}`；folder 同时是 detail identity。
- WORKFLOW 详情：`workflow/name/{name}`（by-name）；`frs_id` 来自 discovery。

**DSS / WORKFLOW 的 organization 级同名**：folder discovery 可可靠取得对应 FRS object 的
`frs_id`，但 by-name detail 端点无 folder 约束——组织内其它 folder 若存在同名，by-name 返回的
classic detail 无法保证是同一 folder 的对象（summary warning 提示，属 by-name 固有限制）。MTT 经
`mttask/frs/{frs_id}` 无此问题。

> **Amendment 8（已被 Amendment 9 supersede）** 曾规定 DSS/WORKFLOW 不接受 folder。
>
> **Amendment 9 明确 folder 语义（FRS discovery identity ≠ V2 detail identity）**：三类型 get-task
> 与 selected 输入**均要求 folder**。folder 经 **同一份 V3 `/objects` discovery**（`list_tasks`）
> 取得目标对象的 **FRS object ID**（`frs_id`）——这对 DSS/WORKFLOW 也是可靠的（folder 内 exact-name
> 命中唯一 FRS object）。但 DSS/WORKFLOW 的 **detail 仍走 V2 by-name**，`dsstask/name/{name}` /
> `workflow/name/{name}` **不带 folder 约束**：组织内其它 folder 存在同名时，by-name detail 无法
> 保证与 discovery 得到的 FRS object 是同一个。故：
> - `frs_id`（FRS folder identity）**可用**；`id`（by-name detail 的 classic V2 ID）的 folder
>   归属**不可保证**。这是 by-name endpoint 限制，**不是 FRS 与 classic ID 映射关系存疑**。
> - 三类型 normalized detail / Excel 统一暴露 `id`（classic）+ `frs_id`（FRS object ID）。
> - dedup 键三类型统一 `(type, folder, name)`；跨 folder 同名分属不同 FRS object，不去重、不阻断。
> - 编排层可用 `task_id=<frs_id> + task_id_type="frs"` 跳过二次 discovery（三类型一致；CLI/selected
>   不暴露）。**未新增** `POST /public/core/v3/lookup` 或 `/export`（现有 GET `/objects` 已够）。

### 7.7 MTT Last Run 与 Connection Parameter（Amendment 11，真实样本 + 兼容解析）

真实样本：`mt_DWH_TO_REG_V_DMF_SSA_FACT_RESULT_QUICK`（`GET mttask/frs/{frs_id}`；Source 参数化、
Target 非参数化）。**未新增 endpoint**，三个新字段全部来自同一份 MTT FRS detail。

#### 7.7.1 `lastRunTime`（实测与官方约定不符）

```
createTime  = "2021-11-17T23:57:11.000Z"   ← ISO 字符串
updateTime  = "2026-10-28T03:12:56.000Z"   ← ISO 字符串
lastRunTime = 1794466734000                ← epoch 毫秒（JSON number）
```

- 官方 REST API「所有 date/time 以 UTC ISO 返回」的约定**不适用于此字段**：同一响应中
  `lastRunTime` 实测为 epoch **毫秒**。`1794466734000` → `2026-11-12T06:58:54.000Z`
  （日本时间 2026-11-12 15:58:54）。
- 项目解析（`parse_last_run_time`）：唯一来源 `raw.lastRunTime`；输出固定 UTC
  `yyyy-MM-ddTHH:mm:ss.SSSZ`，不依赖执行机本地时区。正数 number 按毫秒（不猜测为秒）；
  带时区 ISO 转 UTC；缺失 / null / 空字符串 / `0` → `null` 且不告警（**不宣称「从未运行」**）；
  数值字符串、非法字符串、布尔、负数、非有限、越界、无时区日期时间 → `null` + warning。
- **支持的 ISO 输入格式**（项目自带解析，不依赖 `datetime.fromisoformat`；Python 3.10 / 3.11 /
  3.13 行为一致）：`YYYY-MM-DD(T|t|空格)HH:MM[:SS[.f{1,6}]](Z|z|±HH:MM|±HHMM)`。
  时区必填；秒可省略；小数秒 1–6 位（右补零到微秒，输出**截断**到毫秒）；偏移 `hh ≤ 23`、`mm ≤ 59`。
  **不支持**（→ `null` + warning）：basic 格式（如 `20261112T065854Z`）、仅日期、小数秒 > 6 位、
  `±HH`、逗号小数、闰秒 `:60`、日历越界、转 UTC 后越界。早期实现依赖 `fromisoformat`，在 3.10
  无法解析 1/2/4/5 位小数秒、在 3.11+ 却接受 basic 格式，跨版本不一致，已改为上述显式子集。
- 不用 `updateTime` 或 activityLog 补值；**不解释为「最近成功运行时间」**。
- 仅由 taskflow 触发的 MTT，`lastRunTime` 是否更新、与前端 Last Run 是否一致 **待验证**。
- DSS Last Run **已取消**，不计划用日志或 HTML 补值。

#### 7.7.2 `parameters[].uiProperties` / `runtimeParameterData`（非官方公开字段）

`uiProperties` 与 `runtimeParameterData` 的相关键**不在官方 mttask 公开字段表中**，本项目只按已知
样本做**兼容解析**，不得把命名推测当作官方语义。样本（Source 侧）：

```
uiProperties.connectionParameterized = "true"          ← 字符串，非 JSON boolean
uiProperties.connDefault             = "rsh_gdm_e2e_TEC"
uiProperties.paramName               = "parameter_rsh_gdm_e2e"
uiProperties.paramType-mapping       = "Connection"
runtimeParameterData.connectionParameterName     = "parameter_rsh_gdm_e2e"
runtimeParameterData.isConnectionRuntimeParameter = true
label = "parameter_rsh_gdm_e2e"; uiProperties.logcnx = "parameter_rsh_gdm_e2e"
```

Target 侧：`connectionParameterized = "false"`，无 `connDefault` / `paramName`，
`isConnectionRuntimeParameter = false`。

- **参数化判定**：`connectionParameterized` 可解析为明确 true/false（JSON boolean 或去空白、
  忽略大小写的 `"true"`/`"false"`；禁止 `bool("false")`）时以其为准；缺失时仅当
  `paramType-mapping == "Connection"` 或 `connectionParameterName` 非空才判 true，否则未知
  （`null`）；非法值 → `null` + warning；明确 false 与参数名冲突 → false + warning。
- **参数名**：仅对确认参数化的一侧提取，`connectionParameterName` 优先，fallback
  `uiProperties.paramName`；冲突取前者 + warning。**不从** `parameters[].name` / `label` /
  `paramLabel` / `logcnx` 推断。
- `isConnectionRuntimeParameter` 原样安全解析为 normalized `is_connection_runtime_parameter`，
  **不参与** parameterized 判定；它是否对应 UI「Allow parameter to be overridden at runtime」
  **尚未确认**（需「参数化但未勾选运行时覆盖」的样本），不增加别名或 Excel 列。

#### 7.7.3 Connection 列取值与「真实解析名称」的来源判定

默认：**真实解析名称 → `connDefault`（仅限确认参数化）→ connection ID**；`--no-resolve`：ID
（不调用 resolver、不用 `connDefault` 代替 ID）。

`GET connection/{id}` 响应无 `name` / `connectionName` 时，resolver 按既有契约以
`id → ""` 兜底，兜底值同样进入 `resolved`，**仅凭值无法区分「真实名称恰好等于 ID」与
「响应只有 ID」**。因此判定**不比较名称与 ID**，而是由 resolver 记录名称来源：

- 内部 `_resolve_connection_entries()` 额外返回 `has_name[id]`：该值是否真的来自响应的
  `name` / `connectionName`（与既有 `or` 链同一真值判断）。
- `ConnectionCache` 同时缓存名称与来源（`get_or_fetch_entry` / `get_entry`），缓存命中时来源
  信息不丢失、不重复请求；旧接口 `get` / `set` / `get_or_fetch` 行为不变。
- MTT 判定：id 在 `failed` 或不在 `resolved` → 无真实名称；`has_name[id]` 不为 True → resolver
  兜底、无真实名称；否则即真实名称，**即使它恰好等于 connection ID**。
- 公开 `resolve_connection()`（`resolve-connection` CLI、DSS 调用方）输出仍只有
  `{resolved, failed}`，值与此前完全相同；来源信息不进入其输出。

> 订正：初版实现采用名称与 ID 相等的启发式判断（`resolved[id]` 等于 id 本身即视为无名称）。
> 本轮 review 将其替换为 resolver 记录名称来源，并保留缓存来源信息。原启发式会把真实名称恰好
> 等于 ID 的连接误判为无名称、错误地展示 `connDefault` 并告警「returned no name」。
> 现行规则不再包含任何「名称 == ID」比较。

**输出的是任务配置值**，不承诺是某次运行最终使用的连接：参数文件（如本例
`JPN_COMM_DWH_DL.PARAM`）可能在允许覆盖且生效值不同时覆盖连接；项目**不读取参数文件**。

#### 7.7.4 展开规则的已知风险（本次不修复）

transformation 展开只看 `sourceConnectionId` / `targetConnectionId` 是否存在，**未按 `type`
严格过滤**。若 Lookup 等其它类型带同名字段，存在条件性误分类风险。明确参数化但缺少
connection ID 的 SOURCE/TARGET 项只记 warning（`kind: connection_parameter`），**不生成行**。

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
- 跨文件夹同名 task：**MTT 已解决**（V3 FRS object ID → `mttask/frs/{id}`，见 §7.0）；
  **DSS / WORKFLOW 的 FRS discovery 层已可按 folder 取得唯一 frs_id（Amendment 9），但 detail 层
  仍走 V2 by-name 无 folder 约束**（`dsstask/{frs_id}` / `workflow/{frs_id}` 返回 500，不可用）——
  detail 的 folder 消歧仍未解决（见 §7.6 Amendment 9 注）。
- 写操作（update/batch）的 PARTIAL UPDATE 行为——v0.1 严格只读，未触碰。
- （Amendment 11）`isConnectionRuntimeParameter` / `connectionParameterName` 的实际含义（需
  「连接参数化但未勾选运行时覆盖」样本）；Target 参数化任务与完全非参数化任务的真实样本核对；
  taskflow 触发的 MTT 的 `lastRunTime` 更新行为与前端一致性（见 §7.7）。
