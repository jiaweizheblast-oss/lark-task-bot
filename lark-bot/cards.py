"""
飞书交互卡片的 JSON 组装。
一张任务卡片 = 标题 + @负责人 + 任务信息 + 三个按钮（完成/无法完成/跳过）。
点完按钮后我们会把卡片换成"已处理"的样子（见 done_card）。
"""

STATUS_LABEL = {
    "pending": "🆕 待接受",
    "accepted": "⏳ 进行中",
    "done": "✅ 已完成",
    "issue": "🙋 待沟通",
}
HEADER_COLOR = {
    "new": "blue", "accepted": "turquoise", "done": "green", "issue": "orange",
    "due_tomorrow": "orange", "due_today": "orange", "escalated": "red",
}


def _at(open_id):
    return f"<at id={open_id}></at>"


PRIORITY_TAG = {"高": "🔴 高", "中": "🟡 中", "低": "🟢 低"}


def _pri(p):
    return PRIORITY_TAG.get(p, p or "")


def _task_detail_block(task):
    """把一条任务的完整信息渲染成一个 div（标题/详情/注意事项/优先级/负责人/截止）。"""
    lines = [f"**📌 任务：**{task.get('title', '')}"]
    if task.get("detail"):
        lines.append(f"**📝 详情/安排：**{task['detail']}")
    if task.get("note"):
        lines.append(f"**⚠️ 注意事项：**{task['note']}")
    if task.get("priority"):
        lines.append(f"**🚩 优先级：**{_pri(task['priority'])}")
    lines.append(f"**👤 负责人：**{_at(task['assignee_open_id'])}")
    dl = task.get("deadline")
    lines.append(f"**📅 截止：**{dl if dl else '未设置'}")
    return {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}


def _input(name, label, placeholder, max_length=200):
    return {"tag": "input", "name": name,
            "label": {"tag": "plain_text", "content": label},
            "placeholder": {"tag": "plain_text", "content": placeholder},
            "max_length": max_length}


def create_form_card(chat_id, chat_name, assignee_open_id, assignee_name):
    """选完负责人后弹出的派发表单：一次填全任务信息。"""
    v = {"action": "create_task", "chat_id": chat_id, "chat_name": chat_name,
         "open_id": assignee_open_id, "name": assignee_name}
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "🆕 新建任务 · 填写详情"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": f"群：**{chat_name}** · 负责人：**{assignee_name}**"}},
                {"tag": "form", "name": "task_form", "elements": [
                    _input("title", "任务标题 *", "一句话说清要做什么", 100),
                    _input("detail", "详情 / 安排", "具体怎么做、分几步、交付什么", 500),
                    _input("note", "注意事项", "要注意的点、验收标准、易踩的坑", 300),
                    _input("priority", "优先级（高/中/低）", "默认 中", 4),
                    _input("deadline", "截止日期", "如 2026-07-25", 20),
                    {"tag": "action", "actions": [
                        {"tag": "button", "action_type": "form_submit", "name": "submit",
                         "text": {"tag": "plain_text", "content": "✅ 提交派发"}, "type": "primary", "value": v}]},
                ]},
            ]}


def picked_card(chat_name, assignee_name):
    """选好群+人后，私聊里显示的引导卡片（逐步问答开始）。"""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": "✍️ 新建任务"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                "content": f"群：**{chat_name}** · 负责人：**{assignee_name}**\n\n"
                           f"请在下方对话框**逐条回答**我的提问（标题 → 详情 → 注意事项 → 优先级和截止）。"}}]}


def new_task_card(task):
    """刚派发：负责人可【接受任务】或【无法完成/有问题】。task 为任务字典。"""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": HEADER_COLOR["new"], "title": {"tag": "plain_text", "content": "📋 新任务"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"任务 #{task['id']} · 请负责人选择"}]},
                {"tag": "action", "actions": [
                    _btn("✅ 接受任务", {"action": "accept", "task_id": str(task["id"])}, "primary"),
                    _btn("✋ 无法完成 / 有问题", {"action": "raise", "task_id": str(task["id"])}, "default"),
                ]},
            ]}


def accepted_card(task):
    """已接受、进行中：可【完成】或【有问题】。"""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": HEADER_COLOR["accepted"], "title": {"tag": "plain_text", "content": "⏳ 进行中"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"任务 #{task['id']} · 负责人已接受"}]},
                {"tag": "action", "actions": [
                    _btn("🎉 完成", {"action": "done", "task_id": str(task["id"])}, "primary"),
                    _btn("✋ 有问题", {"action": "raise", "task_id": str(task["id"])}, "default"),
                ]},
            ]}


REASON_OPTIONS = [
    ("⏰ 时间来不及，想延期", "时间来不及，想延期"),
    ("❓ 需要更多信息 / 说明", "需要更多信息或说明"),
    ("🙅 可能不太适合我", "可能不太适合我，建议换人"),
    ("💬 其他，想当面沟通", "其他，想当面沟通"),
]


