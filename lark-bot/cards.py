"""
Lark interactive card assembly (JSON).
A task card = title + @assignee + task info + action buttons.
After a button is tapped, the card is swapped for its updated state.
"""
import os

# Timezone label (for international teams): set env var TZ_LABEL, e.g. "IST" / "GMT+5:30".
# It is shown after the due date.
TZ_LABEL = os.environ.get("TZ_LABEL", "").strip()


def fmt_deadline(d, empty="Not set"):
    """Format the due date, with the timezone label appended when configured."""
    if not d:
        return empty
    return f"{d} ({TZ_LABEL})" if TZ_LABEL else f"{d}"


STATUS_LABEL = {
    "pending": "🆕 To Do",
    "accepted": "⏳ In Progress",
    "done": "✅ Completed",
    "issue": "🙋 Needs Reply",
}
HEADER_COLOR = {
    "new": "blue", "accepted": "turquoise", "done": "green", "issue": "orange",
    "due_tomorrow": "orange", "due_today": "orange", "escalated": "red",
}


def _at(open_id):
    return f"<at id={open_id}></at>"


# English values are canonical; Chinese kept as a fallback for legacy data.
PRIORITY_TAG = {"High": "🔴 High", "Medium": "🟡 Medium", "Low": "🟢 Low",
                "高": "🔴 High", "中": "🟡 Medium", "低": "🟢 Low"}


def _pri(p):
    return PRIORITY_TAG.get(p, p or "")


def _task_body_lines(task, assignee_display, at_all=False):
    """Shared task body (internal group / external group / web all use the same layout).
    Line 1 title; line 2 priority · due; line 3 assignee; then Details / Notes."""
    head = f"**{task.get('title', '(Untitled)')}**"
    if at_all:
        head = "<at id=all></at>\n" + head
    meta = []
    if task.get("priority"):
        meta.append(f"Priority {_pri(task['priority'])}")
    meta.append(f"Due {fmt_deadline(task.get('deadline'))}")
    lines = [head, "　·　".join(meta), f"Assignee {assignee_display}"]
    if task.get("detail"):
        lines += ["", f"**📝 Details**\n{task['detail']}"]
    if task.get("note"):
        lines += ["", f"**⚠️ Notes**\n{task['note']}"]
    return "\n".join(lines)


def _task_detail_block(task):
    """Internal-group card: the assignee is shown as an @-mention."""
    return {"tag": "div", "text": {"tag": "lark_md",
            "content": _task_body_lines(task, _at(task["assignee_open_id"]))}}


def _input(name, label, placeholder, max_length=200):
    return {"tag": "input", "name": name,
            "label": {"tag": "plain_text", "content": label},
            "placeholder": {"tag": "plain_text", "content": placeholder},
            "max_length": max_length}


def create_form_card(chat_id, chat_name, assignee_open_id, assignee_name):
    """Assignment form shown after picking an assignee (fill everything at once)."""
    v = {"action": "create_task", "chat_id": chat_id, "chat_name": chat_name,
         "open_id": assignee_open_id, "name": assignee_name}
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "🆕 New Task · Details"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": f"Group: **{chat_name}** · Assignee: **{assignee_name}**"}},
                {"tag": "form", "name": "task_form", "elements": [
                    _input("title", "Title *", "One line: what needs to be done", 100),
                    _input("detail", "Details", "How to do it, steps, deliverables", 500),
                    _input("note", "Notes", "Things to watch, acceptance criteria", 300),
                    _input("priority", "Priority (High/Medium/Low)", "Default: Medium", 8),
                    _input("deadline", "Due date", "e.g. 2026-07-25", 20),
                    {"tag": "action", "actions": [
                        {"tag": "button", "action_type": "form_submit", "name": "submit",
                         "text": {"tag": "plain_text", "content": "✅ Assign"}, "type": "primary", "value": v}]},
                ]},
            ]}


def picked_card(chat_name, assignee_name):
    """Shown in the DM after group + assignee are chosen (start of the guided flow)."""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": "✍️ New Task"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                "content": f"Group: **{chat_name}** · Assignee: **{assignee_name}**\n\n"
                           f"Please **answer my questions one by one** below "
                           f"(title → details → notes → priority & due date)."}}]}


def new_task_card(task):
    """Just assigned: the assignee can Accept Task or Report an Issue."""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": HEADER_COLOR["new"], "title": {"tag": "plain_text", "content": "📋 New Task"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": "👉 Assignee: tap **Accept Task** to start. If you can't complete it "
                               "or have a question, tap **Report an Issue** to reach the sender."}},
                {"tag": "action", "actions": [
                    _btn("✅ Accept Task", {"action": "accept", "task_id": str(task["id"])}, "primary"),
                    _btn("✋ Report an Issue", {"action": "raise", "task_id": str(task["id"])}, "default"),
                ]},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"Task #{task['id']} · Task Console"}]},
            ]}


