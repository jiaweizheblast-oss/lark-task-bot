"""
任务终端机器人。
控制端是"私聊"：授权管理员私聊机器人派任务
  → 选群 → 选负责人 → 输入任务内容和截止 → 机器人到那个群里发卡片并 @ 负责人。
负责人在群里点【完成 / 无法完成 / 跳过】按钮反馈，机器人回写数据库并更新卡片。
每天定时扫描超期任务（见 overdue.py）。

默认 webhook 模式（国际版 Lark 支持）：机器人是个小网页服务，Lark 把事件推给它。
（后台若支持长连接，把环境变量 MODE 设为 ws 即可切换。）
运行：python bot.py
"""
import os
import json
import time
import secrets
import datetime
import threading
import requests
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest, CreateMessageRequestBody,
    PatchMessageRequest, PatchMessageRequestBody,
    GetChatMembersRequest, ListChatRequest,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger, P2CardActionTriggerResponse,
)
from flask import Flask, request, jsonify, Response
from lark_oapi.adapter.flask import parse_req, parse_resp

import db
import cards
from parse import extract_deadline, overdue_stage

# ---------------- 配置（从环境变量读）----------------
APP_ID = os.environ.get("APP_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")
LARK_DOMAIN = os.environ.get("LARK_DOMAIN", "https://open.larksuite.com")  # 国内飞书填 https://open.feishu.cn
ADMIN_SETUP_CODE = os.environ.get("ADMIN_SETUP_CODE", "")
BOT_NAME = os.environ.get("BOT_NAME", "")                 # 机器人名字，用来在群成员里排除它自己
ENCRYPT_KEY = os.environ.get("ENCRYPT_KEY", "")
VERIFICATION_TOKEN = os.environ.get("VERIFICATION_TOKEN", "")
MODE = os.environ.get("MODE", "webhook")
PORT = int(os.environ.get("PORT", "8080"))
PANEL_PASSWORD = os.environ.get("ADMIN_PANEL_PASSWORD", "")       # 网页控制台的登录密码
ADMIN_NOTIFY_OPEN_ID = os.environ.get("ADMIN_NOTIFY_OPEN_ID", "") # 网页派的任务，负责人反馈时通知谁（可选）
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")           # 可选：你的公开网址，用来生成外部汇报链接
TZ_LABEL = os.environ.get("TZ_LABEL", "").strip()                 # 可选：时区标注，如 "越南时间" / "GMT+7"
COMPANY_TZ_OFFSET = float(os.environ.get("COMPANY_TZ_OFFSET", "5.5") or 5.5)  # 公司参照时区偏移（默认印度 GMT+5:30，支持半小时）
WORK_START = int(os.environ.get("WORK_START", "10") or 10)        # 工作时间开始（每天这个点发提醒）
WORK_END = int(os.environ.get("WORK_END", "22") or 22)            # 工作时间结束（默认 22 点 / 晚10点）
ESCALATE_DAYS = int(os.environ.get("OVERDUE_ESCALATE_DAYS", "2") or 2)

client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).domain(LARK_DOMAIN).build()


# ---------------- 发送 / 更新消息 ----------------
def send_text(chat_id, text):
    body = CreateMessageRequestBody.builder().receive_id(chat_id) \
        .msg_type("text").content(json.dumps({"text": text})).build()
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_text] 失败 code={resp.code} msg={resp.msg}")
    return resp


def send_card(chat_id, card):
    body = CreateMessageRequestBody.builder().receive_id(chat_id) \
        .msg_type("interactive").content(json.dumps(card)).build()
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_card] 失败 code={resp.code} msg={resp.msg}")
        return None
    return resp.data.message_id


def patch_card(message_id, card):
    body = PatchMessageRequestBody.builder().content(json.dumps(card)).build()
    req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.patch(req)
    if not resp.success():
        print(f"[patch_card] 失败 code={resp.code} msg={resp.msg}")


def send_dm_to_user(open_id, text):
    """按 open_id 私聊某个用户（用来把负责人的反馈通知任务发布者）。"""
    body = CreateMessageRequestBody.builder().receive_id(open_id) \
        .msg_type("text").content(json.dumps({"text": text})).build()
    req = CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_dm_to_user] 失败 code={resp.code} msg={resp.msg}")


def notify_publisher(task, text):
    """把消息私聊发给任务的发布者（创建者）；创建者未知时退回到统一通知人。"""
    pub = task.get("created_by_open_id") or ADMIN_NOTIFY_OPEN_ID
    if pub:
        send_dm_to_user(pub, text)


def _log(task_id, body, side="system", name=None):
    """记一条任务留言（时间线）。失败不影响主流程。"""
    try:
        db.add_comment(task_id, body, author_side=side, author_name=name)
    except Exception as e:
        print(f"[log] 记录留言失败: {e}")


