# One-click daily recruiting flow

This package simplifies the manager workflow without changing candidate truth,
published Lark workbooks, or historical activity.

## Manager workflow

1. Keep the local Windows Talent Worker running.
2. In Talent Discovery, select one Open Job Requisition and enter the HR
   allocation.
3. Click **开始搜索并生成今日表** once.
4. The page advances automatically through:
   **waiting for worker → searching/freezing → publishing → ready**.
5. Open the workbook with **打开今日招聘表**, or ask the Recruitment Bot for
   today's recruiting sheet.

For a manual-only day, enter the HR names and click
**不搜索，创建/打开今日表**.

## Safety and idempotency

- There is only one recruiting workbook per Asia/Kolkata business date.
- Repeated clicks resume or open the same daily flow.
- A second conflicting search is rejected while today's flow is active.
- Search tasks from an earlier business date are not shown as today's state.
- A frozen result waiting for publication cannot be replaced by another search.
- Deploying this package does not recreate or modify an already-published
  workbook for the current business date.

## Verification

- All 14 bundled offline tests pass.
- Python compilation passes.
- The inline JavaScript in `panel.html` passes a syntax check.
