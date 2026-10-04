# Authentication — Requirements

This document is the testable specification for signing in and staying
signed in: login, the access and refresh tokens, logout, changing a
password, the forgotten-password reset, what invalidates a token, and
which of these flows send email. It is written as plain-English rules.
The code defines current behaviour; this document records it. Each
rule's test names it with `@pytest.mark.requirement("auth:Rn")`, and
`tests/test_requirement_coverage.py` checks every marked rule has one.

Related documents, not restated here:

- Registration, account statuses, blocking and deletion:
  [`users_requirements.md`](users_requirements.md).
- An admin resetting a member's password:
  [`platform_requirements.md`](platform_requirements.md).
- The in-app notices a password change raises:
  [`notifications_requirements.md`](notifications_requirements.md)
  R104–R105.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[UNTESTED]** — implemented, but no test proves it yet.

Vocabulary:

- An **access token** authenticates a request. A **refresh token** is
  exchanged for a new pair of tokens and authenticates nothing else.
- A token's **issue time** is carried to the millisecond.
- The statuses (`registered`, `pending`, `active`, `blocked`, `left`) are
  those of the users document.

---

## Login

- **R1** [✅] A user logs in with username and password and receives a
  bearer access token, the time it expires, and a refresh token.
- **R2** [X] A wrong password or an unknown username → 401
  `INVALID_CREDENTIALS`, the same answer for both.
- **R3** [✅] A `registered` or `pending` user can log in and read their
  own profile, so the app can show them where their application stands.
- **R3a** [X] Such a user is refused anything that needs an approved
  account → 403 `ACCOUNT_NOT_ACTIVE`.
- **R4** [X] A blocked user cannot log in → 401 `ACCOUNT_BLOCKED`.
- **R5** [X] A soft-deleted user cannot log in → 401
  `INVALID_CREDENTIALS`, as if unknown.
- **R6** [X] A user who left cannot log in → 401 `ACCOUNT_LEFT`, the
  code their tokens are refused with (R13); once reactivated they can
  log in again (#507).
- **R7** [✅] Logging in records the time as the user's last
  login, shown on their private profile.

## Using a token

- **R8** [X] A request with no token, a malformed authorization header
  or a token that does not verify → 401.
- **R9** [X] A refresh token presented as an access token → 401
  `INVALID_TOKEN`.
- **R10** [✅] A token issued before tokens carried a type and an issue
  time is still accepted while the user's password is unchanged.
- **R11** [✅] Any logged-in user who is not blocked and has not left
  can read their own profile.
- **R12** [X] A blocked user's tokens → 401 `ACCOUNT_BLOCKED`, and work
  again once the user is unblocked.
- **R13** [X] The tokens of a user who left → 401
  `ACCOUNT_LEFT`.
- **R14** [X] The tokens of a soft-deleted user → 401
  `USER_NOT_FOUND`.

## Refresh

- **R15** [✅] A valid refresh token is exchanged for a new access token,
  its expiry time and a new refresh token.
- **R16** [X] A refresh token that does not verify → 401
  `INVALID_REFRESH_TOKEN`.
- **R16a** [X] An access token presented for refresh → 401
  `INVALID_REFRESH_TOKEN`.
- **R17** [X] A blocked user cannot refresh → 401
  `ACCOUNT_BLOCKED`.
- **R17a** [X] A user who left cannot refresh → 401 `ACCOUNT_LEFT`.
- **R18** [✅] An access token lives for the deployment's
  configured number of minutes, and the expiry time returned with it
  says when; a refresh token lives for 30 days.

## Password change invalidates tokens

- **R19** [X] Once a user's password changes — by their own change or
  an admin's reset — every access and refresh token issued before the
  change is refused (`INVALID_TOKEN`, `INVALID_REFRESH_TOKEN`); tokens
  issued after it work.
- **R19a** [X] A forgotten-password reset (R28) refuses earlier
  tokens in the same way.
- **R20** [X] The comparison is to the millisecond: a token issued
  earlier in the same second as the change is refused, one issued later
  in that second or at the same instant is accepted. A token without an
  issue time is refused once the password has changed.

## Logout

- **R21** [✅] Any logged-in user can log out, whatever their status
  (`registered`, `pending`, `active`) → 204, with no request body.
- **R22** [X] Logging out ends the session the access token belongs to:
  that token → 401 `INVALID_TOKEN`, and the refresh token issued with it
  → 401 `INVALID_REFRESH_TOKEN` (#510).
- **R22a** [X] A session is everything one login issued: the tokens a
  refresh issues belong to the session they were refreshed from, so
  logging out with any of its access tokens refuses them all.
- **R22b** [✅] Logging out ends only that session: the user's other
  sessions, and anyone else's, keep working, and logging in again starts
  a new one.
- **R22c** [✅] A token issued before sessions were recorded belongs to
  none: logging out with it answers 204, and it keeps working until it
  expires.

## Changing one's password

- **R23** [✅] A logged-in user can change their password by giving the
  current one; the old password stops working and the new one works.
- **R24** [X] A wrong current password → 401 `INVALID_CREDENTIALS`.
- **R25** [X] An anonymous caller cannot change a password → 401.
- **R26** [✅] A `registered` or `pending` user can change their
  password, as an active one can (#510).
- **R27** [✅] Any non-empty password is accepted; there is no
  strength rule.

## Forgotten password

- **R28** [✅] Anyone can ask for a password reset by email address. The
  answer is always 204 with no body, whether or not the address belongs
  to an account.
- **R29** [✅] For the address of a live user, the server generates a
  new password and emails it to them; there is no link or token step.
  The emailed password then works and the old one does not.
- **R30** [✅] The password changes only if the email was delivered; a
  failed delivery leaves the old password working.
- **R31** [✅] Each request writes an audit row recording the address,
  whether it matched an account and whether the email was sent. The
  response never reveals either.
- **R32** [✅] The address is matched without regard to case, as
  addresses are unique regardless of case (users R6); the email goes to
  the address as stored (#508).
- **R33** [X] A value that is not an email address → 422.

## Email

- **R34** [✅] Of the flows in this document only the forgotten
  password sends email (R29). Logging in and changing one's own
  password send none; the change raises an in-app notice instead.
  Approval email is users R13; the admin reset email is in the platform
  document.

## Audit

- **R35** [✅] Registering, logging in, refreshing, logging out
  and changing one's password each write an audit row naming the user,
  with the client's address.