def _assignee_comment(task_id, body, name):
    """记一条负责人留言，并把任务标记为“有未读”（看板会显示红点）。"""
    _log(task_id, body, "assignee", name)
    try:
        db.update_task_fields(task_id, unread=True)
    except Exception as e:
        print(f"[unread] 标记失败: {e}")


def _mark_read(task_id):
    """发布者查看/回复后，清掉未读红点。"""
    try:
        db.update_task_fields(task_id, unread=False)
    except Exception as e:
        print(f"[unread] 清除失败: {e}")


# ---------------- 到期提醒（内置每日定时，只在工作时间发）----------------
def _public_base():
    return PUBLIC_BASE_URL.rstrip("/") if PUBLIC_BASE_URL else ""


def _company_now():
    """公司参照时区的当前时间（用于提醒的工作时间判断）。"""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=COMPANY_TZ_OFFSET)


def _remind_internal(t, stage):
    card = cards.reminder_card(stage, t["id"], t["title"], t["assignee_open_id"],
                               t["deadline"], owner_open_id=t.get("owner_open_id"))
    return bool(send_card(t["group_chat_id"], card))


def _remind_external(t, stage):
    eg = db.get_external_group(t["external_group_id"]) if t.get("external_group_id") else None
    if not eg:
        return False
    base = _public_base()
    url = f"{base}/t/{t['token']}" if (base and t.get("token")) else None
    return push_to_webhook(eg["webhook_url"], cards.external_reminder_card(stage, t, url))


def run_reminders():
    """扫描未完成任务，按档位发到期提醒；内部群走机器人、外部群走 webhook；每档只发一次。"""
    today = _company_now().date()
    tasks = db.tasks_still_open()
    sent = 0
    for t in tasks:
        stage = overdue_stage(t["deadline"], today, ESCALATE_DAYS, t.get("last_reminder_stage") or "")
        if not stage:
            continue
        ok = _remind_external(t, stage) if t.get("is_external") else _remind_internal(t, stage)
        if ok:
            db.set_reminder_stage(t["id"], stage)
            sent += 1
    print(f"[reminder] {today}（公司时区）扫描 {len(tasks)} 个未完成，发出 {sent} 条提醒")
    return sent


def _reminder_scheduler():
    """内置每日定时：每天在工作时间开始那一小时（公司时区）跑一次，避免非工作时间打扰。"""
    last_run_date = None
    print(f"[scheduler] 已启动：每天 {WORK_START}:00（GMT+{COMPANY_TZ_OFFSET}）发送每日提醒")
    while True:
        try:
            now = _company_now()
            if now.hour == WORK_START and now.date() != last_run_date:
                last_run_date = now.date()
                print(f"[scheduler] {now} 到达工作时间，发送每日提醒")
                run_reminders()
        except Exception as e:
            print(f"[scheduler] 出错: {e}")
        time.sleep(300)   # 每 5 分钟检查一次


def push_to_webhook(url, card):
    """通过外部群的自定义机器人 webhook 推送一张卡片。"""
    try:
        r = requests.post(url, json={"msg_type": "interactive", "card": card}, timeout=10)
        if not r.ok:
            print(f"[webhook] 推送失败 status={r.status_code} body={r.text[:200]}")
        return r.ok
    except Exception as e:
        print(f"[webhook] 推送异常: {e}")
        return False


# ---------------- 群 / 成员查询 ----------------
def list_bot_groups():
    """机器人所在的所有群。"""
    groups, page_token = [], None
    while True:
        b = ListChatRequest.builder().page_size(100)
        if page_token:
            b = b.page_token(page_token)
        resp = client.im.v1.chat.list(b.build())
        if not resp.success():
            print(f"[groups] 拉取群列表失败 code={resp.code} msg={resp.msg}")
            break
        for it in (resp.data.items or []):
            groups.append({"chat_id": it.chat_id, "name": it.name or "(未命名群)",
                           "external": bool(getattr(it, "external", False))})
        if resp.data.has_more and resp.data.page_token:
            page_token = resp.data.page_token
        else:
            break
    return groups


def list_group_members(chat_id):
    """某群的成员（排除机器人自己）。"""
    members, page_token = [], None
    while True:
        b = GetChatMembersRequest.builder().chat_id(chat_id).member_id_type("open_id").page_size(100)
        if page_token:
            b = b.page_token(page_token)
        resp = client.im.v1.chat_members.get(b.build())
        if not resp.success():
            print(f"[members] 拉取失败 code={resp.code} msg={resp.msg}")
            break
        for m in (resp.data.items or []):
            oid = getattr(m, "member_id", None)
            name = getattr(m, "name", None)
            if not oid:
                continue
            if BOT_NAME and name == BOT_NAME:      # 排除机器人自己
                continue
            members.append({"open_id": oid, "name": name or "(无名)"})
        if getattr(resp.data, "has_more", False) and getattr(resp.data, "page_token", None):
            page_token = resp.data.page_token
        else:
            break
    return members


