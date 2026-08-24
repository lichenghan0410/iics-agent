# IICS Agent

Takeda Japan IICS 运维工具，查询或改修三个 JAPAN_* 文件夹下的 task 配置并导出 Excel。

**当前阶段：v0.1 Read-only MVP（只读模式，写操作不可用）。**

## 运行模式（v0.1）

- **Mode A（主）**：Claude Code CLI 读取本文件作为项目上下文，根据自然语言意图自动调用
  下方 `iics_cli.py` 子命令。
- **Mode C（辅）**：运维人员在终端**手动**执行同一组 `iics_cli.py` 命令（与 Mode A 生成的
  命令完全一致）。下方「可用命令」即手动调用速查。


## 环境准备

```bash
# 1. SSO 登录 IICS Web UI（Okta 弹窗验证）
# 2. 浏览器 DevTools → Network 面板 → 任意 IICS 请求头中找 Cookie: USER_SESSION=<value>
export IICS_SESSION_TOKEN="<USER_SESSION 值>"
# Token 过期后 API 返回 401，需重新从浏览器获取并重新 export
```

## 可用命令

> v0.1 已实现并通过真实 token smoke test。Mode A 自动生成、Mode C 手动输入，命令一致。

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

## MTT 详情强制规则（Amendment 2）

- MTT 的 name 查询**必须带 `--folder`**。
- 必须通过 **V3 folder 枚举取得 federated ID**，再调用 **`mttask/frs/{federated_id}`**。
- **不得**调用裸 `mttask/{id}`。
- **不得**回退 `mttask/name/{name}`。
- **不得**接受用户直接传入 MTT `--id`。
- 内部 task_id 调用**必须显式标记** `task_id_type="federated"`。
- `task_id` 与 `task_name`/`folder` **不得同时提供**。

## MTT 信息增强 / 导出（Amendment 3）

- MTT 归一化 detail：顶层 `id`=classic V2 ID、`federated_id`=V3/FRS ID（**无 `id_type`**）。
- runtime 字段统一为 `runtime_environment {id,name}`（旧名 `runtime_agent` 已弃用）；
  MTT 用 `runtimeEnvironmentId → runtimeEnvironment/{id}`，**不得**用 `agentId`。
- mapping 用 `mappingId → mapping/{id}`（name/updated_on）；`Mapping Location` **不可靠获取、
  不得推测**（Amendment 4 起从业务 Excel / compact 删除；normalized `task_mapping.location` 仍 null）。
- transformation **只来自 MTT `parameters[]`**（顺序保留、不去重、不取首尾），不得用 mapping parameters。
- **Transformation Name（Amendment 4）**：`parameter.name` 去首尾 `$` **优先**，name 缺失/空才
  fallback `parameter.label`（label 可能只是 connection display label，不能作第一优先级）。
- **`get-task MTT` 默认 compact output**（Task Summary 同款业务字段 records）；`--full` 才展示完整
  normalized detail（诊断用）。DSS/WORKFLOW 保持扁平 dict、不受影响。`get_task_detail()` 返回值不变。
- Excel：`Task Summary`（**Amendment 4 起 22 列**，删除 Mapping Location）+ `Export Errors` 两个 sheet；
  **不得新增 MTT 专属 sheet**；MTT 每个 transformation 一行。
- 计数：`task_count`（==list_tasks.count）与 `rows`（物理行数）**分离**；完整成功=task_count 一致
  且 failed_details_count==0；**不得**用 `rows==task_count` 判断。
- enrichment（mapping/runtime/connection）失败 → `enrichment_warnings`，**不**计入 `failed_details`、不终止导出。

## DSS / WORKFLOW 规则

- **不得**使用 `task_id` / `task_id_type` 新参数。
- **不得**用 V3 federated ID 调 V2 by-id（实测返回 500，是 MTT 类型特例）。
- 保持现状 V2 by-name；跨 folder 同名仍是已知限制。

## WORKFLOW export 规则（Amendment 5）

- WORKFLOW 详情继续用 `workflow/name/{name}`（不改路由；不得把 child `taskId` 当 V3 federated ID）。
- WORKFLOW export **只展开 `raw.tasks[]`**：一个 child = 一行；**不得**为 child 再查
  `mttask/{id}` / `mttask/name/{name}` / `workflow/{child_id}` 等 child 详情。
- WORKFLOW Excel 用**独立 9 列 schema**：`Task Type, folder, orgId, id, Task Name, updateTime,
  tasks.taskId, tasks.type, tasks.name`（含 `folder`，取自 export 的 `<FOLDER>`）。父字段逐行重复。
- 无 child 的 workflow 仍保留 1 行；detail 失败保留 1 fallback 行并计入 `rows`（按 `max(1,len)`）。
- WORKFLOW `get-task` 输出不变。
- `--type WORKFLOW` 单类型：单张 9 列 `Task Summary`。**`--type ALL`：通用 `Task Summary`（DSS+MTT）
  + 单独 `Workflow Tasks`（9 列 child 展开）+ `Export Errors`**（Li 已确认分表方案）。

## 安全边界

- v0.1 只读模式，以下操作不可用且不得实现：update、batch、write 相关子命令
- 不得调用 IICS 写 API（curl、直接 requests POST/PUT/PATCH/DELETE 等均禁止）
- 不得创建或修改 `tools/update.py`、`tools/batch.py`、`tools/safety.py`
- 不得输出、打印、写入文件或以任何形式泄露 `IICS_SESSION_TOKEN`
- 如果用户要求修改 runtime / connection，回答：当前 v0.1 不支持写操作，v0.2 版本提供

## 运行目录

默认认为 Claude Code 已经在 IICS Agent 项目根目录启动。

如果用户在对话中明确说明“已经在项目根目录”，直接信任并执行任务，不要再用 `pwd`、`ls`、`dir`、`find` 等命令验证当前目录。

不要主动搜索项目路径，不要遍历磁盘，不要猜测或切换到任何固定路径。

只有在命令报错提示找不到项目文件时，才允许做一次最小检查。例如：

```text id="e7a3xf"
can't open file 'iics_cli.py'
No such file or directory
ModuleNotFoundError: No module named 'tools'
```

此时只检查一次 `iics_cli.py` 是否存在：

```bash id="hzzqhn"
test -f iics_cli.py
```

如果存在，继续执行原任务。

如果不存在，停止执行并提示用户：

```text id="nz6m42"
当前目录似乎不是 IICS Agent 项目根目录。请在包含 iics_cli.py 的目录重新启动 Claude Code，或提供实际项目路径。
```

不要连续执行多个目录探测命令。

不要主动定位 Python 安装路径；只有在 `python ...` 命令失败并明确提示找不到 Python 时，才进行 Python 环境诊断。
