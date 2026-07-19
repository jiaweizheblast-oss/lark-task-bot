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


def run():
    db.init_db()  # 确保表存在
    today = datetime.date.today()
    tasks = db.tasks_still_open()
    sent = 0
    for t in tasks:
        stage = overdue_stage(t["deadline"], today, ESCALATE_DAYS, t["last_reminder_stage"])
        if not stage:
            continue
        card = cards.reminder_card(
            stage, t["id"], t["title"], t["assignee_open_id"],
            t["deadline"], owner_open_id=t.get("owner_open_id"),
        )
        mid = bot.send_card(t["group_chat_id"], card)
        if mid:
            db.set_reminder_stage(t["id"], stage)
            sent += 1
    print(f"[overdue] {today} 扫描 {len(tasks)} 个未完成任务，发出 {sent} 条提醒")


if __name__ == "__main__":
    run()
