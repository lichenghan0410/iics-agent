"""
tools/selected_exporter.py — Selected Task List Export 编排层（Amendment 6）

只读 integration layer：给定混合 task list（name/type/folder）→ 校验/标准化/去重 →
只查询 list 中指定的 task → 按类型分别导出 3 个独立 Excel → 返回 summary JSON。

复用现有底层：get_task_detail（DSS by-name / WORKFLOW by-name / MTT FRS）、list_tasks
（MTT folder discovery）、export_results（各类型 exporter / row builder / Export Errors /
Connection·Mapping·Runtime 缓存）。本模块不重新实现任何 parser / exporter / resolver。

安全边界：只 GET IICS API + 本地读 JSON / 写 Excel；无写操作；不打印 token。
"""

import json
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from tools.query import list_tasks, get_task_detail
from tools.exporter import export_results

_VALID_TYPES = ("DSS", "MTT", "WORKFLOW")
_SESSION_RUN_THRESHOLD = 5


# ── 输入校验 / 标准化 ────────────────────────────────────────────────────────
def validate_selected_tasks(raw):
    """校验并标准化 selected task 输入。返回 (normalized_list | None, input_errors)。

    Amendment 8：folder 语义按类型区分。
    - MTT：folder 是真实 identity/discovery 数据，必须存在、为字符串、trim 后非空。
    - DSS / WORKFLOW：folder 在 detail 层不可用。允许缺省；若给出且 trim 后为空
      （""/空白）→ 归一化丢弃 folder key；若给出非空值 → SelectedTaskInputError
      快速失败（folder is only supported for MTT selected tasks），不触任何 API。
    """
    if not isinstance(raw, list):
        return None, [{"index": None, "field": None,
                       "error_type": "SelectedTaskInputError",
                       "message": "top-level input must be a JSON array"}]
    errors = []
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append({"index": i, "field": None,
                           "error_type": "SelectedTaskInputError",
                           "message": "item must be a JSON object"})
            continue
        # name / type 为所有类型必需
        vals = {}
        bad = False
        for f in ("name", "type"):
            if f not in item:
                errors.append({"index": i, "field": f,
                               "error_type": "SelectedTaskInputError",
                               "message": f"{f} is required"})
                bad = True
                continue
            v = item.get(f)
            if not isinstance(v, str):
                errors.append({"index": i, "field": f,
                               "error_type": "SelectedTaskInputError",
                               "message": f"{f} must be a string"})
                bad = True
                continue
            vals[f] = v
        if bad:
            continue
        name = vals["name"].strip()
        ttype = vals["type"].strip().upper()
        if not name:
            errors.append({"index": i, "field": "name",
                           "error_type": "SelectedTaskInputError",
                           "message": "name must not be empty"})
            continue
        if ttype not in _VALID_TYPES:
            errors.append({"index": i, "field": "type",
                           "error_type": "SelectedTaskInputError",
                           "message": "type must be DSS, MTT, or WORKFLOW"})
            continue

        if ttype == "MTT":
            if "folder" not in item:
                errors.append({"index": i, "field": "folder",
                               "error_type": "SelectedTaskInputError",
                               "message": "folder is required for MTT selected tasks"})
                continue
            fv = item.get("folder")
            if not isinstance(fv, str):
                errors.append({"index": i, "field": "folder",
                               "error_type": "SelectedTaskInputError",
                               "message": "folder must be a string"})
                continue
            folder = fv.strip()
            if not folder:
                errors.append({"index": i, "field": "folder",
                               "error_type": "SelectedTaskInputError",
                               "message": "folder must not be empty for MTT selected tasks"})
                continue
            out.append({"name": name, "type": ttype, "folder": folder})
        else:
            # DSS / WORKFLOW：folder 可缺省；给出非空值即快速失败
            if "folder" in item and item.get("folder") is not None:
                fv = item.get("folder")
                if not isinstance(fv, str):
                    errors.append({"index": i, "field": "folder",
                                   "error_type": "SelectedTaskInputError",
                                   "message": "folder must be a string"})
                    continue
                if fv.strip():
                    errors.append({"index": i, "field": "folder",
                                   "error_type": "SelectedTaskInputError",
                                   "message": "folder is only supported for MTT "
                                              "selected tasks"})
                    continue
                # 空白 folder → 归一化丢弃
            out.append({"name": name, "type": ttype})
    if errors:
        return None, errors
    return out, []


