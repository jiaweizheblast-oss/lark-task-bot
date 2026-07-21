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
import datetime
import urllib.request
import urllib.error
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import channel_pipeline_schema as pipeline_schema

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
FT_CREATED_TIME = 1001
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
    fields = []
    for spec in pipeline_schema.MANUAL_COLUMNS:
        field = {"field_name": spec["header"], "type": FT_TEXT}
        if spec["kind"] == "choice":
            options = channels if spec["key"] == "channel" else job_titles
            if options:
                field["type"] = FT_SINGLE
                field["property"] = {"options": [{"name": value} for value in options]}
        elif spec["kind"] == "int":
            field["type"] = 2
        fields.append(field)
    return fields


def _pipeline_fields_spec(job_titles, channels, stages):
    fields = []
    for spec in pipeline_schema.columns_for("lark"):
        field = {"field_name": spec["header"], "type": FT_TEXT}
        if spec["key"] == "record_date":
            # Correct from first creation: HR must never see an editable text
            # placeholder for Entry Date.
            field["type"] = FT_CREATED_TIME
            field["property"] = {"date_formatter": "yyyy/MM/dd"}
        elif spec["kind"] == "choice":
            options = (
                channels if spec["key"] == "channel"
                else job_titles if spec["key"] == "job"
                else stages
            )
            if options:
                field["type"] = FT_SINGLE
                field["property"] = {"options": [{"name": value} for value in options]}
        if spec["key"] == "source_detail":
            field["description"] = (
                "ONLY fill this when Source Channel is Other. Any other combination is rejected on submit."
            )
        elif spec["key"] == "record_date":
            field["description"] = "System-owned date when the candidate first enters the pipeline."
        elif spec["key"] == "stage_date":
            field["description"] = "System-owned date when Current Stage last changed."
        fields.append(field)
    return fields


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
        body={"table": {"name": pipeline_schema.PIPELINE_TABLE_NAME,
                         "default_view_name": pipeline_schema.PIPELINE_VIEW_NAME,
                         "fields": _pipeline_fields_spec(job_titles, channels, stages)}},
    )
    if pipeline_response.get("code") != 0:
        return {"ok": False, "error": "建 Pipeline 表失败：%s" % (pipeline_response.get("msg") or pipeline_response),
                "app_token": app_token, "url": url, "raw": pipeline_response}
    manual_response = _req(
        "POST", "/open-apis/bitable/v1/apps/%s/tables" % app_token, token=tok,
        body={"table": {"name": pipeline_schema.MANUAL_TABLE_NAME,
                         "default_view_name": pipeline_schema.MANUAL_VIEW_NAME,
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


def _list_fields(app_token, table_id):
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
    return {"ok": True, "fields": (response.get("data") or {}).get("items") or []}


def _field_update(path, token, body):
    """Retry only Lark's documented transient consistency failures."""
    transient_codes = {1254607, 1254608, 1255001, 1255040}
    response = {}
    for attempt in range(3):
        response = _req("PUT", path, token=token, body=body)
        if response.get("code") not in transient_codes or attempt == 2:
            return response
        time.sleep(0.5 * (attempt + 1))
    return response


def _field_create(path, token, body):
    """Create a field, retrying only Lark's transient consistency failures."""
    transient_codes = {1254291, 1254607, 1254608, 1255001, 1255040}
    response = {}
    for attempt in range(4):
        response = _req("POST", path, token=token, body=body)
        if response.get("code") not in transient_codes or attempt == 3:
            return response
        time.sleep(0.5 * (attempt + 1))
    return response


def _delete_retry(path, token):
    """Serialise schema cleanup through Lark's transient consistency window."""
    transient_codes = {1254291, 1254607, 1254608, 1255001, 1255040}
    response = {}
    for attempt in range(4):
        response = _req("DELETE", path, token=token)
        if response.get("code") not in transient_codes or attempt == 3:
            return response
        time.sleep(0.5 * (attempt + 1))
    return response


def _find_field(fields, spec):
    names = (spec["header"], *spec.get("aliases", ()))
    return next(
        (item for item in fields if str(item.get("field_name") or "") in names),
        None,
    )


def _iso_date_text(value):
    text = str(value or "").strip()[:10]
    if not text:
        return True
    try:
        datetime.date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _clear_invalid_legacy_date_values(app_token, table_id, field_name):
    """Clear only invalid HR text before converting a system-owned date field.

    Both dates have always been ignored by the importer, so invalid free text is
    not candidate history. Valid ISO dates remain available for Lark's normal
    field conversion instead of being destroyed.
    """
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err, "cleared": 0}
    cleared, page = 0, ""
    for _ in range(50):
        path = "/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=500" % (
            app_token, table_id)
        if page:
            path += "&page_token=" + page
        response = _req("GET", path, token=tok)
        if response.get("code") != 0:
            return {
                "ok": False,
                "error": "Unable to inspect legacy system dates: %s" %
                         (response.get("msg") or response),
                "raw": response,
                "cleared": cleared,
            }
        data = response.get("data") or {}
        for record in data.get("items") or []:
            raw = (record.get("fields") or {}).get(field_name)
            if raw not in (None, "") and not _iso_date_text(_flatten(raw)):
                update = _req(
                    "PUT",
                    "/open-apis/bitable/v1/apps/%s/tables/%s/records/%s" %
                    (app_token, table_id, record.get("record_id")),
                    token=tok,
                    body={"fields": {field_name: None}},
                )
                if update.get("code") != 0:
                    return {
                        "ok": False,
                        "error": "Unable to clear invalid legacy %s: %s" %
                                 (field_name, update.get("msg") or update),
                        "raw": update,
                        "cleared": cleared,
                    }
                cleared += 1
        if data.get("has_more") and data.get("page_token"):
            page = data["page_token"]
        else:
            break
    return {"ok": True, "cleared": cleared}


def _verify_system_date_fields(app_token, pipeline_table_id):
    """Verify that service-owned dates are absent from the Lark HR surface."""
    listed = _list_fields(app_token, pipeline_table_id)
    if not listed.get("ok"):
        return listed
    fields = listed["fields"]
    entry_spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                      if item["key"] == "record_date")
    date_spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                     if item["key"] == "stage_date")
    entry_field = _find_field(fields, entry_spec)
    date_field = _find_field(fields, date_spec)
    problems = []
    if entry_field:
        problems.append("Legacy Entry Date field is still exposed in Lark")
    if date_field:
        problems.append("Legacy editable Stage Started On field is still exposed in Lark")
    return {
        "ok": not problems,
        "errors": problems,
        "entry_field_id": None,
        "stage_date_field_id": None,
    }


