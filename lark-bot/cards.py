"""
飞书交互卡片的 JSON 组装。
一张任务卡片 = 标题 + @负责人 + 任务信息 + 三个按钮（完成/无法完成/跳过）。
点完按钮后我们会把卡片换成"已处理"的样子（见 done_card）。
"""

STATUS_LABEL = {
    "done": "✅ 已完成",
    "unable": "🚫 无法完成",
    "skip": "⏭️ 已跳过",
    "pending": "⏳ 待处理",
}
HEADER_COLOR = {
    "new": "blue", "done": "green", "unable": "red", "skip": "grey",
    "due_tomorrow": "orange", "due_today": "orange", "escalated": "red",
}


def _at(open_id):
    return f"<at id={open_id}></at>"


def task_card(task_id, title, assignee_open_id, deadline=None):
    """新任务卡片（带按钮）。"""
    ddl = f"\n**截止：**{deadline}" if deadline else "\n**截止：**未设置"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": HEADER_COLOR["new"],
            "title": {"tag": "plain_text", "content": "📋 新任务"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
                "content": f"**任务：**{title}\n**负责人：**{_at(assignee_open_id)}{ddl}"}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"任务编号 #{task_id} · 请负责人点击下方按钮反馈"}]},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 完成"},
                 "type": "primary", "value": {"action": "done", "task_id": str(task_id)}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "🚫 无法完成"},
                 "type": "danger", "value": {"action": "unable", "task_id": str(task_id)}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "⏭️ 跳过"},
                 "type": "default", "value": {"action": "skip", "task_id": str(task_id)}},
            ]},
        ],
    }


def done_card(task_id, title, assignee_open_id, status, deadline=None, operator_open_id=None):
    """点完按钮后，把原卡片替换成这个"已处理"样式（没有按钮，不能再点）。"""
    ddl = f"\n**截止：**{deadline}" if deadline else ""
    who = f"\n**处理人：**{_at(operator_open_id)}" if operator_open_id else ""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": HEADER_COLOR.get(status, "grey"),
            "title": {"tag": "plain_text", "content": f"任务 #{task_id} · {STATUS_LABEL.get(status, status)}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
                "content": f"**任务：**{title}\n**负责人：**{_at(assignee_open_id)}{ddl}{who}"}},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"状态：{STATUS_LABEL.get(status, status)}"}]},
        ],
    }


def reminder_card(kind, task_id, title, assignee_open_id, deadline, owner_open_id=None):
    """超期提醒卡片。kind = due_tomorrow / due_today / escalated。"""
    at = _at(assignee_open_id)
    if kind == "due_tomorrow":
        header, color = "⏰ 明天到期", HEADER_COLOR["due_tomorrow"]
        line = f"{at} 任务「{title}」将于 **明天（{deadline}）** 到期。"
    elif kind == "due_today":
        header, color = "⏰ 今天到期", HEADER_COLOR["due_today"]
        line = f"{at} 任务「{title}」**今天（{deadline}）到期**，请尽快处理。"
    else:  # escalated
        header, color = "🚨 任务超期（升级）", HEADER_COLOR["escalated"]
        owner_at = f"\n升级提醒：{_at(owner_open_id)} 请关注" if owner_open_id else ""
        line = f"{at} 的任务「{title}」已超期（截止 {deadline}）。{owner_at}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": color, "title": {"tag": "plain_text", "content": header}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": line}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"任务 #{task_id}"}]},
        ],
    }


def help_text():
    return (
        "**🤖 任务机器人用法**\n"
        "发命令时请在消息里 **@一下机器人** 来触发（例：`@机器人 /task ...`）。\n\n"
        "• `@机器人 /task @某人 任务内容 截止:2026-07-25` — 派一个任务（仅管理员/HR）\n"
        "• 负责人在群里点卡片按钮反馈：完成 / 无法完成 / 跳过\n"
        "• `@机器人 /whoami` — 查看你自己的身份和 open_id\n"
        "• `@机器人 /bind @某人 Vendor 供应商A` — 把待确认的人绑定为某角色（仅管理员/HR）\n"
        "• `@机器人 /pending` — 查看还没绑定身份的人（仅管理员/HR）\n"
        "• `@机器人 /claimadmin 口令` — 用管理口令把自己设为管理员（首次配置用）"
    )
