---
name: worktrees-and-stacked-prs
description: "Do code work in git worktrees, never the main checkout; split multi-part work into stacked PRs rather than parallel ones off main"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3646888a-9cb4-4071-9b7b-e4d828b42a81
  modified: 2026-09-28T05:18:24.628Z
---

Always do code changes in a git worktree, not in the main checkout. When work splits into several PRs, prefer a stack (each branch based on the previous one) over independent branches off `main`.

**Why:** the user said so directly (2026-09-28), interrupting a plan to fan out five parallel branches off main for the club_server-review SDK tasks.

**How to apply:** create one worktree per branch; base branch N+1 on branch N; each PR targets the previous branch, and they merge bottom-up. Build the stack sequentially, not with parallel agents that would each branch off main.
