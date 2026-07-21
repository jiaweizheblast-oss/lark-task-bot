"""
Lark 多维表格（Bitable）客户端：机器人以「应用身份」建自己的表、读写记录、把表分享给管理员。

为什么这样能绕开之前的授权噩梦：
  机器人自己建的 base，机器人天生就是主人 —— 读写随便，不用加协作者、不用 OAuth、
  也不碰知识库那套权限。之前卡住是因为想去读 Wade 建在知识库里的表；换成「机器人建自己的表」
  就全跳过了。机器人只需要已经有的 APP_ID/APP_SECRET（发任务消息用的那对）+ bitable:app 权限。

联调须知：真实 HTTP 调用只能在能连 Lark 外网的环境（线上 Railway）跑，本地沙箱连不上 Lark。
所有函数统一返回 dict：成功 {"ok": True, ...}；失败 {"ok": False, "error": "...", "raw": <Lark原始返回>}，
把 Lark 的 code/msg 原样透传，方便定位是权限、网络还是参数问题。
"""
import os
import json
import time
import urllib.request
import urllib.error
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DOMAIN = os.environ.get("LARK_DOMAIN", "https://open.larksuite.com").rstrip("/")

# Channel Analytics belongs to the outward-facing Recruitment Bot. Keep these
# credentials separate from APP_ID / APP_SECRET, which remain owned by Task Bot.
# There is deliberately no fallback: missing Recruitment Bot configuration must
# fail closed instead of creating a Base under the wrong Lark application.
APP_ID = os.environ.get("RECRUITMENT_LARK_APP_ID", "")
APP_SECRET = os.environ.get("RECRUITMENT_LARK_APP_SECRET", "")

# 字段类型（Bitable）：1=多行文本 3=单选
FT_TEXT = 1
FT_SINGLE = 3
FT_MODIFIED_TIME = 1002

_token_cache = {"v": "", "exp": 0.0}


def table_url(base_url, table_id):
    """Return a Base link that opens the requested table, not Lark's default."""
    if not base_url or not table_id:
        return base_url or ""
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["table"] = str(table_id)
    # A view belongs to a table. The Base creation response points at the
    # default table's view, so retaining it can override/invalidly redirect the
    # requested Candidate Pipeline table.
    query.pop("view", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


# ---------------- 底层 HTTP ----------------
def _req(method, path, token=None, body=None, timeout=15):
    url = DOMAIN + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"code": -1, "msg": "HTTP %s" % e.code}
    except Exception as e:
        return {"code": -1, "msg": "网络错误：%s" % e}


def tenant_token():
    """应用身份 token（缓存到过期前 60s）。返回 (token|None, error|None)。"""
    now = time.time()
    if _token_cache["v"] and _token_cache["exp"] - 60 > now:
        return _token_cache["v"], None
    if not APP_ID or not APP_SECRET:
        return None, (
            "Missing RECRUITMENT_LARK_APP_ID / RECRUITMENT_LARK_APP_SECRET "
            "in Railway variables"
        )
    r = _req("POST", "/open-apis/auth/v3/tenant_access_token/internal",
             body={"app_id": APP_ID, "app_secret": APP_SECRET})
    if r.get("code") == 0 and r.get("tenant_access_token"):
        _token_cache["v"] = r["tenant_access_token"]
        _token_cache["exp"] = now + int(r.get("expire", 7200))
        return _token_cache["v"], None
    return None, "拿 token 失败：%s" % (r.get("msg") or r)


def ping():
    """连接自检：能不能拿到应用身份 token。联调第一步先点这个。"""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "domain": DOMAIN, "app_id_tail": APP_ID[-6:] if APP_ID else ""}


