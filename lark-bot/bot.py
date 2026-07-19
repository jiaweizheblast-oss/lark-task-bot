"""
任务机器人主程序。
它做三件事：
  1) 监听群消息，识别 /task /bind /whoami 等命令
  2) 机器人被拉进群 / 有人进群时，自动登记群和成员
  3) 有人点任务卡片按钮（完成/无法完成/跳过）时，回写数据库并更新卡片

默认用 webhook 模式（国际版 Lark 支持这个）：机器人是一个小网页服务，
飞书/Lark 把事件推到它的网址。Railway 会自动给这个服务一个公网网址。
（如果你的后台支持"长连接"，把环境变量 MODE 设为 ws 即可切换，不用改代码。）
运行方式：python bot.py
"""
import os
import json
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest, CreateMessageRequestBody,
    PatchMessageRequest, PatchMessageRequestBody,
    GetChatMembersRequest,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger, P2CardActionTriggerResponse,
)
from flask import Flask
from lark_oapi.adapter.flask import parse_req, parse_resp

import db
import cards
from parse import extract_deadline, clean_title

# ---------------- 配置（全部从环境变量读）----------------
APP_ID = os.environ.get("APP_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")
LARK_DOMAIN = os.environ.get("LARK_DOMAIN", "https://open.larksuite.com")  # 国内飞书填 https://open.feishu.cn
ADMIN_SETUP_CODE = os.environ.get("ADMIN_SETUP_CODE", "")             # /claimadmin 用的一次性口令
BOT_OPEN_ID = os.environ.get("BOT_OPEN_ID", "")                       # 可选：机器人自己的 open_id，用来排除自我@
BOT_NAME = os.environ.get("BOT_NAME", "")                             # 机器人名字（如 Task Bot），用来在命令里区分"@机器人"和"@负责人"
ENCRYPT_KEY = os.environ.get("ENCRYPT_KEY", "")                       # 事件订阅的 Encrypt Key（后台生成）
VERIFICATION_TOKEN = os.environ.get("VERIFICATION_TOKEN", "")         # 事件订阅的 Verification Token
MODE = os.environ.get("MODE", "webhook")                             # webhook（默认）/ ws（长连接）
PORT = int(os.environ.get("PORT", "8080"))                           # Railway 会自动注入 PORT

# 调用飞书 API 用的客户端
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).domain(LARK_DOMAIN).build()


# ---------------- 发送 / 更新消息的小工具 ----------------

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


def sync_chat_members(chat_id):
    """拉取一个群的全部成员，登记进 users 表；不认识的外部人先记为 待确认(Unknown/pending)。"""
    page_token = None
    count = 0
    while True:
        b = GetChatMembersRequest.builder().chat_id(chat_id).member_id_type("open_id").page_size(100)
        if page_token:
            b = b.page_token(page_token)
        resp = client.im.v1.chat_members.get(b.build())
        if not resp.success():
            print(f"[members] 拉取失败 code={resp.code} msg={resp.msg}")
            return count
        data = resp.data
        for m in (data.items or []):
            open_id = getattr(m, "member_id", None)
            name = getattr(m, "name", None)
            if not open_id:
                continue
            existing = db.get_user(open_id)
            if existing:
                db.upsert_user(open_id, display_name=name)          # 已存在只更新名字
            else:
                db.upsert_user(open_id, display_name=name, role="Unknown", status="pending")
            count += 1
        if getattr(data, "has_more", False) and getattr(data, "page_token", None):
            page_token = data.page_token
        else:
            break
    print(f"[members] 群 {chat_id} 同步了 {count} 个成员")
    return count


# ---------------- 命令处理 ----------------

