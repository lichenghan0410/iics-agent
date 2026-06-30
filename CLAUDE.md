# IICS Agent

Takeda Japan IICS 运维工具，查询三个 JAPAN_* 文件夹下的 task 配置并导出 Excel。

**当前阶段：v0.1 Read-only MVP（只读模式，写操作不可用）。**

## 运行模式（v0.1）

- **Mode A（主）**：Claude Code CLI 读取本文件作为项目上下文，根据自然语言意图自动调用
  下方 `iics_cli.py` 子命令。
- **Mode C（辅）**：运维人员在终端**手动**执行同一组 `iics_cli.py` 命令（与 Mode A 生成的
  命令完全一致）。下方「可用命令」即手动调用速查。
- **不存在 Mode B / Cowork Interactive Agent**：该模式已在 ADR Amendment 1 移除，项目不
  实现 `iics_agent.py`，不依赖 Anthropic SDK。

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

## 安全边界

- v0.1 只读模式，以下操作不可用且不得实现：update、batch、write 相关子命令
- 不得调用 IICS 写 API（curl、直接 requests POST/PUT/PATCH/DELETE 等均禁止）
- 不得创建或修改 `tools/update.py`、`tools/batch.py`、`tools/safety.py`
- 不得输出、打印、写入文件或以任何形式泄露 `IICS_SESSION_TOKEN`
- 如果用户要求修改 runtime / connection，回答：当前 v0.1 不支持写操作，v0.2 版本提供

## 运行目录

所有命令必须在包含 `iics_cli.py` 的项目根目录执行：

```bash
cd "D:\IICS Agent"   # 若当前不在此目录，先 cd
python iics_cli.py list-tasks ...
```
