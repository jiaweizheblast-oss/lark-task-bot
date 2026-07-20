"""
招聘渠道表格的「生成」与「解析」。
- build_template_xlsx: 生成当天空表（渠道/职位下拉、日期+填写人预填），发给 HR 填。
- parse_sheet: 解析 HR 交回的 .xlsx / .csv，按表头映射、校验、跳过空行/全零行，
  产出可入库的行（上层再 upsert 进 channel_daily）。

网站和机器人两个入口都调这两个函数，所以数据天然互通。
"""
import csv
import io
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from channel_report import CHANNELS

HEADERS = ["日期", "招聘渠道", "关联职位", "今日新增简历数", "初筛通过数",
           "已推荐面试数", "已拒绝数", "备注", "填写人"]

# 表头 -> 内部字段（容错：接受几种常见写法）
HEADER_MAP = {
    "日期": "record_date",
    "招聘渠道": "channel", "渠道": "channel",
    "关联职位": "job", "职位": "job",
    "今日新增简历数": "new_resumes", "新增简历数": "new_resumes", "新增": "new_resumes",
    "初筛通过数": "passed_screening", "初筛通过": "passed_screening", "初筛": "passed_screening",
    "已推荐面试数": "recommended", "已推荐": "recommended", "推荐": "recommended",
    "已拒绝数": "rejected", "已拒绝": "rejected", "拒绝": "rejected",
    "备注": "note",
    "填写人": "filled_by",
}


# ---------------- 生成模板 ----------------
def build_template_xlsx(jobs, day, by=""):
    """jobs: [{'id','title',...}] ; day: 'YYYY-MM-DD' ; by: 预填的填写人。返回 xlsx 字节。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "每日渠道录入"
    ws.append(HEADERS)
    head_fill = PatternFill("solid", fgColor="EEF1F5")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center")

    titles = [j["title"] for j in jobs]

    # 隐藏引用表，放职位下拉项
    refs = wb.create_sheet("_refs")
    for i, t in enumerate(titles, 1):
        refs.cell(row=i, column=1, value=t)
    refs.sheet_state = "hidden"

    # 下拉：渠道用内联列表；职位用引用区间（从源头防打错）
    dv_ch = DataValidation(type="list", formula1='"%s"' % ",".join(CHANNELS), allow_blank=True)
    ws.add_data_validation(dv_ch)
    dv_job = None
    if titles:
        dv_job = DataValidation(type="list", formula1="_refs!$A$1:$A$%d" % len(titles), allow_blank=True)
        ws.add_data_validation(dv_job)

    # 预置「职位 × 渠道」行：日期/渠道/职位/填写人填好，数字给 0，HR 只改数字
    r = 2
    for j in jobs:
        for ch in CHANNELS:
            ws.cell(row=r, column=1, value=day)
            ws.cell(row=r, column=2, value=ch)
            ws.cell(row=r, column=3, value=j["title"])
            for col in (4, 5, 6, 7):
                ws.cell(row=r, column=col, value=0)
            ws.cell(row=r, column=9, value=by)
            r += 1
    # 再留一批空行（日期/填写人预填），HR 想加行也带下拉
    for rr in range(r, r + 40):
        ws.cell(row=rr, column=1, value=day)
        ws.cell(row=rr, column=9, value=by)
    last = r + 40
    dv_ch.add("B2:B%d" % last)
    if dv_job:
        dv_job.add("C2:C%d" % last)

    for i, w in enumerate([12, 14, 20, 16, 12, 14, 12, 22, 12], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ---------------- 解析上传 ----------------
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
    """把 xlsx/csv 读成二维列表（含表头行）。"""
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


def parse_sheet(data, filename, jobs, default_by="", default_date=None):
    """返回 {rows:[可入库行], skipped:int, errors:[逐行问题]}。"""
    title2id = {_s(j["title"]): j["id"] for j in jobs}
    table = _table(data, filename)
    if not table:
        return {"rows": [], "skipped": 0, "errors": ["文件是空的"]}

    header = [_s(x) for x in table[0]]
    idx = {}
    for i, h in enumerate(header):
        key = HEADER_MAP.get(h)
        if key and key not in idx:
            idx[key] = i
    if "channel" not in idx or "job" not in idx:
        return {"rows": [], "skipped": 0,
                "errors": ["表头不对，找不到「招聘渠道 / 关联职位」列。请用系统生成的模板填写。"]}

    def g(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return None
        return row[i]

    rows, errors, skipped = [], [], 0
    for ln, row in enumerate(table[1:], start=2):
        if not any(_s(c) for c in row):
            continue
        ch = _s(g(row, "channel"))
        jobt = _s(g(row, "job"))
        nw = _as_int(g(row, "new_resumes"))
        ps = _as_int(g(row, "passed_screening"))
        rc = _as_int(g(row, "recommended"))
        rj = _as_int(g(row, "rejected"))
        note = _s(g(row, "note"))
        by = _s(g(row, "filled_by")) or default_by
        rd = (_s(g(row, "record_date")) or (default_date or ""))[:10]

        # 未填的预置行（无渠道无职位，或全 0 且无备注）→ 跳过，不污染库
        if not ch and not jobt:
            skipped += 1
            continue
        if nw == 0 and ps == 0 and rc == 0 and rj == 0 and not note:
            skipped += 1
            continue
        if ch not in CHANNELS:
            errors.append("第%d行：渠道「%s」不在预设项" % (ln, ch))
            continue
        jid = title2id.get(jobt)
        if not jid:
            errors.append("第%d行：职位「%s」未找到（请用下拉选）" % (ln, jobt))
            continue
        if not rd:
            errors.append("第%d行：缺日期" % ln)
            continue
        neg = [lab for lab, v in (("新增", nw), ("初筛", ps), ("推荐", rc), ("拒绝", rj)) if v < 0]
        if neg:
            errors.append("第%d行：%s 不能为负" % (ln, "/".join(neg)))
            continue
        rows.append({"record_date": rd, "channel": ch, "job_request_id": jid, "filled_by": by,
                     "new_resumes": nw, "passed_screening": ps, "recommended": rc,
                     "rejected": rj, "note": note})
    return {"rows": rows, "skipped": skipped, "errors": errors}