def handle_command(chat_id, sender_open_id, text, mentions):
    """text 是清理前的原始文本；mentions 是 MentionEvent 列表。"""
    # 取出被 @ 的人（排除机器人自己——发命令时需要 @ 机器人来触发）
    mention_keys = [m.key for m in mentions if getattr(m, "key", None)]
    mentioned_open_ids = []
    for m in mentions:
        oid = getattr(getattr(m, "id", None), "open_id", None)
        mname = getattr(m, "name", None)
        if not oid:
            continue
        if BOT_OPEN_ID and oid == BOT_OPEN_ID:      # 按 open_id 排除机器人
            continue
        if BOT_NAME and mname == BOT_NAME:          # 或按名字排除机器人
            continue
        mentioned_open_ids.append(oid)

    low = text.strip()

    # /help
    if low.startswith("/help") or "帮助" == low:
        send_text(chat_id, cards.help_text())
        return

    # /whoami —— 谁都能用，方便拿到自己的 open_id
    if low.startswith("/whoami"):
        u = db.get_user(sender_open_id)
        role = u["role"] if u else "Unknown"
        send_text(chat_id, f"你的 open_id：{sender_open_id}\n当前身份：{role}")
        return

    # /claimadmin 口令 —— 首次把自己设为管理员
    if low.startswith("/claimadmin"):
        code = low.replace("/claimadmin", "").strip()
        if ADMIN_SETUP_CODE and code == ADMIN_SETUP_CODE:
            db.upsert_user(sender_open_id, role="Admin", kind="internal", status="bound")
            send_text(chat_id, "✅ 已把你设为管理员(Admin)。现在你可以用 /task 派任务了。")
        else:
            send_text(chat_id, "❌ 口令不对。")
        return

    # 下面的命令需要管理员/HR 权限
    admin = db.is_admin(sender_open_id)

    # /task @某人 内容 截止:2026-07-25
    if low.startswith("/task") or low.startswith("/任务") or low.startswith("新任务"):
        if not admin:
            send_text(chat_id, "❌ 只有管理员/HR 能派任务。可先用 /claimadmin 口令 设为管理员。")
            return
        if not mentioned_open_ids:
            send_text(chat_id, "用法：/task @负责人 任务内容 截止:2026-07-25")
            return
        assignee = mentioned_open_ids[0]
        title = clean_title(text, mention_keys)
        deadline = extract_deadline(text)
        if not title:
            send_text(chat_id, "任务内容不能为空。用法：/task @负责人 任务内容 截止:2026-07-25")
            return
        grp = db.get_group(chat_id)
        owner = grp["default_owner_open_id"] if grp else None
        task_id = db.create_task(title, assignee, chat_id, deadline=deadline,
                                 owner_open_id=owner, created_by_open_id=sender_open_id)
        card = cards.task_card(task_id, title, assignee, deadline)
        mid = send_card(chat_id, card)
        if mid:
            db.set_task_card(task_id, mid)
        else:
            send_text(chat_id, "⚠️ 任务已建，但卡片发送失败，请检查机器人发消息权限。")
        return

    # /bind @某人 角色 [供应商]
    if low.startswith("/bind") or low.startswith("/绑定"):
        if not admin:
            send_text(chat_id, "❌ 只有管理员/HR 能绑定身份。")
            return
        if not mentioned_open_ids:
            send_text(chat_id, "用法：/bind @某人 Vendor 供应商A")
            return
        parts = clean_title(text, mention_keys).split()
        role = None
        for p in parts:
            if p.lower() in ("admin", "hr", "vendor"):
                role = p.capitalize() if p.lower() != "hr" else "HR"
                break
        if not role:
            send_text(chat_id, "请指明角色：Admin / HR / Vendor。例：/bind @某人 Vendor 供应商A")
            return
        vendor = None
        rest = [p for p in parts if p.lower() not in ("admin", "hr", "vendor")]
        if rest:
            vendor = " ".join(rest)
        kind = "internal" if role in ("Admin", "HR") else "external"
        db.upsert_user(mentioned_open_ids[0], kind=kind)
        db.bind_user(mentioned_open_ids[0], role, vendor)
        send_text(chat_id, f"✅ 已绑定：角色={role}" + (f"，供应商={vendor}" if vendor else ""))
        return

    # /pending
    if low.startswith("/pending"):
        if not admin:
            send_text(chat_id, "❌ 只有管理员/HR 能查看。")
            return
        rows = db.list_pending_in_group(chat_id)
        if not rows:
            send_text(chat_id, "没有待确认的人。")
        else:
            lines = [f"· {r['display_name'] or '(无名)'}  open_id={r['open_id']}" for r in rows]
            send_text(chat_id, "待确认身份的人：\n" + "\n".join(lines))
        return


# ---------------- 事件回调 ----------------