# ---------------- 建表 ----------------
def _fields_spec(job_titles, channels, statuses):
    """候选人表字段。首列必须是文本型主键 -> 用「候选人」。渠道/职位/状态做单选下拉，HR 不用手打、不会错。"""
    return [
        {"field_name": "候选人", "type": FT_TEXT},
        {"field_name": "日期", "type": FT_TEXT},
        {"field_name": "招聘渠道", "type": FT_SINGLE,
         "property": {"options": [{"name": c} for c in channels]}},
        {"field_name": "其他来源说明（选择 Other 时填写）", "type": FT_TEXT},
        {"field_name": "关联职位", "type": FT_SINGLE,
         "property": {"options": [{"name": t} for t in job_titles]}} if job_titles else
        {"field_name": "关联职位", "type": FT_TEXT},
        {"field_name": "状态", "type": FT_SINGLE,
         "property": {"options": [{"name": s} for s in statuses]}},
        {"field_name": "备注", "type": FT_TEXT},
        {"field_name": "填写人", "type": FT_TEXT},
    ]


def create_base(name, job_titles, channels, statuses, folder_token=""):
    """建一个机器人自己的多维表格 + 一张候选人表。返回 {ok, app_token, table_id, url}。"""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    body = {"name": name}
    if folder_token:
        body["folder_token"] = folder_token
    r = _req("POST", "/open-apis/bitable/v1/apps", token=tok, body=body)
    if r.get("code") != 0:
        return {"ok": False, "error": "建 base 失败：%s" % (r.get("msg") or r), "raw": r}
    app = (r.get("data") or {}).get("app") or {}
    app_token = app.get("app_token")
    url = app.get("url") or (DOMAIN + "/base/" + str(app_token))
    if not app_token:
        return {"ok": False, "error": "建 base 没返回 app_token", "raw": r}
    # 建候选人表（带字段）
    tbody = {"table": {"name": "候选人跟进", "default_view_name": "候选人",
                       "fields": _fields_spec(job_titles, channels, statuses)}}
    r2 = _req("POST", "/open-apis/bitable/v1/apps/%s/tables" % app_token, token=tok, body=tbody)
    if r2.get("code") != 0:
        return {"ok": False, "error": "建表失败：%s" % (r2.get("msg") or r2),
                "app_token": app_token, "url": url, "raw": r2}
    table_id = (r2.get("data") or {}).get("table_id")
    return {"ok": True, "app_token": app_token, "table_id": table_id, "url": url}


def _channel_fields_spec(job_titles, channels):
    """Channel Analytics manual-unidentified schema (one channel/job/day)."""
    return [
        {"field_name": "日期", "type": FT_TEXT},
        {"field_name": "招聘渠道", "type": FT_SINGLE,
         "property": {"options": [{"name": c} for c in channels]}},
        {"field_name": "其他来源说明（选择 Other 时填写）", "type": FT_TEXT},
        {"field_name": "关联职位", "type": FT_SINGLE,
         "property": {"options": [{"name": t} for t in job_titles]}} if job_titles else
        {"field_name": "关联职位", "type": FT_TEXT},
        {"field_name": "今日新增简历数", "type": 2},
        {"field_name": "初筛通过数", "type": 2},
        {"field_name": "已推荐面试数", "type": 2},
        {"field_name": "已拒绝数", "type": 2},
        {"field_name": "备注", "type": FT_TEXT},
        {"field_name": "填写人", "type": FT_TEXT},
    ]


def _pipeline_fields_spec(job_titles, channels, stages):
    return [
        {"field_name": "Candidate", "type": FT_TEXT},
        {"field_name": "Entry Date", "type": FT_TEXT},
        {"field_name": "Source Channel", "type": FT_SINGLE,
         "property": {"options": [{"name": c} for c in channels]}},
        {"field_name": "其他来源说明（选择 Other 时填写）", "type": FT_TEXT,
         "description": "仅当 Source Channel 选择 Other 时填写真实渠道名称；其他渠道请留空。"},
        {"field_name": "Job", "type": FT_SINGLE,
         "property": {"options": [{"name": t} for t in job_titles]}} if job_titles else
        {"field_name": "Job", "type": FT_TEXT},
        {"field_name": "Current Stage", "type": FT_SINGLE,
         "property": {"options": [{"name": s} for s in stages]}},
        # This placeholder is converted immediately after table creation into
        # Lark's read-only Modified Time field scoped to Current Stage.
        {"field_name": "Stage Date", "type": FT_TEXT,
         "description": "系统字段：自动记录 Current Stage 的变更日期，HR 无需填写。"},
        {"field_name": "HR Owner", "type": FT_TEXT},
        {"field_name": "Rejection Reason", "type": FT_TEXT},
        {"field_name": "Note", "type": FT_TEXT},
        {"field_name": "System ID", "type": FT_TEXT},
    ]