def reason_buttons_card(task):
    """负责人点“有问题”后，展示预设原因按钮（点一个即通知发布者）。"""
    actions = [{"tag": "action", "actions": [
        _btn(label, {"action": "issue_reason", "task_id": str(task["id"]), "reason": reason}, "default")]}
        for label, reason in REASON_OPTIONS]
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "orange", "title": {"tag": "plain_text", "content": "✋ 选择原因"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "div", "text": {"tag": "lark_md", "content": "**请选择原因（会通知发布者一起商量）：**"}},
            ] + actions}


def final_card(task, status, operator_open_id=None, reason=None):
    """终态卡片（已完成 / 待沟通），无按钮。"""
    block = _task_detail_block(task)
    if operator_open_id:
        block["text"]["content"] += f"\n**✍️ 处理人：**{_at(operator_open_id)}"
    if reason:
        block["text"]["content"] += f"\n**💬 说明：**{reason}"
    return {"config": {"wide_screen_mode": True},
            "header": {"template": HEADER_COLOR.get(status, "grey"),
                       "title": {"tag": "plain_text", "content": f"任务 #{task['id']} · {STATUS_LABEL.get(status, status)}"}},
            "elements": [
                block,
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"状态：{STATUS_LABEL.get(status, status)}"}]},
            ]}


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


def dispatched_card(chat_name, task):
    """派发成功后，把私聊里的表单卡片更新成这个确认卡片。"""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "green", "title": {"tag": "plain_text", "content": "✅ 已派发"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "note", "elements": [{"tag": "plain_text",
                    "content": f"已发到群：{chat_name} · 负责人会收到 @ 和反馈按钮"}]}]}


def _ext_task_lines(task):
    lines = [f"**📌 任务：**{task.get('title','')}"]
    if task.get("detail"):
        lines.append(f"**📝 详情/安排：**{task['detail']}")
    if task.get("note"):
        lines.append(f"**⚠️ 注意事项：**{task['note']}")
    if task.get("priority"):
        lines.append(f"**🚩 优先级：**{_pri(task['priority'])}")
    lines.append(f"**👤 负责人：**{task.get('assignee_name') or '（见群内）'}")
    if task.get("deadline"):
        lines.append(f"**📅 截止：**{task['deadline']}")
    return lines


def external_task_card(task, status_url):
    """推给外部群的任务卡片：@全体 + 详情 + 一个按钮打开汇报页。
    点开的网页会根据任务状态，依次给出【接受任务】→【标记完成 / 有问题】，
    和内部群的流程保持一致。只用一个按钮，避免两个按钮都跳同一个页面的重复。"""
    name = task.get("assignee_name") or ""
    body = "<at id=all></at>\n" + "\n".join(_ext_task_lines(task))
    tip = f"👉 请**负责人 {name}** 点下面按钮：先接受任务，之后再汇报完成或问题。" \
          f"\n（其他群成员请勿点击，以免弄乱状态）"
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "📋 新任务"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": tip}},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "📝 接受 / 汇报进度"},
                     "type": "primary", "url": status_url}]},
            ]}


def external_reminder_card(kind, task, status_url=None):
    """外部群提醒卡片（通过 webhook 推送）：@全体 + 提醒 + 打开汇报页按钮。"""
    name = task.get("assignee_name") or "负责人"
    title = task.get("title", "")
    dl = task.get("deadline")
    if kind == "due_tomorrow":
        header, color = "⏰ 明天到期", HEADER_COLOR["due_tomorrow"]
        line = f"任务「{title}」将于 **明天（{dl}）** 到期，请尽快处理。"
    elif kind == "due_today":
        header, color = "⏰ 今天到期", HEADER_COLOR["due_today"]
        line = f"任务「{title}」**今天（{dl}）到期**，请尽快处理。"
    else:  # escalated
        header, color = "🚨 任务超期", HEADER_COLOR["escalated"]
        line = f"任务「{title}」已超期（截止 {dl}），请尽快跟进。"
    body = f"<at id=all></at>\n**负责人：{name}**\n{line}"
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": body}}]
    if status_url:
        elements.append({"tag": "action", "actions": [
            {"tag": "button", "text": {"tag": "plain_text", "content": "📝 去接受 / 汇报"},
             "type": "primary", "url": status_url}]})
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"任务 #{task['id']} · 仅负责人操作"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": color, "title": {"tag": "plain_text", "content": header}},
            "elements": elements}


def nudge_card(task):
    """催办提醒卡片（网页/看板点催办时发到群里）。"""
    dl = f"，截止 {task['deadline']}" if task.get("deadline") else ""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "orange", "title": {"tag": "plain_text", "content": "⏰ 催办提醒"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                "content": f"{_at(task['assignee_open_id'])} 任务 #{task['id']}【{task['title']}】请尽快处理{dl}。"}}]}


def help_text():
    return (
        "**🤖 任务终端用法（私聊我操作）**\n"
        "• 发送 `新建任务` —— 我一步步带你：选群 → 选负责人 → 输入内容，然后我到群里 @ 他派任务\n"
        "• `/claimadmin 口令` —— 首次把自己设为管理员\n"
        "• `/whoami` —— 查看你的身份\n\n"
        "只有管理员能派任务；负责人在群里点卡片按钮反馈：完成 / 无法完成 / 跳过。"
    )
