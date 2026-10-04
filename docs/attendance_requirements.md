# Attendance — Requirements

This document is the testable specification for the attendance
subsystem — recording, viewing, and amending whether an enrolled
member showed up to a given **occurrence** of an event (camp,
programme, or one-off).

It is written as plain-English use cases. It deliberately avoids
naming specific HTTP endpoints or source files so that the underlying
API surface can evolve (compact, split, rename) without invalidating
the requirements. Each rule describes *what* the system must allow or
forbid, not *how* a client reaches it.

Attendance is event-agnostic in its own rules — status set, windows,
authorization and audit apply identically across camps, programmes and
one-off events, and it reads one event: a programme keeps one id for its
whole life, however often its timetable is split (R20a). Enrollment-coverage
rules live in
`docs/enrollment_requirements.md` (R60–R62a) and are referenced from
here rather than restated.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — surfaced in review, not yet implemented; cites the open
  issue that introduces it.

## Vocabulary

- An **occurrence** is one expansion of an event's recurrence rule
  (or the single start/end pair for a non-recurring event), keyed by
  `(event_id, occurrence_time_utc)`. `occurrence_time_utc` is the
  occurrence's start time in UTC milliseconds.
- An **attendance record** links one user to one occurrence. There is
  exactly one record per `(event_id, occurrence_time_utc, member)`.
- The **status** of a record is one of:
  `present`, `absent`, `late`, `onLeaveRequested`, `onLeave`.
- The **admin-markable** statuses (writable via the admin
  mark-attendance flow) are `{present, absent, late}`. The two leave
  statuses are reachable only through the leave-declaration flow
  (declare → approve / reject / cancel).
- **Organizer** = the user named as the event's organizer. The
  organizer may act with admin-level rights on their own event.
- **Assigned coach** = a user listed as a coach on the event's current
  schedule. An assigned coach may mark attendance and decide leave on
  that event, and nothing more (R7–R7b).
- **Staff** = users with the `admin` or `coach` role, or super-admin.
- **Effective start** = the occurrence's override start where one
  exists, otherwise its slot. Every window below is measured from it,
  never from the slot (R15b).
- **Edit window** = a 15-day upper bound after the effective start
  during which non-super-admin staff may write attendance.
- **Open window (lower bound)** = a 30-minute lead before the effective
  start, before which **no** caller may write attendance (R12).
- **Leave-declaration window** = the period ending 2 hours before
  `occurrence_time_utc` during which a member may declare leave.

---

## Record shape and invariants

- **R1** [✅] Attendance status comes from a closed set:
  `present, absent, late, onLeaveRequested, onLeave`. Any other value
  at the storage layer is a bug.
- **R2** [✅] There is exactly one attendance row per
  `(event_id, occurrence_time_utc, member)`. Subsequent writes mutate
  it in place rather than inserting a duplicate.
- **R2a** When a programme is split, nothing moves: a record is keyed by
  `(event_id, occurrence_time_utc, member)` and the event keeps its id
  for life (programme R24c, R24d; `event_schedule_model.md`). An earlier
  draft specified a migration of rows past the cutoff to a successor
  event; under the schedule model there is no successor.
- **R3** [✅] The previous status is recorded on every status change.
  It is used to restore state when a leave request is rejected (R29)
  and is preserved across status churn for audit.
- **R4** [✅] `recorded_at` is set to the current server time on every
  successful write (mark, declare, approve, reject-restore). It is not
  settable by the caller: there is no back-dating parameter, and the
  temporal windows (R12–R15) are therefore always judged against server
  time. An earlier draft of this rule described a `reference_time`
  override; no such parameter exists, and inventing one would put R12's
  "no role bypasses this" in the caller's hands.
- **R5** [✅] `leave_reason` is captured on `onLeaveRequested`,
  preserved on transition into `onLeave`, and cleared when a rejected
  leave restores the previous status.
- **R6** [✅] Cancelling a pending leave request (R28) deletes the
  record outright; rejecting a pending leave request restores the
  previous status if one exists, otherwise deletes the record (R30).

---

## Authorization