def configure_system_entry_date_field(app_token, pipeline_table_id):
    """Replace a legacy editable Entry Date with Lark Created Time safely.

    Lark rejects changing an existing text field directly to Created Time with
    ``WrongRequestBody``. The migration therefore renames the disposable old
    field, creates and verifies a new system field, and only then removes the
    old field. Every intermediate state is resumable after a failed deploy.
    """
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    legacy_name = "Entry Date (legacy migration)"
    fields_path = "/open-apis/bitable/v1/apps/%s/tables/%s/fields" % (
        app_token, pipeline_table_id)

    listed = _list_fields(app_token, pipeline_table_id)
    if not listed.get("ok"):
        return listed
    spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                if item["key"] == "record_date")
    exact = next((item for item in listed["fields"]
                  if item.get("field_name") == pipeline_schema.ENTRY_DATE), None)
    legacy = next((item for item in listed["fields"]
                   if item.get("field_name") == legacy_name), None)

    # A previous attempt may have created the correct field and failed only
    # while deleting the renamed legacy field. Finish that cleanup safely.
    if exact and int(exact.get("type") or 0) == FT_CREATED_TIME:
        if legacy:
            deleted = _delete_retry(
                "%s/%s" % (fields_path, legacy.get("field_id")), tok)
            if deleted.get("code") != 0:
                return {
                    "ok": False,
                    "error": "Unable to remove migrated legacy Entry Date: %s" %
                             (deleted.get("msg") or deleted),
                    "raw": deleted,
                }
        return {"ok": True, "updated": bool(legacy),
                "field_id": exact.get("field_id"), "verified": True}

    if exact and legacy:
        return {
            "ok": False,
            "error": "Entry Date migration is ambiguous: editable and legacy fields both exist",
        }

    # Preserve the old column until a replacement exists. Renaming a text
    # field is supported; converting it to Created Time is not.
    old_field = exact or (_find_field(listed["fields"], spec) if not legacy else None)
    if old_field:
        old_type = int(old_field.get("type") or FT_TEXT)
        rename_body = {"field_name": legacy_name, "type": old_type}
        if old_field.get("property"):
            rename_body["property"] = old_field.get("property")
        renamed = _field_update(
            "%s/%s" % (fields_path, old_field.get("field_id")),
            tok,
            rename_body,
        )
        if renamed.get("code") != 0:
            return {
                "ok": False,
                "error": "Unable to preserve legacy Entry Date before replacement: %s" %
                         (renamed.get("msg") or renamed),
                "raw": renamed,
            }
        legacy = dict(old_field, field_name=legacy_name)

    create_body = {
        "field_name": pipeline_schema.ENTRY_DATE,
        "type": FT_CREATED_TIME,
        "property": {"date_formatter": "yyyy/MM/dd"},
    }
    created = _field_create(fields_path, tok, create_body)
    if created.get("code") != 0:
        return {
            "ok": False,
            "error": "Unable to create read-only system Entry Date: %s" %
                     (created.get("msg") or created),
            "raw": created,
        }

    verified = _list_fields(app_token, pipeline_table_id)
    if not verified.get("ok"):
        return verified
    actual = next((item for item in verified["fields"]
                   if item.get("field_name") == pipeline_schema.ENTRY_DATE), None)
    if not actual or int(actual.get("type") or 0) != FT_CREATED_TIME:
        return {
            "ok": False,
            "error": "Created Entry Date could not be verified as a read-only system field",
        }

    legacy = next((item for item in verified["fields"]
                   if item.get("field_name") == legacy_name), legacy)
    if legacy:
        deleted = _delete_retry(
            "%s/%s" % (fields_path, legacy.get("field_id")), tok)
        if deleted.get("code") != 0:
            return {
                "ok": False,
                "error": "System Entry Date is ready, but legacy cleanup failed: %s" %
                         (deleted.get("msg") or deleted),
                "raw": deleted,
            }

    final = _list_fields(app_token, pipeline_table_id)
    if not final.get("ok"):
        return final
    final_exact = next((item for item in final["fields"]
                        if item.get("field_name") == pipeline_schema.ENTRY_DATE), None)
    final_legacy = next((item for item in final["fields"]
                         if item.get("field_name") == legacy_name), None)
    if (not final_exact or int(final_exact.get("type") or 0) != FT_CREATED_TIME
            or final_legacy):
        return {"ok": False,
                "error": "Entry Date replacement did not reach a clean verified state"}
    return {"ok": True, "updated": True,
            "field_id": final_exact.get("field_id"), "verified": True}


