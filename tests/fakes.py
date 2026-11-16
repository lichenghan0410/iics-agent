"""
tests/fakes.py — 导出 / MTT 测试共用的 fake IICS client 与 fixture。

全部为离线 fake：不触网、不读取真实 credential、不读写真实 pin。
MTT fixture 裁剪自真实样本 mt_DWH_TO_REG_V_DMF_SSA_FACT_RESULT_QUICK 的
mttask/frs 响应（仅保留解析需要的字段；去掉 createdBy / updatedBy / description
等个人信息字段；customQuery 截短）。其余 DSS / WORKFLOW / connection name 均为虚构值。
"""

import argparse
import copy
import re

from cache.connection_cache import ConnectionCache, ObjectCache

V3_OBJECTS_PATH = "public/core/v3/objects"


class FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return copy.deepcopy(self._body)


class FakeClient:
    """模拟 IICSClient 的最小接口：_request(GET) + 三个缓存。

    routes:  {V2 path: body | Exception}
    objects: V3 /objects 枚举结果（[{name, type, id, path, status, location}]）
    """

    def __init__(self, routes=None, objects=None):
        self.routes = dict(routes or {})
        self.objects = list(objects or [])
        self.calls = []
        self.connection_cache = ConnectionCache()
        self.mapping_cache = ObjectCache()
        self.runtime_cache = ObjectCache()

    def _request(self, method, path, use_v3=False, params=None, **_kw):
        if method != "GET":
            raise ValueError("v0.1 only supports GET requests")
        self.calls.append((method, path, dict(params) if params else None))
        if use_v3 and path == V3_OBJECTS_PATH:
            q = (params or {}).get("q", "")
            loc = re.search(r'location=="([^"]*)"', q)
            typ = re.search(r'type=="([^"]*)"', q)
            items = [o for o in self.objects
                     if (not loc or o.get("location") == loc.group(1))
                     and (not typ or o.get("type") == typ.group(1))]
            skip = int((params or {}).get("skip", 0))
            size = int((params or {}).get("pageSize", 200))
            return FakeResponse({"count": len(items),
                                 "objects": items[skip:skip + size]})
        if path not in self.routes:
            raise RuntimeError(f"404 fake route not found: {path}")
        body = self.routes[path]
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body)

    def paths(self, prefix=""):
        return [p for (_m, p, _q) in self.calls if p.startswith(prefix)]


def ns(**kw):
    """构造 CLI handler 用的 argparse.Namespace。"""
    return argparse.Namespace(**kw)


# ── MTT 真实样本（裁剪）──────────────────────────────────────────────────────
MTT_FOLDER = "JAPAN_COMMERCIAL_DWH"
MTT_NAME = "mt_DWH_TO_REG_V_DMF_SSA_FACT_RESULT_QUICK"
MTT_FRS_ID = "FAKE_FRS_QUICK_0001"
SRC_CONN_ID = "000LVU0B0000000001U8"
TGT_CONN_ID = "000LVU0B0000000001VU"
MAPPING_ID = "000LVU170000000002VV"
RUNTIME_ID = "000LVU25000000000013"
CUSTOM_QUERY = ("select SEQ_NO\n,K_DATE\nfrom scrm_dwh_pub.v_dmf_ssa_fact_result_quick\n"
                "where to_char(k_date,'yyyymmdd') = to_char(sysdate,'yyyymmdd')")

