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
import hmac
import datetime
import threading
import requests
import psycopg2
from urllib.parse import quote
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
import channel_report
import sheet_io
import attend
import lark_bitable
import channel_sheet_service
import talent_integration
import talent_search_queue
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
NEXUS_INTEGRATION_SIGNING_KEY = os.environ.get("NEXUS_INTEGRATION_SIGNING_KEY", "")
NEXUS_TALENT_WORKER_TOKEN = os.environ.get("NEXUS_TALENT_WORKER_TOKEN", "")

client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).domain(LARK_DOMAIN).build()


# ---------------- 发送 / 更新消息 ----------------
def send_text(chat_id, text):
    body = CreateMessageRequestBody.builder().receive_id(chat_id) \
        .msg_type("text").content(json.dumps({"text": text})).build()
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_text] failed code={resp.code} msg={resp.msg}")
    return resp


def send_card(chat_id, card):
    body = CreateMessageRequestBody.builder().receive_id(chat_id) \
        .msg_type("interactive").content(json.dumps(card)).build()
    req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_card] failed code={resp.code} msg={resp.msg}")
        return None
    return resp.data.message_id


def patch_card(message_id, card):
    body = PatchMessageRequestBody.builder().content(json.dumps(card)).build()
    req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.patch(req)
    if not resp.success():
        print(f"[patch_card] failed code={resp.code} msg={resp.msg}")


def send_dm_to_user(open_id, text):
    """按 open_id 私聊某个用户（用来把负责人的反馈通知任务发布者）。"""
    body = CreateMessageRequestBody.builder().receive_id(open_id) \
        .msg_type("text").content(json.dumps({"text": text})).build()
    req = CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_dm_to_user] failed code={resp.code} msg={resp.msg}")


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
        print(f"[log] failed to record comment: {e}")


def _assignee_comment(task_id, body, name):
    """记一条负责人留言，并把任务标记为“有未读留言”（= 待沟通；不改变真实状态）。"""
    _log(task_id, body, "assignee", name)
    try:
        db.update_task_fields(task_id, unread=True, result=body)   # 存最新留言供看板预览；状态不动
    except Exception as e:
        print(f"[unread] failed to set: {e}")


def _mark_read(task_id):
    """发布者查看/回复后，清掉未读红点。"""
    try:
        db.update_task_fields(task_id, unread=False)
    except Exception as e:
        print(f"[unread] failed to clear: {e}")


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


def _reminder_settings():
    """读取提醒设置（带默认值）：默认开启、三档全开、跳过周末、无节假日。"""
    try:
        s = db.get_settings()
    except Exception:
        s = {}

    def on(k, default_true):
        return s.get(k, "1" if default_true else "0") == "1"

    raw = (s.get("rm_holidays", "") or "").replace("\n", ",").replace("，", ",")
    holidays = [x.strip() for x in raw.split(",") if x.strip()]
    return {
        "enabled": on("rm_enabled", True),
        "tier_before": on("rm_before", True),   # 截止前一天
        "tier_today": on("rm_today", True),      # 当天到期
        "tier_over": on("rm_over", True),        # 已超期
        "skip_weekends": on("rm_skip_weekends", True),
        "holidays": holidays,
    }


def run_reminders():
    """扫描未完成任务，按设置发到期提醒；内部群走机器人、外部群走 webhook；每档只发一次。"""
    cfg = _reminder_settings()
    now = _company_now()
    today = now.date()
    if not cfg["enabled"]:
        print("[reminder] automatic reminders are off, skipping")
        return 0
    if cfg["skip_weekends"] and now.weekday() >= 5:      # 5=周六 6=周日
        print(f"[reminder] {today} weekend, skipping")
        return 0
    if today.isoformat() in cfg["holidays"]:
        print(f"[reminder] {today} holiday, skipping")
        return 0
    tier_on = {"due_tomorrow": cfg["tier_before"], "due_today": cfg["tier_today"], "escalated": cfg["tier_over"]}
    tasks = db.tasks_still_open()
    sent = 0
    for t in tasks:
        stage = overdue_stage(t["deadline"], today, ESCALATE_DAYS, t.get("last_reminder_stage") or "")
        if not stage or not tier_on.get(stage, True):
            continue
        ok = _remind_external(t, stage) if t.get("is_external") else _remind_internal(t, stage)
        if ok:
            db.set_reminder_stage(t["id"], stage)
            sent += 1
    print(f"[reminder] {today} (company tz) scanned {len(tasks)} open tasks, sent {sent} reminders")
    return sent


def _reminder_scheduler():
    """内置每日定时：每天在工作时间开始那一小时（公司时区）跑一次，避免非工作时间打扰。"""
    last_run_date = None
    print(f"[scheduler] started: daily reminders at {WORK_START}:00 (GMT+{COMPANY_TZ_OFFSET})")
    while True:
        try:
            now = _company_now()
            if now.hour == WORK_START and now.date() != last_run_date:
                last_run_date = now.date()
                print(f"[scheduler] {now} working hours reached, sending daily reminders")
                run_reminders()
        except Exception as e:
            print(f"[scheduler] error: {e}")
        time.sleep(300)   # 每 5 分钟检查一次


def push_to_webhook(url, card):
    """通过外部群的自定义机器人 webhook 推送一张卡片。"""
    try:
        r = requests.post(url, json={"msg_type": "interactive", "card": card}, timeout=10)
        if not r.ok:
            print(f"[webhook] push failed status={r.status_code} body={r.text[:200]}")
        return r.ok
    except Exception as e:
        print(f"[webhook] push error: {e}")
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
            print(f"[groups] failed to fetch groups code={resp.code} msg={resp.msg}")
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
            print(f"[members] failed to fetch code={resp.code} msg={resp.msg}")
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
    skip = ans.lower() in ("none", "skip", "no", "n/a", "-") or ans in ("无", "跳过", "没有")
    stage = draft.get("stage")

    if stage == "title":
        if not ans or skip:
            send_text(dm_chat_id, "The title can't be empty. Please enter the **task title**:")
            return
        db.update_draft(sender_open_id, title=ans, stage="detail")
        send_text(dm_chat_id, "Got it. **Details**? (how to do it, the steps, deliverables — send 'none' to skip)")
        return

    if stage == "detail":
        db.update_draft(sender_open_id, detail=(None if skip else ans), stage="note")
        send_text(dm_chat_id, "**Notes**? (things to watch, acceptance criteria — send 'none' to skip)")
        return

    if stage == "note":
        db.update_draft(sender_open_id, note=(None if skip else ans), stage="pridl")
        send_text(dm_chat_id, "Last: **priority and due date**? e.g. 'High 2026-07-25' (either one alone is fine, or send 'none')")
        return

    if stage == "pridl":
        priority = "Medium"
        low_ans = ans.lower()
        for kw, pv in (("high", "High"), ("medium", "Medium"), ("low", "Low"),
                       ("高", "High"), ("中", "Medium"), ("低", "Low")):
            if kw in low_ans or kw in ans:
                priority = pv
                break
        deadline = None if skip else extract_deadline(ans)
        task_id = db.create_task(draft.get("title") or "(Untitled)", draft["assignee_open_id"], draft["chat_id"],
                                 deadline=deadline, created_by_open_id=sender_open_id,
                                 assignee_name=draft.get("assignee_name"),
                                 detail=draft.get("detail"), note=draft.get("note"), priority=priority)
        task = db.get_task(task_id)
        _log(task_id, f"Task assigned to {task.get('assignee_name') or '负责人'}", "system")
        mid = send_card(draft["chat_id"], cards.new_task_card(task))
        if mid:
            db.set_task_card(task_id, mid)
        db.clear_draft(sender_open_id)
        send_card(dm_chat_id, cards.dispatched_card(draft["chat_name"], task))
        return

    db.clear_draft(sender_open_id)
    send_text(dm_chat_id, "Send `new task` to start over.")