def remove_legacy_lark_entry_date_field(app_token, pipeline_table_id):
    """Remove every old Entry Date variant from the Lark HR surface.

    Entry Date is authoritative in the service database and remains visible as
    a locked XLSX column. Lark must not expose an editable or ambiguously
    migrated copy. The operation is idempotent and also cleans a temporary
    field left by an interrupted v7 migration.
    """
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    listed = _list_fields(app_token, pipeline_table_id)
    if not listed.get("ok"):
        return listed
    spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                if item["key"] == "record_date")
    names = {spec["header"], *spec.get("aliases", ()),
             "Entry Date (legacy migration)"}
    targets = [item for item in listed["fields"]
               if str(item.get("field_name") or "") in names]
    removed = []
    for field in targets:
        response = _delete_retry(
            "/open-apis/bitable/v1/apps/%s/tables/%s/fields/%s" %
            (app_token, pipeline_table_id, field.get("field_id")),
            tok,
        )
        if response.get("code") != 0:
            return {
                "ok": False,
                "error": "Unable to remove legacy Entry Date field: %s" %
                         (response.get("msg") or response),
                "raw": response,
                "removed": removed,
            }
        removed.append(field.get("field_id"))
    verified = _list_fields(app_token, pipeline_table_id)
    if not verified.get("ok"):
        return verified
    remaining = [item for item in verified["fields"]
                 if str(item.get("field_name") or "") in names]
    if remaining:
        return {"ok": False,
                "error": "Legacy Entry Date field still exists after deletion",
                "removed": removed}
    return {"ok": True, "removed": removed}


