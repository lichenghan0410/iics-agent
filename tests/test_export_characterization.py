#!/usr/bin/env python3
"""
tests/test_export_characterization.py — 业务输出特征测试（characterization）

在 MTT 字段扩展改动之前编写并运行，固定当时的行为：
- DSS 12 列、WORKFLOW 9 列：列名、顺序与值（之后不得修改）。
- MTT：列名、顺序与值。MTT 字段扩展（Amendment 11）后仅允许按 26 列契约做预期内
  更新：插入 Last Run / Source Connection Parameter / Target Connection Parameter，
  其余列值不变。

全部使用 tests.fakes 的 fake client 与临时目录，不触网、不读取真实 credential。
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iics_cli  # noqa: E402
from tools import exporter  # noqa: E402
from tests import fakes  # noqa: E402
from tests.fakes import ns  # noqa: E402

EXPECTED_DSS_HEADERS = [
    "@type", "id", "FRS ID", "orgId",
    "Task Name", "Task Type", "Task Updated On",
    "Source Connection", "Source Object", "Operation",
    "Target Connection", "Target Object",
]

EXPECTED_DSS_ROW = {
    "@type": "dssTask", "id": "000LVU0I00000000FAKE", "FRS ID": "FAKE_FRS_DSS_0001",
    "orgId": "000LVU", "Task Name": "DSS_FAKE_01", "Task Type": "DSS",
    "Task Updated On": "2026-01-02T03:04:05.000Z",
    "Source Connection": "FAKE_SRC_CONN", "Source Object": "SRC_TBL_A",
    "Operation": "Upsert", "Target Connection": "FAKE_TGT_CONN",
    "Target Object": "TGT_TBL_A",
}

EXPECTED_WF_HEADERS = [
    "Task Type", "orgId", "id", "FRS ID", "Task Name", "updateTime",
    "tasks.taskId", "tasks.type", "tasks.name",
]

EXPECTED_WF_ROWS = [
    ["WORKFLOW", "000LVU", "000LVU0N00000000FAKE", "FAKE_FRS_WF_0001", "WF_FAKE_01",
     "2026-02-03T04:05:06.000Z", "000LVU0Z00000000AAA1", "MTT", "mt_child_1"],
    ["WORKFLOW", "000LVU", "000LVU0N00000000FAKE", "FAKE_FRS_WF_0001", "WF_FAKE_01",
     "2026-02-03T04:05:06.000Z", "000LVU0Z00000000AAA2", "DSS", "dss_child_2"],
    ["WORKFLOW", "000LVU", "000LVU0N00000000FAK2", "FAKE_FRS_WF_0002", "WF_FAKE_EMPTY",
     "2026-02-04T00:00:00.000Z", "", "", ""],
]

EXPECTED_MTT_HEADERS = [
    "@type", "id", "FRS ID", "orgId",
    "Task Name", "Task Type", "Task Location", "Runtime Environment",
    "Task Updated On", "Last Run",
    "Mapping Name", "Mapping Updated On", "Transformation Name",
    "Source Connection", "Source Connection Parameter", "Source Object", "Custom Query",
    "Target Connection", "Target Connection Parameter", "Operation", "Target Object",
    "Parameter File Directory", "Parameter File Name",
    "Pre-Processing Commands", "Post-Processing Commands", "Status",
]

_MTT_TASK_LEVEL = {
    "@type": "mtTask", "id": "000LVU0Z000000000857", "FRS ID": "FAKE_FRS_QUICK_0001",
    "orgId": "000LVU", "Task Name": fakes.MTT_NAME, "Task Type": "MTT",
    "Task Location": fakes.MTT_FOLDER, "Runtime Environment": "FAKE_RUNTIME_ENV",
    "Task Updated On": "2026-10-28T03:12:56.000Z",
    "Last Run": "2026-11-12T06:58:54.000Z",
    "Mapping Name": "m_FAKE_SSA_FACT_RESULT_QUICK",
    "Mapping Updated On": "2026-10-01T00:00:00.000Z",
}
_MTT_TAIL = {
    "Parameter File Directory": "E:\\JPN_COMM_DWH\\param",
    "Parameter File Name": "JPN_COMM_DWH_DL.PARAM",
    "Pre-Processing Commands": None, "Post-Processing Commands": None, "Status": "",
}

EXPECTED_MTT_ROWS = [
    {**_MTT_TASK_LEVEL,
     "Transformation Name": "src_scrm_dwh_pub_V_DMF_SSA_FACT_RESULT_QUICK",
     "Source Connection": "rsh_gdm_e2e_TEC",
     "Source Connection Parameter": "parameter_rsh_gdm_e2e",
     "Source Object": "", "Custom Query": fakes.CUSTOM_QUERY,
     "Target Connection": "", "Target Connection Parameter": "", "Operation": "",
     "Target Object": "", **_MTT_TAIL},
    {**_MTT_TASK_LEVEL,
     "Transformation Name": "tgt_v_dmf_ssa_fact_result_quick",
     "Source Connection": "", "Source Connection Parameter": "",
     "Source Object": "", "Custom Query": "",
     "Target Connection": fakes.TGT_CONN_NAME,
     "Target Connection Parameter": "",
     "Operation": "Insert",
     "Target Object": "S_IF_V_DMF_SSA_FACT_RSLT_QK", **_MTT_TAIL},
]


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


def _ordered(row, headers):
    return [row[h] for h in headers]


def _excel(v):
    """compact 值 → Excel 读回值（None 与 "" 均为空白，读回 None）。"""
    return None if v == "" else v


def _all_client():
    """DSS + MTT + WORKFLOW 同一 folder 的组合 fake client（ALL 导出用）。"""
    folder = "FAKE_ALL_FOLDER"
    routes, objects = {}, []
    for c in (fakes.mtt_client(), fakes.dss_client(), fakes.wf_client()):
        routes.update(c.routes)
        for o in c.objects:
            o = dict(o)
            o["location"] = folder
            objects.append(o)
    return fakes.FakeClient(routes=routes, objects=objects), folder


class CharacterizationBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, client, folder, ttype, summary_only=False, fmt="excel"):
        fn = str(self.dir / f"out_{ttype}_{int(summary_only)}.{'xlsx' if fmt == 'excel' else 'json'}")
        args = ns(folder=folder, type=ttype, format=fmt, filename=fn,
                  summary_only=summary_only)
        return _quiet(iics_cli._cmd_export, client, args)


# ── DSS 12 列 ────────────────────────────────────────────────────────────────
class TestDssCharacterization(CharacterizationBase):

    def test_headers(self):
        self.assertEqual(exporter.DSS_HEADERS, EXPECTED_DSS_HEADERS)

    def test_compact_get_task(self):
        res = _quiet(iics_cli._cmd_get_task, fakes.dss_client(),
                     ns(name=fakes.DSS_NAME, type="DSS", folder=fakes.DSS_FOLDER,
                        full=False, no_resolve=False))
        self.assertEqual(set(res), {"task_count", "rows", "records"})
        self.assertEqual(res["records"], [EXPECTED_DSS_ROW])
        self.assertEqual(list(res["records"][0]), EXPECTED_DSS_HEADERS)

    def test_excel_export(self):
        res = self.export(fakes.dss_client(), fakes.DSS_FOLDER, "DSS")
        self.assertEqual((res["task_count"], res["rows"]), (1, 1))
        sheets = fakes.read_sheets(res["file_path"])
        self.assertEqual(list(sheets), ["Task Summary", "Export Errors"])
        self.assertEqual(sheets["Task Summary"][0], EXPECTED_DSS_HEADERS)
        self.assertEqual(sheets["Task Summary"][1],
                         _ordered(EXPECTED_DSS_ROW, EXPECTED_DSS_HEADERS))


# ── WORKFLOW 9 列 ────────────────────────────────────────────────────────────
class TestWorkflowCharacterization(CharacterizationBase):

    def test_headers(self):
        self.assertEqual(exporter.WORKFLOW_HEADERS, EXPECTED_WF_HEADERS)

    def test_excel_export(self):
        res = self.export(fakes.wf_client(), fakes.WF_FOLDER, "WORKFLOW")
        self.assertEqual((res["task_count"], res["rows"]), (2, 3))
        sheets = fakes.read_sheets(res["file_path"])
        rows = sheets["Task Summary"]
        self.assertEqual(rows[0], EXPECTED_WF_HEADERS)
        self.assertEqual([[_excel(v) for v in r] for r in EXPECTED_WF_ROWS],
                         rows[1:])


# ── MTT ─────────────────────────────────────────────────────────────────────
class TestMttCharacterization(CharacterizationBase):

    def get_task(self, no_resolve=False, client=None):
        return _quiet(iics_cli._cmd_get_task, client or fakes.mtt_client(),
                      ns(name=fakes.MTT_NAME, type="MTT", folder=fakes.MTT_FOLDER,
                         full=False, no_resolve=no_resolve))

    def test_headers(self):
        self.assertEqual(exporter.MTT_HEADERS, EXPECTED_MTT_HEADERS)

    def test_compact_get_task(self):
        res = self.get_task()
        self.assertEqual(set(res), {"task_count", "rows", "records"})
        self.assertEqual((res["task_count"], res["rows"]), (1, 2))
        self.assertEqual(res["records"], EXPECTED_MTT_ROWS)
        for rec in res["records"]:
            self.assertEqual(list(rec), EXPECTED_MTT_HEADERS)

    def test_compact_no_resolve_shows_ids(self):
        client = fakes.mtt_client()
        res = self.get_task(no_resolve=True, client=client)
        self.assertEqual(res["records"][0]["Source Connection"], fakes.SRC_CONN_ID)
        self.assertEqual(res["records"][1]["Target Connection"], fakes.TGT_CONN_ID)
        self.assertEqual(client.paths("connection/"), [])

    def test_excel_matches_compact(self):
        res = self.export(fakes.mtt_client(), fakes.MTT_FOLDER, "MTT")
        self.assertEqual((res["task_count"], res["rows"]), (1, 2))
        self.assertEqual(res["failed_details_count"], 0)
        sheets = fakes.read_sheets(res["file_path"])
        rows = sheets["Task Summary"]
        self.assertEqual(rows[0], EXPECTED_MTT_HEADERS)
        expected = [[_excel(v) for v in _ordered(r, EXPECTED_MTT_HEADERS)]
                    for r in EXPECTED_MTT_ROWS]
        self.assertEqual(rows[1:], expected)

    def test_detail_failure_placeholder_row(self):
        client = fakes.mtt_client()
        client.routes["mttask/frs/" + fakes.MTT_FRS_ID] = RuntimeError("boom")
        res = self.export(client, fakes.MTT_FOLDER, "MTT")
        self.assertEqual((res["task_count"], res["rows"]), (1, 1))
        self.assertEqual(res["failed_details_count"], 1)
        rows = fakes.read_sheets(res["file_path"])["Task Summary"]
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["Task Name"], fakes.MTT_NAME)
        self.assertEqual(row["FRS ID"], fakes.MTT_FRS_ID)
        self.assertEqual(row["Task Location"], fakes.MTT_FOLDER)
        self.assertTrue(all(fakes.blank(row[c]) for c in (
            "Transformation Name", "Source Connection", "Target Connection")))

    def test_no_transformation_keeps_one_row(self):
        raw = dict(fakes.MTT_RAW_SAMPLE)
        raw["parameters"] = []
        res = self.get_task(client=fakes.mtt_client(raw=raw))
        self.assertEqual(res["rows"], 1)
        rec = res["records"][0]
        self.assertEqual(rec["Task Name"], fakes.MTT_NAME)
        self.assertEqual(rec["Transformation Name"], "")
        self.assertEqual(rec["Source Connection"], "")

    def test_summary_only_no_detail_requests(self):
        client = fakes.mtt_client()
        res = self.export(client, fakes.MTT_FOLDER, "MTT", summary_only=True)
        self.assertEqual((res["task_count"], res["rows"]), (1, 1))
        self.assertEqual([p for p in client.paths() if p != fakes.V3_OBJECTS_PATH], [])


# ── ALL ─────────────────────────────────────────────────────────────────────
class TestAllCharacterization(CharacterizationBase):

    def test_all_sheets(self):
        client, folder = _all_client()
        res = self.export(client, folder, "ALL")
        self.assertEqual(res["failed_details_count"], 0)
        self.assertEqual((res["task_count"], res["rows"]), (4, 2 + 1 + 3))
        sheets = fakes.read_sheets(res["file_path"])
        self.assertEqual(list(sheets),
                         ["MTT Task", "DSS Tasks", "Workflow Tasks", "Export Errors"])
        self.assertEqual(sheets["MTT Task"][0], EXPECTED_MTT_HEADERS)
        self.assertEqual(sheets["DSS Tasks"][0], EXPECTED_DSS_HEADERS)
        self.assertEqual(sheets["DSS Tasks"][1],
                         _ordered(EXPECTED_DSS_ROW, EXPECTED_DSS_HEADERS))
        self.assertEqual(sheets["Workflow Tasks"][0], EXPECTED_WF_HEADERS)
        wf = [[_excel(v) for v in r] for r in EXPECTED_WF_ROWS]
        self.assertEqual(sheets["Workflow Tasks"][1:], wf)
        mtt_rows = [dict(zip(sheets["MTT Task"][0], r)) for r in sheets["MTT Task"][1:]]
        self.assertEqual([r["Transformation Name"] for r in mtt_rows],
                         [r["Transformation Name"] for r in EXPECTED_MTT_ROWS])
        self.assertEqual(mtt_rows[0]["Task Location"], folder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
