# One-off events — Requirements

This document is the testable specification for one-off events, written
as plain-English use cases. Like `camps_requirements.md` it deliberately
avoids naming HTTP endpoints or source files, so the API surface can
evolve without invalidating the requirements.

A one-off is an event of type **oneOff**: a single occurrence on a single
day, with no recurrence. Camps are specified in
[`camps_requirements.md`](camps_requirements.md); programmes in
[`programme_requirements.md`](programme_requirements.md).

**Status: implemented on the server (#384).** The client side is still
unbuilt, so these rules are exercised by the test suite rather than by the
app. Every rule is settled; none is marked [OPEN]. Each rule's test names
it with `@pytest.mark.requirement("oneoff:Rn")`.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[OPEN]** — not yet decided.

---

## What makes a one-off different

A one-off has exactly one occurrence, so the distinction between "the
event" and "the occurrence" collapses. Every concept the other two types
need in order to talk about *part* of a series — a cutoff, a series end
time, a split, a recurrence rule — is meaningless here.

This is why a one-off is **dropped**, not cancelled-from-a-date and not
terminated: there is nothing to be partial about. It either happens or
it does not.

---

## Lifecycle

- **R1** [✅] An admin or a coach can create a one-off event.
- **R2** [✅] An admin or the event's organizer can update a one-off,
  including its schedule and venue.
- **R3** [GAP] An admin or organizer can **drop** a one-off, supplying a
  required reason. A dropped event does not happen.
- **R4** [GAP] Dropping takes no effective time. There is one occurrence,
  so there is nothing for a cutoff to select. A caller supplying one is
  rejected → 422 `INVALID_STATE`, rather than having it silently
  ignored.
- **R5** [X] A one-off may be dropped up to 30 minutes before its
  effective start. Within that window it cannot → 400
  `CANCELLATION_LEAD_TIME_VIOLATED`: the register has opened, so
  "droppable" and "register open" never overlap. The boundary is
  inclusive, matching the register's own (`attendance_requirements.md`
  R12).
- **R5a** [✅] A **super-admin may drop a one-off retrospectively**,
  bypassing R5 — the occasion was called off and nobody told the server.
  It is the only way a marked one-off becomes cancelled, and the credit
  consequence is settled in `credit_system_requirements.md` R48c.
- **R6** [GAP] An admin or organizer can **reinstate** a dropped one-off,
  returning it to scheduled.
- **R7** [GAP] A one-off may be reinstated only while it has not started.
  Once its start time has passed it cannot be reinstated: the occasion is
  gone, and reinstating it would claim an event took place that nothing
  can evidence. Running it after all means creating a new event.
- **R8** [GAP] Dropped-ness is recorded on the event's single occurrence,
  as a cancelled occurrence with its reason — not by setting a series end
  time. A one-off has no series, and a series end time carrying the
  meaning "this is cancelled" is an overload that makes the value mean
  two different things across event types.
- **R9** [✅] An admin can soft-delete a one-off, and restore it.
- **R10** [X] A regular admin cannot hard-delete a one-off → 403.
- **R11** [✅] A super-admin can hard-delete a soft-deleted one-off.
- **R12** [X] Dropping and deleting are distinct: a dropped event is
  still a record of something that was planned and called off; a deleted
  one is a record removed in error. Neither implies the other.

## Recurrence

- **R13** [X] A one-off carries no recurrence rule. Supplying one is
  rejected → 422 `INVALID_RRULE_FOR_ONEOFF`.
- **R14** [X] A one-off carries no series end time and no recurrence
  rule.
- **R14a** A one-off has **exactly one schedule**
  ([`event_schedule_model.md`](event_schedule_model.md)) with a null rule,
  created with it and never joined by a second — a one-off cannot be
  split. Its `effective_until` stays unset: the occasion's end is the end
  of its single occurrence, and a cutoff on a one-off would read as a
  series end (R8, R14). An earlier draft set it to the end time, which
  the API would have reported as `untilTimeUtc` on every one-off.

  This is why R8's decision to record a drop on the occurrence rather than
  as a series end still stands: the schedule says when the occasion is,
  and cancelling the occasion is a fact about the occurrence, not about
  the timetable.
- **R15** [X] The programme-style correction and future-split workflows
  are not available for a one-off; attempting either is rejected with
  400 `INVALID_EVENT_TYPE` — the code the server has always sent for a
  verb a type does not offer (`event_type_matrix.md`).

## Timetable

- **R22** [✅] A one-off's single occurrence carries a `sessions` timetable
  on **exactly the same terms as every other type** (camp R100–R104,
  programme R20): an ordered list of `{name, periodMinutes}` whose period
  total equals the occurrence length, never empty, no non-positive period,
  `NULL` to clear. There is one validator with no event-type branch.

  Because a one-off has exactly one schedule (R14a), the timetable is
  edited in place; there is no split. Changing the length of the occasion
  and the timetable in the same call is accepted; changing the length
  alone, while a timetable is set, is rejected → 422
  `INVALID_SESSIONS_TOTAL`, as it is for a camp (R104).

  Moving the occurrence on its own terms cannot change its length at all:
  the timetable belongs to the schedule, so a per-occurrence reschedule
  that asks for a different duration is rejected → 422
  `INVALID_SESSIONS`, on the same reasoning as programme R20b. Its start
  and venue may still move.

  The timetable is the only representation of "what happens inside the
  occasion". The old public site's free-text `timings` and `batches`
  (from the retired aux-info table) map onto it; nothing else carries
  timing.
