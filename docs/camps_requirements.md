# Camps — Requirements

This document is the testable specification for the camps subsystem,
written as plain-English use cases. It deliberately avoids naming
specific HTTP endpoints or source files so that the underlying API
surface can evolve (compact, split, rename) without invalidating the
requirements. Each rule describes *what* the system must allow or
forbid, not *how* a client reaches it.

A camp is an event of type **camp**. Programmes and one-off events are
out of scope here and are tracked separately.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — surfaced in review, not yet implemented; cites the open
  issue that introduces it.

Vocabulary:

- A camp's **eligibility criteria** = any of `gender`, `minAge`,
  `maxAge` set on the camp itself, mirroring the convention adopted for
  groups in #11. The two ages are an age band
  ([eligibility](eligibility_requirements.md) R1), reported as the window
  `dobOnOrAfterUtc`–`dobOnOrBeforeUtc` it comes to on the camp's start day.
- An **enrollment** carries one of the statuses: `invited`, `requested`,
  `accepted`, `rejected`, `assigned`, `assignedTrial`,
  `withdrawRequested`, `withdrawn`, `declined`, `removed`. Active =
  anything not in the terminal set
  `{declined, withdrawn, removed, rejected}`.
- An **occurrence** is one expansion of a camp's recurrence rule (or
  the single start/end pair if non-recurring). Attendance is recorded
  per occurrence.
- **Organizer** = the user named as the camp's organizer. The organizer
  may act with admin-level rights on their own camp; everyone else
  needs the `admin` role.
- **Staff** = users with the `admin` or `coach` role, or super-admin.

---

## Camp lifecycle (admin)

- **R1** [✅] An admin or a coach can create a camp.
- **R2** [✅] An admin or the camp's organizer can update a camp's
  metadata in place: title, description, visibility, venue, organizer,
  coaches, start time, end time and recurrence rule. Edits apply across
  the whole camp; there is no per-occurrence "correction" or
  "future-split" workflow here.
- **R2a** A camp has **exactly one schedule**
  ([`event_schedule_model.md`](event_schedule_model.md)), created with it
  and never joined by a second — a camp cannot be split. Updating its
  times, rule, venue, organizer, coaches or occurrences writes that one
  schedule; updating its title, description or visibility writes the
  event. The distinction does not surface to a caller, which sees one
  update.
- **R2c** [X] A camp's **schedule** — its start, end, recurrence rule
  and occurrences — may be changed only while nothing has been recorded
  against its occurrences. Once any attendance record exists those fields
  are frozen → 422 `EVENT_ALREADY_STARTED`; once an occurrence override
  exists → 409 `OCCURRENCE_OVERRIDES_PRESENT`, unless the caller asks for
  the overrides to be reset. An earlier draft named a single
  `INVALID_STATE`; the two codes are the ones the surface has always sent.

  Attendance and overrides are keyed by occurrence slot, so moving the
  slots orphans them: a register marked for a Tuesday would belong to an
  occurrence that no longer exists. Programmes forbid in-place schedule
  changes outright for the same reason (R26); a camp is short and usually
  edited minutes after a mistake, so the narrower rule is enough.

  Title, description, visibility, venue, organizer and coaches stay
  editable throughout — none of them moves an occurrence.
- **R2d** To move a camp's days once it has begun to be recorded, cancel
  it and create another. There is deliberately no re-keying of existing
  records onto new slots: "the nearest surviving occurrence" is a guess
  about what a human meant, and a wrong guess moves someone's attendance
  to a day they were not there.
- **R2b** [X] The series end is **not** settable through update. A
  camp's length is its rule's `COUNT`, and its end is derived from that;
  `UNTIL` is written only by cancelling (R5a). An earlier draft listed
  "series end time" among the updatable fields, which allowed two
  independent statements of when a camp finishes.
- **R3** [X] The programme-style "correct this and prior occurrences"
  workflow is not available for camps; attempting it is rejected with
  400 `INVALID_EVENT_TYPE` (`event_type_matrix.md`).
- **R4** [X] The programme-style "split future occurrences off as a new
  series" workflow is not available for camps; attempting it is
  rejected with 400 `INVALID_EVENT_TYPE`. Camps are bounded series
  (a `COUNT`, R18), so the split-on-effective-date flow does not apply.
- **R5** [✅] An admin or organizer can cancel a camp series, supplying
  a required reason and an effective time.
