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

CHANNELS = [
    "LinkedIn", "Naukri", "Telegram", "Facebook", "WhatsApp",
    "Company Careers", "Employee Referral", "Recruitment Agency",
    "Job Fair / Offline", "Other",
]
OTHER_CHANNEL = "Other"

INTERNAL_PENDING_STATUS = "Pending"
PIPELINE_STATUS = [
    "Contacted / Awaiting Reply", "HR Screening", "Interview", "Offer",
    "Hired", "Rejected", "Withdrawn", "Resigned",
]
ALL_PIPELINE_STATUS = [INTERNAL_PENDING_STATUS, *PIPELINE_STATUS]
_ST_PASSED = {"Interview", "Offer", "Hired", "Resigned"}
_ST_RECO = {"Interview", "Offer", "Hired", "Resigned"}
_ST_REJECT = {"Rejected"}

_LEGACY_STAGE_ALIASES = {
    "": INTERNAL_PENDING_STATUS,
    "New Lead": INTERNAL_PENDING_STATUS,
    "Interview 1": "Interview",
    "Interview 2 / Final": "Interview",
    "On Hold": "Withdrawn",
    "初筛通过": "Interview",
    "已推荐面试": "Interview",
    "已录用": "Hired",
    "已拒绝": "Rejected",
}


def canonical_stage(value):
    stage = str(value or "").strip()
    return _LEGACY_STAGE_ALIASES.get(stage, stage or INTERNAL_PENDING_STATUS)


def validate_source(channel, source_detail=""):
    """Validate a controlled source. `Other` always requires an explanation."""
    errors = []
    if channel not in CHANNELS:
        errors.append("渠道「%s」不在预设项内" % channel)
    if channel == OTHER_CHANNEL and not (source_detail or "").strip():
        errors.append("选择 Other 时必须填写其他来源说明")
    if channel != OTHER_CHANNEL and (source_detail or "").strip():
        errors.append("只有 Source Channel 选择 Other 时才能填写 Other Source Detail")
    if len((source_detail or "").strip()) > 200:
        errors.append("其他来源说明不能超过 200 字符")
    return errors


def candidates_to_daily(cands):
    """候选人行（每行一个候选人）→ channel_daily 同形聚合行（每 日×渠道×职位 一行）。
    每个候选人计 1 份 new_resumes；按其当前状态在漏斗里「达到即累计」：
      状态∈{初筛通过,已推荐面试,已录用} → passed_screening+1；
      状态∈{已推荐面试,已录用}          → recommended+1；
      状态=已拒绝                       → rejected+1。
    候选人 dict 需含：record_date(或 apply_date)、channel、job_request_id、status。"""
    agg = {}
    for c in cands:
        rd = c.get("record_date", c.get("apply_date"))
        ch = c.get("channel") or ""
        jid = c.get("job_request_id")
        key = (rd, ch, jid)
        cell = agg.get(key)
        if cell is None:
            cell = agg[key] = {"record_date": rd, "channel": ch, "job_request_id": jid,
                               "new_resumes": 0, "passed_screening": 0,
                               "recommended": 0, "rejected": 0}
        cell["new_resumes"] += 1
        st = c.get("status") or INTERNAL_PENDING_STATUS
        if st in _ST_PASSED:
            cell["passed_screening"] += 1
        if st in _ST_RECO:
            cell["recommended"] += 1
        if st in _ST_REJECT:
            cell["rejected"] += 1
    return list(agg.values())


