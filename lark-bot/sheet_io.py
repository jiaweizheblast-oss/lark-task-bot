"""
通用表格引擎：一套「生成 / 解析 / 防重复」通吃两种 HR 表。

- 列定义参数化（column spec）→ 同一个 build_xlsx / parse_rows 引擎驱动：
    · 渠道汇总表（第二个程序，现有）：一行一个渠道，数字型
    · 候选人联系表（第一个程序，爬取数据）：一行一个候选人，带「联系状态」下拉
- 网站和 Lark 机器人都调这里，数据天然互通。
- 本模块只负责「表格机制」：生成、解析、字段约束、防重复上传。
  它不写候选人真相层（Candidate / ContactActivity 等由核心 service 负责）——
  候选人表的解析只产出结构化行，交给上层，不在这里落库。

公共 API（保持向后兼容，bot.py 现有调用不用改）：
    build_template_xlsx(jobs, day, by)                          -> 渠道空表字节
    parse_sheet(data, filename, jobs, default_by, default_date) -> {rows, skipped, errors}
新增（候选人表，机制就绪，等第一个程序的候选人数据接入）：
    build_candidate_template_xlsx(jobs, day, candidates, sources)
    parse_candidate_sheet(data, filename, jobs, sources)        -> {rows, skipped, errors}
"""
import csv
import io
from datetime import date
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from channel_report import CHANNELS

# 候选人联系表用到的下拉
CANDIDATE_STATUS = ["未联系", "已联系", "无法联系", "不合适"]
CANDIDATE_SOURCES = ["RecruitEm", "领英公开", "GitHub", "StackOverflow"] + CHANNELS


# ---------------- 列定义 ----------------
def _col(key, header, kind="text", choices=None, aliases=None, minv=None):
    """kind: text / int / date / choice。choices 给 choice 列的下拉项；minv 给 int 列下限。"""
    return {"key": key, "header": header, "kind": kind,
            "choices": choices, "aliases": aliases or [], "minv": minv}


def channel_columns(job_titles):
    return [
        _col("record_date", "日期", "date"),
        _col("channel", "招聘渠道", "choice", choices=CHANNELS, aliases=["渠道"]),
        _col("job", "关联职位", "choice", choices=job_titles, aliases=["职位"]),
        _col("new_resumes", "今日新增简历数", "int", aliases=["新增", "新增简历数"], minv=0),
        _col("passed_screening", "初筛通过数", "int", aliases=["初筛通过", "初筛"], minv=0),
        _col("recommended", "已推荐面试数", "int", aliases=["已推荐", "推荐"], minv=0),
        _col("rejected", "已拒绝数", "int", aliases=["已拒绝", "拒绝"], minv=0),
        _col("note", "备注", "text"),
        _col("filled_by", "填写人", "text"),
    ]


def candidate_columns(job_titles, sources=None):
    sources = sources or CANDIDATE_SOURCES
    return [
        _col("candidate_ref", "候选人ID", "text"),          # 系统预填的稳定引用（可空）
        _col("name", "候选人", "text"),                      # 系统预填
        _col("source", "来源渠道", "choice", choices=sources),
        _col("job", "关联职位", "choice", choices=job_titles),
        _col("region", "地区", "text"),
        _col("experience", "经验", "choice", choices=["有", "无", "未知"]),
        _col("intention", "求职意向", "choice", choices=["有", "无", "未知"]),
        _col("contact_status", "联系状态", "choice", choices=CANDIDATE_STATUS),  # HR 拉下拉标
        _col("contact_note", "联系方式/备注", "text"),
        _col("contact_date", "联系日期", "date"),
    ]


