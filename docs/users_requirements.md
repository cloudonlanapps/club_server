# Users — Requirements

This document is the testable specification for user accounts: how an
account comes into being and is approved, the statuses it moves through,
roles, the profile fields and who may change them, reads of other users,
and deletion. It is written as plain-English rules. The code defines
current behaviour; this document records it. Each rule's test names it
with `@pytest.mark.requirement("users:Rn")`, and
`tests/test_requirement_coverage.py` checks every marked rule has one.

Related documents, not restated here:

- Login, tokens, passwords and what those flows email:
  [`auth_requirements.md`](auth_requirements.md).
- The public staff page and public profiles, the coach's consent to
  appear there (`is_public_profile`), guest coaches and the staff
  display order: [`public_requirements.md`](public_requirements.md) R1–R7.
- The identity document a registration must carry:
  [`media_requirements.md`](media_requirements.md) R77.
- The in-app notices each account change raises:
  [`notifications_requirements.md`](notifications_requirements.md)
  R97–R106.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[UNTESTED]** — implemented, but no test proves it yet.

Vocabulary:

- A user's **status** is one of `registered` (signed up, not yet put
  before admins), `pending` (awaiting approval), `active`, `blocked`,
  `left`.
- The **roles** are `admin` and `coach`; a user may hold both or neither.
  There is no `member` role: a **member** is a user with no role.
- The **super admin** is a flag on one account, not a role. It passes
  every role check.
- **Staff** = admin, coach or super admin.
- A **review request** is an admin's note asking a pending user to revise
  their registration. At most one per user is **active** (unresolved).

---

## Registration

- **R1** [✅] Anyone can register an account with a username, a
  password and their profile. With identity verification on (the
  default) the account starts `registered`, and no admin hears of it
  until the user submits it for review (R10).
- **R2** [✅] With identity verification off the account starts
  `pending`, and admins are notified at once.
- **R3** [X] Self-registration and admin creation both require a name
  (first or last, not blank), a gender, a date of birth and a phone
  number. Every missing one is listed in a single 400
  `MISSING_REQUIRED_PROFILE_FIELDS`.
- **R4** [✅] A first name alone, or a last name alone, satisfies the
  name requirement.
- **R5** [X] A username already taken → 409 `DUPLICATE_USERNAME`, on
  registration and on admin creation, including when two registrations
  race for it.
- **R6** [X] An email already held by another live user, compared
  without regard to case → 409 `DUPLICATE_EMAIL`, on registration, admin
  creation, profile edit and reapply; nothing is changed.
- **R7** [✅] A soft-deleted user's email is free for a new account.
- **R8** [✅] Anyone, without logging in, can ask whether a username is
  available; a taken one answers false. A missing, empty or over-50
  character name → 422.