- **R22a** [✅] The timetable may also be corrected through the ordinary
  update (`PATCH /events/by_id/{id}`) at any time, including after the
  one-off has started (#423), on the terms of R22 against the occasion's
  length. It is a description, like the title: correcting it moves nothing.
  The occasion's times still move only through `/reschedule`, which
  refuses one that has started.

## Occurrences and attendance

- **R16** [✅] An admin or organizer can reschedule the occurrence.
- **R16a** [X] A reschedule may only move the occasion **later**. A new
  start earlier than its current effective start → 422 `POSTPONE_ONLY`.
  The reasoning is the same as for a programme occurrence and is not
  type-specific: people have been told a time, and pulling the occasion
  forward can make someone miss an event that had already happened by
  the time they looked. Postponing asks them to wait, which is
  recoverable. See programme R19a.
- **R16b** [X] The occurrence may not be rescheduled within 30 minutes
  of its effective start → 400 `RESCHEDULE_LEAD_TIME_VIOLATED`, on the
  same terms and for the same reason as R5: the register has opened.
  "Reschedulable" and "register open" never overlap either.

  Today neither guard exists for a one-off. Only camps are checked, and
  only against a start in the past; `new_start_time` is otherwise
  unbounded, so a one-off three weeks out can be moved to yesterday.
  Tracked as #375.
- **R17** [GAP] Dropping the event and cancelling its occurrence are the
  same operation under two names, and both are offered: *drop* reads
  naturally for something with a single occasion, while occurrence-cancel
  keeps the surface uniform across event types. They resolve to one
  implementation, so they cannot drift apart or disagree about state.
- **R18** [✅] An admin / coach / organizer can mark attendance for the
  occurrence.
- **R19** [GAP] Attendance mutations on a dropped one-off are rejected,
  by the same rule that rejects them on any cancelled occurrence.

## Everything else

The following carry over from `camps_requirements.md` unchanged, reading
*camp* as *one-off event*: eligibility and DOB windows (camp R13, R15,
R40–R44); organizer-versus-coach permissions (R16a–R16b); reads and
their 401/403 rules (R21–R26); invite and assign and their error cases
(R45–R46, R48–R55); enrollment decisions (R57–R63); withdrawal
(R64–R68); member self-service (R69–R80); leave (R87–R90); audit
(R94–R96); visibility (R97–R99).

Trial enrollment (camp R47) does not apply: a trial runs across several
occurrences of a series, and a one-off has one.

## Conflicts

- **R20** [GAP] A one-off follows camp semantics: a clash is
  **reported, never blocked**, whatever it clashes with — a camp, another
  one-off, or a programme. This includes the **member** gate: joining a
  one-off that overlaps something else the member is in succeeds.

  `enrollment_requirements.md` R29, R36 and R45 block on member overlap,
  with the suppression written as camp-only. That carve-out widens to
  every non-programme pairing: blocking is programme-versus-programme
  alone (programme R30, R30c). A one-off is a single occasion, and the club
  scheduling one on top of something else is a decision it is entitled to
  make with the facts in front of it.
- **R20a** [GAP] A one-off may not be scheduled more than **52 weeks**
  from now, and neither may a camp. This is a hard bound, not a
  convention. It keeps both types finitely expandable, which is what lets
  a clash against a programme be reported cheaply: the programme is
  compared as a rule (programme R30a) and the one-off as its single
  occurrence.

  The bound is stated in weeks rather than as "a year" because the
  scheduling it governs is weekly: 52 weeks is the same distance ahead
  whichever year it is asked in, where a year is not.
- **R20b** [GAP] The 52 is **configurable per deployment**, defaulting to
  52. Unlike the attendance windows, which are hard-coded on purpose
  (`attendance_requirements.md` R16), this is a scheduling policy rather
  than an invariant: it protects nothing but the cost of a conflict
  report, and how far ahead a club plans is a fact about the club. A
  deployment that raises it pays in report size and nothing else.
- **R21** [GAP] Conflict detection runs on every path that sets a
  one-off's schedule — creation, reschedule (R16), the generic update
  (R2), and drop-reinstate (R6) — and reports on each. Reporting on a
  mutation is not optional merely because it does not block.
- **R21b** [GAP] The generic update may change a one-off's times and
  venue, and when it does it enforces **exactly** the same guards as
  reschedule: postpone-only (R16a), the 30-minute lead (R16b), and
  conflict reporting (R21). Two ways in, one set of rules.

  Today it enforces none of them, so both guards are bypassed by calling
  update instead of reschedule.
- **R21c** The two paths must give the **same answer** for the same
  change: a move update refuses is a move reschedule refuses, with the
  same code. Stated as an observable because that is what a test can see —
  and because a guard written twice is how this codebase acquired five
  copies of the cancelled-occurrence predicate and three conflict engines.
  One implementation, called from both, is how it is expected to be kept.
- **R21a** [GAP] Detection is the shared gate module, not a one-off
  implementation: venue, organizer, coach and member, each computing a
  finding that the caller's policy then acts on. See programme R31b.

---

## See also

- [`camps_requirements.md`](camps_requirements.md) — the source of the
  shared rules listed above.
- [`programme_requirements.md`](programme_requirements.md) — the
  terminate/extend lifecycle a one-off deliberately does not have.
- [`attendance_requirements.md`](attendance_requirements.md) —
  attendance is event-agnostic; R18–R19 defer to it.
