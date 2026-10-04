# Credit system requirements — review

**Round 1** — reviewed against the requirement documents alone, without
reading the implementation. Later rounds are numbered `_review_2` and so on,
and must also cover `event_lifecycle_requirements.md`, which did not exist
when this round was written.

Reviewed on 2026-09-03 against `camps_requirements.md`,
`oneoff_requirements.md`, `programme_requirements.md`,
`enrollment_requirements.md` and `attendance_requirements.md`,
without reading the implementation. Findings are numbered **CR<n>**.
Where a finding is a conflict with an event document, the event-side
entry is in `events_requirements_review_1.md` (F5.x) and is cited here.

- ~~**CR1** Trial exhaustion removes automatically, but removal needs an
  admin.~~ ✅ *Incorporated: credit R52a, R52b — a system action needing no disposition, but it must write an audit row and today writes none.* R52 removes a member whose trial credit is exhausted. R71
  says a member cannot be removed while bound credit is unresolved and
  the admin supplies the disposition as part of the removal. Enrollment
  R48 makes removal an admin action with an actor and an audit row.
  Who is the actor for R52, what disposition applies, and are R82 and
  R84 satisfied?
- ~~**CR2** R83 contradicts R47.~~ ✅ *Incorporated: credit R83, R83a — one charge *row* per triple; a refund and re-charge reuse it.* R83 says a deduction can never be
  recorded twice for the same `(member, event, occurrence)`, enforced
  at the storage layer. R47 says moving from leave back to a charged
  status charges it again after a refund, which is a legitimate second
  deduction entry for the same triple. R83 must mean "net charged at
  most once", or key on something other than the triple.
- ~~**CR3** Charges are keyed by event id, but rows move between event
  ids.~~ ✅ *Incorporated: credit R82a — charges key on the chain root, as accounts already do.* R44, R44a and R83 identify a charge by `(member, event,
  occurrence)`. Programme R24d moves attendance rows at or after a
  split cutoff to the successor event id. After the move, reconciliation
  (R44a, R44b) sees an uncharged `(member, successor, occurrence)` and
  can charge again. R7 already keys accounts on the chain root; charges
  should key on the chain root or on the attendance row.
- ~~**CR4** Termination release is missing.~~ ✅ *Incorporated: credit R73a–R73d — the termination release, at the cutoff, as R72's second exception.* Programme R10 (issue #374)
  releases every bound balance to a general account at termination,
  keeping the validity window and applying no penalty. This document
  has no such rule, and three of its rules contradict it: R72 says
  nothing converts automatically outside removal, R75 says a transfer
  creates a fresh admin-set window, and R65a says a transfer always
  creates a new account while programme R10 says "a general account".
  Timing is also open; see events review F5.5.
- ~~**CR5** R48 to R48b describe an unreachable case for programmes.~~ ✅ *Incorporated: credit R48b, R48c — kept, and made reachable by a super-admin retrospective occurrence cancellation, which the event documents will state.*
  R48b says a register can be marked early and then cancelled because
  the register opens 30 minutes before the start and cancellation is
  allowed while the occurrence is future. Programme R17 and one-off R5
  forbid cancellation from 30 minutes before the start. Camps have no
  such guard but never touch credit (R6). Unless a retrospective
  cancellation override exists, these rules are dead. See events review
  F5.6.
- ~~**CR6** R66a and R69 disagree about spanning accounts.~~ ✅ *Incorporated: credit R69, R69a — R69 bounds the penalty at the bound balance; *other accounts* means general ones.* R66a takes
  one penalty across the member's bound accounts oldest-first until used
  up. R69 says a penalty is bounded by the balance of the account it is
  applied to and the member's other accounts are not consulted, reduced
  or examined. If R69 means non-bound accounts only, say so.
- ~~**CR7** R38's message is wrong for admin-driven paths.~~ ✅ *Incorporated: credit R38, R38a — the refusal names the unfunded member, not an admin to contact.* R35 gates
  invite, assign and trial-assign on the member's credit; R38 refuses
  "with a message directing the member to contact an admin". On those
  three paths the caller is the admin.
- ~~**CR8** No statement on super-admin and R41.~~ ✅ *Incorporated: credit R41b1 — no role bypasses the credit check, super-admin included.* Attendance R11
  enumerates what super-admin may bypass. This document does not say
  whether a super-admin may mark attendance for a member with no usable
  credit. Say "no role bypasses" or say who does.
- ~~**CR9** "Today" is undefined.~~ ✅ *Incorporated: credit vocabulary, R33, R37 — validity is a UTC-instant comparison; there is no “today”.* The vocabulary makes usability
  depend on whether the validity window contains today, and R37 judges
  it "against today". Validity windows are dates and the server runs on
  UTC milliseconds; the day boundary and timezone are not stated.
- ~~**CR10** Gating invites on the invitee's balance (R35) is a design question.~~ ✅ *Incorporated: credit R35, R35a — invite is ungated; the block moves to accept.* Enrollment R26 makes invites advisory. Requiring the
  invitee to already hold credit stops an admin inviting someone they
  intend to fund next. Not a defect, but worth confirming.
- ~~**CR11** Tag drift.~~ ✅ *Incorporated: credit R74a — tagged `[GAP / #294]` like the rest.* The header says every rule is `[GAP / #294]`;
  R74a is `[GAP]` with no issue.
