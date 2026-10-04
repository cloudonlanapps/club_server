# Enrollment — Requirements

This document is the testable specification for the enrollment
subsystem — the lifecycle that links a user (member) to an event
(camp, programme, or one-off) from invitation/request through
acceptance, withdrawal, and removal.

It is written as plain-English use cases. It deliberately avoids
naming specific HTTP endpoints or source files so that the underlying
API surface can evolve (compact, split, rename) without invalidating
the requirements. Each rule describes *what* the system must allow or
forbid, not *how* a client reaches it.

Enrollments are event-agnostic — the same state machine applies to every
event type. Two things are **not**: time-conflict suppression, which is
camp-only (R30, R37), and trial assignment, which is programme-only
(R31a). Eligibility is *not* a camp carve-out; it applies to every type
(R25, R31, R38, R42, and programme R25a).

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — surfaced in review, not yet implemented; cites the open
  issue that introduces it.

## Vocabulary

- An **enrollment** links one user to one event. There is exactly one
  enrollment per `(event, user)` pair, and it carries one of the
  following statuses:
  `invited`, `requested`, `accepted`, `rejected`, `assigned`,
  `assignedTrial`, `withdrawRequested`, `withdrawn`, `declined`,
  `removed`, `retracted`.
- **Enrolled** = `{accepted, assigned, assignedTrial, withdrawRequested}`.
  The member has taken up a place. Blocks re-enrollment; contributes to
  time-conflict detection.
- **Terminal** = `{declined, withdrawn, removed, rejected, retracted}`.
  A terminal enrollment is closed for good (R6a).
- **Non-terminal** = everything else, i.e. **Enrolled** plus
  `{invited, requested}`. This is the set that covers a future
  occurrence for attendance (R60).
- The word **active** is deliberately not used. It named both of the
  above in different places, including in two constants of the same name
  in different modules whose contents differ by exactly `invited` and
  `requested`. Every rule below says *enrolled* or *non-terminal*.
- **Organizer** = the user named as the event's organizer. The
  organizer may act with admin-level rights on their own event;
  everyone else needs the `admin` role.
