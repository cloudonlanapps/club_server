# Notifications — Requirements

This document is the testable specification for the notifications
subsystem: the per-user feed, pending actions, broadcasts, retention, the
undo policy, and every notification the server generates. It is written as
plain-English rules and avoids naming HTTP endpoints or source files, so
the API surface can evolve without invalidating it. The code defines
current behaviour; this document records it. Each rule's test names it with
`@pytest.mark.requirement("notifications:Rn")`, and
`tests/test_requirement_coverage.py` checks every marked rule has one.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[UNTESTED]** — implemented, but no test proves it yet.

## Vocabulary

- A **notification** is one stored message addressed to exactly one user.
  One business action delivered to many people produces one notification
  per recipient.
- The **payload** of a generated notification is structured facts, never
  prose: a schema version (currently `1`), the dotted **type** (e.g.
  `group.join_request`) and a `data` object. Turning facts into text,
  including translation, is the client's job.
- A notification is **informational** when it records that something
  happened, or **actionable** when it carries a **pending-action pointer**:
  a kind plus the id (or, for user approval, the username) of the domain
  record whose unresolved state means the recipient still has something to
  do.
- A **broadcast** is an admin-authored message fanned out at send time into
  one notification per recipient.
- **Admins** = active users with the `admin` role, plus super-admins.
- **Staff** = active users with the `admin` or `coach` role, plus
  super-admins. Staff-facing event notices go to all staff, not only the
  staff of that event; #414 narrows this to the event's organizer and
  coaches.
- An event's **enrolled members** are the users whose enrollment on it is
  not terminal: invited, requested, accepted, assigned, assigned as trial,
  or withdrawal requested.
- An event's **change audience** is its enrolled members plus its assigned
  coaches and its organizer, each user once.
- The **channel** recorded on every generated notification is `app`: the
  in-app feed is the only delivery path for generated notifications today.

---

## Payload and generation contract

- **R1** [✅] Every generated notification carries a structured payload with
  the schema version, the dotted type and a `data` object.
- **R2** [✅] A notification's recorded type equals the type inside its
  payload, so clients can filter on either.
- **R3** [✅] Payload data carries identifiers (group id, event id, member
  username, and so on) rather than pre-rendered text; names such as a
  group's name or an event's title travel alongside the ids.
- **R4** [✅] Each recipient's copy is an independent row: marking one
  recipient's copy read does not change anyone else's.
- **R5** [✅] A recipient that does not exist is skipped silently; the
  other recipients still get their rows and the originating action
  completes.
- **R6** [✅] A soft-deleted recipient is skipped the same way.
- **R7** [✅] Apart from R7a and R7b, account status does not filter the
  recipients of a notification addressed to a named user. (Lists derived
  from roles — admins, staff — contain only active users.)
