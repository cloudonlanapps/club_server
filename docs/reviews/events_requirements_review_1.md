# Event requirements — independent review

**Round 1** — reviewed against the requirement documents alone, without
reading the implementation. Later rounds are numbered `_review_2` and so on,
and must also cover `event_lifecycle_requirements.md`, which did not exist
when this round was written.

Reviewed on 2026-09-03: `camps_requirements.md`, `oneoff_requirements.md`,
`programme_requirements.md`. The review was done against the documents
alone, without reading the implementation, and answers four questions:

1. Do the documents precisely and completely define the requirements?
2. Are there conflicts across the three documents?
3. Is every rule feasible and independently testable?
4. Is the language consistent, with technical terms defined once?
5. (added later the same day) Do the three event documents agree with
   `enrollment_requirements.md`, `credit_system_requirements.md` and
   `attendance_requirements.md`?

Issues found in those three supporting documents themselves are kept
separately: `enrollment_requirements_review_1.md`,
`credit_system_requirements_review_1.md`,
`attendance_requirements_review_1.md`. Section 5 below lists only what
the cross-check revealed about the event documents.

Verdict: none of the four can be confirmed as they stand. The documents
are well reasoned, but there are real conflicts between them, several
rules that are not testable as written, and a handful of terms that
carry two meanings.

Rule references are prefixed with the document: **C** = camps,
**O** = one-off, **P** = programme. Findings are numbered **F<section>.<n>**
so each can be cited on its own.

---

## 1. Precision and completeness

Intent cannot be judged from the text; what follows are rules that leave
the observable outcome open.

- ~~**F1.1** C R5 (cancel a camp series)~~ ✅ *Incorporated: camp R5, R5a–R5d — cancel's mechanism, effect and undo.* states the ability and the reason field
  but not the effect. It does not say what happens to occurrences,
  enrollments or listings, nor whether cancel can be undone. One-offs get
  drop and reinstate with time guards; camps get nothing comparable.

  *Cross-check:* enrollment R16 shows what "cancel a series" is: the series end time set with no successor. The camps document should say so, and say how a cancelled camp differs from one created with an `UNTIL`, since both carry a series end time.
- ~~**F1.2** C R2 allows changing a camp's recurrence rule in place.~~ ✅ *Incorporated: camp R2c, R2d — the schedule freezes once anything is recorded (#390).* Attendance
  and overrides are keyed by occurrence slot, so a rule change can orphan
  them. P R26 forbids exactly this for programmes for that reason. The
  camps document is silent on what happens to existing records.
