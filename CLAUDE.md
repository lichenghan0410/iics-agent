# IICS Agent

Takeda Japan IICS 运维工具。

**当前阶段：v0.1 Read-only MVP。**

当前能力包括：

- 查询 IICS task 列表
- 查询 task 详情
- 解析 connection ID
- 导出 Excel / JSON
- 导出用户指定的 task 清单

v0.1 不支持任何 IICS 写操作。

---

## 运行模式

### Mode A — Claude Code

Claude Code 负责：

- 理解用户自然语言意图
- 选择合适的 `iics_cli.py` capability
- 整理必要输入
- 调用 CLI
- 解释 Python 返回的 JSON
- 向用户报告结果

### Mode C — Manual CLI

运维人员可以直接在终端执行同一套 `iics_cli.py` 命令。

Mode A 与 Mode C 使用完全相同的 Python 实现；唯一区别是命令由 Claude Code 生成还是由人工输入。

### 职责边界

**Agent：**

- 输入理解
- capability 选择
- orchestration
- 用户沟通与结果解释

**Python：**

- validation
- FRS discovery
- IICS API 调用
- routing
- parsing
- caching
- enrichment
- Excel / JSON 生成
- authoritative counts / failures / warnings / file paths

不要在 Agent 层重新实现 Python 已负责的业务逻辑。

---

## Authentication

IICS 使用浏览器 SSO `USER_SESSION` 值，并通过环境变量：

```text
IICS_SESSION_TOKEN
```

提供给 Python。

Token 过期后，需要由运维人员重新从浏览器获取 `USER_SESSION` 并重新设置环境变量。

不得：

- 输出 token
- echo token
- 将 token 写入文件
- 将 token 写入日志
- 将 token 提交 Git
- 以任何形式向用户或其他输出暴露 token

人工获取 `USER_SESSION` 的完整操作步骤见：

```text
README.zh-CN.md
```

---

## Scope

支持的 IICS folders：

- `JAPAN_COMMERCIAL_DWH`
- `JAPAN_SCRM_BAU`
- `JAPAN_SCRM_DI`

支持的 task types：

- `DSS`
- `MTT`
- `WORKFLOW`

目标 IICS Org：

```text
use4.dm-us.informaticacloud.com
```

---

## Tool Capabilities

所有已支持的 IICS 操作必须通过：

```text
iics_cli.py
```

当前公开 capability：

- `list-tasks`
- `get-task`
- `resolve-connection`
- `export`
- `export-selected`

不要绕过现有 CLI / Python tool layer 自行重新实现已经支持的操作。

完整命令 syntax、参数、task-type-specific procedure、selected export workflow 和结果解释规则放在：

```text
.claude/skills/iics-agent/SKILL.md
```

---

## CLI Result Contract

`iics_cli.py` 的输出契约：

- stdout：machine-readable JSON result
- stderr：progress / diagnostic logs
- 成功命令退出码：`0`
- 顶层返回 `{"error": ...}` 时退出码：`1`

解释命令结果时：

- 以 stdout JSON 为业务结果
- 不把 stderr 日志当作业务数据
- 不自行重新统计 Python 已返回的数量
- Python 返回的 counts、failures、warnings、file paths 为权威结果

单个 task 或 enrichment 失败不一定意味着整个命令出现顶层 fatal error；按具体 JSON result 判断。

---

## Critical IICS Invariants

以下规则在所有相关 IICS 操作中始终成立。

### Task identity and detail routing

所有 `DSS` / `MTT` / `WORKFLOW` 的 `get-task` 操作都要求提供 `folder`，用于 folder-scoped FRS discovery。

### MTT

MTT detail 必须使用：

```text
mttask/frs/{frs_id}
```

不得：

- 使用裸 `mttask/{id}` 作为正式 detail route
- fallback 到 `mttask/name/{name}`
- 绕过正式 CLI 自行构造替代 API route

MTT 的 folder 参与可靠的 FRS detail identity，可以解决跨 folder 同名 MTT 的 detail 消歧。

### DSS / WORKFLOW

DSS detail 仍使用 V2 by-name：

