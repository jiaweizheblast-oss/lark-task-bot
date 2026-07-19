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
import bot  # 复用 bot.py 里集中好的提醒逻辑（run_reminders）


def run():
    db.init_db()  # 确保表存在
    bot.run_reminders()


if __name__ == "__main__":
    run()
