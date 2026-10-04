# Attendance requirements — review

**Round 1** — reviewed against the requirement documents alone, without
reading the implementation. Later rounds are numbered `_review_2` and so on,
and must also cover `event_lifecycle_requirements.md`, which did not exist
when this round was written.

Reviewed on 2026-09-03 against `camps_requirements.md`,
`oneoff_requirements.md`, `programme_requirements.md`,
`enrollment_requirements.md` and `credit_system_requirements.md`,
without reading the implementation. Findings are numbered **A<n>**.
Where a finding is a conflict with an event document, the event-side
entry is in `events_requirements_review_1.md` (F5.x) and is cited here.

- ~~**A1** R11 and R20 contradict each other on coverage.~~ ✅ *Incorporated: attendance R11a — super-admin does bypass coverage; R11 was wrong.* R11 says
  enrollment-coverage rejections are not overrideable by anyone. R20
  says super-admin bypasses them on writes, and enrollment R62a agrees
  with R20. Decide which, and state it once.
- ~~**A2** The windows use two different clocks.~~ ✅ *Incorporated: attendance R13, R15, R15b and the vocabulary — one clock, the effective start.* R12 opens the
  register 30 minutes before the *effective* start (the override's new
  start where one exists). R13 closes the edit window 15 days after
  `occurrence_time_utc`, the slot, and R15 closes leave declaration 2
  hours before the slot; R14 and R33 inherit R13. Programme R19a and
  one-off R16a make postponement the only kind of reschedule, so every
  override moves the effective start later than the slot. A session
  postponed by more than 15 days therefore has an edit window that
  closes before its register opens, and a postponed session's leave
  window closes against the old time. All windows should be relative to
  the effective start, or the document should say why the slot is used.
- ~~**A3** R7 conflicts with camp R83 and one-off R18~~ ✅ *Incorporated: camp R83 corrected — admin or organizer only, and the leave statuses are not admin-markable (events review F5.1).* on whether a
  coach may mark attendance, and R24 conflicts with camp R83 on whether
  staff may mark `onLeave`. This document is the narrower one and reads
  as the authority; the event-side entry is events review F5.1.
- ~~**A4** Leave decisions have no authorization rule.~~ ✅ *Incorporated: attendance R7a — leave decisions carry the same rule as R7.* R7 covers
  marking only. The leave-decision section is headed "admin /
  organizer", but R14 says approve and reject are gated "on the staff
  side", and staff includes coaches, whom R7 excludes. State who may
  approve and reject leave as a rule.
- ~~**A5** `reference_time` (R4) may defeat the windows.~~ ✅ *Incorporated: attendance R4 — `reference_time` does not exist and will not be added.* R4 lets a
  caller back-date `recorded_at`. The document does not say whether R12,
  R13 and R15 are evaluated against server time or the supplied time. If
  the latter, R12's "no role bypasses this" is false.
- ~~**A6** R20 restates enrollment R60 to R62a in full~~ ✅ *Incorporated: attendance R20 — cited, no longer a second definition.*, although the
  preamble says coverage rules are referenced rather than restated. Two
  copies drift; A1 is already an example, since the bypass wording
  differs between the two documents.
- ~~**A7** R21a is an architecture directive~~ ✅ *Incorporated: attendance R21a — restated as an observable.*, names source files, and
  is not testable as a behaviour. Same class as events review F3.1.
  Restate as the observable (one predicate, one answer) or move it to a
  design note.
- ~~**A8** Migration on split is missing.~~ ✅ *Incorporated: attendance R2a. No collision rule needed — the successor is created by the same operation that migrates, so it holds no rows.* R2 keys a record by
  `(event_id, occurrence_time_utc, member)`. Programme R24d moves
  attendance rows to the successor event id at a split, preserving row
  identity for pending leave notifications. This document does not
  mention the move, nor what happens if the successor already holds a
  row for the same member and slot.
- ~~**A9** Bulk marking has no result shape.~~ ✅ *Incorporated: attendance R22a — the per-member bulk response.* R22 processes each triple
  independently but does not say what the caller gets back. Credit R41b
  and R97 require a per-member report of which members were refused and
  why, returned on every deployment. That report is an attendance
  contract and belongs here.
- ~~**A10** Invited and requested users can declare leave.~~ ✅ *Incorporated: attendance R26, R26a — narrowed to joined members.* R26 allows
  leave for any occurrence the enrolment covers, and the future coverage
  set (R20, enrollment R60) includes `invited` and `requested`. A user
  who never joined can create attendance rows months ahead. If intended,
  say so; if not, R26 needs the narrower set. See events review F5.8.
- ~~**A11** Boundary instants.~~ ✅ *Incorporated: attendance R12, R13, R15 — all three boundaries stated inclusive.* R12 says exactly 30 minutes before is
  allowed. R13 and R15 do not say whether exactly 15 days after, or
  exactly 2 hours before, is inside or outside the window.
- ~~**A12** Tag mismatch.~~ ✅ *Incorporated: attendance vocabulary — stale [GAP / #22] removed.* The vocabulary marks the open window
  "[GAP / #22]" while R12, which enforces it, is marked [X].