def current_snapshot(rows, job_id=None):
    """Aggregate the current application portfolio without inventing history.

    ``rows`` contains one current row per candidate × hiring requisition.  It
    deliberately includes baseline imports: those rows are valid for today's
    workload and status distribution even though their earlier stage dates are
    unknown.  Date-window activity is calculated separately by ``analytics``
    from immutable intake/stage events.
    """
    jid = int(job_id) if job_id else None
    filtered = [
        row for row in rows
        if jid is None or int(row.get("job_request_id") or 0) == jid
    ]
    by_status, by_channel, by_hr, by_job = {}, {}, {}, {}
    baseline_count = 0
    for row in filtered:
        stage = canonical_stage(row.get("status") or row.get("current_stage"))
        channel = str(row.get("channel") or "Unspecified").strip() or "Unspecified"
        hr = str(row.get("filled_by") or row.get("hr_owner") or "Unassigned").strip() or "Unassigned"
        job = str(row.get("job_title") or "Unassigned Job").strip() or "Unassigned Job"
        by_status[stage] = by_status.get(stage, 0) + 1
        by_channel[channel] = by_channel.get(channel, 0) + 1
        by_hr[hr] = by_hr.get(hr, 0) + 1
        by_job[job] = by_job.get(job, 0) + 1
        baseline_count += int(bool(row.get("baseline_import")))

    active_stages = {
        INTERNAL_PENDING_STATUS, "Contacted / Awaiting Reply", "HR Screening",
        "Interview", "Offer",
    }
    completed_stages = {"Hired", "Rejected", "Withdrawn", "Resigned"}

    def _series(values, preferred=()):
        order = {name: index for index, name in enumerate(preferred)}
        return [
            {"label": label, "count": count}
            for label, count in sorted(
                values.items(),
                key=lambda item: (
                    order.get(item[0], len(order)), -item[1], item[0].casefold()
                ),
            )
        ]

    return {
        "as_of": date.today().isoformat(),
        "total": len(filtered),
        "in_progress": sum(by_status.get(stage, 0) for stage in active_stages),
        "completed": sum(by_status.get(stage, 0) for stage in completed_stages),
        "hired": by_status.get("Hired", 0),
        "rejected": by_status.get("Rejected", 0),
        "withdrawn": by_status.get("Withdrawn", 0),
        "resigned": by_status.get("Resigned", 0),
        "unassigned_hr": by_hr.get("Unassigned", 0),
        "baseline_count": baseline_count,
        "history_complete_count": len(filtered) - baseline_count,
        "by_status": _series(by_status, ALL_PIPELINE_STATUS),
        "by_channel": _series(by_channel, CHANNELS),
        "by_hr": _series(by_hr),
        "by_job": _series(by_job),
        "history_note": (
            "Baseline rows are included in the current snapshot, but activity "
            "before the first recorded stage event is not reconstructed."
        ),
    }


def validate_candidate(payload):
    """候选人行校验。返回 (errors, warnings)。errors 非空 -> 拒收。"""
    errors, warnings = [], []
    errors.extend(validate_source(payload.get("channel"), payload.get("source_detail")))
    st = payload.get("status") or INTERNAL_PENDING_STATUS
    if st not in ALL_PIPELINE_STATUS:
        errors.append("状态「%s」不在预设项内" % st)
    if not (payload.get("name") or "").strip():
        errors.append("候选人姓名必填")
    if not payload.get("job_request_id"):
        errors.append("请选择关联职位")
    if False and st == "Rejected" and not (payload.get("rejection_reason") or "").strip():
        errors.append("阶段为 Rejected 时必须填写 Rejection Reason")
    return errors, warnings


# ---------------- 校验 ----------------
def validate(payload):
    """返回 (errors, warnings)。errors 非空 -> 拒收；warnings -> 允许但提示复核。"""
    errors, warnings = [], []
    errors.extend(validate_source(payload.get("channel"), payload.get("source_detail")))
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


def build_report(rows, target, jobs, window_from=None, window_to=None,
                 data_space="identity_derived"):
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

    is_derived = data_space == "identity_derived"
    return {
        "space": data_space,
        "data_space": data_space,
        "timezone": "Asia/Kolkata",
        "text": "\n".join(lines),
        "overall": ov,
        "breakdown": bd,
        "alerts": al,
        "best_channel": (best["channel"] if best else None),
        "identity_derived": {
            "available": is_derived,
            "note": (
                "Metrics come from identified candidate applications and immutable stage events."
                if is_derived else
                "Legacy unidentified batch counts remain separate from identified candidates."
            ),
        },
    }