def sync_chat_members(chat_id):
    """把群成员登记进 users 表（不认识的记为待确认）。"""
    for m in list_group_members(chat_id):
        if db.get_user(m["open_id"]):
            db.upsert_user(m["open_id"], display_name=m["name"])
        else:
            db.upsert_user(m["open_id"], display_name=m["name"], role="Unknown", status="pending")


# ---------------- 私聊终端命令 ----------------
def handle_task_wizard(sender_open_id, dm_chat_id, text, draft):
    """派任务逐步问答：标题 → 详情 → 注意事项 → 优先级+截止。"""
    ans = text.strip()
    skip = ans in ("无", "跳过", "没有", "none", "-")
    stage = draft.get("stage")

    if stage == "title":
        if not ans or skip:
            send_text(dm_chat_id, "任务标题不能为空，请输入 **任务标题**：")
            return
        db.update_draft(sender_open_id, title=ans, stage="detail")
        send_text(dm_chat_id, "好的。**详情 / 安排**？（具体怎么做、分几步、交付什么；没有就发「无」）")
        return

    if stage == "detail":
        db.update_draft(sender_open_id, detail=(None if skip else ans), stage="note")
        send_text(dm_chat_id, "**注意事项**？（要注意的点、验收标准、易踩的坑；没有就发「无」）")
        return

    if stage == "note":
        db.update_draft(sender_open_id, note=(None if skip else ans), stage="pridl")
        send_text(dm_chat_id, "最后，**优先级和截止日期**？例如「高 2026-07-25」（可只发其一，或发「无」）")
        return

    if stage == "pridl":
        priority = "中"
        for p in ("高", "中", "低"):
            if p in ans:
                priority = p
                break
        deadline = None if skip else extract_deadline(ans)
        task_id = db.create_task(draft.get("title") or "（无标题）", draft["assignee_open_id"], draft["chat_id"],
                                 deadline=deadline, created_by_open_id=sender_open_id,
                                 assignee_name=draft.get("assignee_name"),
                                 detail=draft.get("detail"), note=draft.get("note"), priority=priority)
        task = db.get_task(task_id)
        _log(task_id, f"任务已派发给 {task.get('assignee_name') or '负责人'}", "system")
        mid = send_card(draft["chat_id"], cards.new_task_card(task))
        if mid:
            db.set_task_card(task_id, mid)
        db.clear_draft(sender_open_id)
        send_card(dm_chat_id, cards.dispatched_card(draft["chat_name"], task))
        return

    db.clear_draft(sender_open_id)
    send_text(dm_chat_id, "发送 `新建任务` 重新开始。")


def handle_dm(sender_open_id, dm_chat_id, text):
    low = text.strip()

    if low.startswith("/help") or low == "帮助":
        send_text(dm_chat_id, cards.help_text())
        return

    if low.startswith("/whoami"):
        send_text(dm_chat_id, f"你的 open_id：{sender_open_id}\n当前身份：{db.get_role(sender_open_id)}")
        return

    if low.startswith("/claimadmin"):
        code = low.replace("/claimadmin", "").strip()
        if ADMIN_SETUP_CODE and code == ADMIN_SETUP_CODE:
            db.upsert_user(sender_open_id, role="Admin", kind="internal", status="bound")
            send_text(dm_chat_id, "✅ 已把你设为管理员。发送 `新建任务` 开始派任务。")
        else:
            send_text(dm_chat_id, "❌ 口令不对。")
        return

    # 开始派任务
    if low in ("新建任务", "/task", "派任务", "/新建任务", "新任务"):
        if not db.is_admin(sender_open_id):
            send_text(dm_chat_id, "❌ 只有管理员能派任务。请先发送 `/claimadmin 口令` 把自己设为管理员。")
            return
        groups = list_bot_groups()
        send_card(dm_chat_id, cards.group_select_card(groups))
        return

    # 逐步问答进行中 → 把这条文本当成对当前问题的回答
    draft = db.get_draft(sender_open_id)
    if draft and draft.get("stage"):
        handle_task_wizard(sender_open_id, dm_chat_id, text, draft)
        return

    send_text(dm_chat_id, "发送 `新建任务` 开始派任务，或 `/help` 看用法。")


# ---------------- 事件回调 ----------------
def on_message(data: P2ImMessageReceiveV1):
    try:
        msg = data.event.message
        if msg.message_type != "text":
            return
        sender_open_id = data.event.sender.sender_id.open_id
        text = (json.loads(msg.content or "{}").get("text") or "").strip()
        if msg.chat_type == "p2p":
            handle_dm(sender_open_id, msg.chat_id, text)
        else:
            # 群里被 @ 且像是想派任务 → 提示去私聊
            if text.startswith("/") or "新建任务" in text or "派任务" in text:
                send_text(msg.chat_id, "派任务请私聊我，发送 `新建任务` 即可～")
    except Exception as e:
        print(f"[on_message] 出错: {e}")