def remove_legacy_lark_stage_date_field(app_token, pipeline_table_id):
    """Remove the misleading editable stage-date column from the HR surface.

    Lark's Modified Time field tracks every record edit; its public field API
    cannot scope the timestamp to ``Current Stage``.  The application already
    keeps the authoritative stage transition date in candidate_stage_event, so
    exposing an editable or semantically false Lark column is less safe than
    omitting it.
    """
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    listed = _list_fields(app_token, pipeline_table_id)
    if not listed.get("ok"):
        return listed
    date_spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                     if item["key"] == "stage_date")
    date_field = _find_field(listed["fields"], date_spec)
    if not date_field:
        return {"ok": True, "removed": False}
    response = _delete_retry(
        "/open-apis/bitable/v1/apps/%s/tables/%s/fields/%s" %
        (app_token, pipeline_table_id, date_field.get("field_id")),
        tok,
    )
    if response.get("code") != 0:
        return {
            "ok": False,
            "error": "Unable to remove legacy editable Stage Started On field: %s" %
                     (response.get("msg") or response),
            "raw": response,
        }
    verified = _list_fields(app_token, pipeline_table_id)
    if not verified.get("ok"):
        return verified
    if _find_field(verified["fields"], date_spec):
        return {"ok": False, "error": "Legacy Stage Started On field still exists after deletion"}
    return {"ok": True, "removed": True, "field_id": date_field.get("field_id")}


def delete_known_unsynced_test_rows(app_token, pipeline_table_id):
    """Delete only the exact disposable row identified for this migration.

    This is not a heuristic or a general clear-table operation.  It can never
    delete a row with a System ID.  The caller also gates it on an empty
    last-sync marker.
    """
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err, "removed": []}
    response = _req(
        "GET",
        "/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=500" %
        (app_token, pipeline_table_id),
        token=tok,
    )
    if response.get("code") != 0:
        return {
            "ok": False,
            "error": "Unable to inspect unsynchronised test rows: %s" %
                     (response.get("msg") or response),
            "raw": response,
            "removed": [],
        }
    removed = []
    candidate_spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                          if item["key"] == "name")
    channel_spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                        if item["key"] == "channel")
    detail_spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                       if item["key"] == "source_detail")
    system_id_spec = next(item for item in pipeline_schema.PIPELINE_COLUMNS
                          if item["key"] == "cand_id")

    def value_for(fields, spec):
        for name in (spec["header"], *spec.get("aliases", ())):
            if name in fields:
                return _flatten(fields.get(name))
        return ""

    for record in (response.get("data") or {}).get("items") or []:
        fields = record.get("fields") or {}
        fingerprint = (
            value_for(fields, candidate_spec),
            value_for(fields, channel_spec),
            value_for(fields, detail_spec),
            value_for(fields, system_id_spec),
        )
        if fingerprint != ("hhb", "Talent Discovery", "dvvzvz", ""):
            continue
        deleted = _delete_retry(
            "/open-apis/bitable/v1/apps/%s/tables/%s/records/%s" %
            (app_token, pipeline_table_id, record.get("record_id")),
            tok,
        )
        if deleted.get("code") != 0:
            return {
                "ok": False,
                "error": "Unable to delete the disposable test row: %s" %
                         (deleted.get("msg") or deleted),
                "raw": deleted,
                "removed": removed,
            }
        removed.append(record.get("record_id"))
    return {"ok": True, "removed": removed}