- **R7** [✅] The admin-side mark-attendance flow (mark and clear) requires
  an admin, the organizer of this event, **or a coach assigned to it**
  (#247). "Assigned" is read from the current schedule's coach rows
  (programme R20a), never from the global `coach` role: a coach who is
  neither admin, organizer nor assigned is rejected → 403 /
  `INSUFFICIENT_PERMISSION`.
- **R7a** [✅] Approve-leave and reject-leave carry the **same** rule as
  R7: admin, organizer, or assigned coach. "Staff" in R14 and R33 is not
  the wider set — a coach who is none of the three is rejected there too.
- **R7b** [✅] The assigned-coach tier is **attendance only** (#247).
  Enrollment (invite, assign, approve, reject, remove, withdraw
  decisions), editing the event and every credit operation stay exactly
  as they were: organizer-or-admin, or admin. The organizer's own powers
  are unchanged.
- **R8** [✅] Listing the attendance roster for an occurrence requires
  staff (admin or coach). Plain users cannot read the full roster.
- **R9** [✅] A user may read **their own** attendance record for any
  occurrence; staff may read any user's record on the user's behalf.
- **R10** [X] An anonymous caller cannot reach any attendance
  operation → 401.
- **R11** [✅] Super-admin bypasses the *upper* temporal bounds on
  attendance writes for retrospective corrections: the 15-day edit
  window and the 2-hour leave-declaration window. The override does
  not extend to the open-window lower bound (R12) — marking an occurrence
  before it has started is data invention, not audit correction, and
  no role has that ability.
- **R11a** [✅] Super-admin **does** bypass the enrollment-coverage check
  on writes (R20), agreeing with `enrollment_requirements.md` R62a. An
  earlier draft of R11 claimed the opposite, calling coverage a
  data-safety invariant no role could override. The read path filters
  uncovered occurrences regardless of role; only writes are bypassable.

---

## Temporal windows on staff writes

- **R12** [X] Mark-attendance earlier than 30 minutes before the
  occurrence's *effective* start → 422 `ATTENDANCE_NOT_YET_OPEN`. The
  boundary is inclusive: a write at exactly `effective start - 30 min`
  is allowed. Effective start is the occurrence override's new start
  where one exists, otherwise the slot key — so a rescheduled occurrence's
  register opens relative to where it was moved to. No role bypasses
  this, super-admin included (R11).
- **R13** [✅] Mark-attendance later than 15 days after the occurrence's
  **effective start** → 422 `EDIT_WINDOW_CLOSED`. Super-admin bypasses
  (R11). The boundary is inclusive: a write at exactly
  `effective start + 15 days` is allowed.
- **R14** [✅] Approve-leave / reject-leave on the staff side are also
  gated by the same 15-day edit window (R13).
- **R15** [✅] Declare-leave (member-side) later than 2 hours before the
  occurrence's **effective start** → 422 `LEAVE_WINDOW_CLOSED`.
  Super-admin bypasses (R11). The boundary is inclusive: a declaration at
  exactly `effective start - 2 hours` is allowed.
- **R15a** The leave window has no lower bound: leave may be declared any
  distance ahead of an occurrence. So an attendance record can exist for an
  occurrence months away, which the mark path can never produce — marking
  opens 30 minutes before the occurrence (R12). Anything reasoning about how
  early an attendance row can exist must use this bound, not R12's. See
  programme R24c, where assuming otherwise produced a wrong requirement.
- **R15b** All three windows are measured from the **effective start**.
  One clock, not two. This matters because a reschedule may only postpone
  (programme R19a), so an override always moves the effective start
  later: measuring the edit window from the slot instead would close it
  before a postponed occurrence's register had opened.
- **R16** [✅] The 30-minute lead, 2-hour leave-declaration cutoff,
  and 15-day edit window are hard-coded constants. Changing them is a
  code change, not a configuration change.

---

## Pre-checks (apply to every attendance mutation)

- **R17** [X] Mutation against a non-existent or soft-deleted event
  → 404 `EVENT_NOT_FOUND`.
- **R18** [X] Mutation that names a non-existent or soft-deleted user
  → 404 `USER_NOT_FOUND`.
- **R19** [X] Cancelling, approving, or rejecting a leave request
  when no attendance row exists → 404
  `ATTENDANCE_NOT_FOUND`.
- **R20** [X] Mutation against an occurrence that the member's
  enrollment does **not** cover → 422 `INVALID_STATE`
  ("user not enrolled at occurrence time"). Coverage is defined by
  `enrollment_requirements.md` R60–R62a, which is the authority. The
  summary below is a reader's aid, not a second definition — where they
  differ, the enrollment document wins:
  - For a present-or-future occurrence, the enrollment status must be
    in `{accepted, assigned, assignedTrial, invited, requested,
    withdrawRequested}`.
  - For a past occurrence, `enrolled_at` must be ≤ occurrence time
    and `withdrawn_at` (if set) must be ≥ occurrence time.
  - Mid-series joiners are **not** retroactively covered (R62a).
  Super-admin bypasses on writes (R11) but the read path silently
  filters uncovered occurrences out of the user's listing
  regardless.
- **R20a** Coverage reads the programme, and the programme is one event.
  A split changes the timetable, not the event id, so the enrollment row
  looked up by `(event_id, member)` is the whole story (programme R29,
  R29a; `event_schedule_model.md`). An earlier draft specified chain-wide
  coverage across successor events; there are no successors.
- **R21** [X] Mutation against a cancelled occurrence → 422
  `CANCELLED_OCCURRENCE`. Both routes count: an occurrence-level
  override with status `cancelled`, and a series cutoff the occurrence
  falls at or after. Cancelled-ness is per-occurrence, never
  whole-event — see programme R27, and #369 for the two paths that
  still read it as whole-event.

  Wired on mark, clear, declare-leave, approve-leave and reject-leave.
  Cancel-leave (R28) is deliberately exempt: retracting one's own
  pending request is cleanup, and a cancelled occurrence is exactly when a
  member is most likely to want it.
- **R21a** Every subsystem gives the **same answer** for the same
  occurrence: a mutation refused as cancelled is also excluded from the
  pending-mark reminder, is not charged, and is shown as cancelled in the
  listing. Any pair that disagrees is a defect.
- **R21b** [X] Cancelling an occurrence **deletes** its `present`,
  `absent` and `late` records: a session that did not happen keeps no
  rows saying who was there. Leave records (`onLeave`,
  `onLeaveRequested`) are member-initiated with their own approval
  lifecycle and are kept. Any credit charged for the occurrence is
  refunded **before** the rows go, since the refund finds whom to repay
  through them (credit R48). Cancellation is deliberate and
  double-confirmed; a cancellation made in error loses the register, and
  the `attendance_marked` audit rows are the manual recovery path (#337).
- **R21c** [X] A series cutoff — a camp cancellation or a programme
  termination — does the same for every occurrence at or after the
  cutoff. Under the ordinary rules the cutoff is ahead of every open
  register, so this deletes nothing; a super-admin back-dating a camp
  cancellation (camp R5c) is the one route by which marked days fall at or
  after it, and their registers go with the cancellation (#337).
- **R21d** [X] A restored occurrence is **unmarked**. Staff mark the
  register again if the session goes ahead, and each mark charges through
  the ordinary path (credit R44a); the restore itself charges nothing
  (#337).

  Stated as an observable rather than as "one implementation", because a
  test can only see the answers. It is nonetheless written four times
  today, in three shapes, and #369 changes what it means — so one
  implementation is how this rule is expected to be kept (programme R28).

---

## Marking attendance (admin / organizer)

- **R22** [✅] Mark-attendance accepts a bulk request: one
  `(event_id, occurrence_time_utc)` and a list of
  `(member, status, notes?)` triples. Each triple is processed
  independently against the same edit-window check.
- **R22a** [✅] The bulk response reports **per member**: those marked,
  and those refused with the reason. Members settle independently — one
  member's lapsed credit never prevents another's attendance being
  recorded. The shape does not vary with configuration: a deployment not
  running on credits gets the same response with an empty refused list
  (`credit_system_requirements.md` R41b, R97).
- **R23** [✅] If a record already exists for `(event, occurrence,
  member)`, the write updates `status`, `notes`, `recorded_at`, and
  rolls the existing status into `previous_status`. If no record
  exists, a new one is inserted.
- **R24** [X] A status outside `{present, absent, late}` on the
  admin-markable path → 422 `INVALID_ATTENDANCE_STATUS`. The leave
  statuses cannot be set directly through this flow — they are
  reachable only via declare/approve.
- **R25** [✅] Marking a member who currently has `onLeave` or
  `onLeaveRequested` is allowed and overwrites the leave status; the
  prior leave status is preserved as `previous_status`. Leave is not
  a hard lock against later corrections (e.g. a member who was
  granted leave but actually showed up).

---

## Leave declaration flow (member-initiated)

- **R26** [GAP] A member may declare leave for an occurrence their
  enrollment covers (R20), **and only if they have actually joined** —
  status `accepted`, `assigned` or `assignedTrial`. At least 2 hours
  before the effective start (R15).
- **R26a** [GAP] `invited` and `requested` do not permit a leave
  declaration → 422 `INVALID_STATE`. They cover an occurrence for reading
  and for marking (R20), because staff may legitimately mark someone whose
  invitation is still outstanding on the day. But excusing yourself from
  something you have not joined is not a thing to record, and today it
  writes an attendance row months ahead for a person who may never
  accept — a row R24d then migrates across every split.

  This is the one place attendance narrows the coverage set rather than
  using it whole. Declaration sets the record to `onLeaveRequested` with the
  supplied reason; if a record already exists, its status is rolled
  into `previous_status`.
- **R27** [X] Declaring leave when the member already has
  `onLeaveRequested` or `onLeave` for the same occurrence → 422
  `LEAVE_ALREADY_DECLARED`.
- **R28** [✅] A member may cancel their own pending leave request
  (status `onLeaveRequested`); the attendance record is deleted
  outright (no `previous_status` restoration via this path).
- **R29** [X] Cancel-leave from any status other than
  `onLeaveRequested` → 422 `INVALID_STATE`.

## Leave decision (admin / organizer)

- **R30** [✅] Approve-leave transitions `onLeaveRequested → onLeave`
  and stamps `recorded_at`. The leave reason is preserved.
- **R31** [✅] Reject-leave: if `previous_status` is set, restore it
  (and clear the leave reason); otherwise delete the record. Either
  way, `recorded_at` is updated when restoring.
- **R32** [X] Approve-leave / reject-leave from any status other than
  `onLeaveRequested` → 422 `INVALID_STATE`.
- **R33** [✅] Approve-leave and reject-leave are also bound by the
  15-day edit window (R14).

---

## Listing & reading

- **R34** [✅] Staff can list the full roster of attendance records
  for a single `(event_id, occurrence_time_utc)`.
- **R35** [✅] Staff can list attendance records across a date range,
  optionally filtered by event id(s) and/or member.
- **R36** [X] A date range wider than 365 days → 422 `RANGE_TOO_LARGE`
  on the range-list flow.
- **R37** [✅] A member can list their own attendance records across
  a date range; the listing is silently filtered to occurrences their
  enrollment covers (R20). Coverage filtering applies even to past
  occurrences (R62a) — a mid-series joiner does not see records they
  could not have been marked against.
- **R38** [✅] A member can read their own attendance record for a
  single occurrence; absence of a record is a successful empty
  response (200 / null), not 404. 404 is reserved for the leave-flow
  mutations (R19) where the row's existence is a precondition.

---

## Audit logging

- **R39** [✅] Every successful mark-attendance write produces an
  audit entry capturing the actor, the affected member, the
  `(event_id, occurrence_time_utc)` pair, the new status, and the
  action `attendance_marked`.
- **R40** [✅] Leave decisions (approve / reject) and leave
  declarations / cancellations produce corresponding audit entries
  identifying the actor, the affected member, and the action.
- **R41** [✅] Failed mutations (404 / 422 / 401 / 403) do **not**
  write audit rows. The audit log records state transitions, not
  attempts.

---

## Cross-references

- Enrollment coverage of occurrences:
  [`docs/enrollment_requirements.md`](enrollment_requirements.md) —
  R60 (active-enrollment coverage for present/future), R61 (audit-
  correct historical coverage by `enrolled_at`/`withdrawn_at`),
  R62 (write rejection / read filtering on uncovered occurrences),
  R62a (mid-series joiners not retroactive).
- Camp-side framing and cancellation semantics:
  [`docs/camps_requirements.md`](camps_requirements.md) — see camp
  R84 (attendance-coverage rejection from the camp perspective) and
  the cancellation rules referenced by R21 above.
- Worked end-to-end examples (declare/approve/reject/cancel and the
  enrollment ↔ attendance interplay) live in
  [`docs/enrollment-attendance-workflows.md`](enrollment-attendance-workflows.md).