def on_bot_added(data):
    try:
        chat_id = data.event.chat_id
        name = getattr(data.event, "name", None)
        print(f"[event] 机器人被拉进群 {chat_id} ({name})")
        db.upsert_group(chat_id, name=name)
        sync_chat_members(chat_id)
        send_text(chat_id, "👋 任务机器人已就位。管理员请私聊我发送 `新建任务` 来派活；"
                           "首次请先私聊我发送 `/claimadmin 口令`。")
    except Exception as e:
        print(f"[on_bot_added] 出错: {e}")


def on_user_added(data):
    try:
        for u in (getattr(data.event, "users", []) or []):
            oid = getattr(getattr(u, "user_id", None), "open_id", None)
            name = getattr(u, "name", None)
            if oid and not db.get_user(oid):
                db.upsert_user(oid, display_name=name, role="Unknown", status="pending")
    except Exception as e:
        print(f"[on_user_added] 出错: {e}")


def card_resp(toast_type, content, card=None):
    """构造卡片按钮的回复：一个 toast 提示 + 可选地把卡片替换成新的（Lark 推荐的更新方式）。"""
    d = {"toast": {"type": toast_type, "content": content}}
    if card is not None:
        d["card"] = {"type": "raw", "data": card}
    return P2CardActionTriggerResponse(d)


def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    try:
        operator = data.event.operator.open_id
        value = data.event.action.value or {}
        action = value.get("action")

        # 派任务：选群 → 直接把卡片换成"选负责人"
        if action == "pick_group":
            if not db.is_admin(operator):
                return card_resp("error", "只有管理员能派任务")
            members = list_group_members(value.get("chat_id"))
            return card_resp("info", "请选择负责人",
                             cards.person_select_card(value.get("chat_id"), value.get("chat_name"), members))

        # 派任务：选负责人 → 开始"逐步问答"（第一步问标题）
        if action == "pick_person":
            if not db.is_admin(operator):
                return card_resp("error", "只有管理员能派任务")
            db.set_draft(operator, value.get("chat_id"), value.get("chat_name"),
                         value.get("open_id"), value.get("name"), stage="title")
            dm_chat = data.event.context.open_chat_id
            if dm_chat:
                send_text(dm_chat, f"开始给【{value.get('name')}】派任务。\n请先输入 **任务标题**：")
            return card_resp("success", "开始填写",
                             cards.picked_card(value.get("chat_name"), value.get("name")))

        # 任务生命周期：接受 / 完成 / 有问题 / 选择原因
        task_id = value.get("task_id")
        task = db.get_task(int(task_id)) if task_id else None
        if not task:
            return card_resp("error", "任务不存在")
        if operator != task["assignee_open_id"]:
            return card_resp("error", "只有该任务的负责人能操作")

        who = task.get("assignee_name") or "负责人"
        if action == "accept":
            db.update_task_status(task["id"], "accepted")
            _log(task["id"], "接受了任务", "assignee", who)
            return card_resp("success", "已接受 ✅", cards.accepted_card(task))

        if action == "done":
            db.update_task_status(task["id"], "done")
            _log(task["id"], "标记完成", "assignee", who)
            notify_publisher(task, f"✅ {who} 完成了任务 #{task['id']}【{task['title']}】")
            return card_resp("success", "已完成 🎉", cards.final_card(task, "done", operator))

        if action == "raise":
            return card_resp("info", "请选择原因", cards.reason_buttons_card(task))

        if action == "issue_reason":
            reason = value.get("reason") or "未说明"
            db.update_task_status(task["id"], "issue", result=reason)
            _assignee_comment(task["id"], reason, who)
            notify_publisher(task, f"⚠️ {who} 对任务 #{task['id']}【{task['title']}】反馈：\n"
                                   f"「{reason}」\n"
                                   f"请沟通处理（可在控制台该任务里回复，或用『新建任务』重新派发）。")
            return card_resp("success", "已提交给发布者 ✅",
                             cards.final_card(task, "issue", operator, reason=reason))

        return card_resp("error", "未知操作")
    except Exception as e:
        print(f"[on_card_action] 出错: {e}")
        return card_resp("error", "处理出错，请稍后再试")


# ---------------- 启动（webhook / ws） ----------------
def build_handler(encrypt_key="", verification_token=""):
    return (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_im_chat_member_bot_added_v1(on_bot_added)
        .register_p2_im_chat_member_user_added_v1(on_user_added)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )


app = Flask(__name__)
_webhook_handler = build_handler(ENCRYPT_KEY, VERIFICATION_TOKEN)


@app.route("/", methods=["GET"])
def health():
    return "ok"


@app.route("/webhook/event", methods=["POST"])
def webhook_event():
    return parse_resp(_webhook_handler.do(parse_req()))


