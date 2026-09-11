"""
tools/selected_exporter.py — Selected Task List Export 编排层（Amendment 6 / 9）

只读 integration layer：给定 task list（name/type/folder）→ 校验/标准化/去重 →
只查询 list 中指定的 task → 按类型分别导出 3 个独立 Excel → 返回 summary JSON。

Amendment 9：三类型统一 {name, type, folder}；每个 (folder, type) 只 discovery 一次
（list_tasks），取 frs_id 后经 get_task_detail(task_id=frs_id, task_id_type="frs") 直传，
不为每个 task 二次 discovery。去重键统一 (type, folder, name)。DSS/WORKFLOW 的 folder 用于
FRS discovery，但 by-name detail 无法按 folder 消歧（见 summary warnings）。

复用现有底层：get_task_detail、list_tasks、export_results（各类型 exporter / row builder /
Export Errors / Connection·Mapping·Runtime 缓存）。本模块不重新实现任何 parser/exporter/resolver。

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

    Amendment 9：三种类型统一要求 {name, type, folder}，folder 必须存在、为字符串、
    trim 后非空（folder 用于 FRS discovery 取 frs_id）。任何缺失/空白 folder →
    SelectedTaskInputError，先于任何 IICS API。selected 输入不暴露 task_id/task_id_type。
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
        # name / type / folder 三字段对所有类型均必需
        vals = {}
        bad = False
        for f in ("name", "type", "folder"):
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
        folder = vals["folder"].strip()
        ttype = vals["type"].strip().upper()
        if not name:
            errors.append({"index": i, "field": "name",
                           "error_type": "SelectedTaskInputError",
                           "message": "name must not be empty"})
            continue
        if not folder:
            errors.append({"index": i, "field": "folder",
                           "error_type": "SelectedTaskInputError",
                           "message": "folder must not be empty"})
            continue
        if ttype not in _VALID_TYPES:
            errors.append({"index": i, "field": "type",
                           "error_type": "SelectedTaskInputError",
                           "message": "type must be DSS, MTT, or WORKFLOW"})
            continue
        out.append({"name": name, "type": ttype, "folder": folder})
    if errors:
        return None, errors
    return out, []


def _selection_identity(item):
    """去重身份键（Amendment 9）：三种类型统一 (type, folder, name)。

    跨 folder 同名（含 DSS/WORKFLOW）分属不同 FRS object，不去重、不 ambiguous_input 阻断。
    """
    return (item["type"], item.get("folder", ""), item["name"])


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


# ── folder discovery（Amendment 9：三类型共用）──────────────────────────────
def build_discovery_index(client, folder, task_type):
    """对单个 (folder, task_type) 执行一次 list_tasks，建立 name → [candidates]。

    三种类型共用（每个 unique (folder, type) 只调用一次 list_tasks）。
    返回 (index, error)：error 非 None 表示该 folder discovery 失败。
    """
    listed = list_tasks(client, folder_path=folder, task_type=task_type)
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


def _process_type(client, ttype, tuples, failures, output_dir, ts):
    """三类型统一处理（Amendment 9）：每 (folder, type) 一次 discovery → frs_id →
    detail 直传（task_id=frs_id, task_id_type="frs"，跳过二次 discovery）。

    - MTT：get_task_detail(task_type, task_id=frs_id, task_id_type="frs")，回填 name/location。
    - DSS/WORKFLOW：get_task_detail(task_name=name, task_type, task_id=frs_id,
      task_id_type="frs")，by-name detail + frs_id 回填。
    """
    requested = len(tuples)
    if requested == 0:
        return _empty_result()

    # 按 folder 分组，每个 unique folder 只 discovery 一次（一次 list_tasks 调用）
    folders = list(OrderedDict((t["folder"], None) for t in tuples).keys())
    index_by_folder = {}
    error_by_folder = {}
    for folder in folders:
        idx, err = build_discovery_index(client, folder, ttype)
        index_by_folder[folder] = idx
        error_by_folder[folder] = err

    details = []
    failed_local = []
    for t in tuples:
        folder, name = t["folder"], t["name"]
        ferr = error_by_folder.get(folder)
        if ferr:
            f = {"type": ttype, "folder": folder, "name": name,
                 "error_type": ferr.get("error_type", ""),
                 "error": ferr.get("error", "")}
            failed_local.append(f)
            failures.append(f)
            continue
        cands = index_by_folder.get(folder, {}).get(name, [])
        if len(cands) == 0:
            f = {"type": ttype, "folder": folder, "name": name,
                 "error_type": "selected_task_not_found",
                 "error": f"Selected {ttype} task was not found in the given folder."}
            failed_local.append(f)
            failures.append(f)
            continue
        if len(cands) > 1:
            # 同一 folder 内 discovery 无法唯一确定 FRS object（非跨 folder 同名问题）
            f = {"type": ttype, "folder": folder, "name": name,
                 "error_type": "ambiguous_task",
                 "error": f"Multiple {ttype} candidates matched the same name in the folder.",
                 "candidates": [{"frs_id": c.get("frs_id", ""), "path": c.get("path", ""),
                                 "name": c.get("name", ""), "type": c.get("type", "")}
                                for c in cands]}
            failed_local.append(f)
            failures.append(f)
            continue
        frs_id = cands[0].get("frs_id", "")
        if ttype == "MTT":
            d = get_task_detail(client, task_type="MTT", task_id=frs_id,
                                task_id_type="frs")
        else:
            d = get_task_detail(client, task_name=name, task_type=ttype,
                                task_id=frs_id, task_id_type="frs")
        if isinstance(d, dict) and "error" in d:
            f = {"type": ttype, "folder": folder, "name": name,
                 "error_type": d.get("error_type", ""), "error": d.get("error", "")}
            failed_local.append(f)
            failures.append(f)
            continue
        if not d.get("task_name"):
            d["task_name"] = name
        if ttype == "MTT" and not d.get("task_location"):
            d["task_location"] = folder
        details.append(d)

    rows, file_path, enrich = _write_type(client, ttype, details, failed_local,
                                          output_dir, ts)
    success = len(details)
    return {"requested": requested, "success": success,
            "failed": requested - success, "rows": rows,
            "enrichment_warnings_count": enrich, "file_path": file_path}


# Amendment 10：认证失败语义从 RuntimeError 扩展到 AuthenticationError
_SESSION_ERROR_TYPES = frozenset({"RuntimeError", "AuthenticationError"})


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
    if max_run >= _SESSION_RUN_THRESHOLD and max_type in _SESSION_ERROR_TYPES:
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
    results["DSS"] = _process_type(client, "DSS", by_type["DSS"],
                                   failures, output_dir, ts)
    results["MTT"] = _process_type(client, "MTT", by_type["MTT"],
                                   failures, output_dir, ts)
    results["WORKFLOW"] = _process_type(client, "WORKFLOW", by_type["WORKFLOW"],
                                        failures, output_dir, ts)

    warnings = []
    if by_type["DSS"]:
        warnings.append("DSS details are resolved by name only. The supplied folder is "
                        "used to resolve the FRS ID, but it does not disambiguate the V2 "
                        "detail response when duplicate names exist elsewhere in the "
                        "organization.")
    if by_type["WORKFLOW"]:
        warnings.append("WORKFLOW details are resolved by name only. The supplied folder "
                        "is used to resolve the FRS ID, but it does not disambiguate the "
                        "V2 detail response when duplicate names exist elsewhere in the "
                        "organization.")
    warnings.extend(_session_warnings(failures))

    return {
        "input_count": input_count,
        "unique_count": unique_count,
        "duplicates_removed": input_count - unique_count,
        "results": results,
        "failures": failures,
        "warnings": warnings,
    }
