# Daily recruiting batch state machine

This package replaces the previous Railway application package. It preserves
the TG automation, Channel Analytics, Job Requisitions, and all existing
database records.

## Manager flow

1. Select one or more Open Job Requisitions.
2. Enter the HR names and candidate counts.
3. Click **Start search and create today's table** once.
4. The local Worker searches, freezes the usable cohort, and queues one daily
   publication.
5. The **Today's recruiting batch** card becomes the single source of truth:
   searching, frozen, publishing, action required, or published.
6. When published, the card permanently shows **Open today's recruiting
   table**.

## Failure recovery

If Lark creation succeeds but the Railway success receipt fails, the page
shows **Repair publication (no new search)**. This operation:

- preserves the exact frozen candidates, HR allocation, source catalog, and
  Open Job catalog;
- performs zero scanner calls;
- archives the failed immutable command;
- queues revision + 1 so the local Worker can archive any unused partial Lark
  artifact and publish a clean workbook;
- never deletes candidates, activities, historical workbooks, or search
  evidence.

Clicking the old **Publish today's unified table** action is suppressed while a
daily publication command already exists. This prevents an unsafe retry of the
same revision.

## Rebuild rule

After publication, **Rebuild today's table (old table not submitted)** keeps
the same frozen cohort and allocation. It is for formatting/publication
repair, not for changing candidate count or HR allocation.

Changing the candidate target, HR roster, or selected jobs is a different
operation: a replacement search must produce a new frozen cohort and a new
daily revision. It must never overwrite a submitted workbook.

