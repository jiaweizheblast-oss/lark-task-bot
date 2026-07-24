# Failed daily publication resume

This build fixes the manager-facing error:

`today already has a different immutable publication batch`

When today's immutable publication exists in `failed` state, clicking publish
again now:

- resumes the same `publication_id`;
- preserves the original immutable payload and frozen search cohorts;
- clears only the failed worker lease/error state;
- queues the same publication for the local Worker;
- does not run another search;
- does not delete candidates, sources, matches, activities, or Lark files.

Deploy the complete package to Railway. After the deployment is active, refresh
Talent Discovery and click **Publish today's unified workbook** once. The Worker
will claim the original failed publication and continue it.