def _selection_identity(item):
    """去重身份键：MTT 用 (type, folder, name)；DSS / WORKFLOW 用 (type, name)。"""
    if item["type"] == "MTT":
        return (item["type"], item.get("folder", ""), item["name"])
    return (item["type"], item["name"])


def _dedup(tasks):
    """按 _selection_identity 去重，保持首次出现顺序。"""
    seen = set()
    out = []
    for t in tasks:
        key = _selection_identity(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


# ── MTT folder discovery ─────────────────────────────────────────────────────
def build_mtt_discovery_index(client, folder):
    """对单个 folder 执行一次 list_tasks(folder, MTT)，建立 name → [candidates]。

    返回 (index, error)：error 非 None 表示该 folder discovery 失败。
    """
    listed = list_tasks(client, folder_path=folder, task_type="MTT")
    if isinstance(listed, dict) and "error" in listed:
        return {}, {"error_type": listed.get("error_type", ""),
                    "error": listed.get("error", "")}
    index = {}
    for obj in listed.get("tasks", []):
        index.setdefault(obj.get("name", ""), []).append(obj)
    return index, None


# ── 分类型处理 ───────────────────────────────────────────────────────────────
def _empty_result():
    return {"requested": 0, "success": 0, "failed": 0, "rows": 0,
            "enrichment_warnings_count": 0, "file_path": None}


def _write_type(client, ttype, details, failed_local, output_dir, ts):
    """调用现有 exporter 生成该类型 workbook，返回 (rows, file_path, enrich_count)。"""
    fn = str(Path(output_dir) / f"selected_{ttype}_{ts}.xlsx")
    data = {"folder": "", "tasks": [], "details": details,
            "failed_details": failed_local}
    res = export_results(client, data=data, format="excel", filename=fn,
                         export_type=ttype)
    if "error" in res:
        return 0, None, 0
    return res.get("rows", 0), res.get("file_path"), res.get("enrichment_warnings_count", 0)


def _process_by_name(client, ttype, tuples, failures, output_dir, ts):
    """DSS / WORKFLOW：纯 by-name detail（Amendment 8：无 folder，无 ambiguity 分支）。"""
    requested = len(tuples)
    if requested == 0:
        return _empty_result()
    details = []
    failed_local = []
    for t in tuples:
        d = get_task_detail(client, task_name=t["name"], task_type=ttype)
        if isinstance(d, dict) and "error" in d:
            f = {"type": ttype, "name": t["name"],
                 "error_type": d.get("error_type", ""), "error": d.get("error", "")}
            failed_local.append(f)
            failures.append(f)
            continue
        details.append(d)
    rows, file_path, enrich = _write_type(client, ttype, details, failed_local,
                                          output_dir, ts)
    success = len(details)
    return {"requested": requested, "success": success,
            "failed": requested - success, "rows": rows,
            "enrichment_warnings_count": enrich, "file_path": file_path}


def _process_mtt(client, tuples, failures, output_dir, ts):
    """MTT：每 folder 一次 discovery → name index → federated id → 现有 FRS detail。"""
    requested = len(tuples)
    if requested == 0:
        return _empty_result()

    folders = list(OrderedDict((t["folder"], None) for t in tuples).keys())
    index_by_folder = {}
    error_by_folder = {}
    for folder in folders:
        idx, err = build_mtt_discovery_index(client, folder)
        index_by_folder[folder] = idx
        error_by_folder[folder] = err

    details = []
    failed_local = []
    for t in tuples:
        folder, name = t["folder"], t["name"]
        ferr = error_by_folder.get(folder)
        if ferr:
            f = {"type": "MTT", "folder": folder, "name": name,
                 "error_type": ferr.get("error_type", ""),
                 "error": ferr.get("error", "")}
            failed_local.append(f)
            failures.append(f)
            continue
        cands = index_by_folder.get(folder, {}).get(name, [])
        if len(cands) == 0:
            f = {"type": "MTT", "folder": folder, "name": name,
                 "error_type": "selected_task_not_found",
                 "error": "Selected MTT task was not found in the given folder."}
            failed_local.append(f)
            failures.append(f)
            continue
        if len(cands) > 1:
            f = {"type": "MTT", "folder": folder, "name": name,
                 "error_type": "ambiguous_mtt",
                 "error": "Multiple MTT candidates matched the same name in the folder.",
                 "candidates": [{"id": c.get("id", ""), "path": c.get("path", ""),
                                 "name": c.get("name", ""), "type": c.get("type", "")}
                                for c in cands]}
            failed_local.append(f)
            failures.append(f)
            continue
        federated_id = cands[0].get("id", "")
        d = get_task_detail(client, task_type="MTT", task_id=federated_id,
                            task_id_type="federated")
        if isinstance(d, dict) and "error" in d:
            f = {"type": "MTT", "folder": folder, "name": name,
                 "error_type": d.get("error_type", ""), "error": d.get("error", "")}
            failed_local.append(f)
            failures.append(f)
            continue
        if not d.get("task_name"):
            d["task_name"] = name
        if not d.get("task_location"):
            d["task_location"] = folder
        details.append(d)

    rows, file_path, enrich = _write_type(client, "MTT", details, failed_local,
                                          output_dir, ts)
    success = len(details)
    return {"requested": requested, "success": success,
            "failed": requested - success, "rows": rows,
            "enrichment_warnings_count": enrich, "file_path": file_path}


def _session_warnings(failures):
    """§21.1：连续 >=5 次相同 session/401 相关 error_type → 追加提示（不 abort）。"""
    run_type = None
    run = 0
    max_run = 0
    max_type = None
    for f in failures:
        et = f.get("error_type", "")
        if et == run_type:
            run += 1
        else:
            run_type = et
            run = 1
        if run > max_run:
            max_run = run
            max_type = et
    if max_run >= _SESSION_RUN_THRESHOLD and max_type == "RuntimeError":
        return ["Multiple consecutive failures with the same error type were "
                "detected; this may indicate an expired session token rather than "
                "per-task issues."]
    return []


def export_selected_tasks(client, selected_tasks, output_dir):
    """selected task 编排主入口。selected_tasks 为已校验/标准化（未去重）的 list。"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    input_count = len(selected_tasks)
    unique = _dedup(selected_tasks)
    unique_count = len(unique)

    by_type = {t: [] for t in _VALID_TYPES}
    for t in unique:
        by_type[t["type"]].append(t)

    failures = []
    results = {}
    results["DSS"] = _process_by_name(client, "DSS", by_type["DSS"],
                                      failures, output_dir, ts)
    results["MTT"] = _process_mtt(client, by_type["MTT"], failures, output_dir, ts)
    results["WORKFLOW"] = _process_by_name(client, "WORKFLOW", by_type["WORKFLOW"],
                                           failures, output_dir, ts)

    warnings = []
    if by_type["DSS"]:
        warnings.append("DSS details are resolved by name only; folder identity is "
                        "unavailable and duplicate names elsewhere in the organization "
                        "cannot be disambiguated.")
    if by_type["WORKFLOW"]:
        warnings.append("WORKFLOW details are resolved by name only; folder identity is "
                        "unavailable and duplicate names elsewhere in the organization "
                        "cannot be disambiguated.")
    warnings.extend(_session_warnings(failures))

    return {
        "input_count": input_count,
        "unique_count": unique_count,
        "duplicates_removed": input_count - unique_count,
        "results": results,
        "failures": failures,
        "warnings": warnings,
    }