def create_channel_base(name, job_titles, channels, stages, folder_token=""):
    """Create one Base containing the primary Pipeline and optional bulk counts."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    body = {"name": name}
    if folder_token:
        body["folder_token"] = folder_token
    response = _req("POST", "/open-apis/bitable/v1/apps", token=tok, body=body)
    if response.get("code") != 0:
        return {"ok": False, "error": "建 base 失败：%s" % (response.get("msg") or response), "raw": response}
    app = (response.get("data") or {}).get("app") or {}
    app_token = app.get("app_token")
    url = app.get("url") or (DOMAIN + "/base/" + str(app_token))
    if not app_token:
        return {"ok": False, "error": "建 base 没返回 app_token", "raw": response}
    pipeline_response = _req(
        "POST",
        "/open-apis/bitable/v1/apps/%s/tables" % app_token,
        token=tok,
        body={"table": {"name": "Candidate Pipeline", "default_view_name": "Pipeline",
                         "fields": _pipeline_fields_spec(job_titles, channels, stages)}},
    )
    if pipeline_response.get("code") != 0:
        return {"ok": False, "error": "建 Pipeline 表失败：%s" % (pipeline_response.get("msg") or pipeline_response),
                "app_token": app_token, "url": url, "raw": pipeline_response}
    manual_response = _req(
        "POST", "/open-apis/bitable/v1/apps/%s/tables" % app_token, token=tok,
        body={"table": {"name": "未建档批量统计（特殊情况）", "default_view_name": "补充统计",
                         "fields": _channel_fields_spec(job_titles, channels)}},
    )
    if manual_response.get("code") != 0:
        return {"ok": False, "error": "建渠道汇总表失败：%s" % (manual_response.get("msg") or manual_response),
                "app_token": app_token, "url": url, "raw": manual_response}
    pipeline_table_id = (pipeline_response.get("data") or {}).get("table_id")
    manual_table_id = (manual_response.get("data") or {}).get("table_id")
    schema = ensure_channel_base_schema(
        app_token,
        pipeline_table_id,
        manual_table_id,
    )
    return {"ok": True, "app_token": app_token,
            "pipeline_table_id": pipeline_table_id,
            "manual_table_id": manual_table_id,
            "url": table_url(url, pipeline_table_id),
            "schema": schema}


def configure_system_stage_date_field(app_token, pipeline_table_id):
    """Make Stage Date a read-only Lark field tracking Current Stage changes."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    response = _req(
        "GET",
        "/open-apis/bitable/v1/apps/%s/tables/%s/fields?page_size=100" %
        (app_token, pipeline_table_id),
        token=tok,
    )
    if response.get("code") != 0:
        return {"ok": False, "error": "列出 Pipeline 字段失败：%s" %
                (response.get("msg") or response), "raw": response}
    fields = (response.get("data") or {}).get("items") or []
    by_name = {str(item.get("field_name") or ""): item for item in fields}
    stage_field = by_name.get("Current Stage")
    date_field = by_name.get("Stage Date")
    if not stage_field or not date_field:
        return {"ok": False, "error": "Candidate Pipeline 缺少 Current Stage 或 Stage Date 字段"}
    if int(date_field.get("type") or 0) == FT_MODIFIED_TIME:
        return {"ok": True, "updated": False, "field_id": date_field.get("field_id")}
    path = "/open-apis/bitable/v1/apps/%s/tables/%s/fields/%s" % (
        app_token, pipeline_table_id, date_field.get("field_id")
    )
    base_body = {
        "field_name": "Stage Date",
        "type": FT_MODIFIED_TIME,
        "description": "系统字段：自动记录日期，HR 无需填写。",
    }
    scoped_body = dict(base_body)
    scoped_body["property"] = {
        "date_formatter": "yyyy/MM/dd",
        "fields": [stage_field.get("field_id")],
    }
    update = _req("PUT", path, token=tok, body=scoped_body)
    tracking_mode = "current_stage"
    if update.get("code") != 0:
        # Some Lark tenants expose Modified Time but not field-scoped Modified
        # Time through the public API. Retain the important safety property
        # (system-owned/read-only) and keep the canonical stage date in the DB.
        fallback_body = dict(base_body)
        fallback_body["property"] = {"date_formatter": "yyyy/MM/dd"}
        update = _req("PUT", path, token=tok, body=fallback_body)
        tracking_mode = "record_modified"
    if update.get("code") != 0:
        return {"ok": False, "error": "锁定 Stage Date 失败：%s" %
                (update.get("msg") or update), "raw": update}
    return {"ok": True, "updated": True, "field_id": date_field.get("field_id"),
            "tracking_mode": tracking_mode}