- **R8a** [✅] A soft-deleted user's username is reported taken, since
  registering it is refused (R5) (#505).
- **R9** [X] A date of birth must fall on a UTC midnight → 422
  `INVALID_DOB_NOT_UTC_MIDNIGHT`, on registration, admin creation and
  profile edit.
- **R9a** [X] Reapply (R19) applies the same check to the date of birth
  it accepts → 422 `INVALID_DOB_NOT_UTC_MIDNIGHT`, and nothing is changed
  (#506).
- **R9b** [X] Gender is one of `male`, `female`, `other`,
  `prefer_not_to_say`; anything else → 422. A field the request does not
  define → 422.
- **R9c** [✅] The address is optional and structured (two lines, city,
  state, pincode); parts not sent are null, and a user registered
  without one has a null address.

## Review and approval

- **R10** [✅] A `registered` user can submit their account for review:
  it becomes `pending` and every admin is notified. The identity
  document this needs when verification is on is media R77.
- **R11** [X] Submitting from any status other than `registered` → 409
  `INVALID_STATE`; anonymously → 401.
- **R12** [✅] An admin can approve a `pending` user, who becomes
  `active` and can then use the member app.
- **R13** [✅] Approval emails the user that their account is approved
  when they have an email address. The email is best effort: approval
  succeeds without an address, and the audit row records whether it was
  sent.
- **R14** [X] Approving a user who is not `pending` (`registered` or
  `active`, for example) → 422 `INVALID_STATE`.
- **R15** [✅] An admin can send a `pending` user back with a reason (1
  to 500 characters): the answer is 201, the user returns to
  `registered`, and an active review request carrying the reason is
  opened. The admins' outstanding approval notice is withdrawn
  (notifications R38).
- **R16** [✅] Sending back a user who already has an active review
  request closes the earlier one as `superseded`; a user never has two
  active review requests.
- **R17** [X] Sending back a user who is not `pending` → 409
  `INVALID_STATE`; oneself → 400 `CANNOT_RECONSIDER_SELF`; an unknown
  user → 404; an empty or over-long reason → 422.
- **R18** [✅] While a user is `registered` with an active review
  request, the reason is shown as the admin review note on their own
  profile and on the staff view of their private profile; otherwise the
  note is null. It never appears on the basic profile or in the user
  list.
- **R19** [✅] A user who was sent back can resubmit their registration
  fields (names, date of birth, gender, phone, email) — gender and date
  of birth included, which otherwise only the super admin may change
  (R35). This closes the review request as `resubmitted`; the user stays
  `registered` until they submit for review again (R10).
- **R20** [X] Reapplying for someone else → 403
  `INSUFFICIENT_PERMISSION`.
- **R21** [X] Reapplying without an active review request → 409
  `NO_ACTIVE_REVIEW_REQUEST`, and nothing is changed; reapplying while
  not `registered` → 409 `INVALID_STATE`.
- **R22** [X] Reapply accepts only the registration fields; any other
  field → 422 and nothing is applied.
- **R23** [✅] Approving or blocking a user closes their active review
  request, if any, as `approved` or `blocked`, with the admin's optional
  closing note.

## Blocking, leaving and reactivation

- **R24** [✅] An admin can block a user in any status, `registered`
  included; the user becomes `blocked`. What a block does to login and
  tokens is in auth R4 and R12.
- **R25** [✅] An admin can unblock a blocked user, who becomes
  `active`.
- **R26** [X] Blocking a user who is already blocked → 422
  `ALREADY_BLOCKED`; unblocking one who is not blocked → 422
  `NOT_BLOCKED`.
- **R27** [✅] An admin can mark an `active` user as `left`, and
  reactivate a user who left, who becomes `active`.
- **R27a** [X] Marking a `registered`, `pending` or `blocked` user as
  left → 422 `INVALID_STATE`, and their status is unchanged, so
  reactivation cannot make `active` a user who was never approved or
  was blocked (#513).
- **R28** [X] Marking a user left who already left → 422
  `ALREADY_LEFT`; reactivating a user who did not leave → 422
  `NOT_LEFT`.

## Roles

- **R29** [✅] The roles are `admin` and `coach`; `member` is not a
  role, and assigning or removing it → 422.
- **R30** [✅] An admin can give a user a role and take one away.
- **R31** [X] Giving a role the user already holds → 409
  `ROLE_ALREADY_ASSIGNED`; taking one they do not hold → 404
  `ROLE_NOT_FOUND`; a value that is not a role → 422.
- **R31a** [X] `super_admin` is not a role: giving it or taking it → 422
  and the user's roles are unchanged. The user does not become super
  admin; that standing changes only by handover (R56) (#514).

## Account administration access

- **R32** [X] Approving, sending back, blocking, unblocking,
  marking left, reactivating, creating, soft-deleting and restoring
  users, and giving or taking roles, are admin operations: a coach or a
  member → 403, an anonymous caller → 401.
- **R32a** [X] A member cannot give or take roles → 403.

## Profile

- **R33** [✅] A user can edit their own profile.
- **R34** [✅] An admin can edit any user's profile, except as R37 says.
- **R34a** [X] A coach or member editing another user's profile
  → 403.
- **R35** [X] Gender and date of birth can be changed only by the super
  admin: a user editing their own, or an admin who is not super admin
  editing anyone's → 403 `PROTECTED_FIELDS` naming the fields, and
  nothing is changed.
- **R36** [✅] The super admin can change gender and date of birth, and
  clear the date of birth by sending null.
- **R36a** [✅] A profile edit changes only the fields it sends; a
  field left out keeps its value.
- **R37** [X] Only the super admin can edit the super admin's profile:
  another admin → 403 `SUPER_ADMIN_PROTECTION`, whatever the field, and
  nothing is changed. The super admin can edit their own.
- **R38** [✅] A user can choose whether their real name is shown
  publicly; it is off until set.
- **R39** [✅] Only a coach can make their own profile public; an
  admin's edit of the flag is ignored, and a user without the coach role
  who sets it stays not public. Making the profile not public also
  switches the public name off. What a public profile shows is public
  R1–R7.
- **R40** [✅] An admin can create a user directly, with a password the
  admin chooses, and that user can log in with it. The guest flag is
  public R6.
- **R40a** [✅] An account an admin creates starts `active`, whether the
  request names `active` as its status or no status at all.
- **R40b** [X] A status other than `active` on admin creation — another
  status, or a value that is not a status → 422, and no user is created
  (#522).

## Reading users

- **R41** [✅] An admin or coach can list users, paginated. The list
  leaves out soft-deleted users and the super admin, and leaves out
  `registered` users unless asked for them by status.
- **R42** [✅] The list filters by status, by role, and by a search term
  matched against username, names, nickname and email.
- **R43** [✅] The list filters by age (a minimum and a maximum in
  whole years) and sorts by creation time (the default), username, first
  name or last name, either way.
- **R44** [X] An anonymous caller cannot list users → 401.
- **R44a** [X] A member cannot list users → 403.
- **R45** [✅] An admin or coach can count users per status. Every
  status is reported, zero included, and the total includes `registered`
  users. Soft-deleted users and the super admin are counted nowhere.
- **R46** [X] A member cannot count users → 403; an anonymous caller →
  401.
- **R47** [✅] Staff can read any user's basic profile by username, the
  super admin's and a soft-deleted user's included (the latter with its
  deletion time). An unknown username → 404.
- **R47a** [✅] A member can read another user's basic profile.
- **R48** [✅] Staff can read any user's private profile.
- **R48a** [✅] A member can read their own private profile;
  reading another user's → 403.
- **R49** [✅] A user can list the groups they belong to, and
  staff can list anyone's; a member asking for another user's → 403.

## Deletion

- **R50** [✅] An admin can soft-delete a user. The user keeps their
  record with its deletion time, readable by username, and leaves the
  user list for the deleted-users list.
- **R51** [✅] An admin can list soft-deleted users, paginated, with a
  search term; the super admin never appears in it.
- **R51a** [X] A coach listing soft-deleted users → 403.
- **R52** [✅] An admin can restore a soft-deleted user.
- **R53** [X] Restoring a user who is not deleted → 422
  `NOTHING_TO_RESTORE`, and the user is unchanged (#526).
- **R53a** [X] Soft-deleting a user who is already deleted → 422
  `ALREADY_DELETED`, and their deletion time is unchanged; an unknown
  user → 404 (#523).
- **R54** [✅] The super admin can permanently delete a user who has
  been soft-deleted. Their enrollments and group memberships go with
  them; the events they organized and the evaluation templates they
  created pass to the super admin who deleted them.
- **R55** [X] Only the super admin can permanently delete → 403; a user
  who has not been soft-deleted first → 422
  `HARD_DELETE_NEEDS_SOFT_DELETE` (#526).

## Super admin

- **R56** [✅] The super admin can hand the super-admin standing to
  another user.
- **R56a** [✅] Once handed over, the former super admin no longer
  holds it: they are refused what only the super admin may do.
- **R57** [X] Only the super admin can hand it over → 403.
- **R58** [X] Handing it to oneself → 400
  `CANNOT_TRANSFER_TO_SELF`.
- **R59** [X] The super admin cannot be blocked, soft-deleted or
  permanently deleted → 403 (`SUPER_ADMIN_PROTECTION` for block and soft
  delete), not even by themselves.
- **R59a** [X] Marking the super admin left → 403
  `SUPER_ADMIN_PROTECTION`.
- **R60** [X] The super admin never appears in the user list, the
  deleted-users list or the counts.

## Audit

- **R61** [✅] Submitting for review, sending back (with its reason),
  reapplying and approving (with whether the email was sent) each write
  an audit row naming the actor.
- **R62** [✅] Every other account change in this document —
  profile edit, block, unblock, role changes, creation, soft and
  permanent deletion, restore, marking left, reactivation and handing
  over the super-admin standing — writes an audit row naming the actor
  and the user acted on.