- **Staff** = users with the `admin` or `coach` role, or super-admin.
- **Self-service path** = a user acting on their own enrollment (or
  staff/super-admin acting on someone else's, see R7).

---

## State machine

- **R1** [✅] Enrollment status comes from a closed set:
  `invited, requested, accepted, rejected, assigned, assignedTrial,
  withdrawRequested, withdrawn, declined, removed`. Any other value at
  the storage layer is a bug.
- **R2** [✅] The previous status is recorded on every status change.
  It is used to restore state when a withdrawal request is rejected
  (R29) or cancelled (R28).
- **R3** [✅] Timestamps follow the rules:
  - `created_at` is set on first insert and is immutable thereafter.
  - `updated_at` is set on every mutation.
  - `enrolled_at` is set on transitions into `accepted`, `assigned`,
    `assignedTrial` (and re-set when an `assign` overwrites a terminal
    row).
  - `withdrawn_at` is set on `withdrawn` and `removed`.
- **R4** [✅] A withdrawal/rejection reason is captured on `rejected`,
  `removed`, and `withdrawRequested`; it is cleared when the row
  transitions away from a terminal state via re-invite / re-assign /
  re-request, and on cancel-withdraw / reject-withdraw.
- **R5** [✅] The "trial" flag is set when entering `assignedTrial`. It
  is not cleared on later transitions within the same stint — the row
  remembers it was a trial enrollment. A rejoin (assign, assign-trial,
  invite accepted, request approved) starts a new stint and sets the flag
  from the path taken: true only for assign-trial (#468).
- **R6** [GAP] There is at most one **live** enrollment per
  `(event, user)` pair — one row in a non-terminal status. Status changes
  mutate that row in place.
- **R6a** [GAP] A terminal enrollment is **closed for good**. Rejoining
  writes a **new** row; it never reuses the closed one. Terminal rows
  accumulate as the member's history with that event.

  The previous rule allowed exactly one row per pair, so a member who
  joined in March, withdrew in June and rejoined in September overwrote
  both stamps. Their March-to-June attendance then failed coverage (R61)
  and vanished from their own listing: the row said they joined in
  September, so the spring they actually attended had never happened.
  One row with one `enrolled_at` and one `withdrawn_at` cannot hold two
  periods, and re-enrollment is ordinary — a member leaving a programme
  and returning a season later.
- **R5a** [GAP] A member may **retract** their own pending request
  → status `retracted`, terminal. Decline covers `invited` and withdraw
  covers the enrolled statuses, so without this a member who asked to
  join and changed their mind has no exit: the only way out is an admin
  rejecting someone who no longer wants to join, which reads as a refusal
  in the audit log.
- **R5b** [X] Retract from any status other than `requested` → 422
  `INVALID_STATE`. It is the member-side counterpart of `rejected`, as
  `declined` is the member-side counterpart of an invitation — which is
  why it is its own status rather than a reuse of `withdrawn`. A member
  who never got in did not withdraw.

  The name deliberately avoids "cancelled", a word already carrying four
  meanings across these documents (lifecycle L14).
- **R6b** [GAP] Coverage (R61) reads **every** row for the pair, not the
  live one: an occurrence is covered if it falls inside any stint. A
  closed stint keeps covering the occurrences it spanned, which is what
  makes the history audit-correct.
- **R6c** [GAP] Everything that reads "the enrollment" for a pair means
  the **live** row — the state machine (R8–R58), listings, and the
  conflict gate. Only coverage reads all of them (R6b).

  Consequences worth naming, because they are not local: the unique
  constraint on `(membername, event_id)` becomes a partial one over
  non-terminal rows; the paths that today overwrite a terminal row —
  re-invite, assign, self-request — insert instead; and every lookup that
  expects at most one row per pair must select the live one. Combined
  with R59a the pair is `(member, chain root)`, not `(member, link)`.

---

## Authorization (who can call what)

- **R7** [✅] A user's self-service actions on their own enrollment are
  permitted to that user, **or** to a caller who has `admin`, `coach`,
  or super-admin.
- **R8** [X] One plain user cannot use the self-service flow on behalf
  of another user → 403.
- **R9** [X] An anonymous caller cannot reach any enrollment operation
  → 401.
- **R10** [✅] Admin-side enrollment actions (invite, assign, assign-
  trial, approve, reject, remove, approve-withdraw, reject-withdraw)
  require an admin **or** the organizer of this event. A coach without
  the admin role is rejected → 403 / `INSUFFICIENT_PERMISSION`.
- **R11** [✅] Super-admin bypasses past-event guards (R15) on every
  enrollment mutation (admin and self-service paths) for retrospective
  corrections. The override applies only to the temporal guard;
  eligibility 422s (R25 / R31 / R38 / R42) are **not** overrideable —
  eligibility is a data-safety invariant.

---

## Pre-checks (apply to every mutation)

- **R12** [X] Mutation against a non-existent or soft-deleted event →
  404 `EVENT_NOT_FOUND`.
- **R13** [X] Mutation against a non-existent or soft-deleted user →
  404 `USER_NOT_FOUND` (applies to invite / assign / assign-trial,
  which name a user who may not yet have an enrollment row).
- **R14** [X] Mutation against a non-existent enrollment row when the
  flow requires one (approve, reject, remove, approve-withdraw,
  reject-withdraw, accept, decline, withdraw, cancel-withdraw)
  → 404 `ENROLLMENT_NOT_FOUND`.
- **R15** [GAP] **Join-side flows are blocked when the event has no live
  occurrence at or after the moment of the request** → 422
  `INVALID_STATE`. Join-side means invite, assign, assign-trial, approve,
  accept and request. Exit-side flows (decline, reject, remove, withdraw,
  cancel-withdraw, approve-withdraw, reject-withdraw) remain allowed so
  members can leave and admins can clean up. Super-admin bypasses (R11).

  This one predicate replaces the two rules that stood here — "ended" and
  "cancelled" — which were separately defined and disagreed. It is owned
  by [`event_lifecycle_requirements.md`](event_lifecycle_requirements.md)
  L12, which shows it is correct for every type and every ending verb.
- **R16** [GAP] There is **no whole-event notion of cancelled** in this
  document. The previous rule tested `until_time` set with no successor,
  which reads a *cutoff* as a cancellation: it closed enrollment the
  instant a programme was terminated or a camp cancelled with a future
  cutoff, while both keep running to that cutoff and both must keep
  accepting members (lifecycle L21, programme R5). Cancellation is a
  property of an **occurrence** (lifecycle L14, attendance R21).
- **R17** [✅] Eligibility / time-conflict pre-checks are mutation-
  specific and listed under each flow below.

---

## Listing & reading enrollments

- **R18** [✅] Admin / coach can list the enrollments for an event,
  optionally filtered by status. The result maps each member to their
  current status.
- **R19** [✅] A member (or staff acting on their behalf, R7) can read
  that member's enrollment for a given event.
- **R20** [✅] A member's events listing returns the events they are
  enrolled in (any non-terminal status) plus public events they are
  eligible for (post-#18).
- **R21** [X] Listing on a non-existent / soft-deleted event → 404.

---

## Invite (admin / organizer → user)

- **R22** [✅] Inviting a user with no existing enrollment row creates
  a new row in `invited` status.
- **R23** [✅] When only a terminal row exists
  (`declined` / `withdrawn` / `removed` / `rejected`), invite
  overwrites it: previous status preserved, status becomes `invited`,
  reason cleared, updated time refreshed.
- **R24** [X] Inviting when an active (non-terminal) enrollment exists
  → 422 `ALREADY_ENROLLED`. This includes `invited`, `requested`, and
  `withdrawRequested` rows — invite does not "re-send" or "refresh"
  an active row.
- **R25** [X] Inviting a user who fails the event's eligibility
  criteria (gender, DOB bounds) → 422 `USER_NOT_ELIGIBLE_FOR_EVENT`.
  Eligibility is also enforced when invite replaces a terminal-state
  enrollment (R23 path) — the replaced row must still satisfy the
  current criteria. Mirrors camp R54.
- **R26** [✅] Invite does **not** run a time-conflict check. Invites
  are advisory; the conflict is evaluated later at acceptance.

## Assign (admin / organizer → user, no consent)

- **R27** [✅] Assign creates or overwrites the row to `assigned` (or
  `assignedTrial` for the trial variant), setting the enrolled time
  and clearing the withdrawn time and reason.
- **R28** [X] Assign on an active enrollment → 422 `ALREADY_ENROLLED`.
  The active set checked here is the narrower one (excludes `invited`
  and `requested`), so an outstanding invite or self-request can be
  upgraded to `assigned`.
- **R29** [✅] Assign runs a time-conflict check against the user's other
  **enrolled** rows. Any overlap fails the call with 409 `TIME_CONFLICT`.
- **R29a** [GAP] Overlap is the **member gate** of the shared conflict
  module (programme R31b), and uses the one definition of overlap
  (camp R32): two occurrences overlap iff `start_a < end_b AND
  end_a > start_b`. The current check compares whole-event windows
  instead, which flags a Monday programme against a Tuesday one.
- **R29b** [GAP] A candidate event is excluded only when it is
  soft-deleted, or has no live occurrence in the overlapping span. It is
  **not** excluded for carrying a cutoff: a terminated programme still
  running to its cutoff is exactly the event a member can double-book
  against (lifecycle L12, L21).
- **R30** [GAP] The time-conflict check **blocks only for a
  programme-versus-programme clash**. Every other pairing reports and
  proceeds — camp, one-off, and a programme against either of those
  (programme R30, R30c; one-off R20).

  An earlier draft made the suppression camp-only, which left a one-off
  join blocking on a clash its own document says must only be reported.
- **R31a** [X] Assign-trial is **programme-only** → 400
  `EVENT_TYPE_NOT_SUPPORTED` for a camp or a one-off. A trial runs for
  part of a recurring series, which is why it exists only there
  (programme R32a). This document owns the state machine, so the
  restriction is stated here rather than only in the event documents.
- **R31** [X] Assign (and assign-trial) re-check eligibility;
  ineligible users are rejected with 422
  `USER_NOT_ELIGIBLE_FOR_EVENT` (mirrors camp R55). No super-admin
  override applies — eligibility is a data-safety invariant.
- **R32** [✅] Assign-trial sets the trial flag. A regular assign
  leaves the trial flag untouched (default false on insert).

## Self-request (user → event)

- **R33** [✅] A user requesting to join an event with no existing
  enrollment row creates a new row in `requested` status.
- **R34** [✅] When only a terminal row exists, self-request overwrites
  it to `requested` (mirroring R23).
- **R35** [X] When an active (non-terminal) enrollment exists →
  422 `ALREADY_ENROLLED`.
- **R36** [✅] Self-request runs a time-conflict check against the
  user's other active enrollments → 409 `TIME_CONFLICT` on overlap.
- **R37** [GAP / #16] For camps, the time-conflict check is suppressed
  (cross-reference camp R28 / R77).
- **R38** [X] Self-request from an ineligible user → 422
  `USER_NOT_ELIGIBLE_FOR_EVENT` (mirrors camp R78).

## Decision on a request (admin / organizer)

- **R39** [✅] Approve transitions `requested → accepted` and stamps
  the enrolled time.
- **R40** [✅] Reject transitions `requested → rejected`, optionally
  storing a reason.
- **R41** [X] Approve / reject from any status other than `requested`
  → 422 `INVALID_TRANSITION`.
- **R42** [X] Approve re-checks eligibility; if the user is no longer
  eligible at decision time → 422 `USER_NOT_ELIGIBLE_FOR_EVENT` and
  the enrollment remains `requested` (mirrors camp R62 and groups
  R66).
- **R43** [✅] Approve does **not** re-run the time-conflict check. The
  check ran when the user submitted the request (R36); admin approval
  trusts that snapshot.

## Accept / decline an invitation (user)

- **R44** [✅] Accept transitions `invited → accepted` and stamps the
  enrolled time. Eligibility is **not** re-checked at accept; the
  `invited` row is the locked-in eligibility snapshot. Tightening event
  criteria after invite does not block the invitee from accepting
  (mirrors grandfathering).
- **R45** [✅] Accept runs a time-conflict check at the moment of
  consent → 409 `TIME_CONFLICT` if the user took on a clashing
  enrollment between invite and acceptance. (For camps, suppressed per
  R30 / camp R28.)
- **R46** [✅] Decline transitions `invited → declined`. No conflict
  check; declining is always safe.
- **R47** [X] Accept / decline from any status other than `invited`
  → 422 `INVALID_TRANSITION`.

## Removal (admin / organizer → enrolled user)

- **R48** [✅] Remove transitions any current status to `removed`,
  storing an optional reason and stamping the withdrawn time. Unlike
  approve/reject, remove is not gated on the source status — it is the
  admin-side eject button.
- **R48a** [GAP] On a credit-enabled deployment, removing a member who
  holds credit bound to the programme requires a **credit disposition**
  as part of the call; without one the removal is refused and the member
  stays enrolled rather than being removed with credit stranded
  (`credit_system_requirements.md` R71). On a deployment not running on
  credits a supplied disposition is rejected (credit R98). The same
  applies to approve-withdrawal (R54a).
- **R48b** [GAP] Where an occurrence the departing member is still
  enrolled for is under way, settlement is **deferred** to its end rather
  than taken at the call (programme R29c–R29f, credit R74a).
- **R49** [✅] Removal preserves the row (status `removed`, terminal),
  so a future invite/assign/request can overwrite it (R23 / R27 / R34).

## Withdrawal flow (user-initiated)

- **R50** [✅] A user can request withdrawal only from
  `{accepted, assigned, assignedTrial}` → status becomes
  `withdrawRequested`, an optional reason is captured.
- **R51** [X] Withdraw from any other status → 422 `INVALID_TRANSITION`.
- **R52** [✅] A user can cancel their own pending withdrawal request
  → status restored to the previous status (or `accepted` as
  fallback); the reason is cleared.
- **R53** [X] Cancel-withdraw from any status other than
  `withdrawRequested` → 422 `INVALID_TRANSITION`.
- **R54** [✅] An admin or organizer can approve a withdrawal request
  → status becomes `withdrawn`, withdrawn time stamped.
- **R54a** [GAP] Approving a withdrawal carries the same credit
  disposition as a removal (R48a; credit R73). Rejecting one leaves every
  account untouched.
- **R55** [✅] An admin or organizer can reject a withdrawal request
  → status restored to the previous status, reason cleared.
- **R56** [X] Approve-withdraw / reject-withdraw from any status other
  than `withdrawRequested` → 422 `INVALID_TRANSITION`.
- **R57** [X] All four withdrawal mutations are blocked once the event
  has ended → 422 `INVALID_STATE` (super-admin override per R11).

## Re-enrollment after a terminal exit

- **R58** [✅] A user who was previously `declined` / `withdrawn` /
  `removed` / `rejected` can be re-invited (R23), re-assigned (R27),
  re-assigned-trial, or self-request again (R34). The row is
  overwritten in place; the previous terminal status is preserved.
- **R59** [X] A user with an active enrollment cannot start a second
  flow on the same event (R24 / R28 / R35). The single-row invariant
  (R6) forbids "in flight" duplicates.

---

## Eligibility for occurrences (attendance bridge)

The state machine above governs the enrollment row. A separate rule
decides whether a given enrollment makes a user eligible for a
specific occurrence at time `T`.

- **R59a** Every rule in this section, and R20 (listing), R48 (remove)
  and R54 (approve withdrawal), reads one event id, because a programme is
  one event for its whole life (programme R29–R29b;
  `event_schedule_model.md`). An earlier draft read a chain of successor
  events; there is no chain to read.
- **R60** [✅] For a present-or-future occurrence, the enrollment
  covers the occurrence iff the current status is **non-terminal**.
  This is a wider set than **enrolled** —
  `invited` and `requested` count for attendance visibility but not
  for re-enrollment exclusion.
- **R61** [✅] For a past occurrence, the enrollment covers the
  occurrence iff `enrolled_at` is set and falls on or before `T`, and
  either `withdrawn_at` is unset or falls on or after `T`. This gives
  the audit-correct historical view: someone who was enrolled on day
  3 and withdrew on day 5 covers occurrences 3–5 but not 1–2 or 6+.
- **R62** [X] Attendance writes against an occurrence the user's
  enrollment does not cover are rejected (cross-reference camp R84).
  The read path silently filters uncovered occurrences out of the
  user's listing; the write path rejects with 422 `INVALID_STATE`
  ("user not enrolled at occurrence time").
- **R62a** [✅] Mid-series joiners are not retroactively covered.
  Because the enrolled time is stamped at the moment of transition
  into `accepted` / `assigned` / `assignedTrial` (R3) and never
  backfilled, a user approved on day 4 of a 7-day series covers only
  occurrences from day 4 onward and fails R61 for days 1–3. Past
  occurrences before the enrolled time are silently filtered from the
  user's listing and any attendance write against them is rejected
  per R62. Super-admin attendance writes bypass the check —
  `attendance_requirements.md` R11a now agrees, having previously claimed
  coverage was overrideable by no one.

---

## Audit logging

- **R63** [✅] Every successful enrollment mutation writes an audit row
  capturing the actor, the affected user, the event, and the action
  (one of: invited, assigned, trial-assigned, approved, rejected,
  removed, withdraw-requested, withdraw-approved, withdraw-rejected,
  accepted, declined, requested, withdraw-cancelled).
- **R64** [✅] Failed mutations (404 / 422 / 409) do **not** write
  audit rows. The audit log records state transitions, not attempts.
- **R65** [✅] For self-service mutations the actor and target are
  typically the same user; for staff/super-admin acting on behalf of
  another user via the self-service path (R7), the actor is the staff
  member and the target is the affected user.

---

## Cross-references

- Camp-specific carve-outs and event-level rules:
  [`docs/camps_requirements.md`](camps_requirements.md) — see camp
  R28 (time-conflict suppression), R45–R56 (admin-side flows from the
  camp perspective), R63–R68 (withdrawal from the camp perspective),
  R75–R80 (member self-service from the camp perspective), R94–R96
  (audit), R100–R104 (sessions / timetable).
- Group join requests, which use a parallel but distinct state
  machine, are out of scope here.
