# Coding Standards & Practices (Python / club_server)

These rules apply to all Python code in this repository. They complement the workflow rules in `CONTRIBUTING.md` and `CLAUDE.md`.

## File Organization

1. **One class per file** — no monolithic modules with many unrelated classes packed together. Tightly-coupled helpers (e.g. a Pydantic schema family for a single endpoint group) may share a file.

2. **Folder names indicate purpose** — files in `routers/` don't need a `_router` suffix, files in `services/` don't need a `_service` suffix, etc.

3. **Correct folder for content type**:
   - `routers/` — FastAPI route handlers, one file per domain
   - `services/` — Business logic; called by routers, operates on DB models
   - `schemas/` — Pydantic request/response models
   - `db/models/` — SQLAlchemy ORM models
   - `utils.py` / `utils/` — Pure helper functions

4. **Meaningful naming** — file and symbol names should clearly reflect purpose. `snake_case` for filenames and functions, `PascalCase` for classes.

---

## Module Boundaries

5. **`__init__.py` exports only the public API** of a package — don't re-export everything; only symbols actually used outside the module.

6. **Keep implementation details internal** — don't expose internal helpers just to satisfy a workaround in another module; refactor instead.

7. **Test/example data stays in `tests/`** — fixture builders, dummy data, and test utilities belong under `tests/`, not in `club_server/`.

8. **Avoid leaky abstractions** — services should encapsulate transactions, caching, and lifecycle internally rather than requiring routers to manage them.

---

## Code Patterns

9. **Use existing models directly** — don't create redundant wrapper classes when a SQLAlchemy model or Pydantic schema already fits.

10. **Avoid naming conflicts across domains** — when a generic name (`Status`, `Item`, `Notifier`) might collide with similar classes in other modules, prefix with the domain (`EnrollmentStatus`, `EventNotifier`).

11. **Routers stay thin** — routers parse input, call a service, and shape the response. Business logic lives in `services/`.

---

## File Size Limits

12. **Maximum 400 lines per file** — split into multiple files (e.g. one router per sub-resource) if exceeding this limit.

---

## Code Formatting

13. **Run `ruff format` before every commit.** Lint with `ruff check`. CI runs `just lint`.

14. **Zero warnings.** `ruff` with project defaults must report nothing.

15. **File naming** — `snake_case` for all files.

16. **Import order** — stdlib → third-party → project (`club_server.*`). `ruff`'s isort rules enforce this.

17. **Documentation** — public functions, classes, and routers must have docstrings. No commented-out code in committed files.

18. **No magic values** — define constants in a `constants.py` or as enums (e.g. `UserStatus`, `EnrollmentStatus`). Inline literals are only acceptable in tests.

---

## Git & Workflow

Commit, branch, PR, and merge conventions are defined in `CONTRIBUTING.md` and `CLAUDE.md`. They take precedence over anything below.

19. **Commit message format** — imperative mood, scoped where helpful: `feat: add member groups`, `fix: enrollment race condition`, `test: attendance integration tests`. The full PR/branch rules are in `CLAUDE.md` § "Pull request rules".

20. **One logical change per commit** — do not mix unrelated changes.

---

## Python Project Management

21. **Use `uv`** — all dependency management, virtualenv, and script execution goes through `uv` (`uv add ...`, `uv run pytest`, etc.). Do not use `pip` or `poetry` directly.
