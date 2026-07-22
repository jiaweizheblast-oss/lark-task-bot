"""Canonical daily recruiting table contract shared by Lark and Nexus.

Channel Analytics owns no separate spreadsheet. Every metric is derived from
one candidate-by-requisition row in the daily recruiting table.
"""
from __future__ import annotations


SCHEMA_VERSION = "daily-recruiting-table-v20"
BASE_NAME_PREFIX = "Recruiting"
PIPELINE_TABLE_NAME_PREFIX = "Recruiting"
PIPELINE_VIEW_NAME = "All Candidates"

# Compatibility constants for older modules. They must never create a second
# table in the v20 workflow.
BASE_NAME = BASE_NAME_PREFIX
PIPELINE_TABLE_NAME = PIPELINE_TABLE_NAME_PREFIX
MANUAL_TABLE_NAME = "Unidentified Batch Counts (retired)"
MANUAL_VIEW_NAME = "Retired"
MANUAL_COLUMNS = ()
ENTRY_DATE = "Date"
STAGE_STARTED_ON = "Stage Started On"

OTHER_SOURCE_DETAIL = (
    "Other Source Details (Required only when Source Channel is Other)"
)

PIPELINE_COLUMNS = (
    {
        "key": "record_date",
        "header": "Date",
        "kind": "created_time",
        "system": True,
        "aliases": ("Entry Date", "Apply Date"),
    },
    {
        "key": "name",
        "header": "Candidate Name",
        "kind": "text",
        "aliases": ("Candidate",),
        "lock_existing": True,
    },
    {
        "key": "candidate_url",
        "header": "Candidate URL",
        "kind": "text",
        "aliases": ("Profile URL", "LinkedIn URL"),
        "lock_existing": True,
    },
    {
        "key": "channel",
        "header": "Source Channel",
        "kind": "choice",
        "aliases": ("Channel",),
        "lock_existing": True,
    },
    {
        "key": "source_detail",
        "header": OTHER_SOURCE_DETAIL,
        "kind": "text",
        "lock_existing": True,
        "aliases": (
            "Other Source Details",
            "Other Source Detail",
            "Other Source Detail (required if Other)",
            "Source Detail",
            "其他来源说明（选择 Other 时填写）",
            "其他来源说明（选 Other 时必填）",
        ),
    },
    {
        "key": "job",
        "header": "Hiring Job",
        "kind": "choice",
        "aliases": ("Job",),
        "lock_existing": True,
    },
    {
        "key": "filled_by",
        "header": "Assigned HR",
        "kind": "choice",
        "aliases": ("HR Owner",),
    },
    {
        "key": "status",
        "header": "Status",
        "kind": "choice",
        "aliases": ("Current Stage",),
    },
    {
        "key": "cv_url",
        "header": "CV",
        "kind": "text",
        "aliases": ("CV URL", "Resume URL"),
    },
)


def columns_for(surface: str = "lark"):
    del surface
    return PIPELINE_COLUMNS


def field_names_with_aliases(surface: str = "lark"):
    del surface
    names = []
    for column in PIPELINE_COLUMNS:
        for name in (column["header"], *column.get("aliases", ())):
            if name not in names:
                names.append(name)
    return tuple(names)


def compact_day(day: str) -> str:
    return str(day or "").replace("-", "")


def base_name_for(day: str) -> str:
    return f"{BASE_NAME_PREFIX}{compact_day(day)}"


def table_name_for(day: str) -> str:
    return f"{PIPELINE_TABLE_NAME_PREFIX}{compact_day(day)}"
