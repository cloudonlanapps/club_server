---
name: programme-oneoff-umbrella
description: Programme and one-off requirements are finished (#363, closed); implementation is #384, twenty-four children in six dependency-ordered phases.
metadata:
  type: project
---

**#363 is closed** (2026-09-03). It asked what programmes and one-offs should
do; all four stages — requirements, credit lifecycle, attendance, conflicts —
are complete, and `docs/programme_requirements.md` and
`docs/oneoff_requirements.md` carry no `[OPEN]` rule.

**#384 is the build**: twenty-four children in six phases ordered by dependency,
not priority — shared predicates, chain integrity, lifecycle, enrolment and
attendance, conflicts, docs. Its body explains why the children cannot be fixed
independently, and carries the closure protocol (phase-level commits, per-issue
test citations). Nothing is implemented yet.

Three decisions worth not relitigating:

- Conflict behaviour is decided by the **pairing**, not the event type.
  Programme × programme blocks; everything else reports.
- Programmes are **weekly only** — no interval, no monthly. This is what lets
  two programmes be compared as rules rather than expanded series, which is why
  an open-ended programme needs no conflict horizon. A fortnightly programme
  cannot be expressed; that cost was taken deliberately.
- Detection becomes **one module with four gates** (venue, organizer, coach,
  member). Gates compute; the caller names which run and whether each blocks or
  advises.

Two requirements were found **wrong**, not merely unimplemented, and corrected
in place naming the bad assumption: R24c and R32a. Both were caught by checking
the code rather than trusting the document — see
[[implementation-over-stale-docs]].

Working method that produced this: ask edge-case questions one at a time and
follow where each leads. Batching produced worse answers.