def normalize_other_source_field(app_token, table_id):
    """Give the conditional Other detail field one unambiguous shared label."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    response = _req(
        "GET",
        "/open-apis/bitable/v1/apps/%s/tables/%s/fields?page_size=100" %
        (app_token, table_id),
        token=tok,
    )
    if response.get("code") != 0:
        return {"ok": False, "error": "列出字段失败：%s" %
                (response.get("msg") or response), "raw": response}
    items = (response.get("data") or {}).get("items") or []
    target = "其他来源说明（选择 Other 时填写）"
    if any(str(item.get("field_name") or "") == target for item in items):
        return {"ok": True, "updated": False}
    aliases = {
        "Other Source (if Other)",
        "其他来源说明（选 Other 时必填）",
        "Source Detail",
    }
    legacy = next(
        (item for item in items if str(item.get("field_name") or "") in aliases), None
    )
    if not legacy:
        return {"ok": False, "error": "缺少其他来源说明字段"}
    body = {
        "field_name": target,
        "type": int(legacy.get("type") or FT_TEXT),
        "description": "仅当 Source Channel 选择 Other 时填写真实渠道名称；其他渠道请留空。",
    }
    if legacy.get("property") is not None:
        body["property"] = legacy.get("property")
    if legacy.get("ui_type"):
        body["ui_type"] = legacy.get("ui_type")
    update = _req(
        "PUT",
        "/open-apis/bitable/v1/apps/%s/tables/%s/fields/%s" %
        (app_token, table_id, legacy.get("field_id")),
        token=tok,
        body=body,
    )
    if update.get("code") != 0:
        return {"ok": False, "error": "更新其他来源说明字段失败：%s" %
                (update.get("msg") or update), "raw": update}
    return {"ok": True, "updated": True}


def ensure_channel_base_schema(app_token, pipeline_table_id, manual_table_id):
    """Enforce business-table invariants without exposing repair controls to HR."""
    if not app_token or not pipeline_table_id or not manual_table_id:
        return {"ok": False, "error": "Channel Analytics Base 配置不完整"}
    stage_date = configure_system_stage_date_field(app_token, pipeline_table_id)
    pipeline_other = normalize_other_source_field(app_token, pipeline_table_id)
    manual_other = normalize_other_source_field(app_token, manual_table_id)
    cleanup = cleanup_empty_default_tables(
        app_token,
        protected_table_ids=(pipeline_table_id, manual_table_id),
    )
    return {
        "ok": bool(stage_date.get("ok") and pipeline_other.get("ok")
                   and manual_other.get("ok") and cleanup.get("ok")),
        "stage_date": stage_date,
        "pipeline_other_source": pipeline_other,
        "manual_other_source": manual_other,
        "default_table_cleanup": cleanup,
    }


def cleanup_empty_default_tables(app_token, protected_table_ids=()):
    """Delete only Lark-created, empty default tables; never business tables."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err, "removed": []}
    protected = {str(value) for value in protected_table_ids if value}
    response = _req(
        "GET", "/open-apis/bitable/v1/apps/%s/tables?page_size=100" % app_token,
        token=tok,
    )
    if response.get("code") != 0:
        return {"ok": False, "error": "列出数据表失败：%s" % (response.get("msg") or response),
                "removed": [], "raw": response}
    candidates = []
    for item in (response.get("data") or {}).get("items", []):
        table_id = str(item.get("table_id") or "")
        name = str(item.get("name") or "").strip().casefold()
        if table_id and table_id not in protected and name in {"table", "数据表", "表格"}:
            candidates.append((table_id, item.get("name") or "Table"))
    removed, retained = [], []
    for table_id, name in candidates:
        records = _req(
            "GET",
            "/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=1" % (app_token, table_id),
            token=tok,
        )
        if records.get("code") != 0:
            retained.append({"table_id": table_id, "name": name, "reason": "record_check_failed"})
            continue
        if (records.get("data") or {}).get("items"):
            retained.append({"table_id": table_id, "name": name, "reason": "not_empty"})
            continue
        deleted = _req(
            "DELETE", "/open-apis/bitable/v1/apps/%s/tables/%s" % (app_token, table_id),
            token=tok,
        )
        if deleted.get("code") == 0:
            removed.append({"table_id": table_id, "name": name})
        else:
            retained.append({"table_id": table_id, "name": name, "reason": "delete_failed"})
    return {"ok": True, "removed": removed, "retained": retained}


