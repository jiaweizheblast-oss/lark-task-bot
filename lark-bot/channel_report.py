"""
招聘渠道日报：校验 + 分析（纯函数，可脱离数据库单测）。
所有分析函数吃一个 rows 列表（每行 = channel_daily 的一条记录，dict）
和 jobs 列表（job_requests，dict），不直接碰 DB，方便测试与将来替换数据源
（手填 -> 从 Candidate 表 GROUP BY，上层逻辑一行不用改）。

口径（与独立版一致）：
- 流量口径：4 个数字都记“当天动作量”。转化率/推荐率用“近7日滚动”，不做同日，
  避免“今天初筛的其实是前几天的简历”导致 >100% 或忽高忽低。
- 进度头条用“简历量”同量纲比；目标人数进度需 ATS 入职数，这里把“累计已推荐”
  当上游领先指标用绝对值呈现。
"""
from datetime import date, timedelta

CHANNELS = ["BOSS直聘", "猎聘", "内推", "LinkedIn", "招聘会", "其他"]


# ---------------- 校验 ----------------
def validate(payload):
    """返回 (errors, warnings)。errors 非空 -> 拒收；warnings -> 允许但提示复核。"""
    errors, warnings = [], []
    if payload.get("channel") not in CHANNELS:
        errors.append(f"招聘渠道 “{payload.get('channel')}” 不在预设项内")
    if not payload.get("job_request_id"):
        errors.append("请选择关联职位")
    # 填报人不再是唯一键，改为受控 roster 选择、可空；不在此处强制。

    vals = {}
    for label, key in (("今日新增简历数", "new_resumes"), ("初筛通过数", "passed_screening"),
                       ("已推荐面试数", "recommended"), ("已拒绝数", "rejected")):
        try:
            v = int(payload.get(key) or 0)
        except (TypeError, ValueError):
            errors.append(f"{label} 必须是整数")
            v = 0
        if v < 0:
            errors.append(f"{label} 不能为负数（当前 {v}）")
        vals[key] = v

    if errors:
        return errors, warnings

    if vals["passed_screening"] > vals["new_resumes"]:
        warnings.append("初筛通过数 > 今日新增：若在清理往日积压属正常，否则请复核。")
    if vals["recommended"] > vals["passed_screening"]:
        warnings.append("已推荐数 > 初筛通过数：若推荐的是往日初筛通过的简历属正常，否则请复核。")
    if vals["rejected"] > vals["new_resumes"]:
        warnings.append("已拒绝数 > 今日新增：若含往日简历属正常，否则请复核。")
    return errors, warnings


# ---------------- 分析（纯函数） ----------------
def _d(x):
    """把 record_date 规范成 date 对象（DB 给 date，前端给字符串都兼容）。"""
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _ratio(n, d):
    return round(n / d, 4) if d else None


def _win(rows, channel, end, days):
    """[end-days+1, end] 窗口内某渠道各字段合计。"""
    start = end - timedelta(days=days - 1)
    s = {"new": 0, "passed": 0, "recommended": 0, "rejected": 0}
    for r in rows:
        if r["channel"] != channel:
            continue
        rd = _d(r["record_date"])
        if start <= rd <= end:
            s["new"] += r["new_resumes"]
            s["passed"] += r["passed_screening"]
            s["recommended"] += r["recommended"]
            s["rejected"] += r["rejected"]
    return s


def _sum_new(rows, channel, d):
    return _win(rows, channel, d, 1)["new"]


def _ever_active(rows, channel, upto):
    return any(r["channel"] == channel and _d(r["record_date"]) <= upto and r["new_resumes"] > 0
               for r in rows)


def _zero_streak(rows, channel, end, max_lookback=30):
    streak = 0
    for k in range(max_lookback):
        if _sum_new(rows, channel, end - timedelta(days=k)) == 0:
            streak += 1
        else:
            break
    return streak


def breakdown(rows, target, min_volume=5):
    out = []
    for ch in CHANNELS:
        day = _win(rows, ch, target, 1)
        roll7 = _win(rows, ch, target, 7)
        y = _sum_new(rows, ch, target - timedelta(days=1))
        out.append({
            "channel": ch, "day": day, "roll7": roll7,
            "roll7_conversion": _ratio(roll7["passed"], roll7["new"]),
            "roll7_recommend_rate": _ratio(roll7["recommended"], roll7["passed"]),
            "yesterday_new": y,
            "wow_new": (round((day["new"] - y) / y, 4) if y else None),
            "zero_streak": _zero_streak(rows, ch, target),
            "enough_volume": roll7["new"] >= min_volume,
        })
    return out


