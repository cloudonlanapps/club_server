# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# club_server — FastAPI backend for club management

## Where things are documented

- **Running the server, configuration:** `README.md`. Everything is
  environment variables. The one-command dev stack and the isolated test
  stacks live in `native_deploy`; this repo carries only `start_db.sh`.
- **Running and writing tests:** `tests/README.md` (procedure) and
  `tests/CLAUDE.md` (agent context, including the rule to delegate test runs
  to the `test-runner` agent). Do not run pytest from the main session.
- **Workflow, labels, PRs:** `CONTRIBUTING.md`, summarised below.
- **Coding and TDD rules:** `docs/conventions/` (`coding_rules.md`, `tdd_rules.md`).
- **Requirements:** `docs/*_requirements.md`; see "Requirement markers" below.

**Ports:** the conventional dev server is 8101 with its cluster on 5437. Test runs each get their
own cluster in 5440–5499; tests drop all tables per function, so never point
them at 5437.

**Dependency management:** [`uv`](https://github.com/astral-sh/uv), not
`pip`/`poetry`. Add a dep with `uv add <pkg>` (`--dev` for tooling). Run
anything via `uv run --frozen <cmd>`; `--frozen` stops `uv` from rewriting
`uv.lock` as a side effect (#310). Only `uv add` / `uv lock` change the
lockfile, which is committed.

**API docs:** with a dev server running (see README), Swagger UI is at
`http://localhost:8101/docs` and the schema at `/openapi.json`. Use these, not
source-grep, when verifying endpoint shape for SDK-facing work.

## Architecture

The application follows a layered architecture: **Routers → Services → DB Models**, all under `club_server/`.

- `main.py` — FastAPI app setup, CORS, lifespan (background worker start/stop), static file serving
- `config.py` — `pydantic_settings.BaseSettings` loaded from env vars (DATABASE_URL, SECRET_KEY, UPLOAD_DIR, STATIC_DIR are required)
- `dependencies.py` — FastAPI dependency injection: DB sessions, auth (`get_current_user`, `get_current_active_user`), role guards (`require_role`, `require_admin`, `require_admin_or_coach`, `require_super_admin`)
- `routers/` — FastAPI route handlers, one file per domain (auth, users, groups, events, etc.). All mounted under `/v1`
- `services/` — Business logic layer, one file per domain. Called by routers, operate on DB models. Events are split by type (#389): `event.py` holds what every type shares, `programme.py` / `camp.py` / `oneoff.py` each hold one type's rules, and `event_types.py` is the **only** place an event's type is compared (a test enforces this). An event's timetable is a sequence of `EventSchedule` rows (`schedule.py`, `docs/event_schedule_model.md`); `lifecycle.py` is the one implementation of "is this occurrence cancelled" and "does this event still have a live occurrence"; `conflict_gates.py` is the one conflict engine (venue / organizer / coach / member gates)
- `schemas/` — Pydantic request/response models
- `db/models/` — SQLAlchemy async ORM models (asyncpg + SQLAlchemy asyncio)
- `db/engine.py` — Async engine + session factory
- `db/base.py` — Declarative base for all models
- `worker.py` — Background worker (started/stopped via app lifespan)
- `bootstrap.py` — CLI script (`club_bootstrap`) to create the initial super admin user

### Key domain entities

Users (with roles: admin/coach/member/guardian), Groups, Events and their Schedules, Occurrences, Enrollments, Attendance, Venues, Notifications, Media (uploads plus per-entity links and tags), Credit accounts, Evaluations.

### Optional modules

Two modules are switched per deployment. Both default to off, and while off
every one of their routes stays registered and answers 503, so the published
API does not vary with configuration:

- **Credit system** — `CREDIT_SYSTEM_ENABLED`; `docs/credit_system_requirements.md`.
- **Evaluations** — `EVALUATIONS_ENABLED`; `docs/evaluation_requirements.md`.

### Requirement markers in tests

Tests that are the evidence for a rule in `docs/*_requirements.md` carry `@pytest.mark.requirement("doc:Rn")` (e.g. `programme:R3`). `tests/test_requirement_coverage.py` parses the documents and fails when an in-scope rule has no such test, or a marker names a rule no document defines. When you add or change a rule, add or move the marker with it (#385).

### Auth model

JWT bearer tokens via `python-jose`. Roles stored as JSON in the `User.roles` column. Super admin flag (`is_super_admin`) bypasses all role checks.

---

## Project status

This project is **stable**. All code changes — bugs, features, refactors, tooling — must be tracked through GitHub Issues and merged via Pull Requests.

The full contributor workflow is documented in `CONTRIBUTING.md`. Read it before making any code change.

### Exception for process files

Edits to `CLAUDE.md`, to `CONTRIBUTING.md`, or to files under `docs/`, do not require an issue. Commit directly to `main`. If you are on a feature branch, switch to `main`, commit, push, then cherry-pick into the feature branch.

---

## The issue-first rule

> **No code change without a GitHub issue. No merge without a PR linked to that issue.**

When the user asks you to make a change:

1. **Check if an issue exists.** If they reference `#42`, run `gh issue view 42` first.
2. **If no issue exists, propose creating one.** Don't silently start coding. Suggest the appropriate template (Bug / Feature / Task) and draft a title + body for the user to approve.
3. **Only after the issue exists** should you start branching and implementing.

Exceptions: trivial in-session exploration (reading code, answering questions, running tests), and edits to process files covered above (`CLAUDE.md`, `CONTRIBUTING.md`, `docs/`). Anything else that produces a commit needs an issue.

---

## Branch naming

Branches must include the issue number and a short slug:

```
<type>/<issue-number>-<short-slug>
```

Examples:
- `bug/42-login-redirect-loop`
- `feature/57-csv-export`
- `task/61-bump-fastapi`

**Always branch from `main`. Never from `release`.**

---

## Pull request rules

- **Target branch:** `main`. Never open a PR against `release`.
- **Title:** clear, < 70 characters.
- **Body:** must contain `Fixes #N` (or `Closes #N`) so GitHub auto-links and auto-closes the issue.
- **One issue per PR** unless issues are tightly coupled.
- **Never merge.** Merging is a human decision. After opening the PR, stop and let the user review.
- **Never push to `release`.** That branch is human-only — it is updated by promoting `main` → `release` on a separate cadence.

### Merge strategy

When the user authorizes a merge, the **choice between `--squash` and `--rebase` is a human decision** — Claude must always pause and ask, even if squash is the obvious default. Do not assume; the right answer depends on whether the PR's individual commits are worth preserving on the target branch (rebase) or are just work-in-progress noise (squash). Confirm before running the merge.

```bash
gh pr merge <num> --squash    # collapse to one commit (typical default)
gh pr merge <num> --rebase    # replay each PR commit as its own
```

- **Never `--merge`.** A merge-commit on a PR branched off `main` and targeted at `release` once silently dragged 40 ancestry commits (the v2 media migration: #573, #574, #578) from `main` into `release`, breaking production identity-document onboarding. Squash and rebase both filter second-parent ancestry; merge-commits do not. The repo setting now disables `--merge` entirely — the option is removed from the merge-button dropdown and rejected by the API.
- **Never `--delete-branch`.** Feature branches must remain on the remote after merge so the PR's commits stay reachable for archaeology — PR # is preserved in the squash commit title (`(#N)` suffix) but the per-commit detail lives on the branch ref.

---

## Commit and PR description conventions

- Commit messages, PR titles, and PR descriptions must describe **what the change does**, not what it deliberately avoids.
- Do not mention rejected alternatives, deferred phases, or "this does not use X" disclaimers in commit metadata. They are noise — they pollute the changelog and force future readers to reason about paths the project intentionally rejected.
- In particular, **do not reference "GitHub Actions", "GHA", or "Claude Code Action"** in commits, PR bodies, or issue bodies on this repo unless GHA is the actual subject of the change. The project deliberately uses local Claude Code + `gh` CLI; that is the default and does not need to be re-justified per change.
- The one legitimate place for rejected alternatives is the **"Alternatives considered"** field of the feature-request issue template — that field exists for exactly that purpose.
- If a reader needs broader context about why the project chose its current approach, that belongs in `CONTRIBUTING.md` or design docs, not in per-change commit metadata.

---

## Required confirmations (high-blast-radius actions)

Always pause and confirm with the user before:

- `git push` (to any branch)
- `gh pr create`
- `gh pr merge` — additionally, never merge to `release`
- `gh pr close`
- `gh issue close`
- Force-push, `git reset --hard`, branch deletion
- Editing label *definitions*, milestones, or repo settings (creating, renaming, or deleting labels themselves)

You may proceed without confirmation for:

- Reading issues, PRs, code, logs (`gh issue view`, `gh pr view`, `gh pr diff`, `gh run view`)
- Creating local branches
- Editing files locally
- Running tests
- Local commits (not pushed)
- Applying or removing existing labels on issues via `gh issue edit N --add-label`/`--remove-label` (label hygiene — see "Issue status label lifecycle" below; this is required, not optional)

---

## `gh` CLI — project-specific usage

For generic `gh` usage run `gh <subcommand> --help`. The commands worth memorising for *this* repo are the label-aware filters and the destructive-op flags:

```bash
# Find work ready to pick up
gh issue list --label "status:ready"
gh issue list --label "priority:high"

# When creating an issue, set type and starting status explicitly
gh issue create --title "..." --body "..." --label "type:bug,status:triage"

# Merging — squash vs rebase is a human decision, always pause and ask.
# Never --merge (repo-blocked). Never --delete-branch.
gh pr merge <num> --squash       # collapse to one commit (typical)
gh pr merge <num> --rebase       # replay each PR commit individually

# Investigating CI failures
gh run view <run-id> --log-failed
```

All other `gh` commands (`gh issue view`, `gh pr diff`, `gh pr checks`, etc.) work as documented upstream and need no project-specific notes.

---

## Issue status label lifecycle

Every open issue carries exactly one `status:*` label. Closed issues carry **none**. Updating these labels is part of your job — GitHub does not do it for you, and the convention is enforced by code review. Leaving a stale `status:triage` or `status:in-progress` on a closed issue is a bug.

There is **no `status:done` label**. Closure of the issue itself is the terminal state, so the `status:*` label must be **removed** at close, not replaced.

The lifecycle:

| From | To | When |
|---|---|---|
| (none) | `status:triage` | Auto, on issue creation by template |
| `status:triage` | `status:ready` | Triage complete; scope approved by user |
| `status:ready` | `status:in-progress` | You create the feature branch and start implementing |
| `status:in-progress` | `status:blocked` | Work is blocked on an external dependency or decision |
| `status:in-progress` | `status:needs-info` | Waiting for the user/reporter to answer something |
| `status:blocked` / `status:needs-info` | `status:in-progress` | Block cleared, work resumed |
| any `status:*` | (removed) | PR merged and issue auto-closed, or issue manually closed |

Commands — always remove the old label and add the new one in the same call:

```bash
gh issue edit N --remove-label "status:triage"      --add-label "status:ready"
gh issue edit N --remove-label "status:ready"       --add-label "status:in-progress"
gh issue edit N --remove-label "status:in-progress" --add-label "status:blocked"
gh issue edit N --remove-label "status:blocked"     --add-label "status:in-progress"

# clear at close (use whichever status was set)
gh issue edit N --remove-label "status:in-progress"
```

Label hygiene does **not** require per-call user confirmation — it is required, not optional. Apply transitions at the points called out in the **Standard implementation flow** below. If you notice a stale label on any issue (open or closed), fix it on sight rather than letting it accumulate.

---

## Standard implementation flow

When the user says "implement issue #N":

1. `gh issue view N` — read the full issue
2. Confirm understanding with the user if anything is ambiguous. Once the scope is approved and the issue is still at `status:triage`, transition it:
   ```bash
   gh issue edit N --remove-label "status:triage" --add-label "status:ready"
   ```
3. Create branch from latest `main`, then mark the issue in-progress:
   ```bash
   git checkout main && git pull
   git checkout -b <type>/N-<slug>
   gh issue edit N --remove-label "status:ready" --add-label "status:in-progress"
   ```
4. Implement the change following project conventions (see `pyproject.toml`, existing code style)
5. Run tests locally — they must pass
6. `git add` specific files (not `git add -A`) and commit with an imperative message
7. **Pause and confirm** with the user before pushing
8. `git push -u origin <branch>`
9. **Pause and confirm** the PR title/body before opening
10. `gh pr create --title "..." --body "Fixes #N\n\n..."` targeting `main`
11. Report the PR URL back to the user
12. **Stop.** Do not merge. Wait for human review.
13. When the user later authorizes the merge, **pause and explicitly ask whether to use `--squash` or `--rebase`** — this is a human decision per the Merge strategy section above. Do not pick on your own. Never `--merge` (repo setting blocks it). Never `--delete-branch` — the branch must remain on the remote. **Immediately after the merge succeeds**, clear the status label so the closed issue is not left in a stale state:
    ```bash
    gh issue edit N --remove-label "status:in-progress"
    ```

If the work transitions to `status:blocked` or `status:needs-info` mid-flow, update the label at that moment too — do not wait for the next checkpoint.

---

## CHANGELOG.md

`CHANGELOG.md` is the SDK developer-facing record of API boundary changes. After every merged issue, add one line to the current release section.

**What to record:** endpoint additions/removals, request/response field changes, new error codes, validation changes, breaking changes — anything an SDK consumer needs to know.

**What NOT to record:** internal refactors, test improvements, code cleanup, dependency bumps — anything invisible to API consumers.

**Format:** Each entry is a single line with the issue number in parentheses at the end:

```
- <imperative description of the change> (#N).
```

Examples:
```
- Add `POST /uploaded` endpoint for media uploads (#65).
- Reject enrollment mutations on cancelled events with 422 `INVALID_STATE` (#81).
- Add `gender` and `address` fields to user registration and profile endpoints (#73).
```

Group entries under headings like `### New Endpoints`, `### Breaking Changes`, `### Bug Fixes`, `### Validation & Authorization Changes`, etc. as appropriate for the current release section.

---

## Standard PR-fix flow

When the user says "fix PR #N":

1. `gh pr view N` and `gh pr diff N` — understand current state
2. `gh pr checks N` — see what's failing
3. `gh pr checkout N` — get on the branch locally
4. `gh run view <id> --log-failed` if CI failures need investigation
5. Make the fix, run tests locally
6. Commit
7. **Pause and confirm** before pushing
8. `git push` — updates the existing PR
9. Optionally `gh pr comment N --body "Fixed: ..."` to summarize

---

## Standard bug-fix flow

When the user says "fix bug #N" or asks to implement a bug issue:

1. **Verify the bug still exists.** Before writing any fix, reproduce the problem — run the relevant code path or write a minimal test that demonstrates the failure. If the bug no longer reproduces (e.g., fixed by a recent commit), report this to the user and stop.

2. **Check for existing tests.**
   - Search for tests that cover the affected code path — both positive (happy path) and negative (the failure case described in the bug).
   - If tests exist, run them and confirm they fail as the bug describes (or pass, if the bug is already fixed).
   - If no tests exist for the bug scenario, **write tests first** (TDD red phase) — at minimum one test that fails due to the bug (negative case) and one that confirms correct behavior for authorized/valid callers (positive case).

3. **Fix the bug.** Implement the minimal change needed. Run all related tests — the new/existing negative-case test must now pass.

4. **Document test coverage in the PR.** When creating the PR body or commit message, explicitly state which test(s) confirm the fix for each aspect of the issue. Use the format:
   ```
   Test confirmation:
   - `test_function_name` — verifies <what it checks>
   - `test_another_function` — verifies <what it checks>
   ```
   This must appear in the PR description so reviewers can trace each reported problem to a specific test.

5. Continue with the normal implementation flow (commit, confirm push, open PR, etc.).

---

## Branch model recap

```
release        ← human-only; updated by manually promoting main
   ↑
beta_release   ← human-only; updated by manually promoting main (powers Beta deploy)
   ↑
main           ← integration branch; all PRs target this
   ↑
<feature branches from issues>
```

You participate at the feature-branch and `main` level only. Never touch `release` or `beta_release` without explicit user authorization.

---

## Infrastructure Notes

### Static file serving: FastAPI, not nginx

Static files (`/static/`) are served by **FastAPI's `StaticFiles` mount**, not by nginx directly. This is intentional.

**Why:** Flutter web video playback requires CORS headers on static file responses. FastAPI's `CORSMiddleware` doesn't apply to mounted sub-apps like `StaticFiles`, so a custom `StaticFilesCORSMiddleware` was added in `club_server/main.py`. Safari is especially problematic — it sends `no-cors` fetch mode for videos, requiring permissive CORS handling. Serving static files through FastAPI ensures consistent CORS behavior across all browsers.

**History:** Static files were originally served by nginx via `alias` directives. This broke video playback on Flutter web (missing CORS headers) and Safari (strict fetch mode). The fix was to route static requests through FastAPI where the middleware could apply CORS headers consistently. See server commits `41782c9` and `5aced9d`.

**Future consideration:** Moving back to nginx for static files is possible if nginx is configured with proper CORS headers (`add_header Access-Control-Allow-Origin * always`, etc.) in the `/static/` location block. This would be more efficient (nginx `sendfile`, zero Python overhead). Track this as a future improvement.

## Coding standards

The repo's coding and testing standards live in version-controlled docs — read them before any non-trivial change:

- [`docs/conventions/coding_rules.md`](docs/conventions/coding_rules.md) — file organization, module boundaries, formatting, naming, file-size limits, `uv` usage.
- [`docs/conventions/tdd_rules.md`](docs/conventions/tdd_rules.md) — red-first TDD, double-verification across roles, assertion strengthening, test isolation, naming conventions.

These docs are the source of truth. If guidance elsewhere (system prompt, ad-hoc instructions) conflicts with them, raise the conflict with the user rather than silently picking one.
