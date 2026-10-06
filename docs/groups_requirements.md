# Groups — Requirements

This document is the testable specification for the groups subsystem
(first shipped in issue #11), written as plain-English rules. The code
defines current behaviour; this document records it. Each rule's test names
it with `@pytest.mark.requirement("groups:Rn")`, and
`tests/test_requirement_coverage.py` checks every marked rule has one.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[UNTESTED]** — implemented, but no test proves it yet.

Vocabulary:

- A group has one of three **kinds**: `manual`, `semi_auto`, `auto`.
- **Criteria** = any of `minAge`, `maxAge`, `gender`. The two ages are an
  age band ([eligibility](eligibility_requirements.md) R1); the group
  reports the window of birth dates it comes to today as `dobOnOrAfterUtc`
  and `dobOnOrBeforeUtc`.
- The `kind` is derived from the create/update payload (not directly
  settable): no criteria → `manual`; criteria + `semiAuto: false` (or
  omitted) → `auto`; criteria + `semiAuto: true` → `semi_auto`.
- Both ends of the window are inclusive at the **day level** — a user born
  on the upper-bound day is included.
- **Staff** = users with the `admin` or `coach` role, or super-admin.
- A **plain member** is an active user with neither role.

---

## Group lifecycle (admin)

- **R1** [✅] admin can create a `manual` group (no criteria in payload).
- **R2** [✅] admin can create an `auto` group (criteria, `semiAuto` omitted or false).
- **R3** [✅] admin can create a `semi_auto` group (criteria + `semiAuto: true`).
- **R4** [✅] admin can update name and description on any group.
- **R5** [✅] admin can update `minAge`, `maxAge`, `strictAge`, `gender`, and `semiAuto`; `kind` is re-derived.
- **R5a** [✅] an update that omits `semiAuto` keeps the group's current mode: a `semi_auto` group whose criteria change stays `semi_auto`.
- **R6** [✅] admin can convert an empty manual group to `auto` or `semi_auto`.
- **R7** [✅] admin can convert `semi_auto` or `auto` back to `manual` by clearing all criteria; existing members are kept.
- **R8** [X] admin can't convert a manual group **with members** to `auto` → 422 `MEMBERS_EXIST`.
- **R9** [X] admin can't convert manual to `semi_auto` when **any** existing member fails the new criteria → 422 `MEMBERS_INELIGIBLE` (response lists offending usernames).
- **R9a** [X] admin can't change a `semi_auto` group's criteria so that an existing member fails them → 422 `MEMBERS_INELIGIBLE` listing that member; the group's criteria are unchanged.
- **R10** [✅] admin can convert manual → `semi_auto` when every existing member satisfies the criteria (or is staff).
- **R10a** [✅] admin can change a `semi_auto` group's criteria when every existing member still satisfies them.
- **R11** [X] a date-of-birth bound (`dobOnOrAfterUtc`, `dobOnOrBeforeUtc`) is not accepted on create or on update → 422 (eligibility R11); until #16 it was accepted when at UTC midnight.
- **R12** [X] inverted age band (`minAge` greater than `maxAge`, which is an inverted window) is rejected on create and on update → 422.
- **R13** [✅] admin can soft-delete a group; it stays readable by id with its deletion time set.
- **R14** [✅] admin can restore a soft-deleted group.
- **R14a** [X] restoring a group that is not soft-deleted is rejected → 422 `NOTHING_TO_RESTORE` (#526).
- **R15** [X] regular admin can't hard-delete → 403.
- **R16** [✅] super-admin can hard-delete, but only after soft-delete.
- **R17** [X] hard-delete on a non-soft-deleted group is rejected → 422 `HARD_DELETE_NEEDS_SOFT_DELETE` (#526).
- **R18** [X] soft-deleted groups don't appear in the group list.
- **R19** [✅] admin can list deleted groups.
- **R20** [X] coach can't list deleted groups → 403.

## Group reads

- **R21** [✅] admin or coach can list all groups.
- **R22** [✅] admin or coach can view a group by id and list its members.
- **R23** [✅] for an `auto` group, the member list is the dynamically computed user set; the member count reflects that set.
- **R24** [X] plain member can't call any group-administration operation → 403.
- **R25** [X] anonymous callers can't call any group-administration operation → 401.

## Eligible-user query

- **R26** [✅] admin or coach can list the users eligible to join a manual or semi_auto group.
- **R27** [✅] eligible list for **manual** = all active, non-deleted, non-super-admin users, minus current members and minus users with a pending request.
- **R28** [✅] eligible list for **semi_auto** = criteria-matching active users **∪** admins/coaches, minus current members and pending requesters; super-admins excluded.
- **R29** [X] super-admin is never returned in the eligible list.
- **R30** [X] users who are not active (any status other than active, e.g. awaiting approval) are excluded.
- **R31** [X] users with a pending join request for the group are excluded.
- **R32** [X] the eligible list of an `auto` group is refused → 422 `AUTO_GROUP_NOT_JOINABLE` (auto groups have no joinable list — the member list already exposes the computed set).
- **R33** [X] the eligible list of an unknown or soft-deleted group → 404.

## Adding members (admin)

- **R34** [✅] admin can add any active, non-deleted user to a `manual` group.
- **R35** [✅] admin can add a criteria-matching user to a `semi_auto` group.
- **R36** [✅] admin can add a coach or another admin to a `semi_auto` group regardless of criteria.
- **R37** [X] admin can't add a criteria-failing non-staff user to `semi_auto` → 422 `NOT_ELIGIBLE`.
- **R38** [X] admin can't add to an `auto` group, singly or in bulk → 422 `AUTO_GROUP_MODIFICATION_NOT_ALLOWED`.
- **R39** [X] admin can't add the same user twice → 409 `ALREADY_MEMBER`.
- **R40** [X] admin can't add a non-existent or soft-deleted user → 404 `USER_NOT_FOUND`.
- **R41** [✅] bulk add partitions results into `added` / `alreadyMembers` / `notFound` / `notEligible`.
- **R42** [✅] admin can remove a member from a manual or semi_auto group.
- **R43** [X] admin can't remove from an `auto` group → 422.
- **R44** [X] removing a non-member → 404 `MEMBER_NOT_FOUND`.
- **R45** [✅] when admin adds a user (single or bulk) who has a pending request for that group, the request is auto-marked `approved` with `decidedBy = acting admin`.

## Member self-service

A user's own view of groups. Every operation here names the user it acts
for; the rules below say who may name whom.

- **R46** [✅] user can list their own groups — explicit memberships (manual + semi_auto) and matching auto groups.
- **R47** [✅] user can list the groups they can request: manual groups not joined and criteria-matching semi_auto groups not joined; auto groups and joined groups are excluded.
- **R47a** [X] a semi_auto group whose criteria the user fails is excluded from the groups they can request.
- **R47b** [✅] a group the user already has a pending request for is still listed among the groups they can request, flagged as requested.
- **R48** [✅] admin / coach / super-admin can act on any user's self-service view.
- **R49** [X] one plain member can't read another member's self-service view → 403.
- **R50** [X] anonymous callers can't use the self-service view → 401.

## Join requests (member)

- **R51** [✅] user can submit a request for a `manual` group (any active user).
- **R52** [✅] user can submit a request for a `semi_auto` group when criteria match.
- **R53** [X] user can't request to join a `semi_auto` group when criteria fail → 422 `NOT_ELIGIBLE`.
- **R54** [X] user can't request to join an `auto` group → 422 `AUTO_GROUP_NOT_JOINABLE`.
- **R55** [X] user can't submit a duplicate pending request → 409 `REQUEST_PENDING`.
- **R56** [X] user can't request a group they're already in → 409 `ALREADY_MEMBER`.
- **R57** [✅] a request accepts an optional `reason`; the request body itself is also optional.
- **R58** [✅] user can list their own join requests.
- **R59** [✅] user can cancel their own pending request → status `cancelled`.
- **R60** [X] user can't cancel another user's request → 404.
- **R61** [✅] after a request is rejected or cancelled, the user can submit a new one; the same request record is revived as `pending` (at most one record per user and group).

## Join requests (admin / coach)

- **R62** [✅] admin or coach can list a group's join requests, optionally filtered by `status`.
- **R63** [✅] admin can approve a pending request — user added to the group, request marked `approved`, `decidedBy` and `decidedAt` populated.
- **R64** [✅] admin can reject a pending request, with an optional `reason` — marks `rejected`, `decidedBy`/`decidedAt` set, no member added.
- **R65** [X] coach can't approve or reject → 403.
- **R66** [X] approval re-checks `semi_auto` eligibility; if the requester is no longer eligible → 422 `NOT_ELIGIBLE`, the request stays `pending`, and `decidedAt`/`decidedBy` remain null (admin may then reject it).
- **R67** [✅] approval of an admin/coach target succeeds even when criteria don't match (staff exemption holds at decision time).
- **R68** [X] approve/reject on an unknown request id → 404.
- **R69** [X] approve/reject on a non-pending request → 409 `JOIN_REQUEST_NOT_PENDING`.

## Auto-membership computation

The window is worked out from the group's age band for today
(eligibility R4–R8). Its two dates and user dates of birth are both UTC
midnight; **both endpoints are inclusive at the day level**: the upper bound admits
anyone born before midnight of the following day.

- **R70** [✅] user with DOB `== dobOnOrAfterUtc` is included (inclusive lower bound).
- **R71** [✅] user with DOB `== dobOnOrBeforeUtc` is included (inclusive upper bound).
- **R72** [X] user born the day before `dobOnOrAfterUtc` is excluded.
- **R73** [X] user born the day after `dobOnOrBeforeUtc` is excluded.
- **R74** [X] user with no DOB is excluded whenever any DOB bound is set.
- **R75** [X] gender filter is applied for both `auto` and `semi_auto`.
- **R76** [X] users who are not active (any status other than active, e.g. awaiting approval) are excluded.
- **R77** [X] soft-deleted users are excluded.
- **R78** [X] super-admins are excluded.

## Members who stop matching

A semi-auto group stores its members and checks them only when they are
added, when a join request is approved and when the criteria are edited.
With an age band the window moves every day, so a stored member can stop
matching — by growing out of the band, or by a change to their date of
birth or gender. Such a member is kept and flagged; nobody is removed
automatically.

- **R82** [✅] A semi-auto member who no longer meets the group's
  criteria (age band or gender) stays a member, and each member row —
  in the member list and in the group detail — reports `eligible`, worked
  out when it is read.
- **R83** [✅] A group reports `ineligibleMemberCount`, the number
  of its stored members who no longer meet its criteria.
- **R84** [✅] Once a day the scheduler notifies the admins with
  `group.member_ineligible`, naming the group and the member, for each
  semi-auto member who has newly stopped matching.
- **R85** [X] A member already reported is not reported again while they
  remain ineligible, however many scans run.
- **R86** [✅] A member who matches again is listed as eligible,
  and is reported afresh if they stop matching later.
- **R87** [X] Staff are never flagged or reported: they are exempt from a
  semi-auto group's criteria (R36).
- **R88** [X] Manual and auto groups flag nobody and produce no
  notification, and a soft-deleted group is not scanned.

## Audit logging

- **R79** [✅] every group mutation writes an audit row: `create_group`, `update_group`, `soft_delete_group`, `restore_group`, `wipeout_group`, `add_group_member`, `add_group_members_bulk`, `remove_group_member`.
- **R80** [✅] every join-request mutation writes an audit row: `create_group_join_request`, `cancel_group_join_request`, `approve_group_join_request`, `reject_group_join_request`.

## Super-admin invisibility

- **R81** [X] super-admin is invisible to membership operations: adding a super-admin by name and a join request for a super-admin both → 404 `USER_NOT_FOUND`; a bulk add puts the username in the `notFound` bucket. Combined with R29/R78, super-admins never appear in any list and cannot be referenced by any membership write.
