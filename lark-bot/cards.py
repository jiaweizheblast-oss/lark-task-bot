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


def _btn(text, value, typ="default"):
    return {"tag": "button", "text": {"tag": "plain_text", "content": text}, "type": typ, "value": value}


def group_select_card(groups):
    """第 1 步：私聊里让管理员点选要派任务的群。groups: [{chat_id,name,external}]。"""
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**请选择要派任务的群：**"}}, {"tag": "hr"}]
    shown = groups[:20]
    for g in shown:
        tag = "🌐 外部群" if g.get("external") else "🏠 内部群"
        elements.append({"tag": "action", "actions": [
            _btn(f"{tag} · {g['name']}", {"action": "pick_group", "chat_id": g["chat_id"], "chat_name": g["name"]})]})
    if not shown:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": "（机器人还没被拉进任何群。请先把它加进相关的群，再回来派任务。）"}})
    elif len(groups) > len(shown):
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"仅显示前 {len(shown)} 个群"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "🆕 新建任务 · 第 1 步：选群"}},
            "elements": elements}


def person_select_card(chat_id, chat_name, members):
    """第 2 步：选负责人。members: [{open_id,name}]。"""
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": f"群：**{chat_name}**\n**请选择负责人：**"}}, {"tag": "hr"}]
    shown = members[:30]
    for m in shown:
        elements.append({"tag": "action", "actions": [
            _btn(f"👤 {m['name']}", {"action": "pick_person", "chat_id": chat_id, "chat_name": chat_name,
                                     "open_id": m["open_id"], "name": m["name"]})]})
    if not shown:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "（这个群里除了机器人没有别人。）"}})
    elif len(members) > len(shown):
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"仅显示前 {len(shown)} 人"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "🆕 新建任务 · 第 2 步：选负责人"}},
            "elements": elements}


def draft_ready_card(chat_name, assignee_name):
    """第 3 步：提示管理员输入任务内容。"""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": "✍️ 第 3 步：输入任务内容"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                "content": f"**目标群：**{chat_name}\n**负责人：**{assignee_name}\n\n"
                           f"现在直接把**任务内容和截止日期**发给我，例如：\n`写合同初稿 截止:2026-07-25`"}}]}


def dispatched_card(chat_name, assignee_name, title, deadline=None):
    """派发成功后，把私聊里的卡片更新成这个。"""
    ddl = f"\n**截止：**{deadline}" if deadline else ""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "green", "title": {"tag": "plain_text", "content": "✅ 已派发"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": f"**任务：**{title}\n**已发到群：**{chat_name}\n**负责人：**{assignee_name}{ddl}"}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "负责人会在群里收到 @ 和反馈按钮"}]}]}


def help_text():
    return (
        "**🤖 任务终端用法（私聊我操作）**\n"
        "• 发送 `新建任务` —— 我一步步带你：选群 → 选负责人 → 输入内容，然后我到群里 @ 他派任务\n"
        "• `/claimadmin 口令` —— 首次把自己设为管理员\n"
        "• `/whoami` —— 查看你的身份\n\n"
        "只有管理员能派任务；负责人在群里点卡片按钮反馈：完成 / 无法完成 / 跳过。"
    )
