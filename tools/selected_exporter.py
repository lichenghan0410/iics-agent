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
    """校验并标准化 selected task 输入。返回 (normalized_list | None, input_errors)。"""
    if not isinstance(raw, list):
        return None, [{"index": None, "field": None,
                       "message": "top-level input must be a JSON array"}]
    errors = []
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append({"index": i, "field": None,
                           "message": "item must be a JSON object"})
            continue
        vals = {}
        bad = False
        for f in ("name", "type", "folder"):
            if f not in item:
                errors.append({"index": i, "field": f, "message": f"{f} is required"})
                bad = True
                continue
            v = item.get(f)
            if not isinstance(v, str):
                errors.append({"index": i, "field": f, "message": f"{f} must be a string"})
                bad = True
                continue
            vals[f] = v
        if bad:
            continue
        name = vals["name"].strip()
        folder = vals["folder"].strip()
        ttype = vals["type"].strip().upper()
        if not name:
            errors.append({"index": i, "field": "name", "message": "name must not be empty"})
            continue
        if not folder:
            errors.append({"index": i, "field": "folder", "message": "folder must not be empty"})
            continue
        if ttype not in _VALID_TYPES:
            errors.append({"index": i, "field": "type",
                           "message": "type must be DSS, MTT, or WORKFLOW"})
            continue
        out.append({"name": name, "type": ttype, "folder": folder})
    if errors:
        return None, errors
    return out, []


def _dedup(tasks):
    """exact-tuple (type, folder, name) 去重，保持首次出现顺序。"""
    seen = set()
    out = []
    for t in tasks:
        key = (t["type"], t["folder"], t["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def detect_input_ambiguities(unique_tasks):
    """DSS / WORKFLOW：同 type + 同 name 但不同 folder（by-name 无法消歧）→ ambiguous_input。

    返回被标记的 (type, folder, name) 集合。exact-tuple 已去重，不属于 ambiguity。
    """
    folders_by_key = {}
    for t in unique_tasks:
        if t["type"] in ("DSS", "WORKFLOW"):
            folders_by_key.setdefault((t["type"], t["name"]), set()).add(t["folder"])
    ambiguous = set()
    for (typ, name), folders in folders_by_key.items():
        if len(folders) > 1:
            for f in folders:
                ambiguous.add((typ, f, name))
    return ambiguous


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


def _process_by_name(client, ttype, tuples, ambiguous, failures, output_dir, ts):
    """DSS / WORKFLOW：input ambiguity 先过滤，其余走现有 by-name detail。"""
    requested = len(tuples)
    if requested == 0:
        return _empty_result()
    details = []
    failed_local = []
    for t in tuples:
        key = (ttype, t["folder"], t["name"])
        if key in ambiguous:
            f = {"type": ttype, "folder": t["folder"], "name": t["name"],
                 "error_type": "ambiguous_input",
                 "error": f"The same {ttype} name was selected from multiple folders, "
                          f"but {ttype} details are resolved by name only."}
            failed_local.append(f)
            failures.append(f)
            continue
        d = get_task_detail(client, task_name=t["name"], task_type=ttype,
                            folder_path=t["folder"])
        if isinstance(d, dict) and "error" in d:
            f = {"type": ttype, "folder": t["folder"], "name": t["name"],
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

    ambiguous = detect_input_ambiguities(unique)
    by_type = {t: [] for t in _VALID_TYPES}
    for t in unique:
        by_type[t["type"]].append(t)

    failures = []
    results = {}
    results["DSS"] = _process_by_name(client, "DSS", by_type["DSS"], ambiguous,
                                      failures, output_dir, ts)
    results["MTT"] = _process_mtt(client, by_type["MTT"], failures, output_dir, ts)
    results["WORKFLOW"] = _process_by_name(client, "WORKFLOW", by_type["WORKFLOW"],
                                           ambiguous, failures, output_dir, ts)

    warnings = []
    if by_type["DSS"]:
        warnings.append("DSS details are resolved by name only; folder identity cannot "
                        "be guaranteed when duplicate names exist elsewhere in the "
                        "organization.")
    if by_type["WORKFLOW"]:
        warnings.append("WORKFLOW details are resolved by name only; folder identity "
                        "cannot be guaranteed when duplicate names exist elsewhere in "
                        "the organization.")
    warnings.extend(_session_warnings(failures))

    return {
        "input_count": input_count,
        "unique_count": unique_count,
        "duplicates_removed": input_count - unique_count,
        "results": results,
        "failures": failures,
        "warnings": warnings,
    }
