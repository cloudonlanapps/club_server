# Enrollment requirements — review

**Round 1** — reviewed against the requirement documents alone, without
reading the implementation. Later rounds are numbered `_review_2` and so on,
and must also cover `event_lifecycle_requirements.md`, which did not exist
when this round was written.

Reviewed on 2026-09-03 against `camps_requirements.md`,
`oneoff_requirements.md`, `programme_requirements.md`,
`attendance_requirements.md` and `credit_system_requirements.md`,
without reading the implementation. Findings are numbered **E<n>**.
Where a finding is a conflict with an event document, the event-side
entry is in `events_requirements_review_1.md` (F5.x) and is cited here.

- ~~**E1** "Active" has two meanings inside this document.~~ ✅ *Incorporated: enrollment vocabulary — *enrolled* and *non-terminal*; the word *active* retired. Two constants of that name exist in code with different contents.* The
  vocabulary defines active as `{accepted, assigned, assignedTrial,
  withdrawRequested}`. R24 then rejects invites on any "active
  (non-terminal)" row and lists `invited` and `requested` as examples,
  while R28 explicitly uses "the narrower one". The camps vocabulary
  defines active as everything non-terminal. Pick one word for each set
  (for example *enrolled* and *open*) and use it everywhere.
- ~~**E2** R16 defines "cancelled" by storage fields and makes a
  terminated programme cancelled.~~ ✅ *Incorporated: enrollment R15, R16 — replaced by the lifecycle join predicate (L12); there is no whole-event cancelled.* A series is cancelled iff
  `until_time` is set with no `continued_as_event_id`. A terminated live
  link (programme R1, cutoff stored as the series end time) has exactly
  that shape, so R16 blocks the joins that programme R5 says stay open
  until the cutoff (F5.2). It also names columns, which the document's
  own preamble says it avoids. Attendance R21 says cancelled-ness is
  never whole-event; R16 is a whole-event read. It also leaves open how
  a camp created with an `UNTIL` differs from a cancelled one.
- ~~**E3** R29 excludes "cancelled" events from conflict detection~~ ✅ *Incorporated: enrollment R29b — a cutoff no longer excludes an event from conflict detection.*
  using the R16 definition, so a terminated programme that is still
  running until its cutoff is invisible to the member time-conflict
  check. A member can be assigned to a clashing event while still
  attending it.
- ~~**E4** Re-enrolment breaks historical coverage.~~ ✅ *Incorporated: enrollment R6–R6c — a terminal enrollment is closed for good and rejoining writes a new row; coverage reads every stint.* R27 clears
  `withdrawn_at` when assign overwrites a terminal row. R23 (re-invite)
  and R34 (self-request again) do not, and accept (R44) and approve
  (R39) only stamp `enrolled_at`. A member who withdrew and was
  re-invited then has `withdrawn_at` earlier than `enrolled_at`, and
  fails R61 for every past occurrence after the re-enrolment. Further,
  one row per pair (R6) with one `enrolled_at` and one `withdrawn_at`
  cannot represent two enrolment periods: re-setting `enrolled_at`
  erases the first period, so R61's "audit-correct historical view"
  does not survive a re-enrolment.
- ~~**E5** No self-service exit from `requested`.~~ ✅ *Incorporated: enrollment R5a, R5b — a member may retract a pending request; new terminal status `retracted`.* Decline works only
  from `invited` (R47) and withdraw only from the three enrolled
  statuses (R50, R51). A member who requested to join and changed their
  mind has to wait for an admin to reject them. See events review F1.5.
- ~~**E6** Assign-trial is treated as event-agnostic.~~ ✅ *Incorporated: enrollment R31a — assign-trial is programme-only.* R10, R13, R31,
  R32 and R58 all include assign-trial with no event-type gate. Camp R47
  rejects it with `EVENT_TYPE_NOT_SUPPORTED` and programme R32a says
  trials exist only for programmes. This document should carry that
  rule, since it owns the state machine.
- ~~**E7** Overlap is undefined for R29, R36 and R45.~~ ✅ *Incorporated: enrollment R29a — overlap is the shared member gate, camp R32's definition.* The rules say
  "any overlap" and "time-conflict" without saying what is compared.
  Programme R31b records that the current check compares whole-event
  windows, which would flag a Monday programme against a Tuesday one.
  Camp R32 defines occurrence overlap; these rules should cite it, or
  the shared gate module in programme R31b.
- ~~**E8** Chains are absent.~~ ✅ *Incorporated: enrollment R59a — coverage, listing, remove and approve-withdrawal read the chain.* R20 (listing), R48 (remove), R54
  (approve withdrawal) and R60 to R62 (coverage) all act on one event
  id. Programme R29a, R29b, R29d and attendance R20a require enrolment
  to propagate forward across a split chain, departures to retract
  forward, and coverage to read the chain. None of that is referenced
  here.
- ~~**E9** Credit disposition on departure is missing.~~ ✅ *Incorporated: enrollment R48a, R48b, R54a — credit disposition and deferred settlement.* Credit R71, R73
  and R98 attach a mandatory disposition to remove and approve-withdraw
  on credit-enabled deployments and reject it on disabled ones. R48 and
  R54 describe an optional reason and nothing else.
- ~~**E10** R62a contradicts attendance R11 on the super-admin
  bypass.~~ ✅ *Incorporated: attendance R11a changed to agree; R62a stands and now cross-references it.* R62a says super-admin attendance writes bypass the coverage
  check. Attendance R11 says coverage rejections are not overrideable
  because coverage is a data-safety invariant. Attendance R20 sides with
  R62a. One of the two must change.
- ~~**E11** "Ended" (R15) is incomplete.~~ ✅ *Incorporated: enrollment R15 — “ended” subsumed by the same join predicate.* The series end of a
  `COUNT`-bounded camp is not defined, and for a one-off that has been
  postponed (one-off R16) the end should be the effective end, not the
  original.
- ~~**E12** The preamble calls eligibility a camp-specific carve-out.~~ ✅ *Incorporated: enrollment preamble — eligibility is not a camp carve-out.*
  R25, R31, R38 and R42 apply to every event type, and programme R25a
  gives programmes the same criteria. Only time-conflict suppression is
  camp-specific.
- ~~**E13** R26 and R43 make camp R28 misleading.~~ ✅ *Incorporated: camp R28 corrected — it now names assign, self-request and accept (events review F5.3).* Invite and approve
  never run the conflict check for any type, so listing them as
  "suppressed for camps" (camp R28) describes nothing. The event-side
  fix is in events review F5.3; this document is the authority and is
  consistent.