def on_message(data: P2ImMessageReceiveV1):
    try:
        msg = data.event.message
        if msg.message_type != "text":
            return
        sender_open_id = data.event.sender.sender_id.open_id
        chat_id = msg.chat_id
        content = json.loads(msg.content or "{}")
        text = content.get("text", "") or ""
        mentions = msg.mentions or []
        # 只处理以 / 开头或含关键词的命令，避免打扰正常聊天
        if not (text.strip().startswith("/") or text.strip().startswith("新任务") or "帮助" == text.strip()):
            return
        handle_command(chat_id, sender_open_id, text, mentions)
    except Exception as e:
        print(f"[on_message] 出错: {e}")


def on_bot_added(data):
    try:
        chat_id = data.event.chat_id
        print(f"[event] 机器人被拉进群 {chat_id}")
        db.upsert_group(chat_id)
        sync_chat_members(chat_id)
        send_text(chat_id, "👋 任务机器人已就位。发送 /help 查看用法。首次使用请管理员发送 /claimadmin 口令。")
    except Exception as e:
        print(f"[on_bot_added] 出错: {e}")


def on_user_added(data):
    try:
        chat_id = data.event.chat_id
        users = getattr(data.event, "users", []) or []
        for u in users:
            oid = getattr(getattr(u, "user_id", None), "open_id", None)
            name = getattr(u, "name", None)
            if not oid:
                continue
            if not db.get_user(oid):
                db.upsert_user(oid, display_name=name, role="Unknown", status="pending")
        print(f"[event] 群 {chat_id} 新增 {len(users)} 人")
    except Exception as e:
        print(f"[on_user_added] 出错: {e}")


def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    """有人点了任务卡片的按钮。"""
    try:
        operator = data.event.operator.open_id
        action_value = data.event.action.value or {}
        task_id = action_value.get("task_id")
        action = action_value.get("action")
        message_id = data.event.context.open_message_id

        task = db.get_task(int(task_id)) if task_id else None
        if not task:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "任务不存在"}})

        # 只有负责人本人能操作（防止别人误点/越权）
        if operator != task["assignee_open_id"]:
            return P2CardActionTriggerResponse(
                {"toast": {"type": "error", "content": "只有该任务的负责人能操作哦"}})

        status_map = {"done": "done", "unable": "unable", "skip": "skip"}
        new_status = status_map.get(action)
        if not new_status:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "未知操作"}})

        db.update_task_status(task["id"], new_status)
        # 把卡片替换成"已处理"样式（去掉按钮）
        new_card = cards.done_card(task["id"], task["title"], task["assignee_open_id"],
                                   new_status, task["deadline"], operator)
        if message_id:
            patch_card(message_id, new_card)

        toast = {"done": "已标记完成 ✅", "unable": "已记录：无法完成", "skip": "已跳过"}[new_status]
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": toast}})
    except Exception as e:
        print(f"[on_card_action] 出错: {e}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "处理出错，请稍后再试"}})


# ---------------- 启动 ----------------

def build_handler(encrypt_key="", verification_token=""):
    return (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_im_chat_member_bot_added_v1(on_bot_added)
        .register_p2_im_chat_member_user_added_v1(on_user_added)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )


# ---------------- webhook 模式的网页服务 ----------------
app = Flask(__name__)
_webhook_handler = build_handler(ENCRYPT_KEY, VERIFICATION_TOKEN)


@app.route("/", methods=["GET"])
def health():
    return "ok"


@app.route("/webhook/event", methods=["POST"])
def webhook_event():
    # 事件（收消息、进群）和卡片按钮回调都走这里，SDK 会自动验证网址、解密、分发
    return parse_resp(_webhook_handler.do(parse_req()))


# 万一后台把"卡片回调网址"单独分开填，也指向下面这个，同一套处理逻辑
@app.route("/webhook/card", methods=["POST"])
def webhook_card():
    return parse_resp(_webhook_handler.do(parse_req()))


def main():
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("缺少 APP_ID / APP_SECRET 环境变量")
    db.init_db()  # 首次启动自动建表
    if MODE == "ws":
        print("[bot] 长连接(ws)模式启动，正在连接 ...")
        cli = lark.ws.Client(APP_ID, APP_SECRET,
                             event_handler=build_handler(),
                             domain=LARK_DOMAIN,
                             log_level=lark.LogLevel.INFO)
        cli.start()  # 阻塞常驻，自动重连
    else:
        from waitress import serve
        print(f"[bot] webhook 模式启动，监听端口 {PORT}，等待 Lark 事件推送 ...")
        serve(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