MTT_RAW_SAMPLE = {
    "@type": "mtTask",
    "id": "000LVU0Z000000000857",
    "orgId": "000LVU",
    "name": MTT_NAME,
    "createTime": "2021-11-17T23:57:11.000Z",
    "updateTime": "2026-10-28T03:12:56.000Z",
    "agentId": "000LVU0800000000001N",
    "runtimeEnvironmentId": RUNTIME_ID,
    "lastRunTime": 1794466734000,
    "mappingId": MAPPING_ID,
    "frsGuid": "lG8Ziso0TgrgQtVt6cIZ8k",
    "parameterFileName": "JPN_COMM_DWH_DL.PARAM",
    "parameterFileDir": "E:\\JPN_COMM_DWH\\param",
    "paramFileType": "PARAM_FILE_LOCAL",
    "parameters": [
        {
            "@type": "mtTaskParameter",
            "id": 322460110,
            "name": "$src_scrm_dwh_pub_V_DMF_SSA_FACT_RESULT_QUICK$",
            "type": "SOURCE",
            "label": "parameter_rsh_gdm_e2e",
            "description": "",
            "uiProperties": {
                "cnxtype": "Amazon Redshift v2",
                "connDefault": "rsh_gdm_e2e_TEC",
                "connectionParameterized": "true",
                "paramName": "parameter_rsh_gdm_e2e",
                "paramLabel": "",
                "paramType-mapping": "Connection",
                "logcnx": "parameter_rsh_gdm_e2e",
                "isSelectDistinct": "false",
                "objectParameterized": "false",
                "visible": "false",
                "originalPath": "src_scrm_dwh_pub_V_DMF_SSA_FACT_RESULT_QUICK",
            },
            "sourceConnectionId": SRC_CONN_ID,
            "sourceObject": "DUMMY_src_scrm_dwh_pub_V_DMF_SSA_FACT_RESULT_QUICK",
            "customQuery": CUSTOM_QUERY,
            "objectName": "DUMMY_src_scrm_dwh_pub_V_DMF_SSA_FACT_RESULT_QUICK",
            "runtimeParameterData": {
                "@type": "mtTaskRuntimeParameterData",
                "isConnectionRuntimeParameter": True,
                "isObjectRuntimeParameter": False,
                "connectionParameterName": "parameter_rsh_gdm_e2e",
            },
        },
        {
            "@type": "mtTaskParameter",
            "id": 322460111,
            "name": "$tgt_v_dmf_ssa_fact_result_quick$",
            "type": "TARGET",
            "label": "tgt_v_dmf_ssa_fact_result_quick",
            "uiProperties": {
                "connectionParameterized": "false",
                "objectParameterized": "false",
                "visible": "false",
                "flags": "SUPPORTS_MULTI_SCHEMA",
                "defaultTargetUpdateColumns": "",
                "supportApplyDDLChanges": "false",
                "originalPath": "tgt_v_dmf_ssa_fact_result_quick",
            },
            "targetConnectionId": TGT_CONN_ID,
            "targetObject": "S_IF_V_DMF_SSA_FACT_RSLT_QK",
            "targetObjectLabel": "S_IF_V_DMF_SSA_FACT_RSLT_QK",
            "truncateTarget": True,
            "operationType": "Insert",
            "extendedObject": {"@type": "extendedObject", "singleMode": True,
                               "filters": [], "sortFields": []},
            "objectName": "S_IF_V_DMF_SSA_FACT_RSLT_QK",
            "objectLabel": "S_IF_V_DMF_SSA_FACT_RSLT_QK",
            "runtimeParameterData": {
                "@type": "mtTaskRuntimeParameterData",
                "isConnectionRuntimeParameter": False,
                "isObjectRuntimeParameter": False,
            },
            "dbSchema": "",
        },
    ],
}

# Source 连接名与 connDefault 一致；Target 名为虚构占位（真实名称由真实 resolver 决定）
SRC_CONN_NAME = "rsh_gdm_e2e_TEC"
TGT_CONN_NAME = "fake_resolved_target_conn"


def mtt_objects():
    return [{"name": MTT_NAME, "type": "MTT", "id": MTT_FRS_ID,
             "path": f"{MTT_FOLDER}/{MTT_NAME}", "status": "VALID",
             "location": MTT_FOLDER}]


def mtt_routes(raw=None, conn=None):
    """MTT 样本的全部 fake 路由。conn 可覆盖 connection 响应（body 或 Exception）。"""
    routes = {
        "mttask/frs/" + MTT_FRS_ID: copy.deepcopy(raw or MTT_RAW_SAMPLE),
        "mapping/" + MAPPING_ID: {"name": "m_FAKE_SSA_FACT_RESULT_QUICK",
                                  "updateTime": "2026-10-01T00:00:00.000Z"},
        "runtimeEnvironment/" + RUNTIME_ID: {"name": "FAKE_RUNTIME_ENV"},
        "connection/" + SRC_CONN_ID: {"id": SRC_CONN_ID, "name": SRC_CONN_NAME},
        "connection/" + TGT_CONN_ID: {"id": TGT_CONN_ID, "name": TGT_CONN_NAME},
    }
    for cid, body in (conn or {}).items():
        routes["connection/" + cid] = body
    return routes