# ================= 多粒度聚合分析（纯函数；网站看板与 Bot 命令共用同一引擎，保证两边数字一致） =================
def _bucket(d, granularity):
    """把一个 date 归到 (排序键, 展示标签)。周按 ISO 周（周一起）、月按 YYYY-MM、年按 YYYY。"""
    if granularity == "week":
        monday = d - timedelta(days=d.weekday())
        iso = d.isocalendar()
        return (monday.isoformat(), "%04d-W%02d" % (iso[0], iso[1]))
    if granularity == "month":
        return ("%04d-%02d" % (d.year, d.month), "%04d-%02d" % (d.year, d.month))
    if granularity == "year":
        return ("%04d" % d.year, "%04d" % d.year)
    return (d.isoformat(), d.isoformat())  # day（默认）


def timeseries(rows, granularity):
    """按粒度归桶，返回按时间升序的桶：每桶四指标合计 + 转化率/推荐率。"""
    buckets = {}
    for r in rows:
        sk, label = _bucket(_d(r["record_date"]), granularity)
        b = buckets.get(sk)
        if b is None:
            b = buckets[sk] = {"key": sk, "label": label,
                               "new": 0, "passed": 0, "recommended": 0, "rejected": 0}
        b["new"] += r["new_resumes"]; b["passed"] += r["passed_screening"]
        b["recommended"] += r["recommended"]; b["rejected"] += r["rejected"]
    out = [buckets[k] for k in sorted(buckets)]
    for b in out:
        b["conversion"] = _ratio(b["passed"], b["new"])
        b["recommend_rate"] = _ratio(b["recommended"], b["passed"])
    return out


def channel_totals(rows):
    """窗口内各渠道合计 + 转化率/推荐率/拒绝率/简历量占比，按简历量降序。"""
    tot = {}
    for ch in CHANNELS:
        tot[ch] = {"channel": ch, "new": 0, "passed": 0, "recommended": 0, "rejected": 0}
    for r in rows:
        ch = r["channel"]
        if ch not in tot:  # 兼容历史里已不在预设表的渠道
            tot[ch] = {"channel": ch, "new": 0, "passed": 0, "recommended": 0, "rejected": 0}
        t = tot[ch]
        t["new"] += r["new_resumes"]; t["passed"] += r["passed_screening"]
        t["recommended"] += r["recommended"]; t["rejected"] += r["rejected"]
    grand = sum(t["new"] for t in tot.values())
    out = sorted(tot.values(), key=lambda t: t["new"], reverse=True)
    for t in out:
        t["conversion"] = _ratio(t["passed"], t["new"])
        t["recommend_rate"] = _ratio(t["recommended"], t["passed"])
        t["reject_rate"] = _ratio(t["rejected"], t["new"])
        t["share"] = _ratio(t["new"], grand)
    return out


def funnel(rows):
    s = {"new": 0, "passed": 0, "recommended": 0, "rejected": 0}
    for r in rows:
        s["new"] += r["new_resumes"]; s["passed"] += r["passed_screening"]
        s["recommended"] += r["recommended"]; s["rejected"] += r["rejected"]
    return {"new": s["new"], "passed": s["passed"], "recommended": s["recommended"], "rejected": s["rejected"],
            "pass_rate": _ratio(s["passed"], s["new"]),
            "recommend_rate": _ratio(s["recommended"], s["passed"]),
            "reject_rate": _ratio(s["rejected"], s["new"])}


def job_progress(rows, jobs):
    """每个在招职位窗口内的新增/推荐 vs 目标（简历量、录用人数）。"""
    by = {}
    for r in rows:
        jid = int(r.get("job_request_id") or 0)
        b = by.get(jid)
        if b is None:
            b = by[jid] = {"new": 0, "recommended": 0}
        b["new"] += r["new_resumes"]; b["recommended"] += r["recommended"]
    out = []
    for j in jobs:
        if (j.get("status") or "open") != "open":
            continue
        b = by.get(j["id"], {"new": 0, "recommended": 0})
        tr = j.get("target_resume_count") or 0
        out.append({"id": j["id"], "title": j["title"],
                    "target_headcount": j.get("target_headcount") or 0,
                    "target_resumes": tr,
                    "window_new": b["new"], "window_recommended": b["recommended"],
                    "resume_progress": _ratio(b["new"], tr) if tr else None})
    out.sort(key=lambda x: (x["resume_progress"] is None, x["resume_progress"] if x["resume_progress"] is not None else 0))
    return out


