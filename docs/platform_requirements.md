# Platform — Requirements

This document is the testable specification for the parts of the server
that belong to no one domain: the system preferences a super admin keeps,
an admin resetting a member's password, the capabilities document that
tells a client what this deployment does, and the audit log. It is
written as plain-English rules. The code defines current behaviour; this
document records it. Each rule's test names it with
`@pytest.mark.requirement("platform:Rn")`, and
`tests/test_requirement_coverage.py` checks every marked rule has one.

Related documents, not restated here:

- The `club_info` and `site_media` preferences and their public reading:
  [`public_requirements.md`](public_requirements.md) R16–R19.
- The notification retention the sweep applies:
  [`notifications_requirements.md`](notifications_requirements.md) R42.
- Which actions each domain audits: that domain's document (for example
  users R61–R62, auth R31 and R35, groups R79–R80).
- What a password change does to earlier tokens:
  [`auth_requirements.md`](auth_requirements.md) R19.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[UNTESTED]** — implemented, but no test proves it yet.

---

## System preferences

A preference is a key with a JSON value, and records who last wrote it
and when. A key never written has no writer and no time.

- **R1** [✅] The super admin can write a preference under any key with
  any JSON value, and read it back with the writer and the time.
- **R2** [✅] The super admin can list every stored preference.
- **R2a** [✅] The list is ordered by key.
- **R2b** [✅] The list also carries each key the server has a default
  for that was never written, with its default value and no writer or
  time (#518).
- **R3** [✅] Reading a key that was never written and has no server
  default answers an empty value (null), with no writer or time (#518).
- **R3a** [✅] Reading a key the server has a default for that was never
  written answers the default, with no writer or time: the notification
  retention reads 90 until written, then the written value (#518).
  The row the table-creation migration seeded (90, no writer) is
  dropped, so a migrated database answers the same as a fresh one.
- **R4** [✅] The notification retention (`notification_info_retention_days`)
  is 90 days while no one has written it.
- **R5** [X] An admin who is not the super admin cannot list or write
  preferences → 403.
- **R5a** [X] Nor read one → 403; a coach or member → 403 on
  every preference operation; an anonymous caller → 401.
- **R6** [✅] Apart from `club_info` and `site_media` (public R17,
  R18), a written value is not checked: the retention accepts any JSON
  value, a number of days or not.
- **R7** [✅] Every preference write records one audit row naming the
  actor and the key, with the previous value (null for a key never
  written) and the new one as compact JSON, cut to 200 characters; its
  summary reads "set system preference <key>". A refused write records
  nothing. Values stay unchecked (R6) (#525).

## Admin password reset

- **R8** [✅] An admin, the super admin included, can reset a user's
  password. The server generates a new password and returns it to the
  admin; the new password works and the old one does not.
- **R9** [✅] When the user has an email address the new password is
  emailed to them as well. The email is best effort: a user without an
  address or a failed delivery does not fail the reset, and the audit row
  records whether the email was sent.
- **R10** [X] The super admin's password cannot be reset this way → 403
  `CANNOT_RESET_SUPER_ADMIN`.
- **R11** [X] An unknown user → 404 `USER_NOT_FOUND`; an anonymous
  caller → 401.
- **R11a** [X] A coach or member → 403; a soft-deleted user → 404
  `USER_NOT_FOUND`.
- **R11b** [✅] An admin can reset another admin's password; only
  the super admin is protected.

## Capabilities

- **R12** [✅] Anyone, signed in or not, whatever their status or role,
  can read what this deployment does, and everyone gets the same answer.
- **R13** [✅] The document is a closed set of four flags: the credit
  system, evaluations, event marketing and identity verification.
- **R14** [✅] Each flag reports the deployment's configuration: credit
  off unless enabled, identity verification on unless disabled.
- **R14a** [✅] Evaluations and event marketing each report
  whether the deployment enables them.

## Audit log: recording

- **R15** [✅] Every audit row names one action from a closed catalogue;
  each action has a single, distinct stored name, and no request writes
  an action outside it.
- **R16** [✅] A row written for a request records the client's address
  (the forwarded address when there is one).

## Audit log: reading

- **R17** [✅] The super admin can read the whole audit log.
- **R18** [X] Anyone else reading the whole log → 403, an admin or coach
  included; an anonymous caller → 401 or 403.
- **R19** [✅] Any admin or coach can read the history of one user
  (rows where they are the actor or the target) or of one entity (a type
  and an id together), whoever owns it. The super admin can too.
- **R20** [X] A member reading a scoped history → 403. A type without an
  id, or an id without a type → 422 `INVALID_RESOURCE_SCOPE`.
- **R21** [✅] An entity's history widens with a breadth of 1 to 3: its
  own rows (the default); also its media-link rows; also, for an event,
  its occurrences' rows. The breadth has no effect on a user's history,
  and a breadth outside 1–3 → 422.
- **R22** [✅] Rows come newest first, ties broken by the newer row
  first, with an offset and a limit. The limit defaults to 50 and is
  held between 1 and 200.
- **R23** [✅] The rows filter by actor, by action and by a time window.
- **R23a** [✅] Both ends of the time window are inclusive.
- **R24** [✅] Each row names its actor and target with their full
  names, and its entity with a label: an event's title, a group's or
  venue's name; an occurrence is shown as its event's title and start
  time. Soft-deleted people and entities still resolve; a referent that
  no longer exists resolves to null; an entity type the log does not
  know passes through unlabelled.
- **R25** [✅] Usernames, events, groups and venues named in a row's
  details are resolved beside them; other details pass through
  unchanged.
- **R26** [✅] Each row carries an English summary sentence: who did
  what, when (in UTC), and from which address when one was recorded.
  Every action in the catalogue has one; an action outside it reads
  "performed action …"; a language without a sentence for an action is
  left out.