def mtt_client(raw=None, conn=None):
    return FakeClient(routes=mtt_routes(raw, conn), objects=mtt_objects())


# ── DSS（虚构）───────────────────────────────────────────────────────────────
DSS_FOLDER = "JAPAN_SCRM_DI"
DSS_NAME = "DSS_FAKE_01"
DSS_FRS_ID = "FAKE_FRS_DSS_0001"
DSS_RAW = {
    "@type": "dssTask",
    "id": "000LVU0I00000000FAKE",
    "orgId": "000LVU",
    "name": DSS_NAME,
    "updateTime": "2026-01-02T03:04:05.000Z",
    "sourceConnectionId": "000LVU0B00000000S001",
    "targetConnectionId": "000LVU0B00000000T001",
    "sourceObjects": [{"name": "SRC_TBL_A"}, {"name": "SRC_TBL_B"}],
    "operation": "Upsert",
    "targetObject": "TGT_TBL_A",
}


def dss_client():
    return FakeClient(
        routes={
            "dsstask/name/" + DSS_NAME: copy.deepcopy(DSS_RAW),
            "connection/000LVU0B00000000S001": {"name": "FAKE_SRC_CONN"},
            "connection/000LVU0B00000000T001": {"name": "FAKE_TGT_CONN"},
        },
        objects=[{"name": DSS_NAME, "type": "DSS", "id": DSS_FRS_ID,
                  "path": f"{DSS_FOLDER}/{DSS_NAME}", "status": "VALID",
                  "location": DSS_FOLDER}],
    )


# ── WORKFLOW（虚构）──────────────────────────────────────────────────────────
WF_FOLDER = "JAPAN_SCRM_DI"
WF_NAME = "WF_FAKE_01"
WF_EMPTY_NAME = "WF_FAKE_EMPTY"
WF_RAW = {
    "@type": "workflow",
    "id": "000LVU0N00000000FAKE",
    "orgId": "000LVU",
    "name": WF_NAME,
    "updateTime": "2026-02-03T04:05:06.000Z",
    "tasks": [
        {"taskId": "000LVU0Z00000000AAA1", "type": "MTT", "name": "mt_child_1",
         "stopOnError": True},
        {"taskId": "000LVU0Z00000000AAA2", "type": "DSS", "name": "dss_child_2"},
    ],
}
WF_EMPTY_RAW = {"@type": "workflow", "id": "000LVU0N00000000FAK2", "orgId": "000LVU",
                "name": WF_EMPTY_NAME, "updateTime": "2026-02-04T00:00:00.000Z",
                "tasks": []}


def wf_client():
    return FakeClient(
        routes={"workflow/name/" + WF_NAME: copy.deepcopy(WF_RAW),
                "workflow/name/" + WF_EMPTY_NAME: copy.deepcopy(WF_EMPTY_RAW)},
        objects=[
            {"name": WF_NAME, "type": "WORKFLOW", "id": "FAKE_FRS_WF_0001",
             "path": f"{WF_FOLDER}/{WF_NAME}", "status": "VALID", "location": WF_FOLDER},
            {"name": WF_EMPTY_NAME, "type": "WORKFLOW", "id": "FAKE_FRS_WF_0002",
             "path": f"{WF_FOLDER}/{WF_EMPTY_NAME}", "status": "VALID",
             "location": WF_FOLDER},
        ],
    )


def read_sheets(path):
    """读回 workbook → {sheet_title: [row values list]}（含表头行）。"""
    from openpyxl import load_workbook
    wb = load_workbook(path)
    return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)]
            for ws in wb.worksheets}


def blank(v):
    """Excel 中 None 与 "" 都显示为空白。"""
    return v is None or v == ""
