# Eligibility by age — Requirements

This document is the testable specification for who an event or a group
admits by age. It is written as plain-English rules. Each rule's test names
it with `@pytest.mark.requirement("eligibility:Rn")`, and
`tests/test_requirement_coverage.py` checks every marked rule has one.

Related documents, not restated here:

- Group kinds, join requests and membership:
  [`groups_requirements.md`](groups_requirements.md).
- Which enrolment steps check eligibility, and what a refusal answers:
  [`enrollment_requirements.md`](enrollment_requirements.md) R25, R31, R38,
  R42; camps R54, R55, R62, R78.
- A user's own date of birth: [`users_requirements.md`](users_requirements.md)
  R9, R35, R36.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.

Vocabulary:

- An **age** is a length of years, months and days.
- An **age band** is a minimum age and a maximum age, each optional, and
  whether the check is **strict**.
- The **reference day** is the calendar day ages are counted on.
- The **window** is the range of dates of birth a band admits on its
  reference day. Dates are calendar dates, carried as UTC-midnight
  milliseconds like a user's date of birth.
- **Criteria** = gender, or either age bound.

---

## The age band

- **R1** [✅] An event of any type and a group each store
  `minAge`, `maxAge` and `strictAge`. They are set on create and on update
  (an event's correction and update included); `null` clears a bound, and
  an update that leaves a field out keeps its value. `strictAge` is false
  unless set.
- **R2** [X] An age is `{years, months, days}`: years 0–150,
  months 0–11 and days 0–30, months and days defaulting to 0. Anything
  else → 422.
- **R10** [X] A minimum age greater than the maximum is refused
  on create and on update → 422, and nothing is changed.
- **R11** [X] `dobOnOrAfterUtc` and `dobOnOrBeforeUtc` are not
  accepted on any write → 422.

## The reference day

- **R3** [✅] A day is the club's calendar day, in the time zone
  the deployment names in `CLUB_TIMEZONE` (an IANA name; `Asia/Kolkata`
  when it names none). An unknown zone stops the server at startup, naming
  the setting.
- **R4** [✅] A camp's and a one-off's reference day is the day the
  event starts; a group's is today.
- **R4a** [✅] A camp's window follows its start day when the start
  day is changed.
- **R4b** [✅] A programme's reference day is the day of its next
  occurrence that is not cancelled, and today when it has none.

## The window

- **R5** [✅] Strict: born on or after the reference day less the
  maximum age, and on or before the reference day less the minimum age —
  aged exactly the minimum to exactly the maximum on the day.
- **R6** [✅] Relaxed, the default: each end is a year less a day
  wider — on or after the reference day less the maximum age less a year
  plus a day, and on or before the reference day less the minimum age plus
  a year less a day.
- **R7** [✅] A bound that is not set places no limit on its side.
- **R8** [✅] Both ends of the window are inclusive. A user with no
  date of birth is outside any window that has a limit.
- **R9** [✅] An age is taken off the reference day years and
  months first, landing on the last day of a shorter month, then days.

## What reports it

- **R12** [✅] An event (to staff, to a member and to the public)
  and a group (in listings and in detail) report `minAge`, `maxAge` and
  `strictAge`, the window as `dobOnOrAfterUtc` and `dobOnOrBeforeUtc`, and
  the reference day as `eligibilityReferenceDayUtc`.

## What checks it

- **R13** [✅] Joining an event — invite, request, assign and
  approve — admits a user inside the window and refuses one outside it, at
  both ends, strict and relaxed.
- **R14** [✅] The users eligible for an event are those inside its
  window.
- **R15** [✅] A member's event listing leaves out a public event
  whose window they are outside, unless they have an enrolment on it.
- **R16** [✅] An auto group's members are the users inside
  today's window.
- **R17** [✅] Adding a member to a semi-auto group, requesting to
  join one, approving a request and listing its eligible users all use
  today's window.
- **R18** [✅] Changing a semi-auto group's band is checked against
  its existing members with the window of the new band.
- **R19** [✅] A group's kind is derived from having any criterion:
  an age bound alone makes it `auto`, or `semi_auto` when asked for.

## Enrolled members who stop matching

Eligibility is checked when a member joins an event (R13). A programme's
window moves with its next occurrence, so an enrolled member can grow out
of it; a member's date of birth or gender can also be corrected, or the
criteria tightened. Such a member stays enrolled and is flagged; nobody is
removed automatically.

- **R20** [✅] An enrolment row — in an event's enrolment list and
  in a member's own enrolment — reports `eligible`, worked out when it is
  read: false while the member is enrolled (accepted, assigned, on trial or
  asking to withdraw) and no longer meets the event's gender or window.
  Any other row is true.
- **R21** [✅] Once a day the scheduler notifies the admins with
  `enrollment.member_ineligible`, naming the programme and the member, for
  each enrolled programme member who has newly stopped matching.
- **R22** [X] A member already reported is not reported again
  while they remain ineligible, however many scans run.
- **R23** [✅] A member who matches again is listed as eligible,
  and is reported afresh if they stop matching later.
- **R24** [X] Camps and one-offs, a programme with no live
  occurrence left and a soft-deleted programme produce no notification.

## Migration

- **R30** [✅] Each stored date bound becomes a strict age counted
  from the record's reference day on the day of the migration, so every
  event and group reports the same two dates immediately afterwards. A
  bound later than its reference day becomes age zero.