- **R5a** Cancelling sets the series **cutoff** to that effective time,
  exactly as terminating a programme does — the storage and the mechanism
  are the same, and only the intent differs
  ([`event_lifecycle_requirements.md`](event_lifecycle_requirements.md)
  L6, L9). The camp runs normally up to the cutoff; occurrences at or
  after it do not happen (L14).
- **R5b** [✅] Enrollment therefore stays **open until the cutoff**, not
  until the moment of the call (L21). A camp cancelled with a future
  effective time is still running, and members may still join it for the
  occurrences that remain.
- **R5c** The effective time must match a camp occurrence start, and a
  super-admin may bypass that for a retrospective cancellation
  (R82's lead-time guard is the per-occurrence equivalent). Undo is
  R5d.
- **R5d** [✅] An admin or organizer can undo a series cancellation,
  clearing the cutoff and returning the camp to scheduled.
- **R6** [✅] An admin can soft-delete a camp.
- **R7** [✅] An admin can restore a soft-deleted camp.
- **R7a** [X] Restoring a camp that is not deleted → 422
  `NOTHING_TO_RESTORE`, and the camp is unchanged (#526).
- **R8** [X] A regular admin cannot hard-delete a camp → 403.
- **R9** [✅] A super-admin can hard-delete a soft-deleted camp.
- **R10** [X] Hard-delete on a camp that is not soft-deleted is
  rejected → 422 `HARD_DELETE_NEEDS_SOFT_DELETE` (#526).
- **R11** [X] Soft-deleted camps do not appear in the default camp
  listing.
- **R12** [✅] An admin can list deleted camps via a dedicated "deleted"
  view.
- **R13** [✅] An admin can set structured eligibility on a camp:
  `gender`, `minAge`, `maxAge`, `strictAge`. Both ends of the window the
  band comes to are inclusive at the day level.
- **R14** [✅] An admin can set `isFeatured`, `galleryUris`, and an
  ordered `sessions` timetable directly on the camp. `isFeatured` and the
  gallery are presentation rather than event management — see programme
  R22a. An earlier draft also listed `imageUri`; no such field exists
  anywhere in the model or the API.
- **R15** [X] An inverted age band (`minAge` greater than `maxAge`,
  which is an inverted window) is rejected → 422 `INVALID_STATE` (mirrors
  R12 in groups).
- **R16a** [X] A coach who is not the camp's organizer cannot update,
  cancel, or delete the camp → 403 / `INSUFFICIENT_PERMISSION`.
- **R16b** [✅] A coach who *is* the organizer can act with admin-level
  rights on their own camp (same as any non-admin organizer).

## Camp recurrence

- **R17** [X] A camp's recurrence rule must be daily → 422
  `INVALID_RRULE_FOR_CAMP`.
- **R18** [X] A camp's recurrence rule must be bounded by a `COUNT`.
  Unbounded camps, and a rule carrying `UNTIL`, are rejected → 422
  `INVALID_RRULE_FOR_CAMP`: a camp's end is derived from its `COUNT`, and
  a cutoff is written only by cancelling (R2b, #388). An earlier draft
  accepted `UNTIL` as a second way of bounding a camp.
- **R19** [✅] A camp's recurrence rule may include explicit
  exception dates (`EXDATE`). An excepted day is a **rest day**: no camp
  happens on it. A rest day is named by the UTC start of the occurrence
  it removes; an `EXDATE` at any other instant removes nothing.
- **R19a** [✅] **A camp's `COUNT` is the number of days it is held.**
  Rest days are not counted: the rule is expanded until it has given
  `COUNT` occurrences that are not rest days, so `FREQ=DAILY;COUNT=7`
  with three `EXDATE`s inside its run is a camp of seven days across ten.

  A club that wants seven camp days across a ten-day window composes the
  rule as `FREQ=DAILY;COUNT=7` with its three rest days as `EXDATE`s. The
  rule is stored and presented exactly as given; there is no camp-days
  field, because `COUNT` is that number.

  Decided 2026-10-08 (#30): the requirement follows what the server and
  the SDK's expansion have always done. It replaces the reading of
  2026-09-04 (#394), under which `COUNT` bounded the rule before rest
  days were removed. A client that expands a stored rule itself applies
  this rule; a plain RFC 5545 expansion gives a camp shorter by its rest
  days.
- **R19b** [✅] A camp's end — its schedule's `effective_until`
  ([`event_schedule_model.md`](event_schedule_model.md)) — is the day
  after its last generated occurrence. It is derived by expanding the
  stored rule as R19a says, so it lies past the rest days the rule
  names.
- **R19c** A rest day and a cancelled occurrence are different facts
  and are recorded differently, which is why a camp has both.

  An `EXDATE` is part of the camp's **shape**: the day was never going to
  run, it is stated when the camp is planned, and there is nothing to
  explain because nothing changed. A cancelled occurrence (R82) is a day
  that *was* going to run and was called off, so it carries a reason, an
  audit row and a notification.

  In a listing they look different too, and correctly: a skipped day does
  not appear at all, a cancelled one appears marked cancelled.
- **R19d** This is why programmes forbid `EXDATE` (programme R14) while
  camps keep it. A camp is planned whole and then run; a programme is
  open-ended, so a skipped week is always a change to something already
  running, never a fact about the original plan.
- **R20** [X] Any other recurrence shape (non-daily, weekly,
  monthly, unbounded) is rejected → 422 `INVALID_RRULE_FOR_CAMP`.

## Camp reads

- **R21** [✅] Admin / coach can list camps, optionally filtering by
  type.
- **R22** [✅] Admin / coach can view a single camp by id.
- **R23** [✅] Admin / coach can list a camp's enrollments, optionally
  filtered by enrollment status.
- **R24** [✅] Admin / coach / organizer can list a camp's occurrences
  and the per-occurrence attendance.
- **R25** [X] A plain user cannot reach admin-level camp views → 403.
- **R26** [X] An anonymous caller cannot reach any camp view → 401.

## Conflict checks (camps are flag-don't-block)

- **R27** [✅] Creating a camp at a venue already booked by
  another event succeeds. The legacy "venue already booked" 409 is
  suppressed for camps.
- **R28** [✅] Joining a camp succeeds even where the member has
  an overlapping enrollment elsewhere: the time-conflict 409 is suppressed
  for camps.

  The paths this actually affects are **assign, self-request and accept**
  (`enrollment_requirements.md` R29, R36, R45) — those are the three that
  run the check for any type. Invite and approve never run it at all
  (enrollment R26, R43), so naming them as "suppressed for camps"
  described nothing.
- **R29** [✅] The system can report potential conflicts for a
  proposed camp schedule (venue, organizer, coaches) without committing
  to creating the camp. The report is a per-occurrence breakdown of
  venue, organizer, and coach overlaps.
- **R30** [✅] The system can report which users in a given list
  have time conflicts against an existing camp.
- **R31** [✅] Both conflict-check operations accept **any** event type.
  The camp-only restriction was v1 of #16 and is lifted: one module with
  named gates serves every type, and what differs is whether a finding
  blocks or is reported (programme R30–R31c, one-off R20–R21a).
- **R32** [✅] Two occurrences overlap iff
  `start_a < end_b AND end_a > start_b`. This is **the** definition of
  overlap; every gate uses it (programme R31b).
- **R32a** Evaluating it across the full occurrence sets of both sides
  works only when both are bounded, which camps and one-offs are — a camp
  by its `COUNT`, a one-off by being single, and both by the 52-week
  scheduling horizon (one-off R20a). An open-ended programme has no such
  set, so a programme-versus-programme comparison is made rule-to-rule
  instead, without expanding either (programme R30a).
- **R33** [✅] Exception dates (`EXDATE`) on the target camp
  remove those days from the conflict report.
- **R34** [✅] Cancelled candidate events are excluded from
  conflict reports — per occurrence: a cancelled override, or a slot at
  or after the cutoff (lifecycle L14). A bounded event still counts up to
  its cutoff (enrollment R29b).
- **R35** [✅] Coach overlap is computed by intersecting the coach lists
  of overlapping events (the coach gate, programme R31b).
- **R36** [X] The legacy "find available time slot" operation is removed
  → 404. No route exists for it; verified by reading the routers rather
  than by a test (#373).
- **R37** [X] The legacy per-venue "check conflict" operation is removed
  → 404. As R36.
- **R38** [X] Programme creation returns 409 on a venue overlap **with
  another programme**. It does not, and must not, block on an overlap
  with a camp or a one-off: behaviour follows the *pairing*, not the type
  (programme R30, R30c). The rule previously did not say which, and read
  as though any overlap blocked.
- **R39** [X] Joining a programme with a time-overlapping enrollment
  returns 409 only where the clash is with **another programme**; the
  member gate reports rather than blocks for every other pairing
  (programme R30c, one-off R20).

## Eligibility (camps, post-#18)

- **R40** [✅] Camp eligibility mirrors group eligibility: a user is
  eligible iff their `gender` matches (when set) and their date of
  birth falls within `[dobOnOrAfterUtc, dobOnOrBeforeUtc]` inclusive
  (when set) — the window its age band comes to on its start day.
- **R41** [X] A user with no recorded date of birth is ineligible for
  any camp that has either age bound set.
- **R42** [X] A user with no recorded gender is ineligible for any
  camp that has `gender` set.
- **R43** [✅] Tightening eligibility on a camp does **not** auto-
  remove already-enrolled users (grandfathering).
- **R44** [X] The legacy free-text eligibility / eligibility-note
  fields on the aux-info side table are removed; the only authoritative
  eligibility is the structured criteria on the camp.

## Inviting and assigning (admin / organizer)

- **R45** [✅] An admin or organizer can invite users to a camp;
  invited users start at status `invited`.
- **R46** [✅] An admin or organizer can assign users directly to a
  camp without consent; assigned users start at status `assigned`.
- **R47** [X] Trial enrollment is not available for camps; attempting it
  is rejected with `EVENT_TYPE_NOT_SUPPORTED`. A trial is a way of
  attending part of a recurring series without joining it, which is a
  programme concept. It is bounded by the balance of the trial credit
  account funding it, not by a count of occurrences (programme R32d) —
  an earlier draft of this rule said otherwise.
  (Corrected 2026-09-02: previously recorded as a camp ability, which the
  implementation has never allowed.)
- **R48** [X] Inviting / assigning an unknown user → 404 `USER_NOT_FOUND`.
- **R49** [X] Inviting / assigning a soft-deleted user → 404.
- **R50** [X] Inviting / assigning on a non-existent or soft-deleted
  camp → 404 `EVENT_NOT_FOUND`.
- **R51** [X] Join-side flows on a camp with no live occurrence left →
  422 `INVALID_STATE` (super-admin override allowed for retrospective
  corrections). "Ended" is not defined here: it is the single join
  predicate in
  [`event_lifecycle_requirements.md`](event_lifecycle_requirements.md)
  L12, which covers a camp whose last occurrence has run, one cancelled with
  a past cutoff, and one whose every remaining occurrence is cancelled.
- **R52** [X] Inviting when a **non-terminal** enrollment already exists
  → 422 `ALREADY_ENROLLED`. Assign and self-request over an existing row
  behave differently and are specified once, in
  `enrollment_requirements.md` R27 and R28: assign upgrades an `invited`
  or `requested` row and only the *enrolled* set blocks it. A terminal row
  is never overwritten by any path — rejoining writes a new row
  (enrollment R6a).
- **R53** [✅] Inviting when only a terminal enrollment exists
  (`declined` / `withdrawn` / `removed` / `rejected`) replaces the
  status with `invited`, recording the previous status.
- **R54** [X] Inviting an ineligible user → 422
  `USER_NOT_ELIGIBLE_FOR_EVENT`. Eligibility is also enforced when
  invite replaces a terminal-state enrollment (R53 path). The
  super-admin past-event override (R51) does **not** apply to
  eligibility — eligibility is a data-safety invariant; super-admins
  must adjust the user's profile or the camp's criteria instead.
- **R55** [X] Assigning an ineligible user → 422
  `USER_NOT_ELIGIBLE_FOR_EVENT`. (Trial assignment is out of scope for
  camps — see R47.)
- **R56** [✅] Assigning a user to a camp runs the member gate as a report,
  never a block (R28): the assignment succeeds whatever it overlaps.

## Decisions on enrollment requests (admin / organizer)

- **R57** [✅] An admin or organizer can approve a `requested`
  enrollment → status becomes `accepted` and the enrollment time is
  stamped.
- **R58** [✅] An admin or organizer can reject a `requested`
  enrollment with an optional reason → status becomes `rejected`.
- **R59** [X] Approve / reject on a non-`requested` enrollment → 422
  `INVALID_TRANSITION`.
- **R60** [X] Approve / reject on a missing enrollment → 404
  `ENROLLMENT_NOT_FOUND`.
- **R61** [X] Approve / reject on an ended camp → 422 (super-admin
  override allowed).
- **R62** [X] Approval re-checks eligibility; if the requester is no
  longer eligible → 422 `USER_NOT_ELIGIBLE_FOR_EVENT`, and the
  enrollment stays `requested` (mirrors R66 in groups).
- **R63** [✅] An admin or organizer can remove an enrollment with an
  optional reason → status becomes `removed`, withdrawal time stamped.

## Withdrawal flow

- **R64** [✅] An enrolled user can request withdrawal from their own
  enrollment (allowed only from `accepted`, `assigned`,
  `assignedTrial`) → status becomes `withdrawRequested`.
- **R65** [✅] A user can cancel their own pending withdrawal request
  → status restored to the previous status.
- **R66** [✅] An admin or organizer can approve a withdrawal request
  → status becomes `withdrawn`, withdrawal time stamped.
- **R67** [✅] An admin or organizer can reject a withdrawal request
  → status restored to the user's previous status.
- **R68** [X] Withdrawal mutations on an ended camp → 422 (super-admin
  override).

## Member self-service

- **R69** [✅] A user can list the camps they are involved with.
- **R70** [✅] A user can list their own occurrences and attendance.
- **R71** [✅] Admin / coach / super-admin can view another user's
  member view on their behalf.
- **R72** [X] One plain user cannot read another user's member view
  → 403.
- **R73** [X] An anonymous caller cannot reach the member views → 401.
- **R74** [✅] A user's camps listing filters out public camps the user
  is ineligible for **and** has no **non-terminal** enrollment on. A camp
  they are enrolled in stays visible regardless of current eligibility
  (grandfathering).

  "Non-terminal" is `enrollment_requirements.md`'s set, which includes
  `invited` and `requested`: a pending invitation is reason enough to keep
  seeing the camp it is for.
- **R75** [✅] A user can accept an invitation → status `accepted`,
  enrollment time stamped. Eligibility is **not** re-checked at accept;
  the `invited` row is the locked-in eligibility snapshot. Tightening
  the camp's criteria after invite does not block the user from
  accepting (mirrors R43 grandfathering).
- **R76** [✅] A user can decline an invitation → status `declined`.
- **R77** [✅] A user can request to join a camp → status `requested`.
  The time-conflict check does not run (R28).
- **R78** [X] A user requesting to join a camp they are ineligible
  for → 422 `USER_NOT_ELIGIBLE_FOR_EVENT`.
- **R79** [X] A user cannot create a second **live** enrollment → 422
  `ALREADY_ENROLLED`. A terminal one does not block: rejoining writes a
  new row rather than reusing the closed one
  (`enrollment_requirements.md` R6, R6a).
- **R80** [✅] Join-side flows are blocked once the camp has no live
  occurrence left → 422 (super-admin override). Exit-side flows stay open.
  "Ended" is defined once, in
  [`event_lifecycle_requirements.md`](event_lifecycle_requirements.md)
  L12 — not here, and not in three other documents.

## Occurrences and attendance

- **R81** [✅] An admin or organizer can reschedule a single occurrence.
- **R81a** [X] A reschedule may only move an occurrence **later** → 422
  `POSTPONE_ONLY`. The reasoning is not type-specific: members were told a
  time, and pulling a day forward can make someone miss a day that had
  already happened by the time they looked. Postponing asks people to
  wait, which is recoverable. Same rule as programme R19a and one-off
  R16a.
- **R81b** [X] An occurrence may not be rescheduled within 30 minutes of
  its effective start → 400 `RESCHEDULE_LEAD_TIME_VIOLATED`. The register
  has opened by then
  (`attendance_requirements.md` R12), and a day whose register is open
  cannot be moved out from under it. Same rule as programme R19b's
  companion and one-off R16b.

  The lead-time guard was already in place for camps; the postpone-only
  guard (R81a) replaces the weaker "not in the past" rule, and the camp
  test that pinned the old code changes with it.
- **R82** [✅] An admin or organizer can cancel a single occurrence and
  undo the cancellation.
- **R82a** [X] Cancelling is likewise barred within 30 minutes of the
  effective start, for the same reason (programme R17, one-off R5), with
  a super-admin retrospective override (programme R17a) → 400
  `CANCELLATION_LEAD_TIME_VIOLATED`. Camps already carried this guard;
  the marker was stale (#373).
- **R83** [✅] An **admin, or the organizer of this camp**, can mark
  per-occurrence attendance: `present`, `absent`, `late`. A coach who is
  neither an admin nor the organizer is rejected → 403
  `INSUFFICIENT_PERMISSION`.

  An earlier draft granted this to any coach and included `onLeave` in
  the markable set. Neither is so: `attendance_requirements.md` R7 owns
  the authorization rule, and R24 restricts the admin-markable statuses to
  `{present, absent, late}` — both leave statuses are reachable only
  through declare/approve (attendance R26–R32).
- **R84** [X] Attendance writes for a user without an enrollment that
  covers the occurrence are rejected.
- **R85** [✅] For past occurrences, an enrollment "covers" the
  occurrence iff the user was enrolled at that time and had not
  already withdrawn before that time.
- **R86** [✅] For present / future occurrences, an enrollment covers
  the occurrence iff its current status is in
  `{accepted, assigned, assignedTrial, invited, requested, withdrawRequested}`.
- **R87** [✅] A user can request leave for a specific occurrence →
  attendance row marked `onLeaveRequested`.
- **R88** [✅] A user can cancel their own leave request before it is
  decided.
- **R89** [✅] An admin or organizer can approve a leave request →
  attendance becomes `onLeave`.
- **R90** [✅] An admin or organizer can reject a leave request.

## Aux-info (legacy, removed by #18)

- **R91** [X] The legacy aux-info read operation is removed → 404.
- **R92** [X] The legacy aux-info write operation is removed → 404.
- **R93** [X] Supporting types previously serialised only for aux-info
  (`Facility`, `FeeItem`, `PackageOffer`, `ClubMembership`,
  `ClubMemberBenefit`, `PromotionalOffer`, `BatchSession`, `Batch`)
  are removed from the schema layer.

## Audit logging

- **R94** [✅] Every camp mutation writes an audit row covering the actor
  and the camp: create, update, cancel, undo-cancel, soft-delete, restore,
  hard-delete.

  *Correct* and *future-update* are not in the list because a camp cannot
  perform them (R3, R4) — an audit row that no call can produce is not a
  requirement, and a test would have nothing to exercise.
- **R95** [✅] Every enrollment mutation writes an audit row covering the
  actor, the target user, and the camp: invite, assign, approve, reject,
  remove, withdraw-request, withdraw-approve, withdraw-reject,
  accept-invite, decline-invite, self-request, withdraw-cancel, retract.

  *Trial-assign* is not in the list: it is programme-only (R47), so on a
  camp there is no such row to write.
- **R96** [✅] Every occurrence-level mutation writes an audit row:
  reschedule, cancel-occurrence, undo-cancel, mark-attendance,
  request-leave, cancel-leave, approve-leave, reject-leave.

## Visibility rules

- **R97** [✅] A camp marked `private` is listed only to staff and to
  users holding a **non-terminal** enrollment on it. A member who withdrew
  or was removed stops seeing it.
- **R98** [✅] A camp marked `public` is listed to all authenticated
  users (subject to R74 once #18 lands).
- **R99** [X] A visibility value outside the supported set is rejected
  on create / update → 422.

## Sessions / timetable (post-#18)

- **R100** [✅] An admin can attach an ordered `sessions` timetable to
  a camp. Each occurrence is `{name, periodMinutes}`; the list order is
  the schedule order. Optional — omitting / clearing means "no
  timetable".
- **R101** [X] Sessions whose period total does not equal the per-
  occurrence window in minutes are rejected → 422
  `INVALID_SESSIONS_TOTAL`.
- **R102** [X] An empty `sessions` array is rejected → 422
  `INVALID_SESSIONS_EMPTY`. Clear a timetable by sending null, not an
  empty list.
- **R103** [X] An occurrence with `periodMinutes <= 0` is rejected at the
  schema layer → 422.
- **R104** [X] Updating the camp window such that the existing
  `sessions` no longer sum to the new window, without supplying
  replacement occurrences in the same call, is rejected → 422
  `INVALID_SESSIONS_TOTAL`. Updating window and occurrences consistently
  in one call is accepted.
- **R105** [✅] A camp's `sessions` timetable may also be corrected through
  the ordinary update (`PATCH /events/by_id/{id}`) at any time, including
  after the camp has started (#423), validated against the camp's
  occurrence length (R101). The window itself still moves only through
  `/reschedule`, which refuses a camp that has started.