@app.route("/webhook/card", methods=["POST"])
def webhook_card():
    return parse_resp(_webhook_handler.do(parse_req()))


# ---------------- 网页控制台（前端派任务 + 看板） ----------------
def _panel_auth():
    if not PANEL_PASSWORD:
        return False
    pw = request.headers.get("X-Auth", "") or request.args.get("pw", "")
    return pw == PANEL_PASSWORD


def _task_json(t):
    t = dict(t)
    for k in ("deadline", "created_at", "updated_at"):
        if t.get(k) is not None:
            t[k] = t[k].isoformat() if hasattr(t[k], "isoformat") else str(t[k])
    return t


@app.route("/panel")
def panel_page():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "panel.html"), encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"panel.html 未找到: {e}", 500


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json(silent=True) or {}
    if PANEL_PASSWORD and d.get("password") == PANEL_PASSWORD:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "密码不对，或后台未设置面板密码"}), 401


@app.route("/api/groups")
def api_groups():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(list_bot_groups())


@app.route("/api/members")
def api_members():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(list_group_members(request.args.get("chat_id", "")))


@app.route("/api/config")
def api_config():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"tz_label": TZ_LABEL, "tz_offset": COMPANY_TZ_OFFSET,
                    "work_start": WORK_START, "work_end": WORK_END})


@app.route("/api/tasks", methods=["GET"])
def api_tasks_list():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify([_task_json(t) for t in db.list_tasks()])


def _base_url():
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    u = request.host_url.rstrip("/")
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]      # Railway 对外是 https
    return u


@app.route("/api/tasks", methods=["POST"])
def api_tasks_create():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip()
    if not title:
        return jsonify({"error": "任务标题必填"}), 400
    pr = (d.get("priority") or "").strip()
    priority = pr if pr in ("高", "中", "低") else "中"
    deadline = extract_deadline(d.get("deadline") or "")
    detail = (d.get("detail") or "").strip() or None
    note = (d.get("note") or "").strip() or None
    eg_id = d.get("external_group_id")

    if eg_id:      # 外部群：走 webhook 推送
        eg = db.get_external_group(int(eg_id))
        if not eg:
            return jsonify({"error": "外部群不存在"}), 400
        token = secrets.token_urlsafe(9)
        task_id = db.create_task(title, None, None, deadline=deadline,
                                 created_by_open_id=(ADMIN_NOTIFY_OPEN_ID or None),
                                 assignee_name=(d.get("assignee_name") or "").strip() or None,
                                 detail=detail, note=note, priority=priority,
                                 token=token, is_external=True, external_group_id=int(eg_id))
        task = db.get_task(task_id)
        _log(task_id, f"任务已派发到外部群，负责人：{task.get('assignee_name') or '（见群内）'}", "system")
        ok = push_to_webhook(eg["webhook_url"], cards.external_task_card(task, f"{_base_url()}/t/{token}"))
        return jsonify({"ok": True, "task_id": task_id, "pushed": ok})

    # 内部群：机器人卡片
    chat_id = d.get("chat_id")
    assignee = d.get("assignee_open_id")
    if not chat_id or not assignee:
        return jsonify({"error": "请选择群和负责人"}), 400
    task_id = db.create_task(title, assignee, chat_id, deadline=deadline,
                             created_by_open_id=(ADMIN_NOTIFY_OPEN_ID or None),
                             assignee_name=d.get("assignee_name"), detail=detail, note=note, priority=priority)
    task = db.get_task(task_id)
    _log(task_id, f"任务已派发给 {task.get('assignee_name') or '负责人'}", "system")
    mid = send_card(chat_id, cards.new_task_card(task))
    if mid:
        db.set_task_card(task_id, mid)
    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/external-groups", methods=["GET"])
def api_ext_list():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify([{"id": g["id"], "name": g["name"]} for g in db.list_external_groups()])


@app.route("/api/external-groups", methods=["POST"])
def api_ext_add():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    url = (d.get("webhook_url") or "").strip()
    if not name or not url:
        return jsonify({"error": "名字和 webhook 网址都要填"}), 400
    if not url.startswith("http"):
        return jsonify({"error": "webhook 网址格式不对"}), 400
    return jsonify({"ok": True, "id": db.add_external_group(name, url)})


