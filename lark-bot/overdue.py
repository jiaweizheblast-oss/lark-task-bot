"""
每日超期扫描（定时任务）。
Railway 会每天定点运行一次：python overdue.py
跑完就退出（不常驻）。

三档提醒：
  · 截止前一天  → 群里 @负责人「明天到期」
  · 当天/刚过期 → 群里 @负责人「今天到期」
  · 过期满 N 天 → @负责人 + @内部owner（升级）
每档只发一次，不会重复轰炸。
"""
import os
import datetime

import db
import cards
import bot  # 复用 bot.py 里已经写好的发送逻辑
from parse import overdue_stage

ESCALATE_DAYS = int(os.environ.get("OVERDUE_ESCALATE_DAYS", "2"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")  # 外部提醒里放汇报链接用


def _remind_external(t, stage):
    """外部群：通过该群的自定义机器人 webhook 推送提醒卡片。"""
    eg = db.get_external_group(t["external_group_id"]) if t.get("external_group_id") else None
    if not eg:
        return False
    url = f"{PUBLIC_BASE_URL}/t/{t['token']}" if (PUBLIC_BASE_URL and t.get("token")) else None
    return bot.push_to_webhook(eg["webhook_url"], cards.external_reminder_card(stage, t, url))


def _remind_internal(t, stage):
    """内部群：机器人直接发到群里并 @ 负责人。"""
    card = cards.reminder_card(
        stage, t["id"], t["title"], t["assignee_open_id"],
        t["deadline"], owner_open_id=t.get("owner_open_id"),
    )
    return bool(bot.send_card(t["group_chat_id"], card))


def run():
    db.init_db()  # 确保表存在
    today = datetime.date.today()
    tasks = db.tasks_still_open()
    sent = 0
    for t in tasks:
        stage = overdue_stage(t["deadline"], today, ESCALATE_DAYS, t["last_reminder_stage"])
        if not stage:
            continue
        ok = _remind_external(t, stage) if t.get("is_external") else _remind_internal(t, stage)
        if ok:
            db.set_reminder_stage(t["id"], stage)
            sent += 1
    print(f"[overdue] {today} 扫描 {len(tasks)} 个未完成任务，发出 {sent} 条提醒")


if __name__ == "__main__":
    run()