def normalize_table_field_names(app_token, table_id, specs, skip_keys=()):
    """Rename legacy labels to the canonical English contract without data loss."""
    tok, err = tenant_token()
    if err:
        return {"ok": False, "error": err}
    listed = _list_fields(app_token, table_id)
    if not listed.get("ok"):
        return listed
    fields = listed["fields"]
    updated, missing = [], []
    for spec in specs:
        if spec["key"] in set(skip_keys):
            continue
        field = _find_field(fields, spec)
        if not field:
            missing.append(spec["header"])
            continue
        if str(field.get("field_name") or "") == spec["header"]:
            continue
        body = {
            "field_name": spec["header"],
            "type": int(field.get("type") or FT_TEXT),
        }
        if field.get("property") is not None:
            body["property"] = field.get("property")
        if field.get("ui_type"):
            body["ui_type"] = field.get("ui_type")
        if spec["key"] == "source_detail":
            body["description"] = "Fill only when Source Channel is Other; otherwise leave blank."
        response = _field_update(
            "/open-apis/bitable/v1/apps/%s/tables/%s/fields/%s" %
            (app_token, table_id, field.get("field_id")),
            tok,
            body,
        )
        if response.get("code") != 0:
            return {"ok": False, "error": "统一字段名称失败（%s）：%s" %
                    (spec["header"], response.get("msg") or response), "raw": response}
        updated.append({"from": field.get("field_name"), "to": spec["header"]})
    if missing:
        return {"ok": False, "error": "缺少字段：%s" % ", ".join(missing),
                "updated": updated}
    return {"ok": True, "updated": updated}


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
    target = pipeline_schema.OTHER_SOURCE_DETAIL
    if any(str(item.get("field_name") or "") == target for item in items):
        return {"ok": True, "updated": False}
    aliases = {
        "Other Source (if Other)", "其他来源说明（选择 Other 时填写）",
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
        "description": "Fill only when Source Channel is Other; otherwise leave blank.",
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
    pipeline_names = normalize_table_field_names(
        app_token, pipeline_table_id, pipeline_schema.columns_for("lark"),
        skip_keys=("record_date", "stage_date"),
    )
    manual_names = normalize_table_field_names(
        app_token, manual_table_id, pipeline_schema.MANUAL_COLUMNS,
    )
    entry_date = remove_legacy_lark_entry_date_field(app_token, pipeline_table_id)
    stage_date = remove_legacy_lark_stage_date_field(app_token, pipeline_table_id)
    verification = _verify_system_date_fields(app_token, pipeline_table_id)
    cleanup = cleanup_empty_default_tables(
        app_token,
        protected_table_ids=(pipeline_table_id, manual_table_id),
    )
    return {
        "ok": bool(pipeline_names.get("ok") and manual_names.get("ok")
                   and entry_date.get("ok") and stage_date.get("ok")
                   and verification.get("ok") and cleanup.get("ok")),
        "pipeline_field_names": pipeline_names,
        "manual_field_names": manual_names,
        "entry_date": entry_date,
        "stage_date": stage_date,
        "verification": verification,
        "default_table_cleanup": cleanup,
    }


def verify_channel_base_schema(app_token, pipeline_table_id, manual_table_id):
    """Read current Lark metadata and prove the schema, without trusting a marker."""
    if not app_token or not pipeline_table_id or not manual_table_id:
        return {"ok": False, "error": "Channel Analytics Base configuration is incomplete"}
    pipeline = _list_fields(app_token, pipeline_table_id)
    manual = _list_fields(app_token, manual_table_id)
    if not pipeline.get("ok"):
        return pipeline
    if not manual.get("ok"):
        return manual
    pipeline_names = {str(item.get("field_name") or "") for item in pipeline["fields"]}
    manual_names = {str(item.get("field_name") or "") for item in manual["fields"]}
    missing_pipeline = [spec["header"] for spec in pipeline_schema.columns_for("lark")
                        if spec["header"] not in pipeline_names]
    missing_manual = [spec["header"] for spec in pipeline_schema.MANUAL_COLUMNS
                      if spec["header"] not in manual_names]
    dates = _verify_system_date_fields(app_token, pipeline_table_id)
    problems = []
    if missing_pipeline:
        problems.append("Missing Pipeline fields: %s" % ", ".join(missing_pipeline))
    if missing_manual:
        problems.append("Missing manual fields: %s" % ", ".join(missing_manual))
    problems.extend(dates.get("errors") or ([] if dates.get("ok") else [dates.get("error") or "Date verification failed"]))
    return {"ok": not problems, "errors": problems, "dates": dates}


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
        deleted = _delete_retry(
            "/open-apis/bitable/v1/apps/%s/tables/%s" % (app_token, table_id),
            tok,
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
    *tuple(
        name
        for spec in pipeline_schema.MANUAL_COLUMNS
        for name in (spec["header"], *spec.get("aliases", ()))
    ),
)
PIPELINE_FIELD_KEYS = pipeline_schema.field_names_with_aliases("lark")


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
    # Workflow dates are database-owned and absent from Lark. Only identity is
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
