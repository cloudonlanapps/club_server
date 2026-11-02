# Notification delivery — design

Status: **agreed direction** (2026-11-02). Nothing here is built.

Order of work: `notify_kit` (below) is built and tested first, in its own
repository with its own requirements document. Its API may turn out
different from what this document sketches, so **club_server's code is not
changed until notify_kit is complete**. Then club_server#9, #10 and #14 are
rewritten from this document and its rules join
[`notifications_requirements.md`](notifications_requirements.md) as `[GAP]`
lines citing those issues.

## Why

Today every generated notification lands in the in-app feed and nowhere
else. Email is a separate path, called directly from five places (forgot
password, admin password reset, account approved, inquiry, broadcast), and
it ignores the per-user preferences, which are stored but gate nothing
(requirements R24). The email providers have a sending limit, so email must
be spent where it matters, and the club must be able to decide that without
a deploy.

The design keeps one rule from today: **who receives a notification is
decided in code** (the member, the event's coaches, all admins, …). What is
new is that **whether each channel fires is decided by data**, so adding a
notification type, or changing what may be emailed, needs no new code.

## Terms

- **Type** — a dotted notification type, as today (`attendance.marked`).
- **Category** — a group of types a member understands ("Sessions",
  "Attendance and leave", "Account"); the unit a member and sudo switch on
  and off. Categories replace the three fixed classes club_server#9
  proposed (transactional / mandatory broadcast / informational): what
  those classes decided is now the per-type *mandatory* flag.
- **Channel** — a way to deliver: `app` (the in-app feed), `email`, `sms`,
  `whatsapp`, `push`. Each is a separate channel.
- **Mandatory** — the member cannot turn the channel off for this type.
  Each type has a default in code; sudo can override it.
- **Expected always on** — a flag in code on types whose email a club almost
  certainly wants (forgot password, account approved, sent back, password
  reset by admin). Sudo can still block them, but is warned first.
- **Delivery** — one attempt to send one notification to one person on one
  channel other than `app`.

## The four levels

A notification reaches a recipient on a channel only if all four allow it.

```
0. Supported          deploy    which channels this deployment has a provider for
                                (published in /v1/capabilities)
1. Channel switch     sudo      app | email | sms | whatsapp | push   on / off, club-wide
2. Category gate      sudo      category x channel: allowed / blocked
                                + per-type override (allowed / blocked, mandatory)
3. User preference    member    category x channel: on / off
                                (skipped when the type is mandatory)
4. Reachability       -         email: has an address · sms / whatsapp: has a phone
                                push: a registered device that allows it
```

**A way out of the app is always kept for forgot password.** Sudo cannot
block the last non-app channel the forgot-password type can use: moving it
from email to SMS is allowed, switching off the only one is greyed out with
the reason.

Recipient filtering stays before all four: a user who has left gets nothing
(R7a), a blocked user only the notices about their account (R7b).

### Defaults

| Channel | Member's default | Member can change it in the app |
|---|---|---|
| app | on | no — not shown; stored like the others so it can be later |
| email | off | yes |
| sms | off | yes |
| whatsapp | off | yes |
| push | off (asks the device on first use) | yes |

A mandatory type's email defaults on. `attendance.marked` is a type like any
other and can use every channel, but its email, SMS and WhatsApp start
blocked at sudo's gate (it notifies again on every re-mark, R89). A type that carries a pending action
(join request, invitation, enrollment request, leave request, user
approval) always writes its feed row: the pending-actions list is built from
it.

### What the member sees

The preference screen is built from the channels the deployment supports
(level 0). A channel sudo has switched off (level 1), or a category sudo
blocks on a channel (level 2), is shown **greyed out with its reason**, not
removed, so a member can never turn on something the club has blocked and
can see that it is the club's choice. A mandatory type's category shows its
switch on and locked, with "Required by the club". The in-app channel is not
shown.

## Data

Code owns the catalogue; the database holds the decisions.

| Table | Rows | Changed by | Holds |
|---|---|---|---|
| `notification_categories` | one per category | migration | key, label, order |
| `notification_types` | one per type | migration (seeded from code) | category; channels the type *can* use; mandatory default per channel; expected always on; whether it has a feed row |
| `notification_channels` | one per channel | sudo | enabled; provider settings live in #7's encrypted row, not here |
| `notification_category_gates` | category x channel | sudo | allowed |
| `notification_type_overrides` | type x channel, sparse | sudo | allowed and/or mandatory, overriding the category and the code default |
| `notification_user_prefs` | user x category x channel, sparse | member | on / off; a missing row means the default |
| `notification_deliveries` | one per send | dispatcher | notification (if any), type, recipient, channel, address used, provider, status, error, times |
| `push_devices` | one per device | member's app | user, platform (web / android), token, last seen |

`notifications` (the feed) is unchanged. Its `channel` column, always `app`
today, becomes redundant: deliveries record the other channels.

`notification_prefs` (one email / push / SMS switch per user) is dropped. It
has never gated anything, and its defaults (email on) contradict the new ones.

### The decision, for one recipient, type and channel

```
channel switched on                                        (level 1)
AND type can use the channel                               (catalogue)
AND allowed  := type override, else category gate          (level 2)
AND ( mandatory := type override, else code default
      OR user pref, else channel default )                 (level 3)
AND reachable                                              (level 4)
```

## Flow

1. A domain action calls the dispatcher with the type, the facts and the
   audience its code chose (unchanged).
2. Inside the action's transaction: recipient filtering (R5–R7b), then one
   feed row per recipient whose `app` channel passes (R8 unchanged).
3. **After commit**, for each other channel that passes: a delivery row and
   the send, from a background worker, never inside the request — a rolled
   back action must not email anyone (#10).
4. The worker hands the recipients, the template and the facts to
   **notify_kit** (below), which renders and sends; the outcome goes into the
   deliveries table. A failure is recorded and never affects the feed.

A type with no feed row (forgot password) goes through steps 3–4 only, so it
is gated and counted like any other email.

**Undoable types are held.** The email (and SMS, WhatsApp, push) of
`event.cancelled` and `occurrence.cancelled` waits for the undo window and
is dropped if the cancellation is undone in it (R47). The window, a constant
today (`UNDO_GRACE_WINDOW_MS`, 5 minutes), becomes the system preference
`notification_undo_window_minutes`, default 5, which sudo can change; the
feed's undo and the held send read the same value.

## notify_kit — the sending package

Sending lives in a standalone package, `cloudonlanapps/notify_kit`, in its
own public repository, developed and tested on its own and taken by
club_server as a git dependency pinned by SHA. It knows about messages,
never about clubs: no users, events, categories or gates.

```
notify_kit
├── Recipient   an address per channel kind (email, phone, push token),
│               optional name and language
├── providers   register_provider(kind, name, factory) with credentials
│               handed in: email (resend, brevo, console), sms, whatsapp,
│               push (web push, firebase), added the same way
├── templates   CRUD behind a TemplateStore interface (an in-memory store
│               ships for tests); email = subject + markdown body with
│               escaped placeholders, inside a layout passed in as data;
│               sms / whatsapp = the provider's approved template id + the
│               order of its blanks
├── send(recipients, template_key, facts) -> one result per recipient
│               (sent / failed / skipped + reason)
└── CLI + fake providers   test sends; tests need no network
```

club_server keeps the catalogue, the gates, the audiences, address lookup,
the deliveries table, encrypted provider keys (#7), the sudo API and a
database-backed `TemplateStore`. Today's `club_server/mailer/` is notify_kit's
start (senders, factory, CLI); its club settings (club name,
`server_base_url`, logo path) and club wording (account approved, forgot
password) move into the server as data. Each club registers its own SMS
sender / WhatsApp business account and templates; the server holds them,
notify_kit is handed them.

## Sudo's screens (club_client, Club Details)

- **Channels** — on / off per channel, the provider settings for email
  (#7: provider and sender per purpose, write-only key, "Send test email").
- **Notifications** — the category x channel grid, each category expandable
  to its types for overrides; types flagged *expected always on* are marked,
  and blocking one asks to confirm and says what members lose.
- **Usage** — emails (and messages) sent today and this month, from
  `notification_deliveries`, beside the plan's limit.

Turning email off for the Broadcast category, or email as a whole, greys
out a broadcast's "also email" with its reason; the server refuses a send
that asks for email anyway.

## Capabilities

`/v1/capabilities` (public, deploy configuration) gains the channels this
deployment supports; the app builds the preference screen from it. Sudo's
runtime state — which supported channels are on, which categories are
allowed and which are mandatory, per channel — comes with the member's
preferences from a signed-in endpoint, and greys items out rather than
removing them.

## How the existing issues fit

| Issue | Today | With this design |
|---|---|---|
| club_server#9 | three fixed classes | replaced: categories, the catalogue, mandatory default + override, expected always on |
| club_server#10 | email through the notification service | kept and extended: deliveries table (its open question settled), after-commit worker, every channel |
| club_server#14 | document the three classes | documents the four levels instead |
| club_server#7 | email provider settings in an encrypted DB row, sudo API | unchanged; it is level 1's email provider. Five send sites now, not four |
| club_server#6 | sender address follows provider | fixed by #7, as #7 says |
| club_server#4, #5 | email logo from public media | unchanged; same settings pattern as #7 |
| club_server#8 | `server_base_url` only for the logo | unchanged; settled by #4 |
| club_server#12 | staff notices to the event's organiser + coaches | unchanged (audience is code) |
| club_server#144 | event notices to members in the event; reminder carries leave | unchanged (audience is code) |
| club_server#143 | attendance and leave notices carry the event title | unchanged; it fixes a breach of R3 |
| club_client#417, #418 | notification wording | unchanged |

## Requirements this changes

When agreed, `notifications_requirements.md` gets `[GAP]` rules for:
R21–R24 (preferences), the empty *Message classes* section (becomes the four
levels), the Vocabulary's *channel* and *notification* (a type may have no
feed row), R38 (reconsider sends the member a "sent back" notice), R129
(broadcast email passes levels 1–2). Its references to the archived repo's
issue numbers (#398, #399, #414, #491) become #9, #10, #12, #14.

## Not in the first cut

- **Push**: device registration, Web Push for browsers, Firebase for the
  Android app, the browser permission prompt, and the page's
  Content-Security-Policy allowing the push service (club_client #315).
- **SMS and WhatsApp**: notify_kit providers once a club registers a sender
  and templates (India: DLT for SMS, Meta-approved templates for WhatsApp),
  plus verified phone numbers and recorded opt-in.
- **Automatic quota cut-off**: sudo turns email off by hand; the usage count
  tells them when.

## Decisions (2026-11-02)

1. SMS and WhatsApp are separate channels.
2. Forgot password goes through the dispatcher as a type with no feed row:
   mandatory, expected always on, gated and counted like any email. Sudo
   cannot block its last non-app channel. (#10 had kept it a direct mailer
   call.)
3. `/v1/capabilities` publishes the supported channels; sudo's runtime state
   comes from a signed-in endpoint; blocked items are greyed out, not removed.
4. Undoable types' sends wait for the undo window, a sudo-adjustable system
   preference (default 5 minutes) shared with the feed's undo.
5. A broadcast's "also email" acts as mandatory for that send: levels 1–2
   apply, level 3 does not (R129). When sudo blocks it, the tick is greyed
   out with the reason and the server refuses it.
6. Categories are the groups switched on and off in the UI; they replace
   #9's three classes.
7. `attendance.marked` is an ordinary type; its email, SMS and WhatsApp start
   blocked at sudo's gate.
8. Sending lives in `cloudonlanapps/notify_kit`, a new public repository,
   built and tested on its own before club_server moves onto it.
9. An event's staff notices (leave and join requests, enrollment changes,
   the not-marked reminder) go to its organiser, its assigned coaches **and
   every admin**: coaches often do not open the app, and admins manage it
   for them. Only coaches who do not run the event stop receiving them
   (club_server#12).