# ---------------- 生成（通用） ----------------
def build_xlsx(columns, prefill_rows=None, sheet_title="录入", extra_blank=0, blank_defaults=None):
    """按列定义生成 xlsx：表头 + choice 列下拉 + 预置行 + 若干带默认值的空行。返回字节。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append([c["header"] for c in columns])
    head_fill = PatternFill("solid", fgColor="EEF1F5")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center")

    # 所有 choice 列的下拉项放隐藏引用表，避免内联列表 255 字符限制
    refs = wb.create_sheet("_refs")
    refs.sheet_state = "hidden"
    dv_map = {}
    ref_col = 1
    for ci, c in enumerate(columns):
        if c["kind"] == "choice" and c["choices"]:
            for i, opt in enumerate(c["choices"], 1):
                refs.cell(row=i, column=ref_col, value=opt)
            letter = get_column_letter(ref_col)
            dv = DataValidation(type="list",
                                formula1="_refs!$%s$1:$%s$%d" % (letter, letter, len(c["choices"])),
                                allow_blank=True)
            ws.add_data_validation(dv)
            dv_map[ci] = dv
            ref_col += 1

    r = 2
    for row in (prefill_rows or []):
        for ci, c in enumerate(columns):
            v = row.get(c["key"])
            if v is not None and v != "":
                ws.cell(row=r, column=ci + 1, value=v)
        r += 1
    for _ in range(extra_blank):
        for ci, c in enumerate(columns):
            v = (blank_defaults or {}).get(c["key"])
            if v is not None:
                ws.cell(row=r, column=ci + 1, value=v)
        r += 1

    last = max(r - 1, 2)
    for ci, dv in dv_map.items():
        letter = get_column_letter(ci + 1)
        dv.add("%s2:%s%d" % (letter, letter, last))

    for i, c in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = 20 if c["kind"] == "text" else 15
    ws.freeze_panes = "A2"

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ---------------- 解析（通用） ----------------
def _s(v):
    return ("" if v is None else str(v)).strip()


def _as_int(v):
    s = _s(v)
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _table(data, filename):
    """xlsx/csv → 二维列表（含表头行）。"""
    if (filename or "").lower().endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [row for row in csv.reader(io.StringIO(text))]
    wb = load_workbook(io.BytesIO(data), data_only=True)
    ws = None
    for name in wb.sheetnames:
        if name != "_refs" and wb[name].sheet_state == "visible":
            ws = wb[name]
            break
    if ws is None:
        ws = wb[wb.sheetnames[0]]
    return [[c.value for c in row] for row in ws.iter_rows()]


def parse_rows(columns, data, filename, required=None, skip=None):
    """通用解析：按列定义映射表头、按 kind 转换、choice/min 校验、跳过空/预置行。
    返回 {rows:[{key:值, __line__}], skipped:int, errors:[逐行问题]}。"""
    table = _table(data, filename)
    if not table:
        return {"rows": [], "skipped": 0, "errors": ["文件是空的"]}

    header = [_s(x) for x in table[0]]
    by_key = {}
    for i, h in enumerate(header):
        for c in columns:
            if h == c["header"] or h in c["aliases"]:
                by_key.setdefault(c["key"], i)
                break
    missing = [k for k in (required or []) if k not in by_key]
    if missing:
        miss_h = [next(c["header"] for c in columns if c["key"] == k) for k in missing]
        return {"rows": [], "skipped": 0,
                "errors": ["表头缺少列：%s（请用系统生成的模板）" % "、".join(miss_h)]}

    col_by_key = {c["key"]: c for c in columns}
    rows, errors, skipped = [], [], 0
    for ln, raw in enumerate(table[1:], start=2):
        if not any(_s(x) for x in raw):
            continue
        rec = {}
        for key, i in by_key.items():
            val = raw[i] if i < len(raw) else None
            rec[key] = _as_int(val) if col_by_key[key]["kind"] == "int" else _s(val)
        if skip and skip(rec):
            skipped += 1
            continue
        errs = []
        for key, c in col_by_key.items():
            if key not in rec:
                continue
            if c["kind"] == "choice" and rec[key] and c["choices"] and rec[key] not in c["choices"]:
                errs.append("第%d行：%s「%s」不在预设项" % (ln, c["header"], rec[key]))
            if c["kind"] == "int" and c["minv"] is not None and rec[key] < c["minv"]:
                errs.append("第%d行：%s 不能小于 %d" % (ln, c["header"], c["minv"]))
        if errs:
            errors += errs
            continue
        rec["__line__"] = ln
        rows.append(rec)
    return {"rows": rows, "skipped": skipped, "errors": errors}


# ---------------- 渠道汇总表（第二个程序，向后兼容） ----------------
def build_template_xlsx(jobs, day, by=""):
    cols = channel_columns([j["title"] for j in jobs])
    prefill = []
    for j in jobs:
        for ch in CHANNELS:
            prefill.append({"record_date": day, "channel": ch, "job": j["title"],
                            "new_resumes": 0, "passed_screening": 0, "recommended": 0,
                            "rejected": 0, "note": "", "filled_by": by})
    return build_xlsx(cols, prefill_rows=prefill, sheet_title="每日渠道录入",
                      extra_blank=40, blank_defaults={"record_date": day, "filled_by": by})


def parse_sheet(data, filename, jobs, default_by="", default_date=None):
    title2id = {_s(j["title"]): j["id"] for j in jobs}
    cols = channel_columns([j["title"] for j in jobs])

    def skip(r):
        if not r.get("channel") and not r.get("job"):
            return True
        if (r.get("new_resumes", 0) == 0 and r.get("passed_screening", 0) == 0
                and r.get("recommended", 0) == 0 and r.get("rejected", 0) == 0
                and not r.get("note")):
            return True
        return False

    res = parse_rows(cols, data, filename, required=["channel", "job"], skip=skip)
    out, errors = [], list(res["errors"])
    for r in res["rows"]:
        jid = title2id.get(r.get("job"))
        if not jid:
            errors.append("第%d行：职位「%s」未找到" % (r.get("__line__", 0), r.get("job", "")))
            continue
        rd = (r.get("record_date") or (default_date or "")).strip()[:10]
        if not rd:
            errors.append("第%d行：缺日期" % r.get("__line__", 0))
            continue
        try:
            date.fromisoformat(rd)
        except ValueError:
            errors.append("第%d行：日期格式非法「%s」（应为 YYYY-MM-DD）" % (r.get("__line__", 0), rd))
            continue
        out.append({"record_date": rd, "channel": r["channel"], "job_request_id": jid,
                    "filled_by": r.get("filled_by") or default_by,
                    "new_resumes": r["new_resumes"], "passed_screening": r["passed_screening"],
                    "recommended": r["recommended"], "rejected": r["rejected"],
                    "note": r.get("note", "")})
    return {"rows": out, "skipped": res["skipped"], "errors": errors}


# ---------------- 候选人联系表（第一个程序，机制就绪；不落候选人库） ----------------
def build_candidate_template_xlsx(jobs, day, candidates=None, sources=None):
    """candidates: 系统爬到的候选人 [{candidate_ref,name,source,job,region,experience,intention}]（预填）。
    HR 只需在「联系状态」下拉里标已联系/无法联系等，并可补充联系方式/日期。"""
    cols = candidate_columns([j["title"] for j in jobs], sources)
    prefill = []
    for c in (candidates or []):
        prefill.append({k: c.get(k) for k in
                        ("candidate_ref", "name", "source", "job", "region", "experience", "intention")})
    return build_xlsx(cols, prefill_rows=prefill, sheet_title="候选人联系表",
                      extra_blank=(5 if candidates else 20), blank_defaults={})


def parse_candidate_sheet(data, filename, jobs, sources=None):
    """解析 HR 交回的候选人表 → 结构化行（含候选人身份 + HR 标的联系状态/备注/日期）。
    只产出行，不写库；上层核心 service 负责更新候选人状态并追加不可变 ContactActivity。"""
    cols = candidate_columns([j["title"] for j in jobs], sources)

    def skip(r):
        return not (r.get("candidate_ref") or r.get("name"))

    return parse_rows(cols, data, filename, required=["name"], skip=skip)
