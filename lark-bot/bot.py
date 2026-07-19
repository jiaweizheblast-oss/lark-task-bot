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
from flask import Flask
from lark_oapi.adapter.flask import parse_req, parse_resp

import db
import cards
from parse import parse_content

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

    # 有草稿在等内容 → 把这条文本当成任务内容和截止
    draft = db.get_draft(sender_open_id)
    if draft and draft.get("assignee_open_id"):
        title, deadline = parse_content(text)
        if not title:
            send_text(dm_chat_id, "任务内容不能为空。请发送：任务内容 截止:2026-07-25")
            return
        task_id = db.create_task(title, draft["assignee_open_id"], draft["chat_id"],
                                 deadline=deadline, created_by_open_id=sender_open_id)
        mid = send_card(draft["chat_id"], cards.task_card(task_id, title, draft["assignee_open_id"], deadline))
        if mid:
            db.set_task_card(task_id, mid)
        db.clear_draft(sender_open_id)
        send_card(dm_chat_id, cards.dispatched_card(draft["chat_name"], draft["assignee_name"], title, deadline))
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

        # 派任务：选负责人 → 存草稿，把卡片换成"请输入任务内容"
        if action == "pick_person":
            if not db.is_admin(operator):
                return card_resp("error", "只有管理员能派任务")
            db.set_draft(operator, value.get("chat_id"), value.get("chat_name"),
                         value.get("open_id"), value.get("name"))
            return card_resp("success", "已选负责人，请输入任务内容",
                             cards.draft_ready_card(value.get("chat_name"), value.get("name")))

        # 任务卡片：完成 / 无法完成 / 跳过
        task_id = value.get("task_id")
        task = db.get_task(int(task_id)) if task_id else None
        if not task:
            return card_resp("error", "任务不存在")
        if operator != task["assignee_open_id"]:
            return card_resp("error", "只有该任务的负责人能操作")
        new_status = {"done": "done", "unable": "unable", "skip": "skip"}.get(action)
        if not new_status:
            return card_resp("error", "未知操作")
        db.update_task_status(task["id"], new_status)
        toast = {"done": "已标记完成 ✅", "unable": "已记录：无法完成", "skip": "已跳过"}[new_status]
        return card_resp("success", toast,
                         cards.done_card(task["id"], task["title"], task["assignee_open_id"],
                                         new_status, task["deadline"], operator))
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
        from waitress import serve
        print(f"[bot] webhook 模式启动，监听端口 {PORT}，等待 Lark 事件推送 ...")
        serve(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