def _summ(win, span, jobs):
    """一个窗口的汇总（当前窗口与「上一周期」共用同一算法）。"""
    fn = funnel(win)
    active_days = len({_d(r["record_date"]).isoformat() for r in win})
    weeks = span / 7.0 if span else 1
    open_jobs = [j for j in jobs if (j.get("status") or "open") == "open"]
    t_res = sum((j.get("target_resume_count") or 0) for j in open_jobs)
    t_head = sum((j.get("target_headcount") or 0) for j in open_jobs)
    return {
        "new": fn["new"], "passed": fn["passed"], "recommended": fn["recommended"], "rejected": fn["rejected"],
        "conversion": fn["pass_rate"], "recommend_rate": fn["recommend_rate"], "reject_rate": fn["reject_rate"],
        "span_days": span, "active_days": active_days,
        "resumes_per_day": round(fn["new"] / span, 2) if span else None,
        "resumes_per_active_day": round(fn["new"] / active_days, 2) if active_days else None,
        "recommended_per_week": round(fn["recommended"] / weeks, 2) if weeks else None,
        "target_headcount": t_head, "target_resumes": t_res,
        "resume_target_progress": _ratio(fn["new"], t_res) if t_res else None,
    }


def channel_series(rows, granularity):
    """每个渠道各自的时间序列（供趋势图下钻到单个渠道）。"""
    out = {}
    # Keep the current controlled catalog first, then append historical source
    # labels found in immutable data. Retired labels stay out of new-entry
    # dropdowns without making old activity disappear from manager analytics.
    actual = sorted({str(r.get("channel") or "Unspecified") for r in rows})
    for ch in list(CHANNELS) + [name for name in actual if name not in CHANNELS]:
        out[ch] = timeseries([r for r in rows if r["channel"] == ch], granularity)
    return out


def insights(cur, prev, chans, jp, prev_chans):
    """从汇总里挑 2-4 条最有用的总结/异常，给顶部摘要条。"""
    out = []
    if prev and prev["new"] > 0:
        chg = (cur["new"] - prev["new"]) / prev["new"]
        arrow = "▲" if chg >= 0 else "▼"
        tone = "" if abs(chg) < 0.1 else ("，势头向好" if chg > 0 else "，建议关注")
        out.append("新增简历环比%s %s（%d→%d）%s" % (arrow, _pct(abs(chg)), prev["new"], cur["new"], tone))
    elif cur["new"] > 0 and prev is not None and prev["new"] == 0:
        out.append("新增简历 %d（上一周期无数据，暂无环比）" % cur["new"])
    top = next((c for c in chans if c["new"] > 0), None)
    if top:
        out.append("简历最多：%s（%d 份，占比 %s）" % (top["channel"], top["new"], _pct(top["share"])))
    cand = [c for c in chans if c["new"] >= 5 and c["conversion"] is not None]
    best = max(cand, key=lambda c: c["conversion"], default=None)
    if best:
        out.append("转化最好：%s（%s）" % (best["channel"], _pct(best["conversion"])))
    if prev_chans:
        prevmap = {c["channel"]: c["new"] for c in prev_chans}
        for c in chans:
            if c["new"] == 0 and prevmap.get(c["channel"], 0) >= 5:
                out.append("%s 本期 0 新增（上期 %d 份），注意是否掉线" % (c["channel"], prevmap[c["channel"]]))
                break
    done = [j for j in jp if j["resume_progress"] is not None and j["resume_progress"] >= 1]
    if done:
        out.append("已达目标简历量：" + "、".join(j["title"] for j in done[:3]))
    else:
        slow = next((j for j in jp if j["resume_progress"] is not None), None)
        if slow:
            out.append("进度最慢：%s（%s）" % (slow["title"], _pct(slow["resume_progress"])))
    return out[:4]