def add_member(app_token, member_id, member_type="email", perm="full_access"):
    """把这张表分享给管理员（机器人是主人，能授权）。member_type: email/openid/userid。
    需要应用开通 drive 相关权限；没开会在这里报权限错误，联调时按提示加。"""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    r = _req("POST", "/open-apis/drive/v1/permissions/%s/members?type=bitable&need_notification=false" % app_token,
             token=tok, body={"member_type": member_type, "member_id": member_id, "perm": perm})
    if r.get("code") != 0:
        return {"ok": False, "error": "分享给管理员失败：%s" % (r.get("msg") or r), "raw": r}
    return {"ok": True}


# ---------------- 读写记录 ----------------
FIELD_KEYS = ("候选人", "日期", "招聘渠道", "关联职位", "状态", "备注", "填写人")
CHANNEL_FIELD_KEYS = (
    "日期", "招聘渠道", "其他来源说明（选择 Other 时填写）",
    "其他来源说明（选 Other 时必填）", "Source Detail", "关联职位", "今日新增简历数",
    "初筛通过数", "已推荐面试数", "已拒绝数", "备注", "填写人",
)
PIPELINE_FIELD_KEYS = (
    "Candidate", "Entry Date", "Source Channel", "其他来源说明（选择 Other 时填写）",
    "Other Source (if Other)", "其他来源说明（选 Other 时必填）", "Source Detail", "Job",
    "Current Stage", "Stage Date", "HR Owner", "Rejection Reason", "Note", "System ID",
)


def _flatten(v):
    """Bitable 有些字段返回对象/数组（人员、单选等），取可读文本。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("text") or v.get("name") or ""
    if isinstance(v, list):
        return "，".join(_flatten(x) for x in v)
    return str(v)


def list_records(app_token, table_id):
    """读全表记录。返回 {ok, records:[{record_id, fields:{候选人/日期/...}}]}（分页取全）。"""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    out, page = [], ""
    for _ in range(50):  # 最多 50 页保护
        path = "/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=500" % (app_token, table_id)
        if page:
            path += "&page_token=" + page
        r = _req("GET", path, token=tok)
        if r.get("code") != 0:
            return {"ok": False, "error": "读记录失败：%s" % (r.get("msg") or r), "raw": r}
        d = r.get("data") or {}
        for it in d.get("items", []):
            f = it.get("fields", {})
            out.append({"record_id": it.get("record_id"),
                        "fields": {k: _flatten(f.get(k)) for k in FIELD_KEYS}})
        if d.get("has_more") and d.get("page_token"):
            page = d["page_token"]
        else:
            break
    return {"ok": True, "records": out}


def list_channel_records(app_token, table_id):
    """Read the full Channel Analytics Bitable with bounded pagination."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    out, page = [], ""
    for _ in range(50):
        path = "/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=500" % (app_token, table_id)
        if page:
            path += "&page_token=" + page
        response = _req("GET", path, token=tok)
        if response.get("code") != 0:
            return {"ok": False, "error": "读渠道记录失败：%s" % (response.get("msg") or response),
                    "raw": response}
        data = response.get("data") or {}
        for item in data.get("items", []):
            fields = item.get("fields", {})
            out.append({"record_id": item.get("record_id"),
                        "fields": {key: _flatten(fields.get(key)) for key in CHANNEL_FIELD_KEYS}})
        if data.get("has_more") and data.get("page_token"):
            page = data["page_token"]
        else:
            break
    return {"ok": True, "records": out}