```text
dsstask/name/{name}
```

WORKFLOW detail 仍使用 V2 by-name：

```text
workflow/name/{name}
```

其 required `folder` 用于 FRS discovery，并可靠取得对应 FRS object 的 `frs_id`。

但是：

**folder 不能给 DSS / WORKFLOW 的 V2 by-name detail endpoint 增加 folder 约束。**

因此当组织内其他 folder 存在同名 DSS / WORKFLOW 时：

- `frs_id` 可以可靠代表 folder-scoped FRS object
- V2 by-name 返回的 classic detail 无法保证属于同一个 folder

不得将 V3 FRS ID 直接作为 DSS / WORKFLOW 的 V2 by-id detail route。

### MTT transformation source

MTT transformations 的权威结果是 Python 已返回的 `transformations` 字段
（来自 MTT `raw.parameters[]`，按原始顺序展开，不去重、不只取首尾）。

不得：

- 从 `raw` 数据自行重新提取或重新排序 transformations
- 使用 Mapping 的 `parameters[]` 生成或补充 transformations

### Mapping Location

Mapping Location 当前没有可靠来源。

不得：

- 从 Task Location 推测 Mapping Location
- 将 Task Location 填入 Mapping Location
- 在没有可靠 API 数据时自行猜测该字段

### Export counts

以下指标语义不同：

- `task_count`：逻辑 task 数
- `rows`：业务 sheet 的物理输出行数
- `failed_details_count`：主详情请求失败数
- `enrichment_warnings_count`：enrichment warning 数

MTT transformation 展开、WORKFLOW child 展开等情况会使：

```text
rows > task_count
```

因此：

**不得使用 `rows == task_count` 作为 export 成功定义。**

判断结果时必须使用 Python 返回的实际 summary、failures 和 warnings。

### Selected task list

当用户提供的是一份明确的 task 清单时：

**必须使用 `export-selected`。**

不得用：

```text
export <folder>
```

替代 selected export，因为 folder-wide export 会查询用户清单之外的 task。

selected list 的输入整理、validation、dedup、FRS discovery 和 reporting procedure 由 `iics-agent` Skill 定义，并由 Python 执行最终校验和导出。

---

## Safety Boundary

v0.1 是严格只读版本。

不得：

- 调用 IICS POST / PUT / PATCH / DELETE 业务 API
- 实现 update / batch / write capability
- 调用尚不存在的写操作逻辑
- 使用 curl 或 ad-hoc HTTP request 绕过正式 CLI
- 输出、记录、保存或泄露 `IICS_SESSION_TOKEN`

不要创建或使用用于 v0.2 写操作的实现，例如：

```text
tools/update.py
tools/batch.py
tools/safety.py
```

Python HTTP client 是 executable safety enforcement 的权威层；Claude instructions 是行为约束，不替代 Python enforcement。

如果用户要求修改 runtime、connection 或其他 IICS 配置，应明确说明：

```text
当前 v0.1 不支持写操作。
```

---

## Working Directory

默认认为 Claude Code 已经在 IICS Agent 项目根目录启动。

如果用户明确说明已经位于项目根目录，直接信任并执行任务。

不要为了确认目录而主动执行：

```text
pwd
ls
dir
find
```

不要：

- 主动搜索项目位置
- 遍历磁盘
- 猜测固定安装路径
- 自动切换到历史路径
- 连续运行多个目录探测命令

只有当实际命令明确出现项目文件找不到的错误时，例如：

```text
can't open file 'iics_cli.py'
No such file or directory
ModuleNotFoundError: No module named 'tools'
```

才允许做一次最小检查：

```bash
test -f iics_cli.py
```

如果文件存在，继续原任务。

如果文件不存在，停止执行并提示用户：

```text
当前目录似乎不是 IICS Agent 项目根目录。请在包含 iics_cli.py 的目录重新启动 Claude Code，或提供实际项目路径。
```

不要继续遍历磁盘寻找项目。

同样，不要主动定位 Python 安装路径。

只有在 `python ...` 命令实际失败并明确提示 Python 不可用时，才进入 Python 环境诊断。