def _month_bounds(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    ms = date(y, m, 1)
    me = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return ms, me


def channel_costs_window(costs, dfrom, dto):
    """把「渠道×月」投入按天数比例分摊到窗口，得到每渠道窗口内成本。"""
    out = {}
    for c in costs or []:
        try:
            ms, me = _month_bounds(str(c["ym"]))
        except (ValueError, KeyError, TypeError):
            continue
        lo, hi = max(ms, dfrom), min(me, dto)
        if lo > hi:
            continue
        overlap = (hi - lo).days + 1
        mdays = (me - ms).days + 1
        out[c["channel"]] = out.get(c["channel"], 0.0) + float(c["amount"]) * overlap / mdays
    return out


def _roi_insight(chans):
    cand = [c for c in chans if c.get("cost_per_resume") is not None and c["new"] >= 5]
    if not cand:
        return None
    best = min(cand, key=lambda c: c["cost_per_resume"])
    return "性价比最高：%s（￥%s/份）" % (best["channel"], best["cost_per_resume"])


def analytics(rows, jobs, granularity="day", dfrom=None, dto=None, job_id=None,
              prev_from=None, costs=None, data_space="identity_derived"):
    """把窗口内 rows 汇总成看板数据。website 画图、bot 回文字都从这里取（单一数据源）。
    传入 rows 若覆盖到 prev_from，则一并算「上一周期」环比；传入 costs 则算成本/ROI。"""
    if granularity not in ("day", "week", "month", "year"):
        granularity = "day"
    if dto is None:
        dto = max((_d(r["record_date"]) for r in rows), default=date.today())
    if dfrom is None:
        dfrom = dto - timedelta(days=29)
    jid = int(job_id) if job_id else None

    def _jf(rs):
        return [r for r in rs if jid is None or int(r.get("job_request_id") or 0) == jid]

    win = _jf([r for r in rows if dfrom <= _d(r["record_date"]) <= dto])
    if jid is not None:
        jobs = [j for j in jobs if j["id"] == jid]
    span = (dto - dfrom).days + 1
    ts = timeseries(win, granularity)
    chans = channel_totals(win)
    fn = funnel(win)
    jp = job_progress(win, jobs)
    summary = _summ(win, span, jobs)
    prev_summary = prev_chans = prev_window = None
    if prev_from is not None:
        pf, pt = prev_from, dfrom - timedelta(days=1)
        prevwin = _jf([r for r in rows if pf <= _d(r["record_date"]) <= pt])
        prev_summary = _summ(prevwin, span, jobs)
        prev_chans = channel_totals(prevwin)
        prev_window = {"from": pf.isoformat(), "to": pt.isoformat()}
    # 渠道成本 / ROI（若已录入投入，按天数分摊到窗口）
    cw = channel_costs_window(costs, dfrom, dto)
    has_cost = bool(cw)
    total_cost = 0.0
    for c in chans:
        cost = round(cw.get(c["channel"], 0.0), 2)
        c["cost"] = cost
        c["cost_per_resume"] = round(cost / c["new"], 2) if (cost and c["new"]) else None
        c["cost_per_recommend"] = round(cost / c["recommended"], 2) if (cost and c["recommended"]) else None
        total_cost += cost
    total_cost = round(total_cost, 2)
    summary["total_cost"] = total_cost
    summary["cost_per_resume"] = round(total_cost / summary["new"], 2) if (total_cost and summary["new"]) else None

    cand = [c for c in chans if c["new"] >= 5 and c["conversion"] is not None]
    best = max(cand, key=lambda c: c["conversion"], default=None)
    ins = insights(summary, prev_summary, chans, jp, prev_chans)
    if has_cost:
        roi = _roi_insight(chans)
        if roi:
            ins = ([roi] + ins)[:4]
    is_derived = data_space == "identity_derived"
    return {
        "space": data_space, "data_space": data_space, "timezone": "Asia/Kolkata",
        "granularity": granularity, "window": {"from": dfrom.isoformat(), "to": dto.isoformat()},
        "summary": summary, "prev_summary": prev_summary, "prev_window": prev_window,
        "timeseries": ts, "channel_series": channel_series(win, granularity),
        "channels": chans, "funnel": fn, "jobs": jp, "has_cost": has_cost,
        "best_channel": (best["channel"] if best else None),
        "insights": ins,
        "identity_derived": {
            "available": is_derived,
            "note": (
                "Metrics are derived from one candidate × hiring job row and "
                "immutable intake/stage events."
                if is_derived else
                "Legacy unidentified batch counts are separate and are never "
                "added to identified candidate totals."
            ),
        },
    }


def analytics_text(a):
    """把 analytics() 结果压成一段文字摘要，给 Lark Bot 命令回显（与网站同一份数字）。"""
    gname = {"day": "日", "week": "周", "month": "月", "year": "年"}.get(a["granularity"], a["granularity"])
    s, w = a["summary"], a["window"]
    lines = ["📊 招聘分析（%s ~ %s · 按%s · 人工录入口径 · Asia/Kolkata）" % (w["from"], w["to"], gname),
             "新增 %d ｜ 初筛 %d（%s）｜ 推荐 %d（%s）｜ 拒绝 %d"
             % (s["new"], s["passed"], _pct(s["conversion"]), s["recommended"], _pct(s["recommend_rate"]), s["rejected"]),
             "速度：%s 份/天，推荐 %s 人次/周" % (s["resumes_per_day"], s["recommended_per_week"])]
    if s.get("resume_target_progress") is not None:
        lines.append("目标简历量进度：%d / %d = %s" % (s["new"], s["target_resumes"], _pct(s["resume_target_progress"])))
    top = [c for c in a["channels"][:3] if c["new"]]
    if top:
        lines.append("渠道 Top：" + "；".join("%s %d份/%s" % (c["channel"], c["new"], _pct(c["conversion"])) for c in top))
    if a.get("best_channel"):
        lines.append("转化最好：" + a["best_channel"])
    return "\n".join(lines)


def manager_analytics_text(analytics_payload):
    """Render the identity-derived manager summary with unambiguous labels."""
    summary = analytics_payload["summary"]
    window = analytics_payload["window"]
    lines = [
        "Recruiting Analytics: %s to %s | Asia/Kolkata" %
        (window["from"], window["to"]),
        "New Applications %d | Entered Interview %d (%s) | "
        "Reached Offer %d (%s) | Rejected %d" % (
            summary["new"], summary["passed"], _pct(summary["conversion"]),
            summary["recommended"], _pct(summary["recommend_rate"]),
            summary["rejected"],
        ),
        "Application pace: %s / day | Offer pace: %s / week" % (
            summary["resumes_per_day"], summary["recommended_per_week"],
        ),
    ]
    if summary.get("resume_target_progress") is not None:
        lines.append(
            "Application target progress: %d / %d = %s" % (
                summary["new"], summary["target_resumes"],
                _pct(summary["resume_target_progress"]),
            )
        )
    top = [row for row in analytics_payload["channels"][:3] if row["new"]]
    if top:
        lines.append(
            "Top channels: " + "; ".join(
                "%s %d applications (%s entered Interview)" % (
                    row["channel"], row["new"], _pct(row["conversion"]),
                )
                for row in top
            )
        )
    return "\n".join(lines)


# Keep existing callers stable while replacing the legacy manual-count wording.
analytics_text = manager_analytics_text


_legacy_build_report = build_report


def build_report(rows, target, jobs, window_from=None, window_to=None,
                 data_space="identity_derived"):
    """Build the manager report while preserving the established JSON shape."""
    report = _legacy_build_report(
        rows, target, jobs, window_from, window_to, data_space=data_space,
    )
    overall_payload = report["overall"]
    window = overall_payload["window"]
    lines = [
        "Recruiting Activity Report: %s to %s | Asia/Kolkata" %
        (window["from"], window["to"]),
        "New Applications: %d" % overall_payload["window_new"],
        "Reached Offer: %d" % overall_payload["window_recommended"],
    ]
    if overall_payload.get("resume_progress") is not None:
        lines.append(
            "Application target progress: %d / %d = %s" % (
                overall_payload["window_new"],
                overall_payload["target_resumes"],
                _pct(overall_payload["resume_progress"]),
            )
        )
    if report.get("best_channel"):
        lines.append("Best qualified channel: " + report["best_channel"])
    if report.get("alerts"):
        lines.append("Review: " + "; ".join(report["alerts"][:3]))
    else:
        lines.append("Review: no activity anomaly detected.")
    report["text"] = "\n".join(lines)
    return report
