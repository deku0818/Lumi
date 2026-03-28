---
description: |
  Use this tool when you have finished planning and your plan is ready for user review. This signals the end of the planning phase and presents your plan to the user for approval.

  The user can either:
  - **Approve**: You exit plan mode and begin implementation
  - **Reject**: You stay in plan mode and continue refining the plan

  ## When to Use
  - After writing your final plan to the plan file
  - When you have no remaining questions about the approach
  - As the last action of your planning turn

  ## When NOT to Use
  - If you still have unresolved questions — use `ask` first
  - For research-only tasks that don't require implementation planning

  **Important:** Do NOT use `ask` to request plan approval. That is exactly what this tool does.
approved: |
  Plan approved by user. You should now exit plan mode and begin implementation according to the plan.

  <system-reminder>
  Plan mode has ended. The user has approved your plan. You may now make edits, run tools, and take actions to implement the plan. The plan file remains available for reference.
  </system-reminder>
rejected: |
  Plan not approved. The user wants you to continue refining the plan. Stay in plan mode, review the feedback, and update your plan accordingly.

  Remember: DO NOT write or edit any project files yet. This is still a read-only exploration and planning phase.
---