def accepted_card(task):
    """Accepted / in progress: Mark Complete or Report an Issue."""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": HEADER_COLOR["accepted"], "title": {"tag": "plain_text", "content": "⏳ In Progress"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": "👉 Tap **Mark Complete** when done. If something comes up, "
                               "tap **Report an Issue** to reach the sender."}},
                {"tag": "action", "actions": [
                    _btn("🎉 Mark Complete", {"action": "done", "task_id": str(task["id"])}, "primary"),
                    _btn("✋ Report an Issue", {"action": "raise", "task_id": str(task["id"])}, "default"),
                ]},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"Task #{task['id']} · Accepted"}]},
            ]}


REASON_OPTIONS = [
    ("⏰ Need more time – request an extension", "Needs more time – requests an extension"),
    ("❓ Need more information", "Needs more information"),
    ("🙅 May not be the right fit for me", "May not be the right fit – suggests reassigning"),
    ("💬 Other – would like to discuss", "Other – would like to discuss"),
]


def reason_buttons_card(task):
    """Shown after the assignee taps 'Report an Issue': preset reasons (one tap notifies the sender)."""
    actions = [{"tag": "action", "actions": [
        _btn(label, {"action": "issue_reason", "task_id": str(task["id"]), "reason": reason}, "default")]}
        for label, reason in REASON_OPTIONS]
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "orange", "title": {"tag": "plain_text", "content": "✋ Select a reason"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "div", "text": {"tag": "lark_md", "content": "**Select a reason (the sender will be notified):**"}},
            ] + actions}


def final_card(task, status, operator_open_id=None, reason=None):
    """Terminal card (Completed / Needs Reply), no buttons."""
    block = _task_detail_block(task)
    if operator_open_id:
        block["text"]["content"] += f"\n**✍️ Handled by:** {_at(operator_open_id)}"
    if reason:
        block["text"]["content"] += f"\n**💬 Note:** {reason}"
    return {"config": {"wide_screen_mode": True},
            "header": {"template": HEADER_COLOR.get(status, "grey"),
                       "title": {"tag": "plain_text", "content": f"Task #{task['id']} · {STATUS_LABEL.get(status, status)}"}},
            "elements": [
                block,
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"Status: {STATUS_LABEL.get(status, status)}"}]},
            ]}


def reminder_card(kind, task_id, title, assignee_open_id, deadline, owner_open_id=None):
    """Due-date reminder card. kind = due_tomorrow / due_today / escalated."""
    at = _at(assignee_open_id)
    if kind == "due_tomorrow":
        header, color = "⏰ Due tomorrow", HEADER_COLOR["due_tomorrow"]
        line = f"{at} Task '{title}' is due **tomorrow ({deadline})**."
    elif kind == "due_today":
        header, color = "⏰ Due today", HEADER_COLOR["due_today"]
        line = f"{at} Task '{title}' is **due today ({deadline})**. Please action it soon."
    else:  # escalated
        header, color = "🚨 Overdue (escalated)", HEADER_COLOR["escalated"]
        owner_at = f"\nEscalation: {_at(owner_open_id)} please take note" if owner_open_id else ""
        line = f"{at}'s task '{title}' is overdue (was due {deadline}).{owner_at}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": color, "title": {"tag": "plain_text", "content": header}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": line}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": f"Task #{task_id}"}]},
        ],
    }


def _btn(text, value, typ="default"):
    return {"tag": "button", "text": {"tag": "plain_text", "content": text}, "type": typ, "value": value}


def group_select_card(groups):
    """Step 1: in the DM, let the admin pick a group. groups: [{chat_id,name,external}]."""
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**Select a group to assign the task to:**"}}, {"tag": "hr"}]
    shown = groups[:20]
    for g in shown:
        tag = "🌐 External" if g.get("external") else "🏠 Internal"
        elements.append({"tag": "action", "actions": [
            _btn(f"{tag} · {g['name']}", {"action": "pick_group", "chat_id": g["chat_id"], "chat_name": g["name"]})]})
    if not shown:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": "(The bot hasn't been added to any group yet. Add it to the relevant group first, then come back.)"}})
    elif len(groups) > len(shown):
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"Showing first {len(shown)} groups"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "🆕 New Task · Step 1: Select group"}},
            "elements": elements}