def handle_dm(sender_open_id, dm_chat_id, text):
    low = text.strip()

    if low.casefold() in {
        "/channel_sheet", "/channel_download", "获取渠道表", "下载渠道表",
    }:
        if not db.is_admin(sender_open_id):
            send_text(dm_chat_id, "❌ 只有管理员可以获取渠道运营表。")
            return
        cfg = _lark_cfg()
        panel_url = (_public_base() + "/panel#recruit") if _public_base() else ""
        send_card(
            dm_chat_id,
            cards.channel_sheet_card(
                url=cfg.get("url") or "",
                panel_url=panel_url,
                configured=bool(cfg.get("app_token") and cfg.get("pipeline_table_id")
                                and cfg.get("manual_table_id")
                                and cfg.get("schema_version") == "channel-analytics-v2"),
                last_sync=cfg.get("last_sync") or "",
            ),
        )
        return

    if low.casefold() in {
        "/submit_channel_sheet", "/channel_upload", "提交渠道表", "同步渠道表",
    }:
        if not db.is_admin(sender_open_id):
            send_text(dm_chat_id, "❌ 只有管理员可以提交渠道运营表。")
            return
        cfg = _lark_cfg()
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        result = channel_sheet_service.sync_lark_table(
            db,
            lark_bitable,
            cfg,
            jobs=db.list_job_requests(only_open=False),
            channels=channel_report.CHANNELS,
            default_date=_kolkata_today().isoformat(),
            synced_at=now_utc.isoformat(),
        )
        send_card(dm_chat_id, cards.channel_sync_result_card(result))
        return

    if low.startswith("/help") or low == "帮助":
        send_text(dm_chat_id, cards.help_text())
        return

    if low.startswith("/whoami"):
        send_text(dm_chat_id, f"Your open_id: {sender_open_id}\nRole: {db.get_role(sender_open_id)}")
        return

    if low.startswith("/claimadmin"):
        code = low.replace("/claimadmin", "").strip()
        if ADMIN_SETUP_CODE and code == ADMIN_SETUP_CODE:
            db.upsert_user(sender_open_id, role="Admin", kind="internal", status="bound")
            send_text(dm_chat_id, "✅ You're now an admin. Send `new task` to start assigning.")
        else:
            send_text(dm_chat_id, "❌ Incorrect code.")
        return

    # start assigning a task
    if low.lower() in ("new task", "/task", "/new", "assign", "newtask") or low in ("新建任务", "派任务", "新任务"):
        if not db.is_admin(sender_open_id):
            send_text(dm_chat_id, "❌ Only admins can assign tasks. Send `/claimadmin <code>` to become an admin first.")
            return
        groups = list_bot_groups()
        send_card(dm_chat_id, cards.group_select_card(groups))
        return

    # 逐步问答进行中 → 把这条文本当成对当前问题的回答
    draft = db.get_draft(sender_open_id)
    if draft and draft.get("stage"):
        handle_task_wizard(sender_open_id, dm_chat_id, text, draft)
        return

    send_text(dm_chat_id, "Send `new task` to start, or `/help` for usage.")


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
            # @-ed in a group and looks like a task command → point them to DM
            tl = text.lower()
            if text.startswith("/") or "new task" in tl or "assign" in tl or "新建任务" in text or "派任务" in text:
                send_text(msg.chat_id, "To assign a task, message me directly and send `new task`.")
    except Exception as e:
        print(f"[on_message] error: {e}")


def on_bot_added(data):
    try:
        chat_id = data.event.chat_id
        name = getattr(data.event, "name", None)
        print(f"[event] bot added to group {chat_id} ({name})")
        db.upsert_group(chat_id, name=name)
        sync_chat_members(chat_id)
        send_text(chat_id, "👋 Task bot is ready. Admins: message me and send `new task` to assign. "
                           "First time: send me `/claimadmin <code>`.")
    except Exception as e:
        print(f"[on_bot_added] error: {e}")


def on_user_added(data):
    try:
        for u in (getattr(data.event, "users", []) or []):
            oid = getattr(getattr(u, "user_id", None), "open_id", None)
            name = getattr(u, "name", None)
            if oid and not db.get_user(oid):
                db.upsert_user(oid, display_name=name, role="Unknown", status="pending")
    except Exception as e:
        print(f"[on_user_added] error: {e}")


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
                return card_resp("error", "Only admins can assign tasks")
            members = list_group_members(value.get("chat_id"))
            return card_resp("info", "Select an assignee",
                             cards.person_select_card(value.get("chat_id"), value.get("chat_name"), members))

        # 派任务：选负责人 → 开始"逐步问答"（第一步问标题）
        if action == "pick_person":
            if not db.is_admin(operator):
                return card_resp("error", "Only admins can assign tasks")
            db.set_draft(operator, value.get("chat_id"), value.get("chat_name"),
                         value.get("open_id"), value.get("name"), stage="title")
            dm_chat = data.event.context.open_chat_id
            if dm_chat:
                send_text(dm_chat, f"Assigning a task to {value.get('name')}.\nPlease enter the **task title**:")
            return card_resp("success", "Let's begin",
                             cards.picked_card(value.get("chat_name"), value.get("name")))

        # 任务生命周期：接受 / 完成 / 有问题 / 选择原因
        task_id = value.get("task_id")
        task = db.get_task(int(task_id)) if task_id else None
        if not task:
            return card_resp("error", "Task not found")
        if operator != task["assignee_open_id"]:
            return card_resp("error", "Only the task's assignee can do this")

        who = task.get("assignee_name") or "Assignee"
        if action == "accept":
            db.update_task_status(task["id"], "accepted")
            _log(task["id"], "Accepted the task", "assignee", who)
            return card_resp("success", "Accepted ✅", cards.accepted_card(task))

        if action == "done":
            db.update_task_status(task["id"], "done")
            _log(task["id"], "Marked complete", "assignee", who)
            notify_publisher(task, f"✅ {who} completed task #{task['id']} '{task['title']}'")
            return card_resp("success", "Completed 🎉", cards.final_card(task, "done", operator))

        if action == "raise":
            return card_resp("info", "Select a reason", cards.reason_buttons_card(task))

        if action == "issue_reason":
            reason = value.get("reason") or "Not specified"
            _assignee_comment(task["id"], reason, who)   # 记留言 + 标未读（发布者看板→待沟通），状态不变
            notify_publisher(task, f"⚠️ {who} reported an issue on task #{task['id']} '{task['title']}':\n"
                                   f"'{reason}'\n"
                                   f"Reply to them in the console to discuss.")
            # 状态保持不变，卡片刷回对应状态，负责人仍可接受/完成
            back = cards.new_task_card(task) if task.get("status") == "pending" else cards.accepted_card(task)
            return card_resp("success", "Reported to the sender ✅", back)

        return card_resp("error", "Unknown action")
    except Exception as e:
        print(f"[on_card_action] error: {e}")
        return card_resp("error", "Something went wrong, please try again")


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


def _talent_worker_auth():
    if len(NEXUS_TALENT_WORKER_TOKEN) < 32:
        return False
    value = request.headers.get("Authorization", "")
    expected = f"Bearer {NEXUS_TALENT_WORKER_TOKEN}"
    return hmac.compare_digest(value, expected)


def _search_task_json(row, *, include_payload=True, include_result=True):
    value = {
        "task_id": str(row["task_id"]),
        "schema_version": row["schema_version"],
        "task_type": row["task_type"],
        "revision": row["revision"],
        "status": row["status"],
        "core_job_ref": row["core_job_ref"],
        "payload_sha256": row["payload_sha256"],
        "result_sha256": row.get("result_sha256"),
        "worker_id": row.get("worker_id"),
        "attempt_count": row.get("attempt_count", 0),
        "last_error_code": row.get("last_error_code"),
    }
    for field in (
        "created_at", "updated_at", "expires_at", "claimed_at",
        "lease_expires_at",
    ):
        current = row.get(field)
        value[field] = current.isoformat() if current is not None else None
    if include_payload:
        value["payload"] = row.get("payload")
    if include_result:
        value["result"] = row.get("result")
    return value


def _talent_snapshot_json(row):
    if not row:
        return {"status": "empty", "snapshot": None}
    return {
        "status": "ready",
        "snapshot": {
            "schema_version": row["schema_version"],
            "source_system": row["source_system"],
            "generated_at": row["generated_at"].isoformat(),
            "received_at": row["received_at"].isoformat(),
            "content_sha256": row["content_sha256"],
            "content": row["content"],
        },
    }


@app.route("/api/integration/v1/talent/snapshot", methods=["POST"])
def api_talent_snapshot_ingest():
    """Accept one signed, manager-only mirror; never mutate candidate truth."""
    raw = request.get_data(cache=True)
    if len(raw) > talent_integration.MAX_BODY_BYTES:
        return jsonify({"error": "snapshot_too_large"}), 413
    try:
        payload = json.loads(raw.decode("utf-8"))
        verified = talent_integration.verify_snapshot(
            payload, NEXUS_INTEGRATION_SIGNING_KEY
        )
        stored = db.store_talent_snapshot(verified)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({"error": "invalid_json"}), 400
    except ValueError as exc:
        code = "stale_snapshot" if "older than" in str(exc) else "snapshot_rejected"
        return jsonify({"error": code}), 409 if code == "stale_snapshot" else 422
    except Exception as exc:
        print("[talent_snapshot] unavailable:", type(exc).__name__)
        return jsonify({"error": "snapshot_unavailable"}), 503
    status_code = 201 if stored["accepted"] else 200
    return jsonify({
        "status": "stored" if stored["accepted"] else "unchanged",
        "accepted": stored["accepted"],
        "idempotent": stored["idempotent"],
        "content_sha256": verified["content_sha256"],
        "generated_at": verified["generated_at"],
    }), status_code


