"""
纯逻辑函数（不碰数据库、不碰网络），方便单独测试：
- 解析截止日期
- 清理消息文本里的 @提及占位符
- 判断某个任务今天该发哪一档超期提醒
"""
import re
import datetime

# 匹配 2026-07-25 / 2026/7/25 这种日期
_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def extract_deadline(text):
    """从文本里找第一个日期，返回 datetime.date；找不到返回 None。
    支持写 '截止:2026-07-25'、'ddl 2026/7/25'、或直接写日期。"""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def clean_title(text, mention_keys):
    """把命令词、@提及占位符、日期从文本里去掉，剩下的就是任务标题。
    mention_keys 例如 ['@_user_1']。"""
    t = text or ""
    # 去掉命令词
    for cmd in ["/task", "/任务", "新任务", "/bind", "/绑定", "/claimadmin"]:
        t = t.replace(cmd, " ")
    # 去掉 @提及占位符
    for k in (mention_keys or []):
        t = t.replace(k, " ")
    # 去掉 '截止:'、'ddl' 和日期本身
    t = _DATE_RE.sub(" ", t)
    t = re.sub(r"(截止|deadline|ddl)\s*[:：]?", " ", t, flags=re.IGNORECASE)
    # 压缩空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


STAGE_ORDER = {"": 0, "due_tomorrow": 1, "due_today": 2, "escalated": 3}


def overdue_stage(deadline, today, escalate_days, last_stage):
    """
    决定今天要给这个任务发哪一档提醒；不需要发就返回 None。
    三个触点：due_tomorrow（截止前一天）、due_today（当天/刚过期）、escalated（过期满 N 天升级）。
    档位只升不降，且每档只发一次（靠 last_stage 去重），所以中途某天没跑到也能补发。
    """
    if deadline is None:
        return None
    last = STAGE_ORDER.get(last_stage, 0)
    days_left = (deadline - today).days   # >0 未到期，0 今天到期，<0 已过期
    overdue_days = -days_left             # 过期了几天

    if days_left >= 2:
        candidate = None
    elif days_left == 1:
        candidate = "due_tomorrow"
    elif overdue_days < escalate_days:
        # 当天到期，或过期但还没到升级天数 → 催“到期/已过期”
        candidate = "due_today"
    else:
        candidate = "escalated"

    if candidate is None:
        return None
    if STAGE_ORDER[candidate] <= last:   # 同档或更低不再重复发
        return None
    return candidate