def person_select_card(chat_id, chat_name, members):
    """Step 2: pick the assignee. members: [{open_id,name}]."""
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": f"Group: **{chat_name}**\n**Select an assignee:**"}}, {"tag": "hr"}]
    shown = members[:30]
    for m in shown:
        elements.append({"tag": "action", "actions": [
            _btn(f"👤 {m['name']}", {"action": "pick_person", "chat_id": chat_id, "chat_name": chat_name,
                                     "open_id": m["open_id"], "name": m["name"]})]})
    if not shown:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "(There's no one in this group besides the bot.)"}})
    elif len(members) > len(shown):
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"Showing first {len(shown)} people"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "🆕 New Task · Step 2: Select assignee"}},
            "elements": elements}


def draft_ready_card(chat_name, assignee_name):
    """Step 3: prompt the admin to enter the task content."""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": "✍️ Step 3: Enter task details"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                "content": f"**Group:** {chat_name}\n**Assignee:** {assignee_name}\n\n"
                           f"Now send me the **task content and due date**, e.g.:\n`Draft the contract due:2026-07-25`"}}]}


def dispatched_card(chat_name, task):
    """After a successful assignment, replace the DM form card with this confirmation."""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "green", "title": {"tag": "plain_text", "content": "✅ Assigned"}},
            "elements": [
                _task_detail_block(task),
                {"tag": "note", "elements": [{"tag": "plain_text",
                    "content": f"Sent to {chat_name} · the assignee will be @-mentioned with action buttons"}]}]}


def external_task_card(task, status_url):
    """Task card pushed to an external group: same layout as the internal card.
    Differences: external groups @all (individual ids aren't available — a Lark limit),
    and there is a single button that opens the web page (Accept → Complete / Issue there)."""
    name = task.get("assignee_name") or ""
    tip = (f"👉 **{name or '(see group)'}**, please tap the button below: first **Accept Task**, "
           f"then report progress or issues on the same page.\n"
           f"⚠️ Other group members, please don't tap this — it may disrupt the status.")
    return {"config": {"wide_screen_mode": True},
            "header": {"template": HEADER_COLOR["new"], "title": {"tag": "plain_text", "content": "📋 New Task"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": _task_body_lines(task, name or "(see group)", at_all=True)}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": tip}},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "✅ Accept / Report Progress"},
                     "type": "primary", "url": status_url}]},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"Task #{task['id']} · External group (webhook)"}]},
            ]}


def external_reminder_card(kind, task, status_url=None):
    """External-group reminder card (pushed via webhook): @all + reminder + button to the report page."""
    name = task.get("assignee_name") or "Assignee"
    title = task.get("title", "")
    dl = task.get("deadline")
    if kind == "due_tomorrow":
        header, color = "⏰ Due tomorrow", HEADER_COLOR["due_tomorrow"]
        line = f"Task '{title}' is due **tomorrow ({dl})**. Please action it soon."
    elif kind == "due_today":
        header, color = "⏰ Due today", HEADER_COLOR["due_today"]
        line = f"Task '{title}' is **due today ({dl})**. Please action it soon."
    else:  # escalated
        header, color = "🚨 Overdue", HEADER_COLOR["escalated"]
        line = f"Task '{title}' is overdue (was due {dl}). Please follow up."
    body = f"<at id=all></at>\n**Assignee: {name}**\n{line}"
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": body}}]
    if status_url:
        elements.append({"tag": "action", "actions": [
            {"tag": "button", "text": {"tag": "plain_text", "content": "📝 Accept / Report"},
             "type": "primary", "url": status_url}]})
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"Task #{task['id']} · Assignee only"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": color, "title": {"tag": "plain_text", "content": header}},
            "elements": elements}


def nudge_card(task):
    """Nudge card (sent to the group when 'Nudge' is tapped in the console/board)."""
    dl = f" (due {task['deadline']})" if task.get("deadline") else ""
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "orange", "title": {"tag": "plain_text", "content": "⏰ Reminder"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md",
                "content": f"{_at(task['assignee_open_id'])} Task #{task['id']} '{task['title']}' — please action it soon{dl}."}}]}


def help_text():
    return (
        "**🤖 Task Console — how to use (message me directly)**\n"
        "• Send `new task` — I'll guide you: pick group → pick assignee → enter details, "
        "then I'll @-mention them in the group\n"
        "• `/claimadmin <code>` — set yourself as admin the first time\n"
        "• `/whoami` — check your role\n\n"
        "Only admins can assign tasks. Assignees respond via the card buttons: Accept / Report an Issue / Complete."
    )