@app.route("/api/talent/snapshot", methods=["GET"])
def api_talent_snapshot_get():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(_talent_snapshot_json(db.get_latest_talent_snapshot()))


@app.route("/api/talent/search-tasks", methods=["POST"])
def api_talent_search_task_create():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        task = talent_search_queue.build_task(request.get_json(silent=True) or {})
        job = db.get_job_request_by_core_ref(task["core_job_ref"])
        if not job:
            return jsonify({"error": "unknown_core_job_ref"}), 422
        if job["status"] != "open":
            return jsonify({"error": "job_not_active"}), 409
        row, inserted = db.enqueue_talent_search_task(task)
    except ValueError as exc:
        return jsonify({"error": "invalid_search_task", "detail": str(exc)}), 422
    except Exception as exc:
        print("[talent_search_task] unavailable:", type(exc).__name__)
        return jsonify({"error": "search_queue_unavailable"}), 503
    return jsonify({
        "status": "queued" if inserted else "unchanged",
        "idempotent": not inserted,
        "task": _search_task_json(row),
    }), 201 if inserted else 200


@app.route("/api/talent/search-tasks", methods=["GET"])
def api_talent_search_task_list():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "tasks": [
            _search_task_json(row)
            for row in db.list_talent_search_tasks(limit=50)
        ]
    })


@app.route("/api/integration/v1/talent/search-tasks/claim", methods=["POST"])
def api_talent_search_task_claim():
    if not _talent_worker_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        body = request.get_json(silent=True) or {}
        if set(body) != {"worker_id", "lease_seconds"}:
            raise ValueError("invalid claim fields")
        worker_id = talent_search_queue.valid_worker_id(body["worker_id"])
        lease_seconds = talent_search_queue.valid_lease_seconds(body["lease_seconds"])
        claimed = db.claim_talent_search_task(worker_id, lease_seconds)
    except ValueError as exc:
        return jsonify({"error": "invalid_claim", "detail": str(exc)}), 422
    except Exception as exc:
        print("[talent_search_claim] unavailable:", type(exc).__name__)
        return jsonify({"error": "search_queue_unavailable"}), 503
    if not claimed:
        return jsonify({"task": None}), 200
    row, lease_token = claimed
    return jsonify({"task": row["payload"], "lease_token": lease_token})


def _worker_lease_body():
    body = request.get_json(silent=True) or {}
    worker_id = talent_search_queue.valid_worker_id(body.get("worker_id"))
    lease_token = str(body.get("lease_token") or "")
    if len(lease_token) < 32 or len(lease_token) > 200:
        raise ValueError("lease_token is invalid")
    return body, worker_id, lease_token