- ~~**F1.3** O R2 and O R21 contradict each other.~~ ✅ *Incorporated: one-off R21b, R21c — update enforces the same guards as reschedule (#391).* R2 lets the generic update
  change a one-off's schedule and venue. R21 claims to list every path
  that sets the schedule and names only creation, reschedule and
  reinstate. The postpone-only rule (R16a) and the 30-minute guard (R16b)
  can therefore be bypassed through update.
- ~~**F1.4** C R52 and C R53 cover invite only.~~ ✅ *Incorporated: camp R52 — assign and self-request cite enrollment R27, R28.* What assign does on an existing
  active or terminal enrollment is unspecified. Same for a self-request
  (C R77) over a terminal enrollment.

  *Cross-check:* enrollment R27 and R28 answer this. Assign overwrites a terminal row and upgrades an `invited` or `requested` row; only the narrow active set blocks it. C R52 and R53 should cite them rather than leave assign implied.
- ~~**F1.5** No way to withdraw a pending request.~~ ✅ *Incorporated: enrollment R5a, R5b — a member may retract a pending request.* C R64 allows withdrawal only
  from `accepted`, `assigned` or `assignedTrial`. A user in `requested`
  has no exit path.

  *Cross-check:* confirmed by enrollment R47 and R50. Decline works only from `invited` and withdraw only from the three enrolled statuses, so a `requested` member has no self-service exit anywhere.
- ~~**F1.6** C R81 and C R101 interact without a rule.~~ ✅ *Incorporated: programme R20b — a duration change is refused where a timetable exists (#393).* If a single occurrence is
  rescheduled with a new duration, the sessions timetable no longer sums
  to the window. Same gap between P R19 and P R20.
- ~~**F1.7** P R25a claims every settable field is reachable~~ ✅ *Incorporated: camp R14 — imageUri removed; it exists nowhere.*, but `imageUri`
  (C R14) appears in neither the correction nor the split column.
- ~~**F1.8** P R6 does not say whether a programme whose cutoff has already passed can be extended.~~ ✅ *Incorporated: programme R6a — extend only before the cutoff, because credit is already released.* O R7 explicitly forbids reviving a passed one-off,
  so the answer matters.
- ~~**F1.9** P R8 and P R12 leave the relationship between the stored cutoff and the rule's `UNTIL` undefined.~~ ✅ *Incorporated: dissolved — the cutoff *is* the rule's UNTIL, one fact in one place.* R8 clears both, implying they can
  diverge. R2–R4 validate a cutoff against the rule, which fails if
  terminate also wrote `UNTIL`.
- ~~**F1.10** P R29a says an enrolment made before the cutoff is recorded on "both" links.~~ ✅ *Incorporated: dissolved — there is no chain.* For a chain longer than two it should mirror R29d and
  say every later link.

## 2. Conflicts across the documents

- ~~**F2.1** Correction versus split on titles.~~ ✅ *Incorporated: dissolved — a split sets only scheduling and staffing; title lives on the programme.* P R22b says a correction
  rewrites every link of the chain. P R25b says a split deliberately
  gives the successor a new title. A later typo fix on link one clobbers
  the successor's intended new name. These two rules cannot both hold as
  written.
- ~~**F2.2** Trial enrolment is defined two ways.~~ ✅ *Incorporated: camp R47 — the trial definition corrected.* C R47 and the one-off
  document say a trial runs for a number of occurrences. P R32d says that
  description is wrong and a trial is bounded by credit. The programme
  document claims the camp document was corrected, but the old
  definition is still there.
- ~~**F2.3** Postpone-only and the 30-minute register guard are called type-agnostic but are missing from camps.~~ ✅ *Incorporated: camp R81a, R81b, R82a — all three types get both guards (#375).* P R19a and O R16a say the
  reasoning is not type-specific. C R81 and C R82 carry neither guard.
  One-off and programme also forbid cancelling an occurrence within 30
  minutes; camps do not.
- ~~**F2.4** Conflict-check targets.~~ ✅ *Incorporated: camp R31 — conflict checks accept every type.* C R31 rejects any non-camp target with
  `EVENT_TYPE_NOT_SUPPORTED`. O R20 and O R21 say one-offs follow camp
  semantics and must report on every path. P R31a and P R31b say the same
  module serves every type.
- ~~**F2.5** C R38 is ambiguous and may contradict P R30c.~~ ✅ *Incorporated: camp R38 — blocks against another programme only.* If the venue overlap
  in R38 is against a camp, P R30c says a programme is never blocked by a
  camp. If it is against another programme, they agree. The rule does not
  say which.
- ~~**F2.6** C R32 says overlap is evaluated over the full bounded occurrence sets of both sides.~~ ✅ *Incorporated: camp R32, R32a — one definition of overlap; rule-to-rule where a side is unbounded.* An open-ended programme (P R12) has no bounded set.
  O R20a and P R30c describe a rule-versus-occurrence comparison instead,
  but no document specifies that algorithm.
- ~~**F2.7** P R27 says cancellation is decided by the cutoff "uniformly for every event type".~~ ✅ *Incorporated: programme R27 — two routes, one rule, owned by the lifecycle document.* O R8 and O R14 say a one-off has no series end time and
  is dropped by cancelling its occurrence. Per-occurrence cancellation
  (P R16, C R82) is also not decided by a cutoff. R27 is not the single
  rule it claims to be.

  *Cross-check:* attendance R21 already states the correct two-route rule (occurrence override, or slot at or after the series cutoff). P R27 should be rewritten to match it instead of claiming the cutoff alone decides.
- ~~**F2.8** C R39 asserts a programme join blocks on member overlap.~~ ✅ *Incorporated: camp R39 — the member gate reports except programme-versus-programme.* The
  programme document never states the policy for the member gate. P R31c
  says a join asks the member gate but not whether it blocks. Same gap
  for the organizer gate at programme creation.

  *Cross-check:* enrollment R29, R36 and R45 answer this: assign, self-request and accept block with 409 on member overlap for every type except camps. The programme document should state that policy; today it is only implied by the enrollment document.
- ~~**F2.9** Exception dates.~~ ✅ *Incorporated: camp R19–R19c — EXDATE expresses a short camp inside a longer span.* P R14 forbids `EXDATE` because a silent skip has
  no reason or audit. C R19 permits `EXDATE` while C R82 already offers
  cancel-with-reason. The rationale applies to both types; this is a
  design decision to make, not a defect.
- ~~**F2.10** Stale cross-references.~~ ✅ *Incorporated: programme mapping table — the conflict model and the timetable are settled.* P's mapping table says C R100–R104 have
  "applicability unknown" while P R20 has settled it, and says the
  conflict model is camp-only while P R30c settles that too. C R98 says
  "once #18 lands" while C R44 and the section headers say #18 has
  landed. P's header says nothing is implemented while five programme
  rules are marked done.

## 3. Feasibility and testability

Most rules are feasible. These fail the "one or more tests" bar:

- ~~**F3.1** Architecture directives, not behaviours.~~ ✅ *Incorporated: programme R28, attendance R21a — restated as observables.* P R28 ("evaluated in one
  place"), P R31b ("one module"), P R4 ("not by enumerating"), P R30a
  ("without expanding"), O R17 and O R21a ("resolve to one
  implementation"). A black-box test cannot distinguish these from a
  correct multi-implementation. Either restate as an observable (an
  open-ended programme of any age terminates) or move to a design note.
- ~~**F3.2** Untagged commentary in rule slots.~~ ✅ *Incorporated: programme R10b, R32d, R32e — tagged.* P R10b, R24c, R29c, R32d, R32e
  carry no status tag and state no testable claim.
- ~~**F3.3** Rules describing two contradictory states.~~ ✅ *Incorporated: camp R56, R77 — one behaviour per rule.* C R56 and C R77 are
  marked done yet describe both the current 409 and the future
  suppression.
- ~~**F3.4** Rejections without an observable.~~ ✅ *Incorporated: error codes named across programme R2, R3, R13–R15, one-off R4, R13, camp R36, R37.* O R4, O R13, P R3, P R9,
  P R13–R15, P R17, P R24, C R36, C R37. Camps names codes elsewhere, so
  the convention is inconsistent. C R91 shows the fix: name the status.
- ~~**F3.5** Undefined boundaries.~~ ✅ *Incorporated: camp R51, R80 — “ended” defined once, in the lifecycle document.* "Ended" for camps (C R51, R61, R68, R80).
  "Past" versus "present" occurrence in C R85 and R86. Whether O R20a's
  52 weeks bounds the start or the last occurrence, and what happens to a
  camp whose `COUNT` runs past it.

  *Cross-check:* enrollment R15 defines "ended" for every type: the end time for a non-recurring event, the series end for a recurring one, far-future if unbounded. C R51, R61, R68 and R80 should cite it. It still leaves the series end of a `COUNT`-bounded camp undefined.
- ~~**F3.6** P R30a is not exact as claimed.~~ ✅ *Incorporated: programme R30a, R30a1 — the missing weekday test and midnight crossing.* Tests one to three can all pass
  without any shared date when the range overlap is shorter than a week
  and the shared weekday falls outside it. Because P R30 blocks, that is
  a false block on a legitimate programme. A session crossing midnight
  also breaks the weekday-times-window model. Both are fixable with one
  more check, but the rule needs to say so.
- ~~**F3.7** P R29f deferred settlement~~ ✅ *Incorporated: programme R29f, lifecycle L20a — a scheduled idempotent sweep.* needs a trigger for "once the last
  covered session has passed". The document does not say whether that is
  a scheduled job or evaluated lazily, which decides how it is tested.
- ~~**F3.8** C R94 and C R95 list mutations camps cannot perform~~ ✅ *Incorporated: camp R94, R95 — audit rows for mutations camps cannot perform removed.* (correct,
  future-update, trial-assign per R3, R4, R47). Those audit rows can
  never be exercised.

## 4. Language and definitions

- ~~**F4.1** "Session" has two meanings.~~ ✅ *Incorporated: programme vocabulary — session is a timetable entry, occurrence is the rest.* In the programme document it is an
  occurrence of the series (R2, R17, R19a). In C R100 and P R20 it is a
  sub-period of one occurrence in the timetable. Both meanings appear in
  the same document.
- ~~**F4.2** "Cancel" has four meanings.~~ ✅ *Incorporated: lifecycle L14 — cancelled is a property of an occurrence.* Cancel a camp series (C R5), cancel one
  occurrence (C R82, P R16), an occurrence being cancelled by the cutoff
  (P R27), and the programme vocabulary insisting terminate is not
  cancellation while P R7 says moving the cutoff "cancels" sessions.
- ~~**F4.3** "Enrolled" versus "active enrollment".~~ ✅ *Incorporated: camp R74, R79, R97 — resolved to the enrollment document's non-terminal.* C R74 and C R97 hinge on
  "already enrolled" without saying whether terminal statuses count. The
  vocabulary defines "active" but the rules do not use it.

  *Cross-check:* enrollment R20 settles it for the listing: enrolled means any non-terminal status. C R74 and C R97 should use that wording.
- ~~**F4.4** "Organizer" is a user in camps but a name string in programmes.~~ ✅ *Incorporated: programme R20a — organizer and coaches are users (#386).*
  C R16b grants permissions to the organizer, which needs an identity.
  P R25a lists `organizerName` and `coachNames` as fields, and the
  organizer and coach conflict gates need identity too.
- ~~**F4.5** "Chain", "link", "successor", "chain root"~~ ✅ *Incorporated: dissolved — no chain vocabulary to define.* are used throughout the
  programme document but only "live link" is in the vocabulary.
- ~~**F4.6** Spelling.~~ ✅ *Incorporated: spelling standardised on “enrollment” across every document.* Camps uses "enrollment"; the other two use "enrolment".
- ~~**F4.7** Numbering.~~ ✅ *Incorporated: camp sessions section reordered; programme audit renumbered to R33/R34; the drifting rule count dropped.* C R100–R104 sit before R94. P R32 (audit) is unrelated
  to P R32a–R32e (trial). P's mapping says the camp document holds 105
  rules.

---

## 5. Cross-check against enrollment, credit and attendance

- ~~**F5.1** Coaches and attendance marking.~~ ✅ *Incorporated: camp R83 — admin or organizer only; leave statuses are not admin-markable.* C R83 and O R18 say an
  admin, coach or organizer can mark attendance. Attendance R7 says a
  coach without the admin role is rejected with 403. C R83 also lists
  `onLeave` as a status staff can mark; attendance R24 restricts the
  admin-markable set to `present`, `absent`, `late`, with `onLeave`
  reachable only through leave approval. Two of the three event
  documents assert an ability the attendance document forbids.
- ~~**F5.2** Terminate is cancel in enrollment terms.~~ ✅ *Incorporated: enrollment R15, R16 — replaced by the lifecycle join predicate.* P R5 says a
  terminated programme keeps enrolment open until its cutoff. Enrollment
  R16 says a series is cancelled iff its series end time is set with no
  successor, and blocks every join-side flow on a cancelled series. A
  terminated live link has exactly that shape, so under R16 termination
  closes enrolment at once. This is the overload O R8 warns against, and
  the whole-event read that attendance R21 and issue #369 say must go.
- ~~**F5.3** Which conflict paths camps suppress.~~ ✅ *Incorporated: camp R28 — names assign, self-request and accept.* C R28 names invite,
  assign and approve as the paths whose time-conflict 409 is suppressed.
  Enrollment R26 and R43 say invite and approve never run the check for
  any type. The paths that do run it are assign, self-request and accept
  (enrollment R29, R36, R45). C R28 should name those three, and C R75
  (accept an invitation) should say the check is suppressed there.
- ~~**F5.4** One-off member clashes.~~ ✅ *Incorporated: one-off R20, enrollment R30 — the member gate reports for every non-programme pairing.* O R20 says a one-off clash is
  reported and never blocked, whatever it clashes with. Enrollment R29,
  R36 and R45 block on member overlap for every type, and the
  suppression in R30 and R37 is camp-only. Either the enrollment
  document needs a one-off carve-out or O R20 is wrong for the member
  gate.
- **F5.5** **When termination releases credit.** P R10 releases every
  bound balance at the moment of termination. P R5 keeps sessions
  running and chargeable until the cutoff, and P R29e together with
  credit R74a settle a departure at the last covered session, not at
  the call. Releasing at termination moves credit to a general account
  before the sessions it was bought for have run. The release should be
  timed against the cutoff, or the document should say why not. P R10
  also keeps the validity window, while credit R75 gives every transfer
  a fresh admin-set window, and credit R72 says nothing converts
  automatically outside removal. The credit document has no termination
  rule at all; see `credit_system_requirements_review_1.md` CR4.
- ~~**F5.6** Cancelling a marked occurrence cannot happen.~~ ✅ *Incorporated: programme R17a, one-off R5a — super-admin retrospective cancellation.* Credit
  R48 to R48b refund a cancelled occurrence that already has attendance,
  and R48b says the window is real because the register opens 30
  minutes before the start and cancellation is allowed while the
  occurrence is future. P R17 and O R5 forbid cancellation from 30
  minutes before the start, which is the instant the register opens
  (attendance R12). For programmes and one-offs the case is unreachable.
  Either P R17 needs a super-admin override for retrospective
  cancellation, or the credit rules are dead. Camps (C R82) have no
  guard, but credit does not apply to camps (credit R6).
- ~~**F5.7** The 30-minute boundary instant.~~ ✅ *Incorporated: programme R17, one-off R5, attendance R13/R15 — boundaries stated inclusive.* Attendance R12 says a
  write at exactly 30 minutes before the effective start is allowed. P
  R3, P R17, O R5 and O R16b say "up to 30 minutes before" without
  saying whether that instant is inside or outside, and P R17 and O R5
  say "before it starts" without saying effective start. Since the
  event documents claim the two windows never overlap, the boundary
  must be stated on both sides.
- ~~**F5.8** Covering statuses let non-members declare leave.~~ ✅ *Incorporated: attendance R26, R26a — narrowed to joined members.*
  Attendance R26 lets a member declare leave for any occurrence their
  enrolment covers, and the future coverage set (enrollment R60, C R86)
  includes `invited` and `requested`. So a user who has not joined can
  create attendance rows months ahead, which P R24c and R24d then have
  to migrate. If that is intended, C R86 should say so.
- ~~**F5.9** Citation error.~~ ✅ *Incorporated: programme R10b — cites credit R25.* P R10b cites credit R26 for general
  accounts ranking after bound ones; that ordering is credit R25. R26
  is the prohibition on bypassing a bound account.

## Resolve first

1. F2.1 — correction versus split on titles (P R22b vs P R25b).
2. F1.3 — one-off update bypassing the reschedule guards (O R2 vs O R21).
3. F2.2 — the trial definition (C R47 / one-off text vs P R32d).
4. F2.6 — the missing rule-versus-occurrence conflict algorithm (C R32,
   O R20a, P R30c).
5. F4.1 and F4.2 — the overloaded terms "session" and "cancel".
6. F5.1 — whether coaches may mark attendance (C R83, O R18 vs
   attendance R7).
7. F5.2 — terminate versus enrollment R16's definition of cancelled.