def list_pipeline_records(app_token, table_id):
    """Read all Candidate Pipeline rows with bounded pagination."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    out, page = [], ""
    for _ in range(50):
        path = "/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=500" % (app_token, table_id)
        if page:
            path += "&page_token=" + page
        response = _req("GET", path, token=tok)
        if response.get("code") != 0:
            return {"ok": False, "error": "读 Pipeline 失败：%s" % (response.get("msg") or response),
                    "raw": response}
        data = response.get("data") or {}
        for item in data.get("items", []):
            fields = item.get("fields", {})
            out.append({"record_id": item.get("record_id"),
                        "fields": {key: _flatten(fields.get(key)) for key in PIPELINE_FIELD_KEYS}})
        if data.get("has_more") and data.get("page_token"):
            page = data["page_token"]
        else:
            break
    return {"ok": True, "records": out}


def _rec_fields(cand, jobs_by_id):
    return {
        "候选人": cand.get("name") or "",
        "日期": str(cand.get("apply_date") or "")[:10],
        "招聘渠道": cand.get("channel") or "",
        "关联职位": cand.get("job_title") or jobs_by_id.get(cand.get("job_request_id"), ""),
        "状态": cand.get("status") or "新简历",
        "备注": cand.get("note") or "",
        "填写人": cand.get("filled_by") or "",
    }


def create_record(app_token, table_id, cand, jobs_by_id):
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    r = _req("POST", "/open-apis/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id),
             token=tok, body={"fields": _rec_fields(cand, jobs_by_id)})
    if r.get("code") != 0:
        return {"ok": False, "error": "写记录失败：%s" % (r.get("msg") or r), "raw": r}
    return {"ok": True, "record_id": ((r.get("data") or {}).get("record") or {}).get("record_id")}


def update_record(app_token, table_id, record_id, cand, jobs_by_id):
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    r = _req("PUT", "/open-apis/bitable/v1/apps/%s/tables/%s/records/%s" % (app_token, table_id, record_id),
             token=tok, body={"fields": _rec_fields(cand, jobs_by_id)})
    if r.get("code") != 0:
        return {"ok": False, "error": "更新记录失败：%s" % (r.get("msg") or r), "raw": r}
    return {"ok": True}


def update_pipeline_record_fields(app_token, table_id, record_id, fields):
    """Write back system-normalised Pipeline fields without replacing HR input."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    # Stage Date is a read-only Lark Modified Time field. Only identity is
    # written back; HR input fields are never replaced here.
    allowed = {"System ID"}
    safe_fields = {key: value for key, value in dict(fields or {}).items() if key in allowed}
    if not safe_fields:
        return {"ok": True, "updated": False}
    response = _req(
        "PUT",
        "/open-apis/bitable/v1/apps/%s/tables/%s/records/%s" %
        (app_token, table_id, record_id),
        token=tok,
        body={"fields": safe_fields},
    )
    if response.get("code") != 0:
        return {"ok": False, "error": "回写 Pipeline 失败：%s" %
                (response.get("msg") or response), "raw": response}
    return {"ok": True, "updated": True}
