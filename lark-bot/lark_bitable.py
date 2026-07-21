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

DOMAIN = os.environ.get("LARK_DOMAIN", "https://open.larksuite.com").rstrip("/")
APP_ID = os.environ.get("APP_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")

# 字段类型（Bitable）：1=多行文本 3=单选
FT_TEXT = 1
FT_SINGLE = 3

_token_cache = {"v": "", "exp": 0.0}


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
        return None, "缺 APP_ID / APP_SECRET（应在 Railway 环境变量里）"
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
