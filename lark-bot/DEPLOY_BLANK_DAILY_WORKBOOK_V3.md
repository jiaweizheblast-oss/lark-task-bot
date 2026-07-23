# Daily Recruiting Workbook Without Search

This release adds a manager workflow that creates the current Asia/Kolkata
recruiting workbook even when no Talent Discovery scan ran that day.

## Manager workflow

1. Keep at least one Operational Job Requisition in `Open`.
2. Enter the HR names in Talent Discovery. Candidate counts may be blank.
3. Click **不搜索，创建/打开今日表**.
4. Keep the local Windows worker running.
5. Click the same button again after publication to open the Lark workbook.

The signed publication task contains the HR roster, current Open Job
Requisitions, and the number of manual rows per HR. It contains zero sourcing
cohorts and does not create candidates, matches, review tasks, daily batches,
daily tasks, or contact activities.

The workbook contains one sheet per HR, 30 blank manual-entry rows per HR by
default, and a Recruiting Overview with seven charts. If the unpublished task
failed, the website offers a safe reset before rebuilding it; published Lark
workbooks and candidate history cannot be reset through this path.

## Compatibility

The worker accepts both the existing v2 searched-cohort publication tasks and
the new v3 tasks. Existing search-and-publish behavior is unchanged.
