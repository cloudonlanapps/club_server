# Test-Driven Development (TDD) Rules (Python / club_server)

Apply these rules to all changes in this repository. Tests live under `tests/` and are run via `just tests::auto` (see `tests/README.md`).

## 1. Red-First Development
- Write a failing test before implementing any feature or fixing a bug.
- For new endpoints or service functions, the stub should `raise NotImplementedError` until the test is logically sound and the implementation begins.

## 2. Requirement-Driven Coverage
- Every documented requirement (in the issue, design doc, or `docs/`) must have at least one dedicated `test_*` function.
- Do not group multiple requirements into a single test — granular tests make contract violations obvious.

## 3. Double-Verification Pattern (multi-role endpoints)
- For any state-changing endpoint that is observable by more than one role (e.g. admin creates, guardian sees), verify through **both** query paths:
    1. The admin/coach query endpoint (consistency check).
    2. The guardian/end-user query endpoint (visibility & access-control check).
- This catches missing role filters and incorrect access scoping.

## 4. Logical Verification & Strengthening
- Integration tests must assert business outcomes (e.g. "event appears in `GET /events`") rather than just that the call returned 200.
- **Assertion-First Rule** — never write a test step without an immediate, meaningful `assert`.
- **Query Verification** — after every query API call, there must be at least one assertion immediately after.
- **Modification Cycle** — every mutation API call (`POST`/`PATCH`/`DELETE`) must be followed by a query call and assertions verifying the new state.
- **Avoid Weak Assertions** — prefer specific state checks (`status == EnrollmentStatus.cancelled`) over `assert response.status_code == 200` alone or `is not None`.
- Tests should drive the public HTTP API surface (`httpx.AsyncClient`), not call services directly, except for unit tests of pure service logic.

## 5. Model & Schema Integrity
- Pydantic schemas and SQLAlchemy models must be tested for:
    - Validation success and failure cases.
    - JSON serialization round-trip (`Schema.model_validate(schema.model_dump())`).
    - Default values and required-field enforcement.

## 6. Business Constraint Validation
- All business rules (cutoffs, registration windows, role gates, mandatory reasons) must be tested for **both** the success path and the failure path (expecting a 4xx with the documented error code).

## 7. Operational Rules for Robust Testing
- **The "Manual Break" Test** — temporarily change the expected value in an assertion to verify the test actually fails. A test that can never fail is not a test.
- **Pass-Rate Caution** — a green suite measures stability, not coverage completeness.
- **No Dead-End Placeholders** — grep for `# TODO`, `# Verification`, `pytest.skip` before opening a PR. Skipped tests must reference an open issue.

---

## 8. Test Naming Convention
- `test_should_<expected>_when_<condition>` — e.g. `test_should_reject_enrollment_when_event_cancelled`.

---

## 9. Test Isolation
- **No network dependency** — all tests must run offline. External calls are mocked or faked.
- **Per-test database** — `conftest.py` overrides `get_db` so each test gets a clean session and tables are dropped per function. Never share state between tests.
- **Use the helpers in `tests/helpers.py`** — `create_admin_user`, `create_regular_admin_user`, `create_coach_user`, `create_guardian_user`, etc. Do not hand-roll user creation in test bodies.
- **Test every endpoint for every role** that can reach it (admin / coach / guardian / unauthenticated) — both the allowed and forbidden cases.