@app.route("/api/external-groups/<int:eg_id>", methods=["DELETE"])
def api_ext_del(eg_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    db.delete_external_group(eg_id)
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_task_delete(task_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    db.delete_task(task_id)
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def api_task_patch(task_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    d = request.get_json(silent=True) or {}
    fields = {}
    if "deadline" in d:
        fields["deadline"] = extract_deadline(d.get("deadline") or "")
    if "priority" in d and (d.get("priority") or "").strip() in ("高", "中", "低"):
        fields["priority"] = d["priority"].strip()
    if d.get("status") in ("pending", "accepted", "done", "issue"):
        fields["status"] = d["status"]
    reassigned = bool(d.get("assignee_open_id"))
    if reassigned:
        fields["assignee_open_id"] = d["assignee_open_id"]
        fields["assignee_name"] = d.get("assignee_name")
        fields["status"] = "pending"      # 换人后重置为“待接受”
    if fields:
        db.update_task_fields(task_id, **fields)
        # 记录到时间线（发布者在控制台的改动）
        if reassigned:
            _log(task_id, f"发布者改派给 {d.get('assignee_name') or '新负责人'}", "system")
        elif "status" in fields:
            _log(task_id, f"发布者把状态改为「{_ST_LABEL.get(fields['status'], fields['status'])}」", "system")
        if "deadline" in fields and not reassigned:
            _log(task_id, f"发布者把截止日期改为 {fields['deadline'] or '未设置'}", "system")
    task = db.get_task(task_id)
    if reassigned:                        # 换人后向群里重发一张新卡片
        mid = send_card(task["group_chat_id"], cards.new_task_card(task))
        if mid:
            db.update_task_fields(task_id, card_message_id=mid)
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>/nudge", methods=["POST"])
def api_task_nudge(task_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    send_card(task["group_chat_id"], cards.nudge_card(task))
    _log(task_id, "发布者在群里催办了一次", "system")
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>/comments", methods=["GET"])
def api_comments_list(task_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    _mark_read(task_id)      # 发布者打开看了 → 清红点
    out = []
    for c in db.list_comments(task_id):
        c = dict(c)
        if c.get("created_at") is not None:
            c["created_at"] = c["created_at"].isoformat() if hasattr(c["created_at"], "isoformat") else str(c["created_at"])
        out.append(c)
    return jsonify(out)


@app.route("/api/tasks/<int:task_id>/comments", methods=["POST"])
def api_comments_add(task_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    d = request.get_json(silent=True) or {}
    body = (d.get("body") or "").strip()
    if not body:
        return jsonify({"error": "留言不能为空"}), 400
    db.add_comment(task_id, body, author_side="publisher", author_name="发布者")
    _mark_read(task_id)      # 发布者回复了 → 清红点
    # 内部任务：私聊通知负责人；外部任务：负责人下次打开链接就能看到
    if not task.get("is_external") and task.get("assignee_open_id"):
        send_dm_to_user(task["assignee_open_id"],
                        f"💬 发布者在任务 #{task_id}【{task['title']}】留言：\n「{body}」")
    return jsonify({"ok": True})


# ---------------- 日历订阅（iCal）：把任务截止日期同步进个人日历 ----------------
def _ics_esc(s):
    return (str(s or "")).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@app.route("/calendar.ics")
def calendar_ics():
    if not _panel_auth():          # 用 ?pw=面板密码 订阅
        return Response("unauthorized", status=401)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LarkTaskBot//CN//",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:任务截止", "X-WR-TIMEZONE:UTC"]
    for t in db.list_tasks():
        d = t.get("deadline")
        if not d or t.get("status") == "done":
            continue
        ymd = d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d).replace("-", "")
        nxt = d + __import__("datetime").timedelta(days=1) if hasattr(d, "strftime") else d
        ymd2 = nxt.strftime("%Y%m%d") if hasattr(nxt, "strftime") else ymd
        summ = f"[{_ST_LABEL.get(t.get('status'), '')}] {t.get('title', '')}"
        desc = f"负责人：{t.get('assignee_name') or ''}　优先级：{t.get('priority') or ''}"
        lines += ["BEGIN:VEVENT", f"UID:task-{t['id']}@larktaskbot",
                  f"DTSTART;VALUE=DATE:{ymd}", f"DTEND;VALUE=DATE:{ymd2}",
                  f"SUMMARY:{_ics_esc(summ)}", f"DESCRIPTION:{_ics_esc(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", mimetype="text/calendar")


# ---------------- 外部人汇报状态的公开页面 ----------------
_STATUS_CSS = """
*{box-sizing:border-box} body{margin:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,
"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;color:#1c2024;padding:20px;line-height:1.55}
.card{max-width:440px;margin:24px auto;background:#fff;border:1px solid #e7e9ec;border-radius:16px;
box-shadow:0 4px 20px rgba(20,25,35,.06);padding:22px 22px 26px}
.h{font-size:13px;color:#5b5bd6;font-weight:700;letter-spacing:.06em}
.t{font-size:19px;font-weight:750;margin:6px 0 6px}
.meta{color:#5b636b;font-size:13px;margin-bottom:12px}
p{font-size:14px;margin:6px 0} b{color:#1c2024}
.b{display:block;width:100%;border:0;border-radius:10px;padding:13px;font-size:15px;font-weight:650;
cursor:pointer;margin-top:12px;color:#fff}
.b.done{background:#16a34a} .b.issue{background:#c2740a}
.or{text-align:center;color:#9aa2ab;font-size:12px;margin:16px 0 2px}
textarea{width:100%;min-height:72px;border:1px solid #d7dade;border-radius:10px;padding:10px;font-size:14px;
font-family:inherit;resize:vertical;outline:none;margin-top:6px}
.badge{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:3px 11px;margin:2px 0 10px}
.b-pending{background:#eceafc;color:#5b5bd6} .b-accepted{background:#d9f2ee;color:#0d9488}
.b-done{background:#e8f6ec;color:#16a34a} .b-issue{background:#fdf1e3;color:#c2740a}
.warn{background:#fff8ec;border:1px solid #f5e2bf;color:#946200;font-size:12.5px;border-radius:10px;
padding:9px 11px;margin:10px 0 4px}
.flash{background:#e8f6ec;border:1px solid #bfe6c9;color:#137a37;font-size:13px;border-radius:10px;
padding:10px 12px;margin:12px 0 2px;font-weight:600}
.okmsg{background:#e8f6ec;color:#137a37;text-align:center;font-size:15px;font-weight:700;border-radius:10px;
padding:14px;margin-top:6px}
.rsnbox{background:#fdf1e3;border:1px solid #f0d9b8;color:#8a5a12;font-size:13px;border-radius:10px;
padding:10px 12px;margin-top:6px}
.b.talk{background:#5b5bd6} .msgbtns{display:flex;gap:8px;margin-top:8px} .msgbtns .b{margin-top:0}
.thread{margin:6px 0 2px;display:flex;flex-direction:column;gap:8px}
.cmt{border-radius:10px;padding:8px 11px;font-size:13px;max-width:88%}
.cmt .cw{font-size:11px;color:#8a949e;margin-bottom:2px}
.cmt.pub{background:#eef1ff;align-self:flex-start;border:1px solid #e0e5fb}
.cmt.asg{background:#e8f6ec;align-self:flex-end;border:1px solid #cfebd5}
.cmt.sys{background:#f2f3f5;align-self:center;color:#6b7480;font-size:12px;text-align:center;max-width:100%}
.sec{font-size:12.5px;color:#5b636b;font-weight:700;margin:16px 0 6px}
.hint{font-size:12px;color:#8a949e;margin-top:7px;text-align:center;line-height:1.5}
.dllocal{color:#5b5bd6;font-weight:600}
"""


def _h(s):
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_ST_LABEL = {"pending": "🆕 待接受", "accepted": "⏳ 进行中", "done": "✅ 已完成", "issue": "🙋 待沟通"}


def _msg_box(hint):
    """一个输入框 + 一个清晰的“发送给发布者”按钮（不再有两个模糊按钮）。"""
    h = f'<div class="hint">{hint}</div>' if hint else ""
    return ('<div class="or">有问题、想延期、或想说明进度？直接发给发布者：</div>'
            '<textarea name="msg" placeholder="写下你想对发布者说的话…"></textarea>'
            '<button class="b talk" name="action" value="message">💬 发送给发布者</button>' + h)


def _status_actions(task):
    """按当前状态给一个主按钮 + 一个发送留言按钮，作用一目了然。"""
    st = task.get("status", "pending")
    tip = "发送后这条任务会标记为「待沟通」，发布者会尽快回复你。"
    if st == "pending":
        return '<button class="b done" name="action" value="accept">✅ 接受任务</button>' + _msg_box(tip)
    if st == "accepted":
        return '<button class="b done" name="action" value="done">✅ 标记完成</button>' + _msg_box(tip)
    if st == "done":
        return '<div class="okmsg">🎉 已标记完成，谢谢！</div>' + _msg_box("发布者会收到你的补充留言。")
    # issue / 待沟通：正在和发布者沟通中
    return ('<button class="b done" name="action" value="done">✅ 问题已解决，标记完成</button>'
            + _msg_box("发布者会看到你的留言并回复。"))


def _fmt_time(ts):
    try:
        return ts.strftime("%m-%d %H:%M")
    except Exception:
        return str(ts or "")


_SIDE_LABEL = {"publisher": "发布者", "assignee": "负责人", "system": ""}


def _thread_html(comments):
    if not comments:
        return ""
    rows = []
    for c in comments:
        side = c.get("author_side", "system")
        cls = {"publisher": "pub", "assignee": "asg", "system": "sys"}.get(side, "sys")
        name = c.get("author_name") or _SIDE_LABEL.get(side, "")
        head = f"{_h(name)} · {_h(_fmt_time(c.get('created_at')))}" if side != "system" else _h(_fmt_time(c.get("created_at")))
        rows.append(f'<div class="cmt {cls}"><div class="cw">{head}</div><div>{_h(c.get("body",""))}</div></div>')
    return '<div class="sec">💬 沟通记录</div><div class="thread">' + "".join(rows) + "</div>"


def _status_html(task, flash=None, comments=None):
    st = task.get("status", "pending")
    bits = []
    if task.get("priority"):
        bits.append(f"优先级 {_h(task['priority'])}")
    tz_script = ""
    if task.get("deadline"):
        bits.append(f"截止 {_h(cards.fmt_deadline(task['deadline']))}<span id='dlLocal' class='dllocal'></span>")
        tz_script = (
            "<script>(function(){var O=" + str(COMPANY_TZ_OFFSET) + ",WE=" + str(WORK_END) +
            ",el=document.getElementById('dlLocal');if(!el)return;"
            "var p='" + str(task['deadline']) + "'.split('-').map(Number);"
            "var inst=Date.UTC(p[0],p[1]-1,p[2],WE,0,0)-O*3600000,dt=new Date(inst);"
            "var vo=-new Date().getTimezoneOffset()/60;if(vo===O)return;"
            "var loc=dt.toLocaleString([],{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});"
            "el.textContent=' · 你当地约 '+loc;})();</script>"
        )
    if task.get("assignee_name"):
        bits.append(f"负责人 {_h(task['assignee_name'])}")
    detail = f"<p><b>📝 详情 / 安排：</b>{_h(task['detail'])}</p>" if task.get("detail") else ""
    note = f"<p><b>⚠️ 注意事项：</b>{_h(task['note'])}</p>" if task.get("note") else ""
    who = _h(task.get("assignee_name") or "负责人")
    badge = f'<span class="badge b-{st}">当前状态：{_ST_LABEL.get(st, st)}</span>'
    flash_html = f'<div class="flash">{_h(flash)}</div>' if flash else ""
    thread = _thread_html(comments or [])
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>任务汇报</title>
<style>{_STATUS_CSS}</style></head><body><div class="card">
<div class="h">📋 任务汇报</div>
<div class="t">{_h(task.get('title',''))}</div>
<div class="meta">{'　·　'.join(bits)}</div>
{badge}
{detail}{note}
<div class="warn">⚠️ 请仅由负责人 <b>{who}</b> 操作，其他群成员请勿点击，以免弄乱状态。</div>
{thread}
{flash_html}
<form method="post">{_status_actions(task)}</form>
</div>{tz_script}</body></html>"""


@app.route("/t/<token>", methods=["GET"])
def status_page(token):
    task = db.get_task_by_token(token)
    if not task:
        return "<h3 style='font-family:sans-serif;text-align:center;margin-top:40px'>链接无效或任务已删除</h3>", 404
    return _status_html(task, comments=db.list_comments(task["id"]))


@app.route("/t/<token>", methods=["POST"])
def status_submit(token):
    task = db.get_task_by_token(token)
    if not task:
        return "<h3 style='font-family:sans-serif;text-align:center;margin-top:40px'>链接无效</h3>", 404
    action = request.form.get("action")
    msg = (request.form.get("msg") or "").strip()
    who = task.get("assignee_name") or "负责人"
    tid = task["id"]
    flash = None
    if action == "accept":
        db.update_task_status(tid, "accepted")
        _log(tid, "接受了任务", "assignee", who)
        flash = "已接受 ✅ 请在截止前完成，快到期时我们会在群里提醒你。"
    elif action == "done":
        db.update_task_status(tid, "done")
        _log(tid, "标记完成" + (f"：{msg}" if msg else ""), "assignee", who)
        notify_publisher(task, f"✅ {who} 完成了任务 #{tid}【{task['title']}】")
        flash = "已记录：完成，谢谢！🎉"
    elif action == "message":
        if not msg:
            flash = "请先写点内容再发送哦。"
        else:
            _assignee_comment(tid, msg, who)
            notify_publisher(task, f"💬 {who} 对任务 #{tid}【{task['title']}】反馈：\n「{msg}」")
            if task.get("status") in ("pending", "accepted"):
                db.update_task_status(tid, "issue")
                flash = "已发送给发布者 ✅ 这条任务已标为「待沟通」，他会尽快回复你。"
            else:
                flash = "已发送给发布者 ✅ 他会尽快回复你。"
    task = db.get_task_by_token(token)      # 重新读取，拿到最新状态再渲染
    return _status_html(task, flash=flash, comments=db.list_comments(tid))


def main():
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("缺少 APP_ID / APP_SECRET 环境变量")
    db.init_db()
    if MODE == "ws":
        print("[bot] 长连接(ws)模式启动 ...")
        cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=build_handler(),
                             domain=LARK_DOMAIN, log_level=lark.LogLevel.INFO)
        cli.start()
    else:
        threading.Thread(target=_reminder_scheduler, daemon=True).start()   # 内置每日提醒
        from waitress import serve
        print(f"[bot] webhook 模式启动，监听端口 {PORT}，等待 Lark 事件推送 ...")
        serve(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
