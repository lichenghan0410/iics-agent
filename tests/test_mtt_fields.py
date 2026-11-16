#!/usr/bin/env python3
"""
tests/test_mtt_fields.py — MTT 字段扩展（Amendment 11）：Last Run、
Source / Target Connection Parameter、连接展示优先级、warning 与各入口回归。

全部为离线 fake（tests.fakes）+ 临时目录：不触网、不读取真实 credential。
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iics_cli  # noqa: E402
from tools import exporter, query  # noqa: E402
from tools.query import get_task_detail, parse_last_run_time  # noqa: E402
from tools.selected_exporter import export_selected_tasks  # noqa: E402
from tests import fakes  # noqa: E402
from tests.fakes import ns  # noqa: E402

SAMPLE_LAST_RUN = "2026-11-12T06:58:54.000Z"
NEW_COLS = ("Last Run", "Source Connection Parameter", "Target Connection Parameter")


# ── fixture builders ────────────────────────────────────────────────────────
def src_param(name="SRC_A", conn_id="CONN_SRC_A", ui=None, rpd=None, **extra):
    p = {"name": f"${name}$", "type": "SOURCE", "sourceConnectionId": conn_id,
         "sourceObject": f"OBJ_{name}"}
    if ui is not None:
        p["uiProperties"] = ui
    if rpd is not None:
        p["runtimeParameterData"] = rpd
    p.update(extra)
    return p


def tgt_param(name="TGT_A", conn_id="CONN_TGT_A", ui=None, rpd=None, **extra):
    p = {"name": f"${name}$", "type": "TARGET", "targetConnectionId": conn_id,
         "targetObject": f"OBJ_{name}", "operationType": "Insert"}
    if ui is not None:
        p["uiProperties"] = ui
    if rpd is not None:
        p["runtimeParameterData"] = rpd
    p.update(extra)
    return p


PARAM_UI = {"connectionParameterized": "true", "connDefault": "cfg_conn",
            "paramName": "p_conn", "paramType-mapping": "Connection"}
PARAM_RPD = {"isConnectionRuntimeParameter": True, "connectionParameterName": "p_conn"}
PLAIN_UI = {"connectionParameterized": "false"}
PLAIN_RPD = {"isConnectionRuntimeParameter": False}


def make_raw(params, last_run=1794466734000, drop_last_run=False):
    raw = copy.deepcopy(fakes.MTT_RAW_SAMPLE)
    raw["parameters"] = params
    if drop_last_run:
        raw.pop("lastRunTime", None)
    else:
        raw["lastRunTime"] = last_run
    return raw


def client_for(raw, conns=None):
    """conns: {conn_id: body | Exception}；未列出的 connection 默认返回 name=<id>_NAME。"""
    c = fakes.mtt_client(raw=raw)
    for p in raw.get("parameters", []):
        for key in ("sourceConnectionId", "targetConnectionId"):
            cid = p.get(key)
            if cid:
                c.routes.setdefault("connection/" + cid, {"id": cid, "name": cid + "_NAME"})
    for cid, body in (conns or {}).items():
        c.routes["connection/" + cid] = body
    return c


def detail_for(client, resolve=True):
    with contextlib.redirect_stderr(io.StringIO()):
        return get_task_detail(client, task_name=fakes.MTT_NAME, task_type="MTT",
                               folder_path=fakes.MTT_FOLDER,
                               resolve_connections=resolve)


def rows_for(detail):
    return [exporter.compact_record(r)
            for r in exporter.build_task_summary_rows(detail, fakes.MTT_FOLDER)]


def warnings_of(detail, kind=None):
    ws = detail.get("enrichment_warnings") or []
    return [w for w in ws if kind is None or w.get("kind") == kind]


def run_get_task(client, no_resolve=False, full=False):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = iics_cli._cmd_get_task(client, ns(name=fakes.MTT_NAME, type="MTT",
                                                folder=fakes.MTT_FOLDER, full=full,
                                                no_resolve=no_resolve))
    return res, err.getvalue()


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, client, folder=fakes.MTT_FOLDER, ttype="MTT", summary_only=False,
               fmt="excel"):
        fn = str(self.dir / f"exp_{ttype}_{int(summary_only)}.{'xlsx' if fmt == 'excel' else 'json'}")
        with contextlib.redirect_stderr(io.StringIO()):
            return iics_cli._cmd_export(client, ns(folder=folder, type=ttype, format=fmt,
                                                   filename=fn,
                                                   summary_only=summary_only))


# ── Last Run ────────────────────────────────────────────────────────────────
class TestLastRunParsing(unittest.TestCase):

    def assertValue(self, value, expected):
        self.assertEqual(parse_last_run_time(value), (expected, None))

    def assertInvalid(self, value):
        got, err = parse_last_run_time(value)
        self.assertIsNone(got)
        self.assertIsInstance(err, str)
        self.assertTrue(err.startswith("Last Run: invalid value "), err)

    def test_sample_epoch_ms(self):
        self.assertValue(1794466734000, SAMPLE_LAST_RUN)

    def test_epoch_ms_float_keeps_milliseconds(self):
        self.assertValue(1794466734123.0, "2026-11-12T06:58:54.123Z")

    def test_iso_with_z(self):
        self.assertValue("2026-11-12T06:58:54Z", SAMPLE_LAST_RUN)
        self.assertValue("2026-11-12T06:58:54.000Z", SAMPLE_LAST_RUN)

    def test_iso_with_offset_normalized_to_utc(self):
        self.assertValue("2026-11-12T15:58:54.5+09:00", "2026-11-12T06:58:54.500Z")

    def test_missing_null_empty_zero_no_warning(self):
        for v in (None, "", "   ", 0, 0.0):
            self.assertValue(v, None)

    def test_numeric_string_not_parsed(self):
        self.assertInvalid("1794466734000")
        self.assertInvalid(" 1794466734000 ")

    def test_boolean_rejected(self):
        self.assertInvalid(True)
        self.assertInvalid(False)

    def test_invalid_values(self):
        for v in ("abc", -1, -1.5, float("nan"), float("inf"), 1e30,
                  "2026-11-12T06:58:54", "2026-11-12", {"t": 1}, [1]):
            with self.subTest(v=v):
                self.assertInvalid(v)

    def test_independent_of_local_timezone(self):
        if not hasattr(time, "tzset"):
            self.skipTest("time.tzset unavailable")
        old = os.environ.get("TZ")
        try:
            for tz in ("Asia/Tokyo", "America/New_York", "UTC"):
                os.environ["TZ"] = tz
                time.tzset()
                self.assertValue(1794466734000, SAMPLE_LAST_RUN)
                self.assertValue("2026-11-12T15:58:54+09:00", SAMPLE_LAST_RUN)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()


class TestLastRunIsoFormats(unittest.TestCase):
    """明确支持的 ISO 输入格式（Python 3.10 / 3.11 / 3.13 行为一致，不依赖 fromisoformat）。"""

    OUT_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

    SUPPORTED = [
        # 无小数秒
        ("2026-11-12T06:58:54Z", "2026-11-12T06:58:54.000Z"),
        ("2026-11-12T06:58:54+00:00", "2026-11-12T06:58:54.000Z"),
        # 1–6 位小数秒（截断到毫秒）
        ("2026-11-12T06:58:54.5Z", "2026-11-12T06:58:54.500Z"),
        ("2026-11-12T06:58:54.12Z", "2026-11-12T06:58:54.120Z"),
        ("2026-11-12T06:58:54.123Z", "2026-11-12T06:58:54.123Z"),
        ("2026-11-12T06:58:54.1234Z", "2026-11-12T06:58:54.123Z"),
        ("2026-11-12T06:58:54.12345Z", "2026-11-12T06:58:54.123Z"),
        ("2026-11-12T06:58:54.123456Z", "2026-11-12T06:58:54.123Z"),
        ("2026-11-12T06:58:54.999999Z", "2026-11-12T06:58:54.999Z"),
        # 时区偏移 ±HH:MM / ±HHMM，转 UTC（含跨日）
        ("2026-11-12T15:58:54+09:00", "2026-11-12T06:58:54.000Z"),
        ("2026-11-12T15:58:54.5+09:00", "2026-11-12T06:58:54.500Z"),
        ("2026-11-12T15:58:54.12+0900", "2026-11-12T06:58:54.120Z"),
        ("2026-11-12T01:58:54-05:00", "2026-11-12T06:58:54.000Z"),
        ("2026-11-12T05:28:54.000001+05:30", "2026-11-11T23:58:54.000Z"),
        ("2026-11-12T00:30:00+09:00", "2026-11-11T15:30:00.000Z"),
        # 秒可省略；分隔符 T / t / 单个空格；Z 大小写
        ("2026-11-12T06:58Z", "2026-11-12T06:58:00.000Z"),
        ("2026-11-12t06:58:54z", "2026-11-12T06:58:54.000Z"),
        ("2026-11-12 06:58:54Z", "2026-11-12T06:58:54.000Z"),
        ("  2026-11-12T06:58:54Z  ", "2026-11-12T06:58:54.000Z"),
    ]

    UNSUPPORTED = [
        "2026-11-12T06:58:54",            # 无时区
        "2026-11-12T06:58:54.123",        # 无时区
        "2026-11-12",                     # 仅日期
        "2026-11-12T06:58:54.1234567Z",   # 小数秒 > 6 位
        "2026-11-12T06:58:54.Z",          # 小数点后无数字
        "20261112T065854Z",               # basic 格式
        "2026-11-12T065854Z",             # basic 时间
        "2026-11-12T06:58:54+09",         # 偏移缺分钟
        "2026-11-12T06:58:54+24:00",      # 偏移越界
        "2026-11-12T06:58:54+09:60",      # 偏移分钟越界
        "2026-13-01T00:00:00Z",           # 月越界
        "2026-02-30T00:00:00Z",           # 日越界
        "2026-11-12T24:00:00Z",           # 时越界
        "2026-11-12T06:58:60Z",           # 秒越界（不支持闰秒）
        "0001-01-01T00:00:00+09:00",      # 转 UTC 后越界
        "2026-11-12T06:58:54Z extra",
    ]

    def test_supported_formats(self):
        for value, expected in self.SUPPORTED:
            with self.subTest(value=value):
                got, err = parse_last_run_time(value)
                self.assertIsNone(err)
                self.assertEqual(got, expected)
                self.assertRegex(got, self.OUT_RE)

    def test_unsupported_formats_warn(self):
        for value in self.UNSUPPORTED:
            with self.subTest(value=value):
                got, err = parse_last_run_time(value)
                self.assertIsNone(got)
                self.assertTrue(err.startswith("Last Run: invalid value "), err)

    def test_missing_timezone_reason(self):
        _, err = parse_last_run_time("2026-11-12T06:58:54")
        self.assertIn("datetime without timezone", err)

    def test_epoch_output_format(self):
        for v in (1, 1794466734000, 1794466734999.9, 253402300799999):
            with self.subTest(v=v):
                got, err = parse_last_run_time(v)
                self.assertIsNone(err)
                self.assertRegex(got, self.OUT_RE)
        self.assertEqual(parse_last_run_time(253402300799999)[0], "9999-12-31T23:59:59.999Z")


class TestLastRunInDetail(TmpDirCase):

    def test_every_row_repeats_last_run(self):
        raw = make_raw([src_param("S1"), tgt_param("T1"), src_param("S2")])
        rows = rows_for(detail_for(client_for(raw)))
        self.assertEqual([r["Last Run"] for r in rows], [SAMPLE_LAST_RUN] * 3)

    def test_missing_last_run_is_null_without_warning(self):
        detail = detail_for(client_for(make_raw([src_param()], drop_last_run=True)))
        self.assertIsNone(detail["last_run"])
        self.assertEqual(warnings_of(detail, "last_run"), [])
        self.assertIsNone(rows_for(detail)[0]["Last Run"])

    def test_invalid_last_run_warns_but_detail_succeeds(self):
        client = client_for(make_raw([src_param()], last_run="abc"))
        detail = detail_for(client)
        self.assertNotIn("error", detail)
        self.assertIsNone(detail["last_run"])
        ws = warnings_of(detail, "last_run")
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["field"], "Last Run")
        self.assertEqual(ws[0]["error_type"], "EnrichmentError")
        self.assertTrue(ws[0]["error"].startswith("Last Run: invalid value 'abc'"))
        res = self.export(client_for(make_raw([src_param()], last_run="abc")))
        self.assertEqual(res["failed_details_count"], 0)
        self.assertEqual(res["enrichment_warnings_count"], 1)

    def test_raw_value_preserved(self):
        detail = detail_for(client_for(make_raw([src_param()])))
        self.assertEqual(detail["raw"]["lastRunTime"], 1794466734000)


# ── 参数化组合 ───────────────────────────────────────────────────────────────
class TestParameterizedCombinations(unittest.TestCase):

    def rows(self, params):
        detail = detail_for(client_for(make_raw(params)))
        return detail, rows_for(detail)

    def test_source_only_parameterized(self):
        _, rows = self.rows([src_param(ui=PARAM_UI, rpd=PARAM_RPD),
                             tgt_param(ui=PLAIN_UI, rpd=PLAIN_RPD)])
        self.assertEqual(rows[0]["Source Connection Parameter"], "p_conn")
        self.assertEqual(rows[0]["Target Connection Parameter"], "")
        self.assertEqual(rows[1]["Target Connection Parameter"], "")
        self.assertEqual(rows[1]["Source Connection Parameter"], "")

    def test_target_only_parameterized(self):
        _, rows = self.rows([src_param(ui=PLAIN_UI, rpd=PLAIN_RPD),
                             tgt_param(ui=PARAM_UI, rpd=PARAM_RPD)])
        self.assertEqual(rows[0]["Source Connection Parameter"], "")
        self.assertEqual(rows[1]["Target Connection Parameter"], "p_conn")

    def test_both_parameterized(self):
        tgt_ui = dict(PARAM_UI, paramName="p_tgt")
        tgt_rpd = dict(PARAM_RPD, connectionParameterName="p_tgt")
        _, rows = self.rows([src_param(ui=PARAM_UI, rpd=PARAM_RPD),
                             tgt_param(ui=tgt_ui, rpd=tgt_rpd)])
        self.assertEqual(rows[0]["Source Connection Parameter"], "p_conn")
        self.assertEqual(rows[1]["Target Connection Parameter"], "p_tgt")

    def test_both_not_parameterized(self):
        detail, rows = self.rows([src_param(ui=PLAIN_UI, rpd=PLAIN_RPD),
                                  tgt_param(ui=PLAIN_UI, rpd=PLAIN_RPD)])
        self.assertEqual(rows[0]["Source Connection Parameter"], "")
        self.assertEqual(rows[1]["Target Connection Parameter"], "")
        self.assertEqual(warnings_of(detail, "connection_parameter"), [])

    def test_unknown_status_is_null_and_connDefault_unused(self):
        detail, rows = self.rows([src_param(ui={"connDefault": "cfg_conn"},
                                            conn_id="C_UNK")])
        sc = detail["transformations"][0]["source_connection"]
        self.assertIsNone(sc["parameterized"])
        self.assertIsNone(rows[0]["Source Connection Parameter"])
        self.assertEqual(rows[0]["Source Connection"], "C_UNK_NAME")
        self.assertEqual(warnings_of(detail, "connection_parameter"), [])

    def test_string_false_is_false(self):
        for v in ("false", " FALSE ", False):
            with self.subTest(v=v):
                detail, rows = self.rows([src_param(
                    ui={"connectionParameterized": v, "connDefault": "cfg_conn"})])
                self.assertIs(
                    detail["transformations"][0]["source_connection"]["parameterized"],
                    False)
                self.assertEqual(rows[0]["Source Connection Parameter"], "")

    def test_string_true_case_insensitive(self):
        detail, _ = self.rows([src_param(ui={"connectionParameterized": " True ",
                                             "paramName": "p1"})])
        self.assertIs(detail["transformations"][0]["source_connection"]["parameterized"],
                      True)

    def test_missing_flag_with_mapping_type_evidence(self):
        detail, rows = self.rows([src_param(ui={"paramType-mapping": "Connection",
                                                "paramName": "p_ev"})])
        self.assertIs(detail["transformations"][0]["source_connection"]["parameterized"],
                      True)
        self.assertEqual(rows[0]["Source Connection Parameter"], "p_ev")

    def test_missing_flag_with_rpd_name_evidence(self):
        detail, rows = self.rows([src_param(rpd={"connectionParameterName": "p_rpd"})])
        self.assertIs(detail["transformations"][0]["source_connection"]["parameterized"],
                      True)
        self.assertEqual(rows[0]["Source Connection Parameter"], "p_rpd")

    def test_missing_flag_paramName_alone_is_not_evidence(self):
        detail, rows = self.rows([src_param(ui={"paramName": "p_only"})])
        self.assertIsNone(detail["transformations"][0]["source_connection"]["parameterized"])
        self.assertIsNone(rows[0]["Source Connection Parameter"])

    def test_invalid_flag_is_null_with_warning(self):
        detail, rows = self.rows([src_param(name="S_BAD",
                                            ui={"connectionParameterized": "yes"})])
        self.assertIsNone(detail["transformations"][0]["source_connection"]["parameterized"])
        self.assertIsNone(rows[0]["Source Connection Parameter"])
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["field"], "Source Connection Parameter")
        self.assertIn("invalid connectionParameterized", ws[0]["error"])

    def test_explicit_false_conflicts_with_name(self):
        detail, rows = self.rows([src_param(
            name="S_CF", ui={"connectionParameterized": "false"},
            rpd={"connectionParameterName": "p_x"})])
        self.assertIs(detail["transformations"][0]["source_connection"]["parameterized"],
                      False)
        self.assertEqual(rows[0]["Source Connection Parameter"], "")
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)
        self.assertIn("conflicts", ws[0]["error"])


# ── 参数名与独立标记 ─────────────────────────────────────────────────────────
class TestParameterName(unittest.TestCase):

    def conn(self, ui=None, rpd=None, name="S_N"):
        detail = detail_for(client_for(make_raw([src_param(name=name, ui=ui, rpd=rpd)])))
        return detail, detail["transformations"][0]["source_connection"]

    def test_primary_connectionParameterName(self):
        _, c = self.conn(ui={"connectionParameterized": "true", "paramName": "p_main"},
                         rpd={"connectionParameterName": "p_main"})
        self.assertEqual(c["parameter_name"], "p_main")

    def test_paramName_fallback(self):
        detail, c = self.conn(ui={"connectionParameterized": "true", "paramName": "p_ui"})
        self.assertEqual(c["parameter_name"], "p_ui")
        self.assertEqual(warnings_of(detail, "connection_parameter"), [])

    def test_conflict_uses_primary_and_warns(self):
        detail, c = self.conn(ui={"connectionParameterized": "true", "paramName": "p_ui"},
                              rpd={"connectionParameterName": "p_rpd"})
        self.assertEqual(c["parameter_name"], "p_rpd")
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)
        self.assertIn("conflicts with paramName", ws[0]["error"])

    def test_missing_name_warns_and_column_null(self):
        detail, c = self.conn(ui={"connectionParameterized": "true", "paramName": "  ",
                                  "paramLabel": "lbl", "logcnx": "logical"},
                              name="S_MISS")
        self.assertIsNone(c["parameter_name"])
        self.assertIsNone(rows_for(detail)[0]["Source Connection Parameter"])
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["error"],
                         "S_MISS / Source Connection Parameter: parameterized but name missing")

    def test_name_not_inferred_from_label_or_parameter_name(self):
        detail = detail_for(client_for(make_raw([src_param(
            name="p_like_name", label="p_label",
            ui={"connectionParameterized": "true", "logcnx": "p_logcnx",
                "paramLabel": "p_plabel"})])))
        self.assertIsNone(detail["transformations"][0]["source_connection"]["parameter_name"])

    def test_runtime_flag_independent_of_parameterized(self):
        _, c = self.conn(ui={"connectionParameterized": "true", "paramName": "p"},
                         rpd={"isConnectionRuntimeParameter": False})
        self.assertIs(c["parameterized"], True)
        self.assertIs(c["is_connection_runtime_parameter"], False)
        _, c = self.conn(ui={"connectionParameterized": "false"},
                         rpd={"isConnectionRuntimeParameter": True})
        self.assertIs(c["parameterized"], False)
        self.assertIs(c["is_connection_runtime_parameter"], True)

    def test_runtime_flag_safe_parsing(self):
        for raw_v, expected in (("true", True), ("FALSE", False), ("x", None), (None, None)):
            with self.subTest(v=raw_v):
                _, c = self.conn(ui=PLAIN_UI, rpd={"isConnectionRuntimeParameter": raw_v})
                self.assertIs(c["is_connection_runtime_parameter"], expected)

    def test_normalized_model_shape(self):
        detail = detail_for(fakes.mtt_client())
        sc = detail["transformations"][0]["source_connection"]
        self.assertEqual(sc, {
            "id": fakes.SRC_CONN_ID, "name": "rsh_gdm_e2e_TEC", "parameterized": True,
            "parameter_name": "parameter_rsh_gdm_e2e", "configured_value": "rsh_gdm_e2e_TEC",
            "is_connection_runtime_parameter": True, "display_value": "rsh_gdm_e2e_TEC"})
        self.assertIsNone(detail["transformations"][0]["target_connection"])
        tc = detail["transformations"][1]["target_connection"]
        self.assertIs(tc["parameterized"], False)
        self.assertIsNone(tc["parameter_name"])
        self.assertIsNone(tc["configured_value"])
        self.assertIs(tc["is_connection_runtime_parameter"], False)
        self.assertEqual(tc["name"], fakes.TGT_CONN_NAME)
        self.assertEqual(detail["last_run"], SAMPLE_LAST_RUN)


# ── 连接展示 ─────────────────────────────────────────────────────────────────
class TestConnectionDisplay(unittest.TestCase):

    def sample(self, conns):
        c = fakes.mtt_client(conn=conns)
        detail = detail_for(c)
        return c, detail, rows_for(detail)

    def test_resolved_name_first(self):
        _, detail, rows = self.sample({})
        self.assertEqual(rows[0]["Source Connection"], "rsh_gdm_e2e_TEC")
        self.assertEqual(rows[1]["Target Connection"], fakes.TGT_CONN_NAME)
        self.assertEqual(warnings_of(detail), [])

    def test_request_failure_falls_back_to_connDefault(self):
        _, detail, rows = self.sample({fakes.SRC_CONN_ID: RuntimeError("500")})
        sc = detail["transformations"][0]["source_connection"]
        self.assertEqual(sc["name"], "")
        self.assertEqual(sc["display_value"], "rsh_gdm_e2e_TEC")
        self.assertEqual(rows[0]["Source Connection"], "rsh_gdm_e2e_TEC")
        self.assertEqual([w["kind"] for w in warnings_of(detail)], ["connection"])
        self.assertEqual(warnings_of(detail)[0]["id"], fakes.SRC_CONN_ID)

    def test_request_failure_non_parameterized_shows_id(self):
        _, detail, rows = self.sample({fakes.TGT_CONN_ID: RuntimeError("500")})
        self.assertEqual(rows[1]["Target Connection"], fakes.TGT_CONN_ID)
        self.assertEqual([w["kind"] for w in warnings_of(detail)], ["connection"])

    def test_response_without_name_is_id_fallback(self):
        for body in ({"id": fakes.SRC_CONN_ID}, {}):
            with self.subTest(body=body):
                _, detail, rows = self.sample({fakes.SRC_CONN_ID: body})
                sc = detail["transformations"][0]["source_connection"]
                self.assertEqual(sc["name"], "")
                self.assertEqual(rows[0]["Source Connection"], "rsh_gdm_e2e_TEC")
                ws = warnings_of(detail, "connection_parameter")
                self.assertEqual(len(ws), 1)
                self.assertEqual(ws[0]["field"], "Source Connection")
                self.assertIn("returned no name; showing connDefault", ws[0]["error"])
                self.assertEqual(warnings_of(detail, "connection"), [])

    def test_response_without_name_non_parameterized_shows_id(self):
        _, detail, rows = self.sample({fakes.TGT_CONN_ID: {"id": fakes.TGT_CONN_ID}})
        self.assertEqual(rows[1]["Target Connection"], fakes.TGT_CONN_ID)
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["field"], "Target Connection")
        self.assertIn("showing connection ID", ws[0]["error"])

    def test_resolved_name_differs_from_connDefault(self):
        _, detail, rows = self.sample({fakes.SRC_CONN_ID: {"name": "other_real_name"}})
        self.assertEqual(rows[0]["Source Connection"], "other_real_name")
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)
        self.assertIn("differs from connDefault", ws[0]["error"])

    def test_non_parameterized_never_uses_connDefault(self):
        raw = make_raw([src_param(conn_id="C_NP",
                                  ui={"connectionParameterized": "false",
                                      "connDefault": "cfg_conn"})])
        detail = detail_for(client_for(raw, {"C_NP": RuntimeError("500")}))
        self.assertEqual(rows_for(detail)[0]["Source Connection"], "C_NP")

    def test_cache_hit_no_duplicate_request(self):
        client = fakes.mtt_client(conn={fakes.TGT_CONN_ID: {"id": fakes.TGT_CONN_ID}})
        d1 = detail_for(client)
        d2 = detail_for(client)
        conn_calls = client.paths("connection/")
        self.assertEqual(sorted(conn_calls),
                         sorted(["connection/" + fakes.SRC_CONN_ID,
                                 "connection/" + fakes.TGT_CONN_ID]))
        for d in (d1, d2):
            self.assertEqual(rows_for(d)[1]["Target Connection"], fakes.TGT_CONN_ID)
            self.assertEqual(len(warnings_of(d, "connection_parameter")), 1)

    def test_shared_conn_display_unchanged_for_dss(self):
        self.assertEqual(exporter._conn_display({"id": "X", "name": "", "display_value": "D"}),
                         "X")


def _no_name_msgs(detail):
    return [w["error"] for w in warnings_of(detail) if "returned no name" in w["error"]]


# 参数化、无 connDefault 的 Source（用于「无 connDefault」场景）
def _no_default_raw(conn_id="C_ND"):
    return make_raw([src_param("S_ND", conn_id=conn_id,
                               ui={"connectionParameterized": "true", "paramName": "p_nd"},
                               rpd={"connectionParameterName": "p_nd"})])


class TestRealNameVsIdFallback(unittest.TestCase):
    """真实名称来源由 resolver 记录（响应中是否有 name / connectionName），
    不再以「名称 == connection ID」推断无名称。"""

    # ── 响应有 name，且 name 恰好等于 ID ──
    def test_name_equals_id_is_real_name_with_connDefault(self):
        body = {"id": fakes.SRC_CONN_ID, "name": fakes.SRC_CONN_ID}
        detail = detail_for(fakes.mtt_client(conn={fakes.SRC_CONN_ID: body}))
        sc = detail["transformations"][0]["source_connection"]
        self.assertEqual(sc["name"], fakes.SRC_CONN_ID)
        self.assertEqual(sc["display_value"], fakes.SRC_CONN_ID)    # 真实名称优先于 connDefault
        self.assertEqual(rows_for(detail)[0]["Source Connection"], fakes.SRC_CONN_ID)
        self.assertEqual(_no_name_msgs(detail), [])
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)                              # 仅名称与 connDefault 的差异
        self.assertIn("differs from connDefault", ws[0]["error"])

    def test_name_equals_id_is_real_name_without_connDefault(self):
        detail = detail_for(client_for(_no_default_raw(),
                                       {"C_ND": {"id": "C_ND", "name": "C_ND"}}))
        sc = detail["transformations"][0]["source_connection"]
        self.assertEqual((sc["name"], sc["display_value"]), ("C_ND", "C_ND"))
        self.assertEqual(warnings_of(detail), [])

    def test_name_equals_id_non_parameterized_target(self):
        body = {"id": fakes.TGT_CONN_ID, "name": fakes.TGT_CONN_ID}
        detail = detail_for(fakes.mtt_client(conn={fakes.TGT_CONN_ID: body}))
        tc = detail["transformations"][1]["target_connection"]
        self.assertEqual(tc["name"], fakes.TGT_CONN_ID)
        self.assertEqual(warnings_of(detail), [])

    def test_connectionName_is_real_name(self):
        detail = detail_for(client_for(_no_default_raw(),
                                       {"C_ND": {"id": "C_ND", "connectionName": "C_ND"}}))
        self.assertEqual(detail["transformations"][0]["source_connection"]["name"], "C_ND")
        self.assertEqual(warnings_of(detail), [])

    # ── 响应只有 ID ──
    def test_id_only_with_connDefault(self):
        detail = detail_for(fakes.mtt_client(conn={fakes.SRC_CONN_ID: {"id": fakes.SRC_CONN_ID}}))
        sc = detail["transformations"][0]["source_connection"]
        self.assertEqual(sc["name"], "")
        self.assertEqual(sc["display_value"], "rsh_gdm_e2e_TEC")
        self.assertEqual(len(_no_name_msgs(detail)), 1)
        self.assertIn("showing connDefault", _no_name_msgs(detail)[0])

    def test_id_only_without_connDefault(self):
        detail = detail_for(client_for(_no_default_raw(), {"C_ND": {"id": "C_ND"}}))
        sc = detail["transformations"][0]["source_connection"]
        self.assertEqual((sc["name"], sc["display_value"]), ("", "C_ND"))
        self.assertEqual(len(_no_name_msgs(detail)), 1)
        self.assertIn("showing connection ID", _no_name_msgs(detail)[0])

    # ── 分别缓存命中 ──
    def _twice(self, client):
        d1, d2 = detail_for(client), detail_for(client)
        return d1, d2, client.paths("connection/")

    def test_cache_hit_name_equals_id(self):
        body = {"id": fakes.SRC_CONN_ID, "name": fakes.SRC_CONN_ID}
        d1, d2, calls = self._twice(fakes.mtt_client(conn={fakes.SRC_CONN_ID: body}))
        self.assertEqual(calls.count("connection/" + fakes.SRC_CONN_ID), 1)
        for d in (d1, d2):
            sc = d["transformations"][0]["source_connection"]
            self.assertEqual((sc["name"], sc["display_value"]),
                             (fakes.SRC_CONN_ID, fakes.SRC_CONN_ID))
            self.assertEqual(_no_name_msgs(d), [])

    def test_cache_hit_name_equals_id_without_connDefault(self):
        client = client_for(_no_default_raw(), {"C_ND": {"id": "C_ND", "name": "C_ND"}})
        d1, d2, calls = self._twice(client)
        self.assertEqual(calls, ["connection/C_ND"])
        for d in (d1, d2):
            self.assertEqual(d["transformations"][0]["source_connection"]["name"], "C_ND")
            self.assertEqual(warnings_of(d), [])

    def test_cache_hit_id_only(self):
        body = {"id": fakes.SRC_CONN_ID}
        d1, d2, calls = self._twice(fakes.mtt_client(conn={fakes.SRC_CONN_ID: body}))
        self.assertEqual(calls.count("connection/" + fakes.SRC_CONN_ID), 1)
        for d in (d1, d2):
            sc = d["transformations"][0]["source_connection"]
            self.assertEqual((sc["name"], sc["display_value"]), ("", "rsh_gdm_e2e_TEC"))
            self.assertEqual(len(_no_name_msgs(d)), 1)

    def test_cache_hit_id_only_without_connDefault(self):
        client = client_for(_no_default_raw(), {"C_ND": {"id": "C_ND"}})
        d1, d2, calls = self._twice(client)
        self.assertEqual(calls, ["connection/C_ND"])
        for d in (d1, d2):
            self.assertEqual(d["transformations"][0]["source_connection"]["display_value"],
                             "C_ND")
            self.assertEqual(len(_no_name_msgs(d)), 1)

    def test_cache_populated_by_public_resolver_keeps_source(self):
        client = client_for(_no_default_raw(), {"C_ND": {"id": "C_ND", "name": "C_ND"}})
        self.assertEqual(query.resolve_connection(client, ["C_ND"]),
                         {"resolved": {"C_ND": "C_ND"}, "failed": []})
        detail = detail_for(client)
        self.assertEqual(client.paths("connection/"), ["connection/C_ND"])
        self.assertEqual(detail["transformations"][0]["source_connection"]["name"], "C_ND")
        self.assertEqual(warnings_of(detail), [])


class TestResolveConnectionContract(unittest.TestCase):
    """resolve-connection CLI / DSS 调用方的既有输出契约不变。"""

    ROUTES = {
        "connection/A": {"id": "A", "name": "name_a"},
        "connection/B": {"id": "B"},
        "connection/C": {},
        "connection/D": {"id": "D", "name": "D"},
        "connection/E": RuntimeError("500"),
        "connection/F": {"id": "F", "connectionName": "cn_f"},
    }
    EXPECTED = {"resolved": {"A": "name_a", "B": "B", "C": "", "D": "D", "F": "cn_f"},
                "failed": ["E"]}
    IDS = ["A", "B", "C", "D", "E", "F", "A", ""]

    def test_public_output_unchanged_and_cached(self):
        client = fakes.FakeClient(routes=self.ROUTES)
        self.assertEqual(query.resolve_connection(client, self.IDS), self.EXPECTED)
        self.assertEqual(query.resolve_connection(client, self.IDS), self.EXPECTED)
        calls = client.paths("connection/")
        self.assertEqual(sorted(set(calls)), sorted(self.ROUTES))
        self.assertEqual(calls.count("connection/A"), 1)
        self.assertEqual(calls.count("connection/E"), 2)       # 失败不缓存（既有行为）

    def test_without_cache_attribute(self):
        client = fakes.FakeClient(routes=self.ROUTES)
        del client.connection_cache
        self.assertEqual(query.resolve_connection(client, self.IDS), self.EXPECTED)

    def test_cli_resolve_connection_output(self):
        client = fakes.FakeClient(routes=self.ROUTES)
        with contextlib.redirect_stderr(io.StringIO()):
            res = iics_cli._cmd_resolve_connection(client, ns(ids=self.IDS[:-1]))
        self.assertEqual(res, self.EXPECTED)

    def test_invalid_input(self):
        client = fakes.FakeClient()
        for v in (None, [], "A"):
            self.assertEqual(query.resolve_connection(client, v),
                             {"resolved": {}, "failed": []})


class TestConnectionCacheSource(unittest.TestCase):

    def test_legacy_api_unchanged(self):
        from cache.connection_cache import ConnectionCache
        cache = ConnectionCache()
        self.assertIsNone(cache.get("X"))
        cache.set("X", None)
        self.assertEqual(cache.get("X"), "")
        calls = []
        self.assertEqual(cache.get_or_fetch("X", lambda k: calls.append(k) or "n"), "")
        self.assertEqual(calls, [])
        self.assertEqual(cache.get_or_fetch("Y", lambda k: "ny"), "ny")
        self.assertEqual(cache.get("Y"), "ny")
        cache.set("", "ignored")
        self.assertIsNone(cache.get(""))

    def test_entry_api_records_source(self):
        from cache.connection_cache import ConnectionCache
        cache = ConnectionCache()
        calls = []

        def fetch(k):
            calls.append(k)
            return k, True

        self.assertEqual(cache.get_or_fetch_entry("ID1", fetch), ("ID1", True))
        self.assertEqual(cache.get_or_fetch_entry("ID1", fetch), ("ID1", True))
        self.assertEqual(calls, ["ID1"])
        self.assertEqual(cache.get("ID1"), "ID1")              # 旧 get() 仍返回字符串
        self.assertEqual(cache.get_or_fetch_entry("ID2", lambda k: (k, False)), ("ID2", False))
        self.assertEqual(cache.get_entry("ID2"), ("ID2", False))

    def test_entry_without_source_is_refetched_once(self):
        from cache.connection_cache import ConnectionCache
        cache = ConnectionCache()
        cache.set("X", "X")                                    # 旧写法：无来源信息
        self.assertIsNone(cache.get_entry("X"))
        calls = []
        fetch = lambda k: calls.append(k) or (k, True)  # noqa: E731
        self.assertEqual(cache.get_or_fetch_entry("X", fetch), ("X", True))
        self.assertEqual(cache.get_or_fetch_entry("X", fetch), ("X", True))
        self.assertEqual(calls, ["X"])


# ── --no-resolve ────────────────────────────────────────────────────────────
class TestNoResolve(unittest.TestCase):

    def test_no_connection_requests_and_ids_shown(self):
        client = fakes.mtt_client()
        res, err = run_get_task(client, no_resolve=True)
        self.assertEqual(client.paths("connection/"), [])
        r0, r1 = res["records"]
        self.assertEqual(r0["Source Connection"], fakes.SRC_CONN_ID)
        self.assertEqual(r1["Target Connection"], fakes.TGT_CONN_ID)
        self.assertEqual(r0["Source Connection Parameter"], "parameter_rsh_gdm_e2e")
        self.assertEqual(r1["Target Connection Parameter"], "")
        self.assertEqual([r0["Last Run"], r1["Last Run"]], [SAMPLE_LAST_RUN] * 2)
        self.assertNotIn("WARNING", err)

    def test_no_skip_warnings_in_full_detail(self):
        detail = detail_for(fakes.mtt_client(), resolve=False)
        self.assertEqual(warnings_of(detail), [])
        sc = detail["transformations"][0]["source_connection"]
        self.assertEqual((sc["name"], sc["display_value"]), ("", fakes.SRC_CONN_ID))


# ── 展开与计数 ───────────────────────────────────────────────────────────────
class TestExpansion(TmpDirCase):

    def test_order_and_duplicates_preserved(self):
        dup = src_param("S_DUP", ui=PARAM_UI, rpd=PARAM_RPD)
        raw = make_raw([dup, tgt_param("T1"), copy.deepcopy(dup)])
        rows = rows_for(detail_for(client_for(raw)))
        self.assertEqual([r["Transformation Name"] for r in rows],
                         ["S_DUP", "T1", "S_DUP"])
        self.assertEqual(rows[0]["Source Connection Parameter"], "p_conn")
        self.assertEqual(rows[2]["Source Connection Parameter"], "p_conn")

    def test_parameterized_without_id_warns_only(self):
        no_id = {"name": "$S_NOID$", "type": "SOURCE",
                 "uiProperties": {"connectionParameterized": "true"}}
        raw = make_raw([src_param("S1"), no_id, tgt_param("T1")])
        detail = detail_for(client_for(raw))
        rows = rows_for(detail)
        self.assertEqual([r["Transformation Name"] for r in rows], ["S1", "T1"])
        ws = warnings_of(detail, "connection_parameter")
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["transformation_name"], "S_NOID")
        self.assertEqual(ws[0]["field"], "Source Connection")
        self.assertIn("no row generated", ws[0]["error"])

    def test_not_parameterized_without_id_silent(self):
        no_id = {"name": "$T_NOID$", "type": "TARGET",
                 "uiProperties": {"connectionParameterized": "false"}}
        detail = detail_for(client_for(make_raw([src_param("S1"), no_id])))
        self.assertEqual(len(rows_for(detail)), 1)
        self.assertEqual(warnings_of(detail, "connection_parameter"), [])

    def test_expansion_not_filtered_by_type(self):
        lookup = {"name": "$LKP_1$", "type": "LOOKUP", "sourceConnectionId": "C_LKP"}
        rows = rows_for(detail_for(client_for(make_raw([lookup]))))
        self.assertEqual([r["Transformation Name"] for r in rows], ["LKP_1"])

    def test_detail_failure_placeholder_new_columns(self):
        client = fakes.mtt_client()
        client.routes["mttask/frs/" + fakes.MTT_FRS_ID] = RuntimeError("boom")
        res = self.export(client)
        self.assertEqual((res["rows"], res["failed_details_count"]), (1, 1))
        sheet = fakes.read_sheets(res["file_path"])["Task Summary"]
        row = dict(zip(sheet[0], sheet[1]))
        self.assertTrue(all(fakes.blank(row[c]) for c in NEW_COLS))

    def test_no_transformation_keeps_last_run(self):
        rows = rows_for(detail_for(client_for(make_raw([]))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Last Run"], SAMPLE_LAST_RUN)
        self.assertEqual(rows[0]["Source Connection Parameter"], "")


# ── 输出与错误 ───────────────────────────────────────────────────────────────
class TestOutputAndErrors(TmpDirCase):

    def test_26_columns_in_contract_order(self):
        expected = [
            "@type", "id", "FRS ID", "orgId",
            "Task Name", "Task Type", "Task Location", "Runtime Environment",
            "Task Updated On", "Last Run",
            "Mapping Name", "Mapping Updated On", "Transformation Name",
            "Source Connection", "Source Connection Parameter", "Source Object",
            "Custom Query",
            "Target Connection", "Target Connection Parameter", "Operation",
            "Target Object",
            "Parameter File Directory", "Parameter File Name",
            "Pre-Processing Commands", "Post-Processing Commands", "Status",
        ]
        self.assertEqual(exporter.MTT_HEADERS, expected)
        self.assertEqual(len(exporter.MTT_HEADERS), 26)
        self.assertEqual(exporter.TASK_SUMMARY_COLUMNS, expected)
        self.assertEqual(exporter._SOURCE_OBJECT_COL, expected.index("Source Object"))

    def test_sample_acceptance_values(self):
        res, _ = run_get_task(fakes.mtt_client())
        src, tgt = res["records"]
        self.assertEqual(src["Last Run"], SAMPLE_LAST_RUN)
        self.assertEqual(tgt["Last Run"], SAMPLE_LAST_RUN)
        self.assertEqual(src["Source Connection"], "rsh_gdm_e2e_TEC")
        self.assertEqual(src["Source Connection Parameter"], "parameter_rsh_gdm_e2e")
        self.assertEqual(tgt["Target Connection"], fakes.TGT_CONN_NAME)
        self.assertEqual(tgt["Target Connection Parameter"], "")
        self.assertEqual(src["Custom Query"], fakes.CUSTOM_QUERY)
        self.assertEqual(src["Source Object"], "")
        self.assertEqual((src["Target Connection"], src["Target Connection Parameter"]),
                         ("", ""))
        self.assertEqual((tgt["Source Connection"], tgt["Source Connection Parameter"]),
                         ("", ""))

    def test_compact_equals_excel(self):
        raw = make_raw([src_param("S1", ui={"connectionParameterized": "true"}),
                        tgt_param("T1", ui=PARAM_UI, rpd=PARAM_RPD)])
        res, _ = run_get_task(client_for(raw))
        exp = self.export(client_for(raw))
        sheet = fakes.read_sheets(exp["file_path"])["Task Summary"]
        self.assertEqual(sheet[0], exporter.MTT_HEADERS)
        excel = [dict(zip(sheet[0], r)) for r in sheet[1:]]
        for rec, xl in zip(res["records"], excel):
            for col in exporter.MTT_HEADERS:
                v = rec[col]
                self.assertEqual(None if v == "" else v, xl[col], col)
        self.assertIsNone(res["records"][0]["Source Connection Parameter"])

    def test_export_errors_keep_transformation_and_field(self):
        raw = make_raw([src_param("S_MISS", ui={"connectionParameterized": "true"})],
                       last_run="abc")
        res = self.export(client_for(raw))
        self.assertEqual(res["failed_details_count"], 0)
        self.assertEqual(res["enrichment_warnings_count"], 2)
        errs = fakes.read_sheets(res["file_path"])["Export Errors"]
        texts = [r[5] for r in errs[1:]]
        self.assertIn("connection_parameter: S_MISS / Source Connection Parameter: "
                      "parameterized but name missing", texts)
        self.assertTrue(any(t.startswith("last_run: Last Run: invalid value 'abc'")
                            for t in texts))
        for w in res["enrichment_warnings"]:
            self.assertEqual(w["task_name"], fakes.MTT_NAME)
            self.assertEqual(w["task_type"], "MTT")
            self.assertEqual(w["folder"], fakes.MTT_FOLDER)
        cp = [w for w in res["enrichment_warnings"] if w["kind"] == "connection_parameter"]
        self.assertEqual(cp[0]["transformation_name"], "S_MISS")
        self.assertEqual(cp[0]["field"], "Source Connection Parameter")
        self.assertEqual(cp[0]["error_type"], "EnrichmentError")

    def test_collector_does_not_override_existing_context(self):
        data = {"details": [{"task_name": "T", "task_type": "MTT", "task_location": "F",
                             "enrichment_warnings": [{"kind": "x", "error": "e",
                                                      "task_name": "keep"}]}]}
        ws = exporter._collect_enrichment_warnings(data)
        self.assertEqual(ws[0]["task_name"], "keep")
        self.assertEqual(ws[0]["folder"], "F")

    def test_warning_merge_one_per_transformation_field(self):
        issues = query._FieldIssues()
        issues.add(0, "S1", "Source Connection", "first")
        issues.add(0, "S1", "Source Connection", "second")
        issues.add(0, "S1", "Source Connection", "first")
        issues.add(0, "S1", "Source Connection Parameter", "third")
        issues.add(2, "S1", "Source Connection", "dup transformation same name")
        out = []
        issues.flush(out)
        self.assertEqual([(w["field"], w["error"]) for w in out], [
            ("Source Connection", "S1 / Source Connection: first; second"),
            ("Source Connection Parameter", "S1 / Source Connection Parameter: third"),
            ("Source Connection", "S1 / Source Connection: dup transformation same name"),
        ])

    def test_failure_with_connDefault_adds_no_extra_warning(self):
        client = fakes.mtt_client(conn={fakes.SRC_CONN_ID: RuntimeError("x")})
        res = self.export(client)
        self.assertEqual([w["kind"] for w in res["enrichment_warnings"]], ["connection"])


# ── 入口与回归 ───────────────────────────────────────────────────────────────
class TestEntryPoints(TmpDirCase):

    def test_mtt_export_26_columns(self):
        res = self.export(fakes.mtt_client())
        sheets = fakes.read_sheets(res["file_path"])
        self.assertEqual(list(sheets), ["Task Summary", "Export Errors"])
        self.assertEqual(len(sheets["Task Summary"][0]), 26)
        self.assertEqual(sheets["Export Errors"][1], ["No export errors"] + [None] * 5)

    def test_all_export_mtt_sheet(self):
        folder = "FAKE_ALL"
        routes, objects = {}, []
        for c in (fakes.mtt_client(), fakes.dss_client(), fakes.wf_client()):
            routes.update(c.routes)
            objects += [dict(o, location=folder) for o in c.objects]
        res = self.export(fakes.FakeClient(routes, objects), folder=folder, ttype="ALL")
        sheets = fakes.read_sheets(res["file_path"])
        self.assertEqual(list(sheets),
                         ["MTT Task", "DSS Tasks", "Workflow Tasks", "Export Errors"])
        self.assertEqual(sheets["MTT Task"][0], exporter.MTT_HEADERS)
        self.assertEqual(len(sheets["DSS Tasks"][0]), 12)
        self.assertEqual(len(sheets["Workflow Tasks"][0]), 9)
        row = dict(zip(sheets["MTT Task"][0], sheets["MTT Task"][1]))
        self.assertEqual(row["Last Run"], SAMPLE_LAST_RUN)
        self.assertEqual(row["Source Connection Parameter"], "parameter_rsh_gdm_e2e")

    def test_selected_export(self):
        sel = [{"name": fakes.MTT_NAME, "type": "MTT", "folder": fakes.MTT_FOLDER}]
        with contextlib.redirect_stderr(io.StringIO()):
            res = export_selected_tasks(fakes.mtt_client(), sel, str(self.dir))
        mtt = res["results"]["MTT"]
        self.assertEqual((mtt["success"], mtt["rows"]), (1, 2))
        sheet = fakes.read_sheets(mtt["file_path"])["Task Summary"]
        self.assertEqual(sheet[0], exporter.MTT_HEADERS)
        row = dict(zip(sheet[0], sheet[2]))
        self.assertEqual(row["Last Run"], SAMPLE_LAST_RUN)
        self.assertEqual(row["Target Connection"], fakes.TGT_CONN_NAME)
        self.assertTrue(fakes.blank(row["Target Connection Parameter"]))

    def test_summary_only_new_columns_blank_no_detail_calls(self):
        client = fakes.mtt_client()
        res = self.export(client, summary_only=True)
        self.assertEqual([p for p in client.paths() if p != fakes.V3_OBJECTS_PATH], [])
        sheet = fakes.read_sheets(res["file_path"])["Task Summary"]
        row = dict(zip(sheet[0], sheet[1]))
        self.assertTrue(all(fakes.blank(row[c]) for c in NEW_COLS))

    def test_json_export_keeps_full_data(self):
        res = self.export(fakes.mtt_client(), fmt="json")
        data = json.loads(Path(res["file_path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"folder", "tasks", "details", "failed_details"})
        d = data["details"][0]
        self.assertEqual(d["last_run"], SAMPLE_LAST_RUN)
        self.assertIn("raw", d)
        sc = d["transformations"][0]["source_connection"]
        self.assertEqual(sc["parameter_name"], "parameter_rsh_gdm_e2e")
        self.assertEqual(sc["display_value"], "rsh_gdm_e2e_TEC")

    def test_full_returns_normalized_detail_with_warnings(self):
        raw = make_raw([src_param("S_MISS", ui={"connectionParameterized": "true"})])
        res, err = run_get_task(client_for(raw), full=True)
        self.assertIn("raw", res)
        self.assertEqual(res["last_run"], SAMPLE_LAST_RUN)
        self.assertEqual(len(warnings_of(res, "connection_parameter")), 1)
        self.assertNotIn("WARNING", err)

    def test_compact_warnings_to_stderr_stdout_unchanged(self):
        raw = make_raw([src_param("S_MISS", ui={"connectionParameterized": "true"})],
                       last_run="abc")
        client = client_for(raw)
        client.routes["mapping/" + fakes.MAPPING_ID] = RuntimeError("mapping down")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(iics_cli, "create_client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = iics_cli.main(["get-task", fakes.MTT_NAME, "MTT",
                                  "--folder", fakes.MTT_FOLDER])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(set(result), {"task_count", "rows", "records"})
        lines = [ln for ln in err.getvalue().splitlines() if "WARNING" in ln]
        self.assertEqual(lines, [
            "[IICS CLI] WARNING mapping: mapping down",
            "[IICS CLI] WARNING last_run: Last Run: invalid value 'abc' "
            "(unparseable datetime string)",
            "[IICS CLI] WARNING connection_parameter: S_MISS / Source Connection "
            "Parameter: parameterized but name missing",
        ])
        self.assertNotIn("WARNING", out.getvalue())

    def test_dss_compact_has_no_stderr_warning_change(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = iics_cli._cmd_get_task(fakes.dss_client(),
                                         ns(name=fakes.DSS_NAME, type="DSS",
                                            folder=fakes.DSS_FOLDER, full=False,
                                            no_resolve=False))
        self.assertEqual(len(res["records"][0]), 12)
        self.assertNotIn("WARNING", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
