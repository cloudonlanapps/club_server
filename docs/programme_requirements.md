# Programmes — Requirements

This document is the testable specification for programme events, written
as plain-English use cases. Like `camps_requirements.md` it deliberately
avoids naming HTTP endpoints or source files, so the API surface can
evolve without invalidating the requirements.

A programme is an event of type **programme**: a weekly recurring series,
optionally open-ended. Camps are specified in
[`camps_requirements.md`](camps_requirements.md); one-off events in
[`oneoff_requirements.md`](oneoff_requirements.md).

**Status: implemented (#384).** Every rule is settled; none is marked
[OPEN]. The tests are the evidence: each rule's `test_*` function names it
with `@pytest.mark.requirement("programme:Rn")`, and
`tests/test_requirement_coverage.py` fails when one has none.

**Rewritten 2026-09-03 for the schedule model.** A programme is one event
whose timetable is a sequence of schedules
([`event_schedule_model.md`](event_schedule_model.md)), not a chain of
events linked by successor pointers. Rules that existed only to keep a
chain consistent are gone, and the text says so where a reader might
otherwise wonder what happened to them.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[OPEN]** — not yet decided.

Vocabulary:

- **Occurrence** — one instance of the series: an occasion people turn up
  for, keyed by its slot time. Rules about the cutoff, the register and
  cancellation are all about occurrences.
- **Session** — an entry in an occurrence's `sessions` timetable: a
  sub-period *within* one occurrence, such as a warm-up. The two words
  were used interchangeably in an earlier draft, which made rules about
  "a session start" ambiguous.
- **Schedule** — one period of a programme's timetable: its recurrence
  rule, times, venue, coaches and `sessions`, valid over
  `[effective_from, effective_until)`. A programme has one or more,
  contiguous and non-overlapping. See
  [`event_schedule_model.md`](event_schedule_model.md).
- **Current schedule** — the last one. It is open when its
  `effective_until` is unset.
- **Cutoff** — the instant from which a programme stops running, held as
  the current schedule's `effective_until` and carried in its rule as
  `UNTIL`. An occurrence starting at or after the cutoff does not happen
  under that schedule.
- **Terminate** — end a running programme at a cutoff, with no schedule
  after it. The occurrences before it ran and remain history; this is *not*
  a cancellation.

A programme is **one event with one id for its whole life**. It is not a
chain of events, and nothing about it is identified by anything but that
id — enrollments, attendance, overrides and credit charges all point at
it and never move.

---

## Why terminate is not cancel

A camp is short and bounded, so "the camp is cancelled" and "every
remaining occurrence is cancelled" say the same thing. A programme is
open-ended: ending one is an ordinary lifecycle event, not an
abandonment. The occurrences that already ran are history and must keep
their registers, their attendance and their credit.

Conflating the two is the root of several defects recorded below.

---

## Lifecycle

- **R1** [✅] An admin or organizer can **terminate** a running
  programme, supplying a required reason and a cutoff.
- **R2** [X] The cutoff must be an instant at which the series has a
  occurrence. Naming an occurrence start includes that occurrence in the
  termination; the previous occurrence is the last one that runs.
- **R3** [X] The cutoff must be at least 30 minutes in the future → 422
  `CUTOFF_TOO_SOON` otherwise.
  An occurrence already under way, or starting within 30 minutes, cannot be
  the cutoff — its register has opened. To call off an occurrence already in
  that window, cancel that occurrence instead.
- **R4** [✅] The cutoff is validated by matching the candidate instant
  against the **current schedule's** recurrence rule, not by enumerating
  the series. An open-ended programme of any age is therefore terminable,
  and no generation limit applies.
- **R5** [✅] A terminated programme continues to run normally until
  the cutoff: its occurrences appear, attendance is marked, and enrollment
  stays open. Members may still join, be invited or be assigned for the
  remaining occurrences. Enrollment closes at the cutoff, not at the moment
  of termination.
- **R6** [✅] An admin or organizer can **extend** a terminated
  programme by naming a new cutoff, subject to R2–R4. The new cutoff may
  be later or earlier than the current one.
- **R6a** [X] Extend requires the current cutoff to be **still in the
  future** → 422 `INVALID_STATE` once it has passed. A programme whose
  cutoff has passed has ended, and running it again means creating a
  programme.

  The reason is credit, and it is worth stating because the rule looks
  arbitrary without it. Termination releases every bound balance at the
  cutoff (R10, L20): the balances become general accounts, the members are
  notified, and a member may spend that credit on something else the same
  day. Releasing is not reversible, so an extension after the cutoff would
  restore a programme whose funding had already been dispersed — the
  members still enrolled, their credit no longer earmarked, and no way to
  put it back.

  Before the cutoff nothing has been released, so an extension costs
  nothing and restores exactly the prior state.
- **R7** [✅] Moving the cutoff earlier cancels the occurrences between the
  new and old cutoff; the members enrolled for them are notified.
- **R8** [✅] An admin or organizer can **extend a programme
  indefinitely**, removing the cutoff and returning it to open-ended.

  There is only one thing to clear: the cutoff *is* the rule's `UNTIL`,
  one fact in one place. An earlier draft had to say "clears both",
  because a stored cutoff and a rule-level `UNTIL` were separate values
  with nothing keeping them in step (review F1.9).
- **R9** [✅] Terminate, extend and extend-indefinitely act on the
  **current schedule** — the last one. An earlier schedule is a
  historical record and is not editable; there is no way to name one, so
  no rejection is needed. The caller names the programme, and the
  programme has one current schedule by construction.
- **R10** [GAP] Terminating a programme releases every balance bound to
  it. Each member's unspent bound credit moves to a general account,
  keeping its validity window, with no penalty: the club is ending the
  programme, so no member is at fault. Members are notified.
- **R10a** [GAP] Termination takes no credit disposition. An individual
  departure requires one because the outcome is genuinely a decision —
  penalty, validity, reason. A programme ending has one right answer, so
  asking would be asking an admin to justify taking credit from members
  whose programme the club has just ended.
- **R10b** [GAP] Releasing is not a refund. Credit is spent by attendance, not
  by enrollment, so a bound balance at termination is credit that was
  never charged and can no longer be spent where it was earmarked.
  Releasing widens where it may be spent and takes nothing away: general
  accounts are usable on any programme, ranked after bound ones
  (`credit_system_requirements.md` R25 — R26 is the prohibition on
  bypassing a bound account). A programme later extended (R6)
  can still be paid for from the released account.
- **R10c** [GAP] Moving a cutoff earlier (R7) releases nothing on its own.
  The occurrences it removes were never charged, since charging follows
  attendance. Only a balance that can no longer be spent anywhere on the
  programme is released, which is termination.

  Today none of this happens: `services/event.py` contains no reference to
  credit, so terminating a programme strands every bound balance silently
  — the outcome `settle_departure` exists to prevent for a single member.
  Tracked as #374.

## Recurrence

- **R11** [X] A programme's recurrence rule must be weekly and must
  name the days of the week it runs on. It must not carry an interval: a
  programme runs every week, not every second or third, and not monthly.

  This is a deliberate narrowing, taken with the conflict rules. A
  fortnightly or monthly programme cannot be expressed at all, and that
  cost is accepted: keeping every programme to one shape is what lets two
  of them be compared as rules rather than as expanded series (R30a),
  which in turn is what lets an open-ended programme be conflict-checked
  with no horizon at all. Allowing an interval would make the comparison
  a modular-arithmetic problem; allowing monthly would force one side to
  be expanded over a bounded window, reintroducing exactly the horizon
  this design avoids.
- **R12** [X] A programme is **created open-ended**. Its rule carries no
  end date, and a caller may never supply one: `UNTIL` is written only by
  terminate, extend and split, and exists for nothing else
  (`event_schedule_model.md`). A programme acquires a bound when something
  ends it, never at creation.
- **R13** [X] A programme's rule must not carry an occurrence count
  → 422 `INVALID_RRULE_FOR_PROGRAMME`.
  Counting occurrences is a camp concept; a programme is bounded by date or
  not at all.
- **R14** [X] A programme's rule must not carry exception dates
  → 422 `INVALID_RRULE_FOR_PROGRAMME`.
  Skipping a week is done by cancelling that occurrence, which records a
  reason and an audit entry; an exception date is silent.

  Camps do permit `EXDATE`, and the difference is not an inconsistency
  (camp R19–R19c). There it expresses a camp of *n* days running across a
  longer span — seven days of ice inside a ten-day window — which is a
  fact about the plan with nothing to explain. A
  programme is open-ended: there is no moment at which its whole shape is
  settled, so a skipped week is always a change to something already
  running, and the members it affects are owed a reason.
- **R15** [X] Any other recurrence shape is rejected → 422
  `INVALID_RRULE_FOR_PROGRAMME`, mirroring the camp code (camp R20).

## Occurrences

- **R16** [✅] An admin or organizer can cancel a single occurrence of a
  programme, and undo that cancellation.
- **R17** [X] A single occurrence may be cancelled up to 30 minutes
  before its effective start. Within that window it cannot → 400
  `CANCELLATION_LEAD_TIME_VIOLATED`: the register has opened, so
  "cancellable" and "register open" never overlap. The
  boundary is inclusive — a cancellation at exactly
  `effective start - 30 minutes` is allowed, matching the register's own
  boundary (`attendance_requirements.md` R12).
- **R17a** [✅] A **super-admin may cancel an occurrence
  retrospectively**, bypassing R17. This is the case where the occurrence was
  called off in the room, nobody told the server, and a register was
  marked that should not exist.

  It is the only way a marked occurrence can become cancelled, and it is
  what `credit_system_requirements.md` R48–R48c exist for: the charge is
  refunded. What happens to the attendance marks themselves is #337.
- **R18** [✅] The occurrences of an open-ended programme remain
  addressable however long it has run. Future occurrences are generated
  from the present moment forward, not from the series start, so the
  forward window does not shrink as a programme ages. Past occurrences
  remain addressable because each one that mattered left a record —
  attendance, an override, a leave request — and those are keyed by
  occurrence time; they are not re-derived from the recurrence rule.
- **R19** [✅] An admin or organizer can reschedule a single occurrence.
- **R19a** [X] A reschedule may only move an occurrence **later**. A new start
  earlier than the occurrence's current effective start → 422
  `POSTPONE_ONLY`. Members have been told a time; pulling an occurrence
  forward can make someone miss an occurrence that had already happened by
  the time they looked. Postponing asks people to wait, which is
  recoverable.

  The operation keeps the name *reschedule*: it also changes duration and
  venue, either of which may be sent without touching the start, so
  naming the whole thing "postpone" would misname those. The constraint
  is named instead, in the error a caller actually meets.

  Today only camps are guarded, and only against a start in the past
  (`services/occurrence.py:448`); for programmes and one-offs
  `new_start_time` is unbounded, so an occurrence three weeks out can be
  moved to yesterday. Tracked as #375.
- **R19b** [X] Whether an occurrence happens is decided by its **slot**,
  not by where it was moved to. An occurrence whose slot falls before the
  cutoff runs, even if it was postponed to a time after the cutoff: the
  slot is what the cutoff was matched against (R2–R4) and what an
  override migrates by (R24b), and it never changes. An occurrence whose slot
  falls at or after the cutoff does not happen, however it was moved.

  This is what attendance already does, and what the listing does not: the
  listing synthesises the series-cancelled status only for occurrences with
  no override (`services/occurrence.py:355`), so a postponed occurrence whose
  slot is beyond the cutoff is shown as live while its register refuses
  every write. The listing must apply the same rule to overridden
  occurrences, and a reschedule must reject an occurrence whose slot is
  already at or after the cutoff — today nothing stops one, because a
  series-level cutoff leaves no override row for the guard to find.
- **R20** [✅] A programme's occurrences carry a sessions timetable on
  **exactly the same terms as every other type** (camp R100–R104): an
  ordered list whose period total equals the occurrence length, never
  empty, no non-positive period, `NULL` to clear. The validation is one
  rule with no event-type branch, and that is already how it is built.

  The timetable belongs to the **schedule**, so it describes every
  occurrence that schedule produces. A timetable that changes from a date
  is a split (R23); one that was recorded wrong is corrected in place
  (R22c).
- **R20b** [X] Because the timetable is a property of the schedule and
  not of one occurrence, **a reschedule may not change the duration of an
  occurrence whose schedule carries a timetable** → 422
  `INVALID_SESSIONS`. Its start and venue may still move.

  There is no per-occurrence timetable and no rescaling: the periods are a
  plan people follow, not a ratio, and silently turning a 20-minute warm-up
  into 17 would invent times nobody chose. To run a different-length
  session, change the timetable — which for a programme is a split, and for
  a camp is bounded by camp R2c.

  Today the duration change is accepted and never revalidated, so the
  timetable simply stops adding up for that one occurrence.

## Corrections and restructuring

- **R21** [✅] An admin or organizer can correct a programme's title,
  description and visibility. The correction applies to every
  occurrence, past and future — it is for fixing mistakes, not for
  recording change.
- **R20a** [✅] An organizer and a coach are **users**, referenced by username
  and constrained as such (#386): naming one who is not a user → 404
  `USER_NOT_FOUND`. The organizer gate and the coach gate
  (R31b) need an identity to compare, not a string, and permissions are
  granted to the organizer (camp R16b). An earlier model stored the
  organizer as free text and the coaches as a JSON list of names, with no
  constraint on either.
- **R21a** [X] Coach names are not correctable. Adding or removing a
  coach is a change, not a typo, and is made by splitting the series:
  past occurrences stay attributed to whoever ran them, the new schedule
  carries the new coach, members are notified and conflict detection
  applies. Correction currently accepts coach names and writes them
  without recording a change, so the notification that the update path
  fires does not fire here — a coach can be swapped silently. Removing
  the field closes that.
- **R22** [X] Scheduling fields cannot be corrected. Times, recurrence
  and venue are not part of a correction. The timetable is the one
  exception (R22c).
- **R22c** [✅] An admin or organizer can correct a schedule's `sessions`
  timetable in place, at any time — before or after the series has
  started, with no split and no cutoff (#423). A timetable describes what
  happens inside an occurrence, as a title describes the programme:
  correcting it moves no occurrence and touches no enrollment,
  attendance, override or credit. Without `scheduleId` the **latest**
  schedule is corrected, and every other schedule keeps its own.

  Before this, a timetable recorded wrong had no repair route once the
  series had started: correction refused it, a split needs a cutoff at
  least 30 minutes ahead, and a programme is never rescheduled in place.
- **R22d** [X] `scheduleId` names the schedule to correct — one of those
  `GET /events/by_id/{id}/schedules` returns for this event. Any other id
  → 404 `SCHEDULE_NOT_FOUND`. `scheduleId` without `sessions` → 422.
- **R22e** [X] The corrected timetable is validated against **that
  schedule's** occurrence length, on the R20 terms → 422
  `INVALID_SESSIONS_TOTAL`. `null` clears it.
- **R22a** [✅] `isFeatured` and the gallery are corrected, not split.
  They describe how the programme is presented, which can be wrong
  retroactively and carries no date from which it changes.

  Neither is event management. `isFeatured` is a marketing flag, and the
  gallery is media uploaded against an event so the app and the website
  can show it — managed by the media subsystem, not by this one. They
  appear here only because they are settable fields on an event and the
  correction-versus-split question has to have an answer for every field
  (R25a). Nothing about them varies by occurrence, which is the whole
  reason they correct rather than split.
- **R22b** Title, description and visibility live on the **programme**,
  not on a schedule, so there is exactly one of each and a correction
  changes it once. Nothing can leave two parts of a programme asserting
  different names.

  This is why a split may not set them (R25a): if it could, a later
  correction and an earlier rename would contradict each other, and
  neither answer would be right (review F2.1). The question is removed
  rather than adjudicated.
- **R23** [✅] An admin or organizer can **split** a programme: the
  current schedule is closed at a cutoff and a new one opens at that same
  instant with new terms. The programme, its id, its enrollments, its
  attendance, its overrides and its credit are untouched — a split
  changes the timetable, not the programme.
- **R24** [X] The split's cutoff obeys R2–R4: it must be an occurrence
  start, at least 30 minutes ahead, matched against the recurrence rule.
  A split is therefore never able to land behind an occurrence that has
  already run.
- **R24a** [✅] The new schedule's `effective_from` **is** the cutoff,
  exactly — the same instant that closes the previous one, so the two are
  adjacent with no gap and no overlap
  ([`event_schedule_model.md`](event_schedule_model.md)). An occurrence at
  the cutoff belongs to the new schedule.
- **R24b** Occurrence overrides do not move. They are keyed by
  `(event_id, occurrence_time)`, the event id does not change, and an
  override's slot decides which schedule produced it. A cancellation or
  reschedule recorded for a date beyond the cutoff survives the split
  because nothing about it was touched.

  Under the previous chain model this was a migration (#367), and one
  that had never been built.
- **R24c** Attendance records do not move either, for the same reason:
  they are keyed by `(event_id, occurrence_time_utc, member)` and the
  event id is stable.

  Two earlier drafts of this rule were both wrong, and the sequence is
  worth keeping. The first said no migration was needed because no
  attendance could exist at or after a valid cutoff — true for *marks*,
  false for declared leave, which has no lower window bound and can be
  recorded months ahead. The second therefore specified a migration
  (#381). Under the schedule model there is nothing to migrate, and the
  fragile derivation that produced both drafts is not needed at all.
- **R24d** A split moves **nothing**: not enrollments, not attendance,
  not overrides, not credit. This is the property the schedule model
  exists for, and it is worth stating as a rule so that any future design
  that reintroduces movement is recognised as a regression.
- **R25** [X] A programme cannot be updated through the generic event
  update; corrections and splits are its only metadata paths.
- **R25a** [✅] Every settable field is reachable through one of the two
  paths, and which path follows from **where the field lives**:

  | Field | Lives on | Changed by |
  |---|---|---|
  | `title`, `description`, `visibility` | the programme | correction |
  | `isFeatured`, `galleryUris` | the programme (presentation, R22a) | correction |
  | `gender`, `minAge`, `maxAge`, `strictAge` | the programme | correction |
  | `venueId`, `organizerName`, `coachNames` | the schedule | split |
  | `startTimeUtc`, `endTimeUtc`, `rrule` | the schedule | split |
  | `sessions` | the schedule | split, or correction of one schedule (R22c) |

  A correction changes a fact about the programme that was always true and
  has one value. A split changes what happens from a date onwards, and
  everything it sets is scheduling or staffing. Only `sessions` appears in
  both, and the two cannot disagree: a split sets the timetable of the
  schedule it opens, a correction replaces the timetable of one schedule
  that exists, and each schedule has exactly one.

  Today `sessions`, `gender` and both DOB bounds are reachable through
  neither, so a programme's timetable and its eligibility criteria are
  frozen at creation.
- **R25b** A programme cannot be renamed from a date onwards. That would
  be two programmes, and it is done by ending this one and creating
  another — not by a split, which restructures a timetable and leaves the
  programme it belongs to intact.

  An earlier draft let a split set the title, on the reasoning that a
  successor might deserve a new name. It produced a rule that could not
  hold: a later correction would either destroy that name or leave two
  parts of one programme called different things (review F2.1).
- **R26** [X] A programme is never rescheduled in place. Every change to
  its times, recurrence or venue is made by splitting the series, so the
  occurrences that already ran keep the terms they ran under. A timetable
  that changes from a date is a split too; one recorded wrong is
  corrected (R22c).
  This is the answer to the open question #306 raises about programmes
  being unreschedulable: it is deliberate, not a gap.

## Trial enrollment

- **R32a** [✅] An admin or organizer can assign a trial enrollment on a
  programme; status `assignedTrial`. A trial is a way of attending some
  of a recurring series without joining it, which is why it exists here
  and not for the other two types.
- **R32d** [GAP] A trial is bounded by money, not by a count of occurrences.
  `is_trial` is a flag on the enrollment; there is no occurrence counter
  anywhere. What limits a trial is the balance of the trial credit
  account funding it, spent one occurrence at a time by attendance
  (`credit_system_requirements.md` R50, R52 — trials are not free, and an
  exhausted trial stops). An earlier draft of R32a said a trial "runs for
  a number of occurrences", which describes an implementation the project
  does not have.
- **R32e** [✅] Where a deployment does not run on credits, nothing bounds a
  trial at all: the flag never expires and the member attends
  indefinitely. That is the current behaviour on one deployment. It is recorded here
  as a fact rather than a rule — whether a credit-less deployment needs
  its own trial limit has not been asked.
- **R32b** [X] Assigning a trial to an ineligible user → 422
  `USER_NOT_ELIGIBLE_FOR_EVENT`, on the same terms as a regular
  assignment.
- **R32c** A split does not disturb a trial, and needs no rule to say so:
  a trial is an enrollment on the programme, a split changes only the
  timetable, and nothing about the enrollment is touched (R24d).

## Attendance

- **R27** [GAP] An occurrence is cancelled if **either** it carries a
  cancelled override, **or** its slot falls at or after the cutoff. Two
  routes, one rule, owned by
  [`event_lifecycle_requirements.md`](event_lifecycle_requirements.md) L14
  and stated in `attendance_requirements.md` R21.

  An earlier draft said the cutoff decides "uniformly for every event
  type", which was not true of either route: a one-off has no cutoff at
  all (one-off R8, R14), and a per-occurrence cancellation (R16, camp R82)
  has nothing to do with one.
- **R28** [GAP] Attendance mutations on a cancelled occurrence are
  rejected, and **every subsystem gives the same answer for the same
  occurrence**: refused for a mutation, excluded from the pending-mark
  reminder, not charged, shown as cancelled in the listing. Any pair that
  disagrees is a defect.

  Stated as an observable because a test can only see the answers. One
  implementation is how it is expected to be kept (attendance R21a), but
  that is a design note, not the requirement.
- **R29** Enrollment is on the **programme**. A split changes the
  timetable, so there is nothing to follow and nothing to copy: a member
  enrolled before a split is enrolled after it, because it is the same
  enrollment on the same event.

  Under the chain model this needed four rules (R29a–R29d in an earlier
  draft) and two mechanisms kept in step, and produced four defects —
  #376, #377, and the structural halves of #378 and E8.
- **R29a** Attendance coverage reads the programme, for the same reason.
  Camp R85–R86 hold as written, with no reinterpretation.
- **R29b** [GAP] A withdrawal or removal ends the enrollment. There is no
  question of which links it reaches, because there are no links.
- **R29c** [GAP] Credit settles at the **last occurrence the departing member
  is still enrolled for**, not at the instant of the departure.

  This survives the schedule model unchanged, because it never depended on
  a chain. A member who leaves is still enrolled for occurrences already
  under way in the current schedule, and `credit_system_requirements.md`
  R74 keeps them chargeable for those. Settling the whole bound balance at
  the moment of the call leaves them unpayable and their register
  uncompletable for a member who is legitimately in the room.
- **R29d** [GAP] The settlement is **deferred, not reserved**. The admin
  states the disposition at the moment of departure; it is recorded
  against the enrollment and applied once the last covered occurrence has
  passed. Until then the balance stays bound and spends exactly as it
  always would — the remaining occurrences charge on attendance, approved
  leave costs nothing, moving a charged record to leave refunds it
  (`credit_system_requirements.md` R45, R47). The disposition then acts on
  whatever is left.

  Nothing is held back, because no held figure could be right. How much a
  departing member will actually be charged is unknowable at the moment
  they leave: leave, a cancelled occurrence and never being marked all cost
  nothing, so any amount reserved is an overestimate that leave falsifies
  immediately.

  The settlement is applied in the name of the admin who stated the
  disposition (`credit_system_requirements.md` R82). A session before the
  departure that is marked only after the settlement, inside the edit
  window (attendance R13), is left open by design: the member is still
  covered for it, and it charges once from their general credit, which
  includes the account the transfer created. Nothing is re-settled
  (`credit_system_requirements.md` R74a).
- **R29e** [GAP] Where the departure leaves no occurrence ahead of it,
  settlement is immediate. That stays the common case; R29d applies to a
  departure with occurrences still to run.
- **R29f** [GAP] A scheduled, idempotent sweep performs these deferred
  settlements and the termination release of R10
  (`event_lifecycle_requirements.md` L20a). Not lazily on read: a member
  who never opens the app would never have their credit settled, and the
  notification would never fire.

## Conflicts

- **R30** [GAP] Two programmes may not share a venue at overlapping
  times. The clash is **blocked** — at creation, and at a split (R23),
  which is the only other way a programme's schedule changes (R26).
- **R30a** [GAP] A programme-versus-programme clash is computed from the
  **schedules**, without expanding either. Every schedule of one is
  compared with every schedule of the other; a programme usually has one,
  and a split one has few. Five tests, in order, all of which must hold:

  1. the date ranges overlap — each programme's start to its cutoff, or
     unbounded if open-ended;
  2. the weekday sets intersect;
  3. **the overlapping date range actually contains a day of a shared
     weekday**;
  4. the time-of-day windows overlap, half-open
     (`start_a < end_b AND end_a > start_b`);
  5. the venue is the same.

  Test 3 is not redundant. Two Monday programmes whose ranges overlap only
  from a Thursday to a Saturday satisfy tests 1, 2, 4 and 5 and never
  share an occurrence — and because R30 *blocks*, omitting it would refuse a
  legitimate programme. It costs nothing: an overlap of seven days or more
  always contains every weekday, so only a shorter overlap is examined,
  and then at most six days are.

- **R30a1** [GAP] An occurrence whose time-of-day window crosses midnight
  belongs to **both** weekdays it touches, and its window is compared as
  two: the part before midnight against the first day, the part after
  against the second. Without this, a Monday 23:00–00:30 occurrence and a
  Tuesday 00:00–01:00 occurrence at the same venue are judged never to meet.

  This works because a programme's shape is fixed by R11–R15: weekly,
  named days, no count, no exception dates. Two programmes are therefore
  a date range × a weekday set × a daily time window, and comparing those
  is exact. It is also why an open-ended programme needs no horizon —
  nothing is enumerated, so there is nothing to bound.
- **R30b** [GAP] R30a is exact only because R11 admits one recurrence
  shape. Two fortnightly programmes on alternate weeks would share a
  weekday and never meet, so an interval would make the day-set test
  block pairs that never clash. The constraint lives in R11, where it
  belongs; it is named here because this is what it is for.
- **R30c** [GAP] A programme is never blocked by a camp or a one-off, in
  either direction. Such a comparison is bounded on the camp or one-off
  side by the scheduling horizon (one-off R20a and its configurable
  bound), which is what
  keeps it finite when the programme is open-ended. A programme runs for months at one venue; it cannot
  be prevented, or forced to move, because a short event was scheduled
  across some of its occurrences. Such a clash is **reported**, not
  enforced — the same flag-don't-block treatment camps already get.
- **R30d** [GAP] A programme never conflicts with **itself**. Its own
  schedules are contiguous and non-overlapping by construction (R24a), so
  no two of them can produce occurrences at the same time, and a comparison
  is only ever made between two different programmes.
- **R31** [GAP] Conflict detection runs on every path that writes a
  schedule: creation and split. Those are the only two — R26 forbids
  in-place rescheduling, and terminate, extend and extend-indefinitely
  only move a cutoff, which can remove occurrences but never add one. There is no third path —
  R26 forbids in-place rescheduling — so this is a complete list rather
  than an enumeration to keep in step with the code.
- **R31a** [GAP] A single occurrence moved by a reschedule (R19) is
  outside R30a: it is an exception to the pattern, so the pattern test
  cannot see it. It is checked individually, against occurrences rather
  than rules, and reported rather than blocked — it moves one occurrence,
  not the programme.
- **R31b** [GAP] Conflict detection lives in **one module**, expressed as
  named gates, for every event type. Each gate answers one question about
  one resource:

  | Gate | Asks |
  |---|---|
  | **venue** | is the room booked twice? |
  | **organizer** | is one person running two things at once? |
  | **coach** | is one person coaching two things at once? |
  | **member** | is one person enrolled in two things at once? |

  A gate **computes** a finding; it does not decide what happens next.
  Whether a finding blocks or is merely reported is the caller's policy,
  applied by event type (R30, R30c) — so the same gate serves the
  blocking programme path and the reporting camp path without either
  reimplementing overlap.

  This replaces three separate implementations that today give three
  different answers to the same question: `EventService.check_conflict`
  (template windows only, venue and organizer, blocking),
  `ConflictService` (expanded occurrences, all four resources, camp-only,
  reporting), and `check_enrollment_time_conflict` (whole-event windows,
  member only, at join time). Overlap must be defined once — camp R32 —
  and every gate must use that definition.
- **R31c** [GAP] The caller names which gates to run, and whether each is
  blocking or advisory. Not every path cares about every resource: a join
  asks the member gate and nothing else; a creation asks venue and
  organizer; a coach change asks the coach gate. Declaring this at the
  call site keeps the policy visible where it is decided, and stops the
  gate module growing per-caller special cases.

  A gate that is not asked for is not run, and an advisory finding never
  raises — it is returned alongside the success. This is what allows one
  module to serve a blocking programme creation (R30) and a reporting
  camp creation (R30c) without either branch knowing about the other.

## Audit

- **R33** [✅] Terminate, extend and extend-indefinitely each write an
  audit row recording the actor, the programme, the reason where one is
  required, and the cutoff before and after.
- **R34** [✅] A split writes an audit row recording the actor, the
  programme, the cutoff, and which schedule fields changed. Correction
  writes one recording which programme fields changed.

  Renumbered from R32: the audit rule shared a number with the trial rules
  R32a–R32e, which are about something else entirely and were added
  later. Two unrelated things under one number is how a citation goes
  wrong.

---

## Mapping from camp requirements

How the requirements in `camps_requirements.md` carry over — 118 of them
as this is written, and deliberately not restated as a number that will
drift. This is
a triage, not a merge: "direct" means the rule holds with *camp* read as
*programme*.

### Direct — carry over unchanged (about 70)

Soft-delete, restore, hard-delete and deleted-listing (camp R6–R12);
eligibility and DOB windows (R13, R15, R40–R44); organizer-versus-coach
permissions (R16a–R16b); all reads and their 401/403 rules (R21–R26);
invite and assign and their error cases (R45–R46, R48–R50, R52–R55);
enrollment decisions (R57–R60, R62–R63); withdrawal (R64–R67); member
self-service in full (R69–R80); leave (R87–R90); removed aux-info
(R91–R93); audit (R94–R96); visibility (R97–R99).

### Needs a decision (about 17)

| Camp | Why it does not carry over |
|---|---|
| R2 | Camps are updated in place; a programme cannot be updated at all |
| R3, R4 | Stated as camp prohibitions — they are programme *abilities* |
| R5 | "Cancel the series" becomes terminate/extend; R1–R9 above replace it |
| R17–R20 | Daily and bounded; programmes are weekly and may be open-ended (R11–R15) |
| R27–R39 | The conflict model. Settled by R30–R31c: behaviour follows the *pairing*, not the type. Listed here because the camp rules still need rewriting against it, not because the decision is open. |
| R56 | Conflict check skipped for camps; programmes block instead |
| R100–R104 | Sessions timetable. **Settled** by R20: programmes carry one on the same terms, and it lives on the schedule, so changing it is a split. |

### Settled by the attendance stage

| Camp | Resolution |
|---|---|
| R51, R61, R68 | Carry over unchanged, with "ended" meaning *past the cutoff*. An open-ended programme never ends, and a terminated one is not ended until its cutoff — which is what `check_event_not_past` already computes, treating a recurring event's end as its `until_time` and an unbounded one as never past. No programme-specific rule is needed; the camp wording is simply read against the cutoff. |
| R83–R86 | Carry over **unchanged**. They assume attendance coverage is decided per event, which the schedule model makes true again: a programme is one event for its whole life, so R85's historical test and R86's status test need no reinterpretation. |

### Two camp requirements were programme requirements

Camp **R47** recorded trial enrollment as a camp ability, and **R55** named
trial assignment in a camp rule. Neither has ever been reachable for a
camp: the endpoint is gated to programmes. They came from a single
events requirement covering all three types, which was later split so that
camps could be specified in depth. The camp document kept rules that
belonged to the other types, and the rest of the original was not carried
forward — it survives as `docs/archived/events_requirement.md` in the
workspace superproject, outside this repository.

Both are corrected in `camps_requirements.md`, and R32a–R32c above record
the rule where it belongs.

**This is worth an audit rather than a one-off fix.** If two rules crossed
over, others may have. The camp document is the only specification the
project has trusted, and any rule in it that describes behaviour camps
cannot reach is suspect. R3 and R4 name programme workflows deliberately,
as contrasts, and are fine; the concern is rules that assert a camp
ability the implementation gates elsewhere.

---

## See also

- [`camps_requirements.md`](camps_requirements.md) — the camp
  specification these requirements are triaged against.
- [`attendance_requirements.md`](attendance_requirements.md) — attendance
  is event-agnostic; R27–R29 above defer to it.
- [`credit_system_requirements.md`](credit_system_requirements.md) — the
  credit system applies to programmes only, which makes R10 load-bearing.