- **R7a** [✅] A user who has left receives no notification of any kind —
  a notice naming them, a group or event notice, a broadcast — whatever
  the audience; they are skipped as a soft-deleted recipient is, and the
  other recipients still get theirs (#511).
- **R7b** [✅] A blocked user receives only the notices about their
  account: `user.blocked`, `user.unblocked`, `user.deleted`,
  `user.restored`, `user.role_changed`,
  `account.password_changed_by_admin`, `account.password_changed_self`,
  `profile.changed_by_admin`, `credit.released` and
  `evaluation.withdrawn`. Every other type — broadcasts, group, event,
  occurrence, enrollment, attendance, media, venue and inquiry notices,
  `evaluation.published`, `evaluation.transferred`,
  `account.registration_approved`, `user.registration_pending` — is
  skipped for them as for a soft-deleted recipient (#512).
- **R8** [✅] Notifications are written after the domain change they
  report, in the same transaction; an action that is refused writes none.
- **R9** [✅] An action with no recipients writes nothing.

## The feed

- **R10** [✅] A user can list their own notifications, paginated with a
  total.
- **R11** [✅] The feed and the pending actions are ordered newest
  first.
- **R12** [✅] A user can list only their unread notifications.
- **R13** [✅] A user can read the count of their unread notifications.
- **R14** [✅] A user can mark one of their notifications read.
- **R15** [✅] A user can mark all of their notifications read at once.
- **R16** [X] A user never sees another user's notifications.
- **R17** [X] A user cannot mark another user's notification read → 404,
  as if it did not exist.
- **R18** [X] An unauthenticated caller cannot read the feed or the
  preferences → 401.
- **R19** [✅] A registered or pending user, not yet approved, can list
  their own feed.
- **R20** [X] The pending actions need an active account; a
  registered or pending user → 403 `ACCOUNT_NOT_ACTIVE`.
- **R20a** [✅] A registered or pending user can read their unread count,
  mark one or all of their notifications read, and read and change their
  delivery preferences, as they can list their feed (#516).

## Delivery preferences

- **R21** [✅] A user can read their delivery preferences for email, push
  and SMS. With none stored the defaults are email on, push on, SMS off.
- **R22** [✅] A user can change any subset of their preferences; the
  change persists.
- **R23** [X] A non-boolean preference value → 422.
- **R24** [✅] The preferences are stored but gate nothing today:
  every generated notification reaches the in-app feed whatever they say,
  and broadcast email does not consult them (R129).

## Admin-issued notifications

- **R25** [✅] An admin can create a notification for any existing user,
  supplying its type, channel, payload and an optional pending-action
  pointer. The payload is stored as supplied.
- **R26** [X] A non-admin cannot create a notification → 403.
- **R27** [X] Creating a notification for an unknown user → 404
  `USER_NOT_FOUND`.
- **R27a** [X] Creating a notification for a user who may not receive
  it (R7a, R7b) → 422 `RECIPIENT_NOT_DELIVERABLE`, and nothing is written.
- **R28** [✅] An admin can delete any user's notification.
- **R29** [✅] Creating and deleting a notification each write an
  audit entry naming the actor and the notification id; a create also names
  the target user.

## Pending actions

- **R30** [✅] A user can list their actionable notifications whose linked
  record is still unresolved, paginated with a total.
- **R31** [✅] Five pointer kinds are registered, each unresolved while its
  record is in one state: a group join request while pending; an event
  invitation while the enrollment is invited; an enrollment request while
  the enrollment is requested; a leave request while the attendance record
  is leave-requested; a user approval while the user is pending and not
  deleted.
- **R32** [X] A notification without a pointer, or whose pointer names a
  record that does not exist, never appears in the pending actions.
- **R33** [X] A notification whose pointer kind is not registered
  never appears in the pending actions.
- **R34** [✅] When the linked record leaves its unresolved state by any
  path, the notification drops out of every recipient's pending actions
  without being marked read.
- **R35** [✅] A notification that drops out of the pending actions only
  because its record changed state stays in the general feed.
- **R36** [✅] Resolving through the action's own flow deletes the
  actionable notification from every recipient's feed as well: approving,
  rejecting or cancelling a group join request; accepting an invitation;
  approving an enrollment request; approving a leave request; approving a
  pending user.
- **R36a** [✅] Adding a user who has a pending request to join a group
  directly, singly or in bulk, approves the request and deletes its
  actionable notices from every admin's feed, as approving it does (#524).
- **R37** [✅] The same deletion follows declining an invitation,
  rejecting an enrollment request, rejecting or cancelling a leave
  request, and blocking a pending user.
- **R37a** [✅] Approving or rejecting a withdrawal deletes no notice:
  a withdrawal request carries no actionable notice of its own, and
  staff's `enrollment.rsvp` request notice for the same enrollment stays
  in their feeds (#515).
- **R38** [✅] Reconsidering a pending user deletes the admins' approval
  notifications and sends none; the user's resubmission sends a fresh one,
  so each admin holds one.
- **R39** [✅] Hard-deleting a user deletes every actionable notification
  keyed to that user; informational notifications that merely mention them
  are kept.
- **R40** [X] A user never sees another user's pending actions.
- **R41** [X] An unauthenticated caller cannot list pending actions → 401.

## Retention

- **R42** [✅] A daily sweep deletes informational notifications once
  they pass their expiry: creation time plus the retention period, rounded
  up to the next UTC midnight. The period is the system preference
  `notification_info_retention_days`, 90 days unless an admin changes it.
- **R43** [✅] A broadcast notification whose broadcast has an expiry is
  swept at that expiry instead; one without falls back to the retention
  period.
- **R44** [✅] A broadcast's expiry is rounded up to the next UTC midnight
  when it is set; a value already at midnight is kept.
- **R45** [X] The sweep never deletes an actionable notification whose
  record is unresolved.
- **R46** [X] The sweep skips every notification that carries a
  pending-action pointer, resolved or not; such a notification leaves the
  feed only when a resolution flow deletes it (R36, R36a, R37).

## Undoing a cancellation

Applies when a camp's series cancellation is undone and when a cancelled
occurrence is restored (which includes reinstating a dropped one-off).

- **R47** [✅] For each recipient, if their most recent matching
  cancellation notice is unread and at most five minutes old, it is deleted
  and no restored notice is sent: they never see the churn.
- **R48** [✅] Otherwise — the notice was read, or is older — it is kept
  and a restored notice (`event.restored`, `occurrence.restored`) is sent,
  so they see the cancel and the restore.
- **R49** [✅] The policy runs over the event's change audience.

## Message classes

Left empty on purpose. #398 gives every notification type a message class
(transactional, mandatory broadcast, informational) and #491 records the
classes and their rules here.

## Group notifications

- **R50** [✅] A join request notifies every admin with an actionable
  `group.join_request` carrying the group, the requester and their reason.
- **R51** [✅] Approving or rejecting a join request notifies the requester
  with `group.join_response`, carrying the outcome and, on rejection, the
  reason.
- **R52** [✅] An admin adding a user to a group, singly or in bulk,
  notifies each added user with `group.member_added`, naming who added
  them.
- **R53** [✅] Removing a member notifies the removed user with
  `group.member_removed`.
- **R54** [✅] Soft-deleting a group notifies every explicit member at that
  moment with `group.archived`.
- **R55** [✅] A settings change that actually changes something notifies
  every explicit member with `group.settings_changed`, listing each changed
  field with its old and new value; re-applying the same values is silent.
- **R56** [X] An auto group has no explicit members, so deleting it
  or changing its settings notifies nobody.

## Enrollment notifications

- **R57** [✅] Staff-facing enrollment and attendance notices reach every
  admin and every coach, as the staff definition says.
- **R58** [✅] Inviting a user notifies them with an actionable
  `enrollment.opened`.
- **R59** [✅] Accepting or declining an invitation notifies staff with an
  informational `enrollment.rsvp` carrying the outcome and the member; the
  member is not notified.
- **R60** [✅] Requesting to join notifies staff with an actionable
  `enrollment.rsvp`, outcome `requested`.
- **R61** [✅] Assigning a user, regular or trial, notifies them with
  `enrollment.admin_enrolled`, flagged as trial or not.
- **R62** [✅] Approving a request notifies the member with
  `enrollment.admin_enrolled`, flagged as approved via request.
- **R63** [✅] Rejecting a request notifies the member with
  `enrollment.closed`, carrying the reason.
- **R64** [✅] Requesting withdrawal notifies staff with an informational
  `enrollment.cancelled_self` carrying the member and the reason; the
  member is not notified.
- **R65** [✅] Removing an enrollment notifies the member with
  `enrollment.cancelled_admin`, carrying the reason.
- **R66** [✅] Approving a withdrawal notifies the member with
  `enrollment.cancelled_admin`, flagged as via withdrawal.
- **R67** [X] Rejecting or cancelling a withdrawal request notifies
  nobody.
- **R68** [✅] A trial member removed because their trial credit ran out is
  notified with `enrollment.trial_ended`.

## Event notifications

- **R69** [✅] A venue change notifies the enrolled members with
  `event.venue_changed`, carrying the previous and new venue.
- **R70** [✅] A change to the start time, end time or recurrence rule
  notifies the enrolled members with `event.rescheduled`, listing each
  changed field with its old and new value.
- **R71** [✅] A change of coaches notifies the enrolled members with
  `event.coach_changed`, carrying the new coaches; re-applying the same
  coaches is silent.
- **R72** [✅] One change touching several of these emits one notification
  per kind, so each can be rendered or filtered separately.
- **R73** [✅] Soft-deleting an event notifies its enrolled members with
  `event.deleted`, carrying the deletion time.
- **R74** [✅] Restoring an event notifies its enrolled members with
  `event.restored`.
- **R75** [✅] Cancelling a camp series notifies the change audience with
  `event.cancelled`, carrying the reason and the effective time.
- **R76** [X] A user whose enrollment is already terminal is not
  among the enrolled members, so receives none of these.
- **R77** [✅] Terminating a programme notifies the change audience
  with `event.terminated`, carrying the reason and the cutoff.
- **R78** [✅] Moving a programme's cutoff notifies the change audience
  with `event.extended`, carrying the previous and new cutoff.
- **R79** [✅] Changing a programme from a date on notifies the change
  audience with `event.split`, carrying the cutoff and the changes.
- **R80** [✅] Creating or changing an event in a way that leaves advisory
  clashes (venue, organizer, coach) notifies every admin with
  `event.conflict_detected`, listing the clashing events; no clash is
  silent.
- **R81** [✅] Rescheduling one occurrence notifies the change audience
  with `occurrence.rescheduled`, carrying only the fields that changed.
- **R82** [✅] Cancelling one occurrence, which includes dropping a
  one-off, notifies the change audience with `occurrence.cancelled`,
  carrying the occurrence and the reason.
- **R83** [✅] Restoring a cancelled occurrence follows the undo policy
  (R47, R48).
- **R84** [✅] A super-admin acting past an occurrence or inside its lead
  time — cancelling a camp series, cancelling, restoring or rescheduling
  an occurrence — sends no notification.
- **R85** [✅] Every five minutes the scheduler reminds enrolled members of
  each occurrence starting within 24 hours and within 1 hour with
  `event.upcoming_reminder`; occurrences further out are not reminded.
- **R86** [✅] Each reminder is sent once per occurrence and lead time,
  however often the scan runs.
- **R87** [X] An occurrence that is cancelled, by itself or by the event's
  cutoff, is not reminded; the other occurrences still are.

## Attendance notifications

- **R88** [✅] Marking attendance notifies the member with
  `attendance.marked`, carrying the status.
- **R89** [✅] Re-marking the same record notifies again each time.
- **R90** [✅] Requesting leave notifies staff with an actionable
  `attendance.correction_requested` carrying the member and the reason.
- **R91** [✅] Approving or rejecting a leave request notifies the member
  with `attendance.correction_response`, carrying the outcome.
- **R92** [✅] Every hour the scheduler reminds staff of each occurrence
  that ended in the last seven days with no attendance recorded, with
  `attendance.pending_mark_reminder`, once per occurrence.
- **R93** [X] An occurrence with any attendance recorded, or that was
  cancelled, produces no pending-mark reminder.
- **R94** [✅] Once a day the scheduler warns a member whose current run
  of absences reaches three with `attendance.absence_streak_warning`.
- **R95** [✅] The warning is sent once per streak: a growing streak, or a
  warning since swept away, does not warn again.
- **R96** [✅] Any other mark ends a streak; a streak ended before the scan
  is not warned, and a new streak after it warns again.

## Account and user notifications

- **R97** [✅] A user awaiting approval notifies every admin with an
  actionable `user.registration_pending` keyed by the username: at
  registration when identity verification is off, otherwise only when the
  user submits for review.
- **R98** [✅] Approving a user notifies them with
  `account.registration_approved`.
- **R99** [✅] Blocking and unblocking a user notify them with
  `user.blocked` and `user.unblocked`.
- **R100** [✅] Adding or removing a role notifies the user with
  `user.role_changed`, carrying the old and new roles, what was added or
  removed, and who did it.
- **R101** [✅] Transferring super-admin notifies both users with
  `user.role_changed`.
- **R102** [✅] Soft-deleting a user notifies every other admin with
  `user.deleted`; the deleted user is not notified.
- **R103** [✅] Restoring a user notifies them with `user.restored`.
- **R104** [✅] A user changing their own password is notified with
  `account.password_changed_self`; an admin resetting it notifies them with
  `account.password_changed_by_admin`, naming the admin. A refused change
  notifies nobody.
- **R105** [✅] A forgotten-password reset notifies the user with
  `account.password_changed_self`.
- **R106** [✅] An admin changing a user's profile notifies the user with
  `profile.changed_by_admin`, listing the changed fields; a user editing
  their own profile, an admin change that changes nothing, and curating a
  coach's public listing are silent.

## Other notifications

- **R107** [✅] Renaming a venue that future events use notifies every
  admin with `venue.renamed`, carrying the old and new names and the
  affected events; a rename with no future events, or any other venue
  change, is silent.
- **R108** [✅] When video conversion finishes the uploader is notified
  with `media.processed`, or `media.failed` carrying a reason code; media
  with no uploader notifies nobody.
- **R109** [✅] A public inquiry notifies every admin with
  `inquiry.received`, whether or not an inquiry email address is set.
- **R110** [✅] When a programme's bound credit is released after its
  cutoff, each member is notified once with `credit.released`, carrying
  the credits.
- **R111** [✅] Publishing an evaluation notifies its subject with
  `evaluation.published`; nothing is sent before publication. Unpublishing
  notifies the subject with `evaluation.withdrawn`, and transferring
  notifies the receiving author with `evaluation.transferred`.

## Broadcasts

The broadcast surface is admin-only. To a recipient a broadcast is one more
notification in their feed, of type `broadcast.message`.

- **R112** [✅] An admin can send a broadcast with a payload and one
  audience selector; it fans out at send time into one notification per
  resolved recipient, carrying the payload as supplied and linked to the
  broadcast.
- **R113** [✅] The audience can be every active user; every active user
  with the admin role (super-admins included) or with the coach role; the
  explicit members of a group; or the enrolled members of an event.
- **R114** [✅] The audience can be an explicit list of usernames; names
  that match no user are dropped silently rather than failing the
  broadcast.
- **R115** [X] Names in an explicit list that belong to an inactive
  or deleted user are dropped the same way.
- **R116** [✅] The audience can be an event's staff, which today is
  every staff member (#414 narrows it).
- **R117** [X] A selector with no kind or an unknown kind → 422
  `INVALID_AUDIENCE_SELECTOR`.
- **R118** [X] A group selector naming a missing or deleted group,
  or any selector missing its required field → 422
  `INVALID_AUDIENCE_SELECTOR`.
- **R119** [✅] Each recipient sees the broadcast in their normal feed.
- **R120** [✅] An admin can list broadcasts newest first with their
  recipient counts.
- **R121** [✅] An admin can view a broadcast's read and unread counts,
  derived from its recipients' notifications: a recipient marking theirs
  read moves the count.
- **R122** [✅] An admin can list a broadcast's recipients with their read
  state, optionally only the read or only the unread.
- **R123** [✅] An admin can revoke a broadcast: its notifications are
  deleted from every feed and the broadcast is kept, marked revoked.
- **R124** [✅] Sending and revoking a broadcast each write an audit entry.
- **R125** [X] A non-admin cannot send a broadcast → 403.
- **R126** [X] A non-admin cannot list, view or revoke broadcasts
  → 403.
- **R127** [X] Revoking an unknown broadcast → 404 `BROADCAST_NOT_FOUND`.
- **R128** [X] Viewing an unknown broadcast or its recipients → 404
  `BROADCAST_NOT_FOUND`.
- **R129** [✅] A broadcast may also be emailed: each recipient with an
  address receives the email subject and a body written in markdown and
  rendered to HTML, whatever their preferences; recipients without an
  address are skipped; the audit entry records how many were sent, skipped
  and failed.
- **R130** [✅] A broadcast not marked for email sends no email.
- **R131** [X] A broadcast marked for email without a subject and body →
  422.
- **R132** [✅] An email that fails to send is counted and does not affect
  the in-app fan-out.

---

## Future work

Not decided; recorded so the rules above are read in their light.

- **Delivery beyond the feed.** Email through the notification service,
  honouring preferences by message class (#398, #399); push and SMS.
- **Narrower staff audiences** for event notices and the event-staff
  broadcast selector (#414).
- **A dedicated "you were assigned as coach" notice.** Coaches are user
  references now, but a coach change only tells the enrolled members.
- **Venue closure.** Soft-deleting a venue that future events use notifies
  nobody.
- **Group flows that do not exist:** invitations and their responses,
  self-leave, per-group roles, ownership transfer.
- **Enrollment flows that do not exist:** a waitlist, invitation deadline
  reminders, notice of an effective-start change on an enrollment.
- **Attendance check-in** windows and missed check-ins; no check-in flow
  exists.
- **Large broadcasts.** Fan-out is synchronous; a background path with an
  acknowledgement would suit very large audiences. One selector per
  broadcast; combining selectors is not supported.
- **Guardian forwarding** of a dependent's notifications, mentions and
  replies, and billing notices — none of the underlying features exist.