@app.route("/api/integration/v1/talent/search-tasks/<task_id>/heartbeat", methods=["POST"])
def api_talent_search_task_heartbeat(task_id):
    if not _talent_worker_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        body, worker_id, lease_token = _worker_lease_body()
        if set(body) != {"worker_id", "lease_token", "lease_seconds"}:
            raise ValueError("invalid heartbeat fields")
        lease_seconds = talent_search_queue.valid_lease_seconds(body["lease_seconds"])
        ok = db.heartbeat_talent_search_task(
            task_id, worker_id, lease_token, lease_seconds
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_heartbeat", "detail": str(exc)}), 422
    if not ok:
        return jsonify({"error": "lease_conflict"}), 409
    return jsonify({"status": "extended"})


@app.route("/api/integration/v1/talent/search-tasks/<task_id>/complete", methods=["POST"])
def api_talent_search_task_complete(task_id):
    if not _talent_worker_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        body, worker_id, lease_token = _worker_lease_body()
        if set(body) != {"worker_id", "lease_token", "result"}:
            raise ValueError("invalid completion fields")
        row = db.get_talent_search_task(task_id)
        if not row:
            return jsonify({"error": "task_not_found"}), 404
        result = talent_search_queue.validate_result(
            body["result"],
            task_id=str(row["task_id"]),
            core_job_ref=row["core_job_ref"],
        )
        completed = db.complete_talent_search_task(
            task_id, worker_id, lease_token, result,
            talent_search_queue.sha256(result),
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_result", "detail": str(exc)}), 422
    except Exception as exc:
        print("[talent_search_complete] unavailable:", type(exc).__name__)
        return jsonify({"error": "search_queue_unavailable"}), 503
    if not completed:
        return jsonify({"error": "lease_conflict"}), 409
    return jsonify(completed)


@app.route("/api/integration/v1/talent/search-tasks/<task_id>/fail", methods=["POST"])
def api_talent_search_task_fail(task_id):
    if not _talent_worker_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        body, worker_id, lease_token = _worker_lease_body()
        if set(body) != {"worker_id", "lease_token", "error_code"}:
            raise ValueError("invalid failure fields")
        error_code = talent_search_queue.valid_error_code(body["error_code"])
        ok = db.fail_talent_search_task(
            task_id, worker_id, lease_token, error_code
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_failure", "detail": str(exc)}), 422
    if not ok:
        return jsonify({"error": "lease_conflict"}), 409
    return jsonify({"status": "failed"})


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
        return f"panel.html not found: {e}", 500


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json(silent=True) or {}
    if PANEL_PASSWORD and d.get("password") == PANEL_PASSWORD:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Incorrect password, or the console password isn't set on the server"}), 401


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


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(_reminder_settings())


@app.route("/api/settings", methods=["POST"])
def api_settings_set():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    mapping = {"enabled": "rm_enabled", "tier_before": "rm_before", "tier_today": "rm_today",
               "tier_over": "rm_over", "skip_weekends": "rm_skip_weekends"}
    for jk, dk in mapping.items():
        if jk in d:
            db.set_setting(dk, "1" if d[jk] else "0")
    if "holidays" in d:
        hol = d["holidays"]
        if isinstance(hol, list):
            hol = ",".join(str(x).strip() for x in hol if str(x).strip())
        db.set_setting("rm_holidays", hol or "")
    return jsonify({"ok": True, "settings": _reminder_settings()})


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
        return jsonify({"error": "Title is required"}), 400
    pr = (d.get("priority") or "").strip()
    priority = pr if pr in ("High", "Medium", "Low") else "Medium"
    deadline = extract_deadline(d.get("deadline") or "")
    detail = (d.get("detail") or "").strip() or None
    note = (d.get("note") or "").strip() or None
    eg_id = d.get("external_group_id")

    if eg_id:      # 外部群：走 webhook 推送
        eg = db.get_external_group(int(eg_id))
        if not eg:
            return jsonify({"error": "External group not found"}), 400
        token = secrets.token_urlsafe(9)
        task_id = db.create_task(title, None, None, deadline=deadline,
                                 created_by_open_id=(ADMIN_NOTIFY_OPEN_ID or None),
                                 assignee_name=(d.get("assignee_name") or "").strip() or None,
                                 detail=detail, note=note, priority=priority,
                                 token=token, is_external=True, external_group_id=int(eg_id))
        task = db.get_task(task_id)
        _log(task_id, f"Task assigned to the external group — assignee: {task.get('assignee_name') or '(see group)'}", "system")
        ok = push_to_webhook(eg["webhook_url"], cards.external_task_card(task, f"{_base_url()}/t/{token}"))
        return jsonify({"ok": True, "task_id": task_id, "pushed": ok})

    # 内部群：机器人卡片
    chat_id = d.get("chat_id")
    assignee = d.get("assignee_open_id")
    if not chat_id or not assignee:
        return jsonify({"error": "Please select a group and an assignee"}), 400
    task_id = db.create_task(title, assignee, chat_id, deadline=deadline,
                             created_by_open_id=(ADMIN_NOTIFY_OPEN_ID or None),
                             assignee_name=d.get("assignee_name"), detail=detail, note=note, priority=priority)
    task = db.get_task(task_id)
    _log(task_id, f"Task assigned to {task.get('assignee_name') or '负责人'}", "system")
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
        return jsonify({"error": "Both a name and a webhook URL are required"}), 400
    if not url.startswith("http"):
        return jsonify({"error": "Invalid webhook URL"}), 400
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
        return jsonify({"error": "Task not found"}), 404
    d = request.get_json(silent=True) or {}
    fields = {}
    if "deadline" in d:
        fields["deadline"] = extract_deadline(d.get("deadline") or "")
    if "priority" in d and (d.get("priority") or "").strip() in ("High", "Medium", "Low"):
        fields["priority"] = d["priority"].strip()
    if d.get("status") in ("pending", "accepted", "done"):     # 真实状态只有这三个
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
            _log(task_id, f"Sender reassigned to {d.get('assignee_name') or 'the new assignee'}", "system")
        elif "status" in fields:
            _log(task_id, f"Sender changed status to '{_ST_LABEL.get(fields['status'], fields['status'])}」", "system")
        if "deadline" in fields and not reassigned:
            _log(task_id, f"Sender changed the due date to {fields['deadline'] or 'Not set'}", "system")
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
        return jsonify({"error": "Task not found"}), 404
    send_card(task["group_chat_id"], cards.nudge_card(task))
    _log(task_id, "Sender nudged the assignee in the group", "system")
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>/comments", methods=["GET"])
def api_comments_list(task_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    # 注意：只“看”不清除待沟通；要发布者回复了才算处理完（见下方 POST）
    out = []
    for c in db.list_comments(task_id):
        c = dict(c)
        if c.get("created_at") is not None:
            c["created_at"] = _iso_utc(c["created_at"])
        out.append(c)
    return jsonify(out)


@app.route("/api/tasks/<int:task_id>/comments", methods=["POST"])
def api_comments_add(task_id):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    d = request.get_json(silent=True) or {}
    body = (d.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Message can't be empty"}), 400
    db.add_comment(task_id, body, author_side="publisher", author_name="Sender")
    _mark_read(task_id)      # 发布者回复了 → 清“待沟通”（读了并回复才算处理完）
    # 内部任务：私聊通知负责人；外部任务：负责人下次打开链接就能看到
    if not task.get("is_external") and task.get("assignee_open_id"):
        send_dm_to_user(task["assignee_open_id"],
                        f"💬 The sender messaged on task #{task_id} '{task['title']}':\n'{body}'")
    return jsonify({"ok": True})


# ---------------- 招聘渠道日报模块（人工渠道汇总 manual_unidentified 空间） ----------------
# 权威业务时区 = Asia/Kolkata（UTC+05:30，无 DST）；report_date / 日周边界按它算。
_KOLKATA_OFFSET = datetime.timedelta(hours=5, minutes=30)


def _kolkata_today():
    return (datetime.datetime.utcnow() + _KOLKATA_OFFSET).date()


def _channel_roster():
    """受控填报人名单（settings 存，逗号/换行分隔）。自由文本姓名不作 owner/授权依据。"""
    try:
        s = db.get_settings()
    except Exception:
        s = {}
    raw = (s.get("channel_roster", "") or "").replace("，", ",").replace("\n", ",")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _valid_date(s):
    try:
        datetime.date.fromisoformat((s or "")[:10])
        return True
    except ValueError:
        return False


def _channel_go_live():
    """上线日（ISO）：日历三态上色的锚点。settings 里设了就用；否则取最早有数据那天；再没有就用今天。"""
    try:
        s = db.get_settings()
    except Exception:
        s = {}
    v = (s.get("channel_go_live", "") or "").strip()
    if v:
        try:
            return datetime.date.fromisoformat(v[:10]).isoformat()
        except ValueError:
            pass
    try:
        e = db.earliest_candidate_date()
    except Exception:
        e = None
    return e.isoformat() if e else _kolkata_today().isoformat()


def _chan_json(r):
    r = dict(r)
    for k in ("record_date", "created_at", "updated_at"):
        if r.get(k) is not None:
            r[k] = r[k].isoformat() if hasattr(r[k], "isoformat") else str(r[k])
    return r


def _cand_json(c):
    c = dict(c)
    for k in ("apply_date", "stage_date", "effective_date", "created_at", "updated_at"):
        if c.get(k) is not None:
            c[k] = c[k].isoformat() if hasattr(c[k], "isoformat") else str(c[k])
    return c


@app.route("/api/channel/meta")
def api_channel_meta():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    jobs = db.list_job_requests(only_open=True)
    return jsonify({
        "channels": channel_report.CHANNELS,
        "jobs": [{"id": j["id"], "title": j["title"], "target_headcount": j["target_headcount"]} for j in jobs],
        "roster": _channel_roster(),
        "today": _kolkata_today().isoformat(),
        "timezone": "Asia/Kolkata",
        "go_live": _channel_go_live(),
        "statuses": channel_report.PIPELINE_STATUS,
        "data_days": sorted(set(db.candidate_data_days()) | set(db.channel_data_days())),
    })


@app.route("/api/channel/golive", methods=["POST"])
def api_channel_golive():
    """设置上线日（日历三态锚点）。留空=清除，回落到最早有数据那天。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    v = (d.get("date") or "").strip()
    if v:
        try:
            v = datetime.date.fromisoformat(v[:10]).isoformat()
        except ValueError:
            return jsonify({"error": "日期格式非法（应为 YYYY-MM-DD）"}), 400
    db.set_setting("channel_go_live", v)
    return jsonify({"ok": True, "go_live": _channel_go_live()})


@app.route("/api/channel/roster", methods=["GET"])
def api_channel_roster_get():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(_channel_roster())


@app.route("/api/channel/roster", methods=["POST"])
def api_channel_roster_set():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    names = d.get("names")
    if isinstance(names, list):
        val = "\n".join(str(x).strip() for x in names if str(x).strip())
    else:
        val = (d.get("value") or "").strip()
    db.set_setting("channel_roster", val)
    return jsonify({"ok": True, "roster": _channel_roster()})


@app.route("/api/candidates", methods=["GET"])
def api_candidates():
    """候选人列表（每行一个候选人）。?d=YYYY-MM-DD 只看当天进入的。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify([_cand_json(c) for c in db.list_candidates(request.args.get("d"))])


@app.route("/api/candidates", methods=["POST"])
def api_candidate_create():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    errors, warnings = channel_report.validate_candidate(d)
    if errors:
        return jsonify({"error": "；".join(errors), "errors": errors}), 422
    jid = d.get("job_request_id")
    jid = int(jid) if str(jid or "").strip().isdigit() else None
    if jid is not None and not db.get_job_request(jid):
        return jsonify({"error": "职位不存在（job_request_id 非法）", "errors": ["职位不存在"]}), 422
    rd = (d.get("apply_date") or _kolkata_today().isoformat())[:10]
    if not _valid_date(rd):
        return jsonify({"error": "日期格式非法（应为 YYYY-MM-DD）", "errors": ["日期非法"]}), 422
    stage_date = (d.get("stage_date") or rd)[:10]
    if not _valid_date(stage_date):
        return jsonify({"error": "阶段日期格式非法（应为 YYYY-MM-DD）", "errors": ["阶段日期非法"]}), 422
    cid = db.create_candidate(
        rd, (d.get("name") or "").strip(), d["channel"], jid,
        "New Lead", (d.get("note") or "").strip(),
        (d.get("filled_by") or "").strip(), d.get("source") or "手动",
        (d.get("ext_ref") or "").strip(), "", (d.get("source_detail") or "").strip())
    requested_stage = d.get("status") or "New Lead"
    if requested_stage != "New Lead":
        db.transition_candidate_stage(
            cid, requested_stage, stage_date, (d.get("filled_by") or "").strip(),
            (d.get("rejection_reason") or "").strip(), (d.get("note") or "").strip(),
            (d.get("event_ref") or "").strip())
    return jsonify({"ok": True, "id": cid, "warnings": warnings})


@app.route("/api/candidates/<int:cid>", methods=["PATCH", "PUT"])
def api_candidate_update(cid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    existing = db.get_candidate(cid)
    if not existing:
        return jsonify({"error": "候选人不存在"}), 404
    d = request.get_json(silent=True) or {}
    errors, warnings = channel_report.validate_candidate(d)
    if errors:
        return jsonify({"error": "；".join(errors), "errors": errors}), 422
    jid = d.get("job_request_id")
    jid = int(jid) if str(jid or "").strip().isdigit() else None
    if jid is not None and not db.get_job_request(jid):
        return jsonify({"error": "职位不存在（job_request_id 非法）", "errors": ["职位不存在"]}), 422
    fields = dict(
        name=(d.get("name") or "").strip(), channel=d["channel"], job_request_id=jid,
        source_detail=(d.get("source_detail") or "").strip(), note=(d.get("note") or "").strip(),
        filled_by=(d.get("filled_by") or "").strip())
    if d.get("source"):
        fields["source"] = d["source"]
    if d.get("ext_ref") is not None:
        fields["ext_ref"] = (d.get("ext_ref") or "").strip()
    if d.get("apply_date"):
        if not _valid_date(d["apply_date"]):
            return jsonify({"error": "日期格式非法（应为 YYYY-MM-DD）", "errors": ["日期非法"]}), 422
        fields["apply_date"] = d["apply_date"][:10]
    stage_date = (d.get("stage_date") or d.get("apply_date") or _kolkata_today().isoformat())[:10]
    if not _valid_date(stage_date):
        return jsonify({"error": "阶段日期格式非法（应为 YYYY-MM-DD）", "errors": ["阶段日期非法"]}), 422
    db.update_candidate(cid, **fields)
    transition = None
    requested_stage = d.get("status") or "New Lead"
    if requested_stage != (existing.get("status") or "New Lead"):
        transition = db.transition_candidate_stage(
            cid, requested_stage,
            stage_date,
            (d.get("filled_by") or "").strip(), (d.get("rejection_reason") or "").strip(),
            (d.get("note") or "").strip(), (d.get("event_ref") or "").strip())
    return jsonify({"ok": True, "warnings": warnings, "transition": transition})


@app.route("/api/candidates/<int:cid>/stage-events")
def api_candidate_stage_events(cid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    if not db.get_candidate(cid):
        return jsonify({"error": "候选人不存在"}), 404
    return jsonify([_cand_json(row) for row in db.list_candidate_stage_events(cid)])


@app.route("/api/candidates/<int:cid>", methods=["DELETE"])
def api_candidate_delete(cid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    db.delete_candidate(cid)
    return jsonify({"ok": True})


def _job_json(j):
    j = dict(j)
    if j.get("created_at") is not None and hasattr(j["created_at"], "isoformat"):
        j["created_at"] = j["created_at"].isoformat()
    return j


@app.route("/api/channel/jobs", methods=["GET"])
def api_channel_list_jobs():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify([_job_json(j) for j in db.list_job_requests(only_open=False)])


@app.route("/api/channel/jobs", methods=["POST"])
def api_channel_add_job():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip()
    if not title:
        return jsonify({"error": "职位名必填"}), 400
    jid = db.create_job_request(title, int(d.get("target_headcount") or 0),
                                int(d.get("target_resume_count") or 0), (d.get("owner") or "").strip())
    return jsonify({"ok": True, "id": jid})


@app.route("/api/channel/jobs/<int:jid>", methods=["PATCH"])
def api_channel_update_job(jid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    existing = db.get_job_request(jid)
    if not existing:
        return jsonify({"error": "职位不存在"}), 404
    d = request.get_json(silent=True) or {}
    if existing.get("core_job_ref") and any(
        key in d for key in ("title", "status")
    ):
        return jsonify({
            "error": "核心职位的名称和状态由 Talent Discovery 同步，不能在 Nexus 修改"
        }), 409
    fields = {}
    if "title" in d:
        t = (d.get("title") or "").strip()
        if not t:
            return jsonify({"error": "职位名不能为空"}), 400
        fields["title"] = t
    if "owner" in d:
        fields["owner"] = (d.get("owner") or "").strip()
    if "target_headcount" in d:
        fields["target_headcount"] = int(d.get("target_headcount") or 0)
    if "target_resume_count" in d:
        fields["target_resume_count"] = int(d.get("target_resume_count") or 0)
    if "status" in d and d.get("status") in ("open", "closed"):
        fields["status"] = d["status"]
    db.update_job_request(jid, **fields)
    return jsonify({"ok": True})


# ---------------- 文档 / 模板库 ----------------
@app.route("/api/templates", methods=["GET"])
def api_templates_list():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(db.list_templates())


@app.route("/api/templates", methods=["POST"])
def api_templates_create():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip()
    if not title:
        return jsonify({"error": "标题必填"}), 400
    tid = db.create_template(title, (d.get("category") or "其他").strip(), d.get("content") or "")
    return jsonify({"ok": True, "id": tid})


@app.route("/api/templates/<int:tid>", methods=["PATCH"])
def api_templates_update(tid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    fields = {k: d[k] for k in ("title", "category", "content") if k in d}
    db.update_template(tid, **fields)
    return jsonify({"ok": True})


@app.route("/api/templates/<int:tid>", methods=["DELETE"])
def api_templates_delete(tid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": db.delete_template(tid)})


# ---------------- 渠道成本 ----------------
@app.route("/api/channel/cost", methods=["GET"])
def api_channel_cost_get():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(db.list_channel_costs())


@app.route("/api/channel/cost", methods=["POST"])
def api_channel_cost_set():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    items = d.get("items") if isinstance(d.get("items"), list) else [d]
    for it in items:
        ch = (it.get("channel") or "").strip()
        ym = (it.get("ym") or "").strip()
        if not ch or len(ym) != 7 or ym[4] != "-":
            continue
        try:
            db.upsert_channel_cost(ch, ym, float(it.get("amount") or 0))
        except (ValueError, TypeError):
            continue
    return jsonify({"ok": True, "costs": db.list_channel_costs()})


def _build_channel_report(day=None, wfrom=None, wto=None):
    target = datetime.date.fromisoformat(day[:10]) if day else _kolkata_today()
    wf = datetime.date.fromisoformat(wfrom[:10]) if wfrom else None
    wt = datetime.date.fromisoformat(wto[:10]) if wto else None
    rows = db.channel_rows_upto(target)
    jobs = db.list_job_requests(only_open=False)
    return channel_report.build_report(rows, target, jobs, window_from=wf, window_to=wt)


@app.route("/api/channel/report")
def api_channel_report():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        rep = _build_channel_report(request.args.get("d"), request.args.get("from"), request.args.get("to"))
    except ValueError:
        return jsonify({"error": "日期参数格式非法（应为 YYYY-MM-DD）"}), 400
    return jsonify(rep)


@app.route("/api/channel/report/push", methods=["POST"])
def api_channel_report_push():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    try:
        rep = _build_channel_report(d.get("d"), d.get("from"), d.get("to"))
    except ValueError:
        return jsonify({"error": "日期参数格式非法（应为 YYYY-MM-DD）"}), 400
    pushed = False
    if d.get("external_group_id"):
        eg = db.get_external_group(int(d["external_group_id"]))
        if eg:
            card = {"config": {"wide_screen_mode": True},
                    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": rep["text"]}}]}
            pushed = push_to_webhook(eg["webhook_url"], card)
    elif d.get("chat_id"):
        resp = send_text(d["chat_id"], rep["text"])
        pushed = bool(resp and resp.success())
    return jsonify({"ok": True, "pushed": pushed, "report": rep})


def _parse_date_or(s, default):
    try:
        return datetime.date.fromisoformat((s or "")[:10])
    except ValueError:
        return default


# 默认窗口跨度（天）：按粒度给合理的历史长度
_ANALYTICS_SPAN = {"day": 30, "week": 84, "month": 365, "year": 365 * 3}


@app.route("/api/channel/analytics")
def api_channel_analytics():
    """Channel Analytics, with manual and identity-derived spaces kept separate."""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    g = request.args.get("g", "day")
    if g not in ("day", "week", "month", "year"):
        g = "day"
    today = _kolkata_today()
    dto = _parse_date_or(request.args.get("to"), today)
    dfrom = _parse_date_or(request.args.get("from"), dto - datetime.timedelta(days=_ANALYTICS_SPAN[g] - 1))
    if dfrom > dto:
        dfrom, dto = dto, dfrom
    jid = request.args.get("job_id", "")
    job_id = int(jid) if jid.isdigit() else None
    span = (dto - dfrom).days + 1
    prev_from = dfrom - datetime.timedelta(days=span)          # 上一周期（同长度、紧邻在前）
    space = request.args.get("space", "manual")
    if space == "derived":
        cands = db.list_candidates_range(prev_from.isoformat(), dto.isoformat())
        rows = channel_report.candidates_to_daily(cands)
    else:
        space = "manual"
        rows = db.list_channel_records_range(prev_from.isoformat(), dto.isoformat())
    jobs = db.list_job_requests(only_open=False)
    costs = db.list_channel_costs()
    result = channel_report.analytics(rows, jobs, g, dfrom, dto, job_id, prev_from, costs)
    result["data_space"] = "identity_derived" if space == "derived" else "manual_unidentified"
    return jsonify(result)


@app.route("/api/channel/export.xlsx")
def api_channel_export():
    """把当前看板导出成 xlsx（概览+渠道明细+趋势+职位进度），方便汇报。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    g = request.args.get("g", "day")
    if g not in ("day", "week", "month", "year"):
        g = "day"
    today = _kolkata_today()
    dto = _parse_date_or(request.args.get("to"), today)
    dfrom = _parse_date_or(request.args.get("from"), dto - datetime.timedelta(days=_ANALYTICS_SPAN[g] - 1))
    if dfrom > dto:
        dfrom, dto = dto, dfrom
    jid = request.args.get("job_id", "")
    job_id = int(jid) if jid.isdigit() else None
    span = (dto - dfrom).days + 1
    prev_from = dfrom - datetime.timedelta(days=span)
    space = request.args.get("space", "manual")
    if space == "derived":
        cands = db.list_candidates_range(prev_from.isoformat(), dto.isoformat())
        rows = channel_report.candidates_to_daily(cands)
    else:
        rows = db.list_channel_records_range(prev_from.isoformat(), dto.isoformat())
    jobs = db.list_job_requests(only_open=False)
    costs = db.list_channel_costs()
    a = channel_report.analytics(rows, jobs, g, dfrom, dto, job_id, prev_from, costs)
    a["data_space"] = "identity_derived" if space == "derived" else "manual_unidentified"
    data = sheet_io.build_analytics_xlsx(a)
    fname = "招聘分析_%s_%s.xlsx" % (a["window"]["from"], a["window"]["to"])
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(fname)},
    )


# ==================== 考勤打卡 ====================
def _client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else (request.remote_addr or "")) or ""


@app.route("/api/att/sites", methods=["GET"])
def api_att_sites():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(db.list_attendance_sites())


@app.route("/api/att/sites", methods=["POST"])
def api_att_site_add():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    try:
        lat, lng = float(d.get("lat")), float(d.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "经纬度必填且为数字"}), 400
    if not name:
        return jsonify({"error": "点位名必填"}), 400
    sid = db.create_attendance_site(name, lat, lng, int(d.get("radius_m") or 200), bool(d.get("require_selfie")))
    return jsonify({"ok": True, "id": sid})


@app.route("/api/att/sites/<int:sid>", methods=["PATCH"])
def api_att_site_update(sid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    fields = {}
    if "name" in d:
        fields["name"] = (d.get("name") or "").strip()
    for k in ("lat", "lng"):
        if k in d:
            try:
                fields[k] = float(d[k])
            except (TypeError, ValueError):
                return jsonify({"error": "经纬度非法"}), 400
    if "radius_m" in d:
        fields["radius_m"] = int(d.get("radius_m") or 200)
    if "require_selfie" in d:
        fields["require_selfie"] = bool(d["require_selfie"])
    db.update_attendance_site(sid, **fields)
    return jsonify({"ok": True})


@app.route("/api/att/sites/<int:sid>", methods=["DELETE"])
def api_att_site_del(sid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": db.delete_attendance_site(sid)})


@app.route("/api/att/persons", methods=["GET"])
def api_att_persons():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(db.list_attendance_persons())


@app.route("/api/att/persons", methods=["POST"])
def api_att_person_add():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "姓名必填"}), 400
    kind = d.get("kind") if d.get("kind") in ("internal", "external") else "external"
    sid = d.get("site_id")
    sid = int(sid) if sid else None
    token = secrets.token_urlsafe(12)
    pid = db.create_attendance_person(name, kind, token, sid)
    return jsonify({"ok": True, "id": pid, "token": token})


@app.route("/api/att/persons/<int:pid>", methods=["DELETE"])
def api_att_person_del(pid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": db.delete_attendance_person(pid)})


@app.route("/api/att/records", methods=["GET"])
def api_att_records():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    pid = request.args.get("person_id")
    pid = int(pid) if (pid and pid.isdigit()) else None
    return jsonify(db.list_attendance_records(request.args.get("from"), request.args.get("to"), pid))


@app.route("/api/att/records/<int:rid>/photo")
def api_att_photo(rid):
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    photo = db.get_attendance_photo(rid)
    if not photo:
        return ("", 404)
    import base64 as _b64
    try:
        data = _b64.b64decode(photo.split(",", 1)[-1])
    except Exception:
        return ("", 404)
    return Response(data, mimetype="image/jpeg")


@app.route("/api/att/lark_sync", methods=["POST"])
def api_att_lark_sync():
    """从 Lark 原生考勤拉内部员工打卡流水（/open-apis/attendance/v1/user_flows/query）。
    需在 Lark 开发者后台给应用授「考勤」权限、员工在考勤组内、配置员工号；否则返回提示，占位不报错。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": False, "synced": 0,
                    "error": "内部 Lark 考勤同步需先在 Lark 开发者后台给应用授「考勤」权限并配置员工考勤组，联调时启用；"
                             "在此之前，内部员工也可直接用 Nexus 网页打卡链接。"})


# -------- 免登录打卡（外部/内部通用；token 链接） --------
@app.route("/checkin/<token>")
def checkin_page(token):
    p = db.get_attendance_person_by_token(token)
    if not p or not p.get("active"):
        return ("打卡链接无效或已停用。", 404)
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "checkin.html"), encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return ("checkin.html not found: %s" % e, 500)


@app.route("/api/checkin/<token>", methods=["GET"])
def api_checkin_meta(token):
    p = db.get_attendance_person_by_token(token)
    if not p or not p.get("active"):
        return jsonify({"error": "invalid"}), 404
    site = db.get_attendance_site(p["site_id"]) if p.get("site_id") else None
    last = db.last_attendance_record(p["id"])
    return jsonify({
        "name": p["name"], "kind": p["kind"],
        "site": ({"name": site["name"], "radius_m": site["radius_m"]} if site else None),
        "require_selfie": bool(site["require_selfie"]) if site else False,
        "last": ({"punch_type": last["punch_type"],
                  "server_time": last["server_time"].isoformat() if hasattr(last["server_time"], "isoformat") else str(last["server_time"])} if last else None),
    })


@app.route("/api/checkin/<token>", methods=["POST"])
def api_checkin_submit(token):
    p = db.get_attendance_person_by_token(token)
    if not p or not p.get("active"):
        return jsonify({"error": "invalid"}), 404
    d = request.get_json(silent=True) or {}
    punch = d.get("punch_type") if d.get("punch_type") in ("in", "out") else "in"
    try:
        lat = float(d.get("lat")) if d.get("lat") is not None else None
        lng = float(d.get("lng")) if d.get("lng") is not None else None
        acc = float(d.get("accuracy")) if d.get("accuracy") is not None else None
    except (TypeError, ValueError):
        lat = lng = acc = None
    site = db.get_attendance_site(p["site_id"]) if p.get("site_id") else None
    photo = d.get("photo")
    need_selfie = bool(site and site.get("require_selfie"))
    if need_selfie and not photo:
        return jsonify({"error": "该点位要求自拍，请允许拍照后再提交"}), 400
    if photo and len(photo) > 4_000_000:
        return jsonify({"error": "照片过大"}), 400
    prev = db.last_attendance_record(p["id"])
    prev_lat = prev_lng = secs = None
    if prev and prev.get("lat") is not None:
        prev_lat, prev_lng = prev["lat"], prev["lng"]
        try:
            secs = (datetime.datetime.now(datetime.timezone.utc) - prev["server_time"]).total_seconds()
        except Exception:
            secs = None
    ip = _client_ip()
    dist, within, flags = attend.evaluate(
        lat, lng, acc,
        ({"lat": site["lat"], "lng": site["lng"], "radius_m": site["radius_m"]} if site else None),
        prev_lat, prev_lng, secs, ip)
    rec = db.add_attendance_record(p["id"], p["name"], p["kind"], punch, lat, lng, acc,
                                   (site["id"] if site else None), dist, within, ip,
                                   (photo if need_selfie else None), ",".join(flags), "web")
    st = rec["server_time"]
    return jsonify({"ok": True, "punch_type": punch,
                    "server_time": st.isoformat() if hasattr(st, "isoformat") else str(st),
                    "distance_m": dist, "within_fence": within,
                    "flags": flags, "flags_text": attend.flags_text(flags)})


@app.route("/api/channel/template")
def api_channel_template():
    """下载来源中立的 Candidate Pipeline workbook。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    day = (request.args.get("d") or _kolkata_today().isoformat())[:10]
    by = request.args.get("by", "")
    jobs = db.list_job_requests(only_open=True)
    data = sheet_io.build_pipeline_template_xlsx(jobs, day, by, db.list_candidates_active())
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="candidate_pipeline_%s.xlsx"' % day},
    )


@app.route("/api/channel/upload", methods=["POST"])
def api_channel_upload():
    """HR 填好的渠道候选人表（.xlsx）上传 -> 解析 -> 统一 application service。
    （按记录 ID 或 姓名+渠道+职位 去重，重传更新不重复建行）。
    填报人以上传时选定的受控 owner 为准（表内声明仅作展示）。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "没有收到文件"}), 400
    if not (f.filename or "").casefold().endswith(".xlsx"):
        return jsonify({"error": "渠道跟进表只接受系统生成的 .xlsx；CSV 不保留受控列和记录 ID"}), 400
    owner = (request.form.get("by") or "").strip()      # 受控填报人（roster 选择），权威 owner
    default_date = (request.form.get("d") or "").strip() or _kolkata_today().isoformat()
    jobs = db.list_job_requests(only_open=False)
    try:
        parsed = sheet_io.parse_pipeline_sheet(f.read(), f.filename or "", jobs, owner, default_date)
    except Exception as e:
        return jsonify({"error": "无法解析文件（请上传系统生成的 .xlsx）：%s" % e}), 400
    return jsonify(channel_sheet_service.import_pipeline_rows(db, parsed, owner=owner))


# ==================== Lark 多维表格同步（机器人自己的表；只对管理员开放） ====================
def _lark_cfg():
    try:
        s = db.get_settings()
    except Exception:
        s = {}
    return {"app_token": s.get("lark_channel_app_token", ""),
            "pipeline_table_id": s.get("lark_channel_pipeline_table_id", ""),
            "manual_table_id": s.get("lark_channel_manual_table_id", ""),
            "url": s.get("lark_channel_url", ""),
            "last_sync": s.get("lark_channel_last_sync", ""),
            "schema_version": s.get("lark_channel_schema_version", "")}


@app.route("/api/lark/ping", methods=["POST", "GET"])
def api_lark_ping():
    """连接自检：机器人能不能以应用身份拿到 Lark token。联调第一步。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(lark_bitable.ping())


@app.route("/api/lark/status")
def api_lark_status():
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    c = _lark_cfg()
    return jsonify({"configured": bool(c["app_token"] and c["pipeline_table_id"]
                                       and c["manual_table_id"]
                                       and c["schema_version"] == "channel-analytics-v2"),
                    "url": c["url"], "last_sync": c["last_sync"],
                    "schema_version": c["schema_version"],
                    "bot_name": BOT_NAME or "(未命名)",
                    "app_id_tail": APP_ID[-8:] if APP_ID else ""})


@app.route("/api/lark/share", methods=["POST"])
def api_lark_share_existing():
    """Repair access to the existing Base without creating a second copy."""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    cfg = _lark_cfg()
    if not cfg.get("app_token"):
        return jsonify({"error": "在线表尚未配置"}), 409
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip()
    permission = (payload.get("permission") or "full_access").strip()
    if not email or "@" not in email or len(email) > 254:
        return jsonify({"error": "请输入当前 Lark 账号实际绑定的邮箱"}), 422
    if permission not in {"edit", "full_access"}:
        return jsonify({"error": "permission 只能是 edit 或 full_access"}), 422
    result = lark_bitable.add_member(
        cfg["app_token"], email, "email", permission
    )
    return jsonify(result), 200 if result.get("ok") else 502


@app.route("/api/lark/reconnect", methods=["POST"])
def api_lark_reconnect():
    """Forget an unsynchronised wrong-app Base link; never delete its document."""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    if payload.get("confirmation") != "RESET_UNSYNCED_CHANNEL_LINK":
        return jsonify({"error": "confirmation_required"}), 422
    current = _lark_cfg()
    if current.get("last_sync"):
        return jsonify({
            "error": "已有同步历史，禁止自动重置；请先进行数据迁移审计"
        }), 409
    for key in (
        "lark_channel_app_token", "lark_channel_pipeline_table_id",
        "lark_channel_manual_table_id", "lark_channel_url",
        "lark_channel_last_sync", "lark_channel_schema_version",
    ):
        db.set_setting(key, "")
    return jsonify({
        "ok": True,
        "forgotten_url": current.get("url") or "",
        "document_deleted": False,
        "candidate_data_changed": False,
    })


@app.route("/api/integration/v1/channel/status")
def api_channel_bot_status():
    """Authenticated Recruitment Bot lookup; returns no candidate records."""
    if not _talent_worker_auth():
        return jsonify({"error": "unauthorized"}), 401
    cfg = _lark_cfg()
    configured = bool(
        cfg.get("app_token") and cfg.get("pipeline_table_id")
        and cfg.get("manual_table_id")
        and cfg.get("schema_version") == "channel-analytics-v2"
    )
    return jsonify({
        "ok": True, "configured": configured,
        "url": cfg.get("url") or "", "last_sync": cfg.get("last_sync") or "",
        "schema_version": cfg.get("schema_version") or "",
    })


@app.route("/api/integration/v1/channel/submit", methods=["POST"])
def api_channel_bot_submit():
    """Authenticated Recruitment Bot submission using the shared service."""
    if not _talent_worker_auth():
        return jsonify({"error": "unauthorized"}), 401
    result = channel_sheet_service.sync_lark_table(
        db, lark_bitable, _lark_cfg(),
        jobs=db.list_job_requests(only_open=False),
        channels=channel_report.CHANNELS,
        default_date=_kolkata_today().isoformat(),
        synced_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    return jsonify(result), 200 if result.get("ok") else 409


@app.route("/api/lark/table", methods=["POST"])
def api_lark_create_table():
    """机器人首次创建 Channel Analytics 多维表格；不创建第二份候选人联系表。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    current = _lark_cfg()
    if (current.get("app_token") and current.get("pipeline_table_id")
            and current.get("manual_table_id")
            and current.get("schema_version") == "channel-analytics-v2"):
        return jsonify({"error": "在线渠道表已配置；为防止分叉，不会重复创建",
                        "url": current.get("url")}), 409
    d = request.get_json(silent=True) or {}
    jobs = db.list_job_requests(only_open=True)
    res = lark_bitable.create_channel_base(
        "Nexus Channel Analytics", [j["title"] for j in jobs], channel_report.CHANNELS,
        channel_report.PIPELINE_STATUS,
        folder_token=(d.get("folder_token") or "").strip(),
    )
    if not res.get("ok"):
        return jsonify(res), 502
    db.set_setting("lark_channel_app_token", res["app_token"])
    db.set_setting("lark_channel_pipeline_table_id", res.get("pipeline_table_id") or "")
    db.set_setting("lark_channel_manual_table_id", res.get("manual_table_id") or "")
    db.set_setting("lark_channel_url", res.get("url") or "")
    db.set_setting("lark_channel_schema_version", "channel-analytics-v2")
    shared = None
    share = (d.get("share_email") or "").strip()
    if share:
        shared = lark_bitable.add_member(res["app_token"], share, "email", "full_access")
    return jsonify({"ok": True, "url": res.get("url"), "table_id": res.get("table_id"), "shared": shared})


@app.route("/api/lark/push", methods=["POST"])
def api_lark_push():
    """Legacy endpoint retained fail-closed: Channel Analytics never seeds candidate rows."""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"error": "该旧功能已停用：Channel Analytics 不再复制候选人；请使用渠道日报表"}), 410


@app.route("/api/lark/pull", methods=["POST"])
def api_lark_pull():
    """从 Lark 在线渠道表提交；网站按钮和 Bot 命令共用同一 application service。"""
    if not _panel_auth():
        return jsonify({"error": "unauthorized"}), 401
    result = channel_sheet_service.sync_lark_table(
        db,
        lark_bitable,
        _lark_cfg(),
        jobs=db.list_job_requests(only_open=False),
        channels=channel_report.CHANNELS,
        default_date=_kolkata_today().isoformat(),
        synced_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    if result.get("ok"):
        return jsonify(result)
    status = 400 if "尚未配置" in str(result.get("error") or "") or "旧版" in str(result.get("error") or "") else 502
    return jsonify(result), status


# ---------------- 日历订阅（iCal）：把任务截止日期同步进个人日历 ----------------
def _ics_esc(s):
    return (str(s or "")).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@app.route("/calendar.ics")
def calendar_ics():
    if not _panel_auth():          # 用 ?pw=面板密码 订阅
        return Response("unauthorized", status=401)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LarkTaskBot//CN//",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Task due dates", "X-WR-TIMEZONE:UTC"]
    for t in db.list_tasks():
        d = t.get("deadline")
        if not d or t.get("status") == "done":
            continue
        ymd = d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d).replace("-", "")
        nxt = d + __import__("datetime").timedelta(days=1) if hasattr(d, "strftime") else d
        ymd2 = nxt.strftime("%Y%m%d") if hasattr(nxt, "strftime") else ymd
        summ = f"[{_ST_LABEL.get(t.get('status'), '')}] {t.get('title', '')}"
        desc = f"Assignee: {t.get('assignee_name') or ''}   Priority: {t.get('priority') or ''}"
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


_ST_LABEL = {"pending": "🆕 To Do", "accepted": "⏳ In Progress", "done": "✅ Completed", "issue": "🙋 Needs Reply"}


def _msg_box(hint):
    """一个输入框 + 一个清晰的“发送给发布者”按钮（不再有两个模糊按钮）。"""
    h = f'<div class="hint">{hint}</div>' if hint else ""
    return ('<div class="or">Have a question, need an extension, or want to share an update? Message the sender directly:</div>'
            '<textarea name="msg" placeholder="Write your message to the sender…"></textarea>'
            '<button class="b talk" name="action" value="message">💬 Send to sender</button>' + h)


def _status_actions(task):
    """按当前状态给一个主按钮 + 一个发送留言按钮，作用一目了然。
    真实状态只有 待接受/进行中/已完成；发留言只是通知发布者，不改变状态。"""
    st = task.get("status", "pending")
    tip = "Once sent, the sender will see your message in their console and reply."
    if st == "pending":
        return '<button class="b done" name="action" value="accept">✅ Accept Task</button>' + _msg_box(tip)
    if st == "done":
        return '<div class="okmsg">🎉 Marked complete. Thank you!</div>' + _msg_box("The sender will receive your additional note.")
    # accepted（含历史 issue 数据）——进行中
    return '<button class="b done" name="action" value="done">✅ Mark Complete</button>' + _msg_box(tip)


def _fmt_time(ts):
    try:
        return ts.strftime("%m-%d %H:%M")
    except Exception:
        return str(ts or "")


def _iso_utc(ts):
    """转成带时区的 ISO 字符串，让前端能按“查看者当地时区”显示（避免留言时间显示成 UTC）。"""
    if not hasattr(ts, "isoformat"):
        return str(ts or "")
    s = ts.isoformat()
    if getattr(ts, "tzinfo", None) is None:
        s += "Z"      # 没带时区信息就按 UTC 处理
    return s


_SIDE_LABEL = {"publisher": "Sender", "assignee": "Assignee", "system": ""}


def _thread_html(comments):
    if not comments:
        return ""
    rows = []
    for c in comments:
        side = c.get("author_side", "system")
        cls = {"publisher": "pub", "assignee": "asg", "system": "sys"}.get(side, "sys")
        name = c.get("author_name") or _SIDE_LABEL.get(side, "")
        ts = c.get("created_at")
        timespan = f'<span class="ct" data-t="{_h(_iso_utc(ts))}">{_h(_fmt_time(ts))}</span>'
        head = f"{_h(name)} · {timespan}" if side != "system" else timespan
        rows.append(f'<div class="cmt {cls}"><div class="cw">{head}</div><div>{_h(c.get("body",""))}</div></div>')
    return '<div class="sec">💬 Conversation</div><div class="thread">' + "".join(rows) + "</div>"


def _status_html(task, flash=None, comments=None):
    st = task.get("status", "pending")
    bits = []
    if task.get("priority"):
        bits.append(f"Priority {_h(task['priority'])}")
    tz_script = ""
    if task.get("deadline"):
        bits.append(f"Due {_h(cards.fmt_deadline(task['deadline']))}<span id='dlLocal' class='dllocal'></span>")
        tz_script = (
            "<script>(function(){var O=" + str(COMPANY_TZ_OFFSET) + ",WE=" + str(WORK_END) +
            ",el=document.getElementById('dlLocal');if(!el)return;"
            "var p='" + str(task['deadline']) + "'.split('-').map(Number);"
            "var inst=Date.UTC(p[0],p[1]-1,p[2],WE,0,0)-O*3600000,dt=new Date(inst);"
            "var vo=-new Date().getTimezoneOffset()/60;if(vo===O)return;"
            "var loc=dt.toLocaleString([],{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});"
            "el.textContent=' · your local ~'+loc;})();</script>"
        )
    if task.get("assignee_name"):
        bits.append(f"Assignee {_h(task['assignee_name'])}")
    detail = f"<p><b>📝 Details:</b>{_h(task['detail'])}</p>" if task.get("detail") else ""
    note = f"<p><b>⚠️ Notes:</b>{_h(task['note'])}</p>" if task.get("note") else ""
    who = _h(task.get("assignee_name") or "Assignee")
    badge = f'<span class="badge b-{st}">Status: {_ST_LABEL.get(st, st)}</span>'
    flash_html = f'<div class="flash">{_h(flash)}</div>' if flash else ""
    thread = _thread_html(comments or [])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Task</title>
<style>{_STATUS_CSS}</style></head><body><div class="card">
<div class="h">📋 Task</div>
<div class="t">{_h(task.get('title',''))}</div>
<div class="meta">{'　·　'.join(bits)}</div>
{badge}
{detail}{note}
<div class="warn">⚠️ Only the assignee <b>{who}</b> should act here. Other group members, please don't tap — it may disrupt the status.</div>
{thread}
{flash_html}
<form method="post">{_status_actions(task)}</form>
</div>{tz_script}
<script>document.querySelectorAll('.ct').forEach(function(e){{var t=e.getAttribute('data-t');if(!t)return;var d=new Date(t);if(isNaN(d))return;e.textContent=d.toLocaleString([],{{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}});}});</script>
</body></html>"""


@app.route("/t/<token>", methods=["GET"])
def status_page(token):
    task = db.get_task_by_token(token)
    if not task:
        return "<h3 style='font-family:sans-serif;text-align:center;margin-top:40px'>Invalid link, or the task was deleted</h3>", 404
    return _status_html(task, comments=db.list_comments(task["id"]))


@app.route("/t/<token>", methods=["POST"])
def status_submit(token):
    task = db.get_task_by_token(token)
    if not task:
        return "<h3 style='font-family:sans-serif;text-align:center;margin-top:40px'>Invalid link</h3>", 404
    action = request.form.get("action")
    msg = (request.form.get("msg") or "").strip()
    who = task.get("assignee_name") or "Assignee"
    tid = task["id"]
    flash = None
    if action == "accept":
        db.update_task_status(tid, "accepted")
        _log(tid, "Accepted the task", "assignee", who)
        flash = "Accepted ✅ Please complete it before the due date — we'll remind the group as it approaches."
    elif action == "done":
        db.update_task_status(tid, "done")
        _log(tid, "Marked complete" + (f": {msg}" if msg else ""), "assignee", who)
        notify_publisher(task, f"✅ {who} completed task #{tid} '{task['title']}'")
        flash = "Recorded: completed. Thank you! 🎉"
    elif action == "message":
        if not msg:
            flash = "Please write something before sending."
        else:
            _assignee_comment(tid, msg, who)     # 标未读（发布者看板会进“待沟通”），不改状态
            notify_publisher(task, f"💬 {who} messaged on task #{tid} '{task['title']}':\n'{msg}'")
            flash = "Sent to the sender ✅ They'll get back to you after reading and replying."
    task = db.get_task_by_token(token)      # 重新读取，拿到最新状态再渲染
    return _status_html(task, flash=flash, comments=db.list_comments(tid))


def main():
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("Missing APP_ID / APP_SECRET environment variables")
    db.init_db()
    try:
        db.seed_job_requests()      # 招聘模块：首次为空时放几个示例职位
    except Exception as e:
        print(f"[db] seed_job_requests skipped: {e}")
    if MODE == "ws":
        print("[bot] starting in long-connection (ws) mode ...")
        cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=build_handler(),
                             domain=LARK_DOMAIN, log_level=lark.LogLevel.INFO)
        cli.start()
    else:
        threading.Thread(target=_reminder_scheduler, daemon=True).start()   # 内置每日提醒
        from waitress import serve
        print(f"[bot] starting in webhook mode, listening on port {PORT} ...")
        serve(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