def anomalies(rows, target, bd):
    al = []
    for b in bd:
        ch = b["channel"]
        if b["zero_streak"] >= 2 and _ever_active(rows, ch, target):
            al.append(f"{ch}：连续 {b['zero_streak']} 天新增为 0，建议检查渠道是否掉线。")
        prev = _win(rows, ch, target - timedelta(days=7), 7)
        pc = _ratio(prev["passed"], prev["new"])
        cc = b["roll7_conversion"]
        if pc and cc is not None and pc > 0:
            drop = (pc - cc) / pc
            if drop >= 0.5:
                al.append(f"{ch}：近7日转化率 {cc:.0%} 较上一周 {pc:.0%} 下降 {drop:.0%}，建议关注质量。")
    return al


def overall(rows, target, jobs, window_from=None, window_to=None):
    """累计/进度按活跃时间窗口算（默认近 30 天），不再 all-time、不混已关闭历史职位。
    未来 recruiting_cycle_id 由核心签发后，改用该 cycle 的窗口。"""
    wt = window_to or target
    wf = window_from or (target - timedelta(days=29))
    in_win = [r for r in rows if wf <= _d(r["record_date"]) <= wt]
    today = [r for r in rows if _d(r["record_date"]) == target]
    open_jobs = [j for j in jobs if (j.get("status") or "open") == "open"]
    win_new = sum(r["new_resumes"] for r in in_win)
    win_rec = sum(r["recommended"] for r in in_win)
    t_head = sum(j["target_headcount"] for j in open_jobs)
    t_res = sum(j["target_resume_count"] for j in open_jobs)
    return {
        "date": target.isoformat(),
        "window": {"from": wf.isoformat(), "to": wt.isoformat()},
        "total_new_today": sum(r["new_resumes"] for r in today),
        "window_new": win_new,
        "window_recommended": win_rec,
        "target_headcount": t_head,
        "target_resumes": t_res,
        "resume_progress": _ratio(win_new, t_res) if t_res else None,
    }


def _pct(x):
    return "—" if x is None else f"{x:.0%}"


def build_report(rows, target, jobs, window_from=None, window_to=None):
    bd = breakdown(rows, target)
    ov = overall(rows, target, jobs, window_from, window_to)
    al = anomalies(rows, target, bd)
    cand = [b for b in bd if b["enough_volume"] and b["roll7_conversion"] is not None]
    best = max(cand, key=lambda b: b["roll7_conversion"], default=None)

    win = ov["window"]
    lines = [f"📊 {target.month}月{target.day}日 招聘渠道日报"
             f"（人工录入·未逐人建档 / 时区 Asia/Kolkata）",
             f"当日新增简历：{ov['total_new_today']} 份",
             f"窗口累计（{win['from']}~{win['to']}）：{ov['window_new']} 份"]
    if ov["resume_progress"] is not None:
        lines.append(f"简历量进度：窗口累计 {ov['window_new']} / 目标 {ov['target_resumes']} 份 = {_pct(ov['resume_progress'])}")
    lines.append(f"窗口累计已推荐：{ov['window_recommended']} 人次（目标录用 {ov['target_headcount']} 人；真实入职进度需 ATS）")
    if best:
        lines.append(f"表现最好渠道：{best['channel']}（近7日转化率 {_pct(best['roll7_conversion'])}）")
    if al:
        lines.append("需要关注：")
        lines.extend(f"  • {a}" for a in al)
    else:
        lines.append("需要关注：暂无异常。")

    return {
        "space": "manual_unidentified",
        "timezone": "Asia/Kolkata",
        "text": "\n".join(lines),
        "overall": ov,
        "breakdown": bd,
        "alerts": al,
        "best_channel": (best["channel"] if best else None),
        "identity_derived": {
            "available": False,
            "note": "逐人建档的渠道指标由 AI-TD 核心按 attribution_source + 去重人头派生，待接入；不与人工数相加。",
        },
    }
