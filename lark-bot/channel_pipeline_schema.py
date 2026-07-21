"""Canonical Channel Analytics workbook/Base contract.

Both the website XLSX path and the Recruitment Bot Lark Base consume this
module.  Keeping one field contract prevents the two HR surfaces from drifting.
"""
from __future__ import annotations


SCHEMA_VERSION = "channel-pipeline-v5"
BASE_NAME = "Channel Analytics - Live"
PIPELINE_TABLE_NAME = "Candidate Pipeline"
PIPELINE_VIEW_NAME = "Pipeline"
MANUAL_TABLE_NAME = "Manual Batch Counts (Special Cases)"
MANUAL_VIEW_NAME = "Batch Counts"
EXPORT_FILENAME_PREFIX = "ChannelAnalytics"

ENTRY_DATE = "Entry Date"
STAGE_STARTED_ON = "Stage Started On"
OTHER_SOURCE_DETAIL = "Other Source Detail (only when Source Channel = Other)"


# ``surfaces`` separates the business contract from transport-only identity.
# Row Ref is an XLSX-only hidden anti-guessing identifier; Lark already has a
# signed record_id supplied by the platform.
PIPELINE_COLUMNS = (
    {
        "key": "name", "header": "Candidate", "kind": "text",
        "aliases": ("候选人",), "lock_existing": True,
    },
    {
        "key": "record_date", "header": ENTRY_DATE, "kind": "date",
        "aliases": ("Date", "日期", "入库日期"), "system": True,
    },
    {
        "key": "channel", "header": "Source Channel", "kind": "choice",
        "aliases": ("Channel", "招聘渠道", "渠道", "来源渠道"),
    },
    {
        "key": "source_detail", "header": OTHER_SOURCE_DETAIL, "kind": "text",
        "aliases": (
            "Other Source Detail (required if Other)",
            "Other Source (if Other)", "Source Detail", "其他来源",
            "来源详情", "其他来源说明", "其他来源说明（选择 Other 时填写）",
            "其他来源说明（选 Other 时必填）",
        ),
    },
    {
        "key": "job", "header": "Job", "kind": "choice",
        "aliases": ("职位", "关联职位"),
    },
    {
        "key": "status", "header": "Current Stage", "kind": "choice",
        "aliases": ("Status", "状态", "招聘状态", "阶段"),
    },
    {
        "key": "stage_date", "header": STAGE_STARTED_ON, "kind": "date",
        "aliases": ("Stage Date", "Stage Date（系统自动）", "阶段日期"),
        "system": True,
    },
    {
        "key": "filled_by", "header": "HR Owner", "kind": "text",
        "aliases": ("填写人",),
    },
    {
        "key": "rejection_reason", "header": "Rejection Reason", "kind": "text",
        "aliases": ("拒绝原因",),
    },
    {
        "key": "note", "header": "Note", "kind": "text",
        "aliases": ("备注",),
    },
    {
        "key": "cand_id", "header": "System ID", "kind": "text",
        "aliases": ("记录ID", "系统ID"), "system": True, "hidden": True,
    },
    {
        "key": "row_ref", "header": "Row Ref", "kind": "text",
        "aliases": (), "system": True, "hidden": True, "surfaces": ("xlsx",),
    },
)


MANUAL_COLUMNS = (
    {"key": "record_date", "header": "Date", "kind": "date", "aliases": ("日期",)},
    {"key": "channel", "header": "Source Channel", "kind": "choice",
     "aliases": ("招聘渠道", "渠道")},
    {"key": "source_detail", "header": OTHER_SOURCE_DETAIL, "kind": "text",
     "aliases": ("Source Detail", "其他来源说明（选择 Other 时填写）", "其他来源说明（选 Other 时必填）")},
    {"key": "job", "header": "Job", "kind": "choice", "aliases": ("关联职位", "职位")},
    {"key": "new_resumes", "header": "New Resumes", "kind": "int", "aliases": ("今日新增简历数",)},
    {"key": "passed_screening", "header": "Passed Screening", "kind": "int", "aliases": ("初筛通过数",)},
    {"key": "recommended", "header": "Recommended for Interview", "kind": "int", "aliases": ("已推荐面试数",)},
    {"key": "rejected", "header": "Rejected", "kind": "int", "aliases": ("已拒绝数",)},
    {"key": "note", "header": "Note", "kind": "text", "aliases": ("备注",)},
    {"key": "filled_by", "header": "HR Owner", "kind": "text", "aliases": ("填写人",)},
)


def columns_for(surface: str):
    """Return ordered columns visible to a transport."""
    return tuple(
        column for column in PIPELINE_COLUMNS
        if surface in column.get("surfaces", ("xlsx", "lark"))
    )


def field_names_with_aliases(surface: str = "lark"):
    """Names read during migrations; canonical names always win on write."""
    names = []
    for column in columns_for(surface):
        for name in (column["header"], *column.get("aliases", ())):
            if name not in names:
                names.append(name)
    return tuple(names)


def filename_for(day: str):
    """Daily archive filename; dates stay ISO in cells and compact in names."""
    return "%s_%s.xlsx" % (EXPORT_FILENAME_PREFIX, str(day or "").replace("-", ""))
