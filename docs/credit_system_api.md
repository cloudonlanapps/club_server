# Credit System — API Design

Companion to [`docs/credit_system_requirements.md`](credit_system_requirements.md),
which is the authority. That document says *what* the system must do
and deliberately names no endpoints; this one records the HTTP surface
chosen to satisfy it, so that the mapping is written down rather than
rediscovered. Where the two disagree, the requirements win and this
document is wrong.

Tracked by #294, and implemented as
described here, with the follow-ups #445–#449 and #451.

## Deployment gating

The subsystem is optional per deployment (R92–R98). It is controlled by
a single setting, `CREDIT_SYSTEM_ENABLED`, supplied through the deploy
conf's `EXTRA_ENV` passthrough alongside the rest of the product
identity. It defaults to **false**, so an existing deployment that says
nothing keeps today's behaviour exactly.

Every credit endpoint is registered on every deployment. When the
setting is false they all answer the same way (R94):

```
503  {"detail": {"code": "CREDIT_SYSTEM_DISABLED",
                 "message": "The credit system is not enabled on this deployment"}}
```

This follows the precedent already set by media encryption, which
returns 503 `ENCRYPTION_NOT_CONFIGURED` when its key is absent. A
single FastAPI dependency, `require_credit_system_enabled`, is attached
to each credit router, so the refusal happens at the edge before any
handler body runs and no credit table is touched (R94a).

The alternative — leaving the routers unregistered — was considered and
rejected. It would have made the published schema depend on deployment
configuration, giving the SDK two contracts for one server, and pushed
a registration-time condition into main.py that tests could only reach
by rebuilding the app.

Also when the setting is false:

- The enrollment and attendance integration points short-circuit before
  touching any credit table (R95).
- A `creditDisposition` sent to removal or withdrawal-approval is
  rejected, not ignored (R98).
- The bulk attendance response keeps its shape, with an always-empty
  `refused` list (R97). No response body varies with configuration.

## Shape and conventions

The subsystem follows the conventions already in the server:

- Request and response bodies are camelCase, via `CamelCaseModel`.
  Undeclared fields are rejected (`extra="forbid"`).
- Timestamps are epoch milliseconds UTC, named with a `Utc` suffix.
- Listings return `PaginatedResponse[T]` — `items`, `total`, `offset`,
  `limit` — with `offset` and `limit` as query parameters.
- Member-scoped reads take the username in the **path** and are
  guarded by `require_self_or_staff`, matching `/myevents` and
  `/mygroups`. A member reads their own credit through the same
  endpoint an admin uses to read it on their behalf.
- Errors are `{"detail": {"code": ..., "message": ...}}` with the code
  drawn from the table at the end of this document.

Three routers:

| Router | Prefix | Tag | Audience |
|---|---|---|---|
| `credits` | `/credits` | `Credits` | admin, and staff for reads |
| `mycredits` | `/mycredits` | `My Credits` | the member, or staff on their behalf |
| (existing `events`) | `/events` | `Events` | one added roster endpoint |

`credits` will exceed the 400-line limit once handlers carry their
error mapping, so it is split at the outset: `credits.py` for the
account lifecycle and `credits_query.py` for the listing and ledger
reads.

## Resources

### CreditAccount

```
accountId       string(8)   letters and digits, server-generated, unique
membername      string      owner
kind            string      "event" | "general"
eventId         int | null  chain-root event id; null for general accounts
isTrial         bool
balance         int         derived from the ledger, never stored
validFromUtc    int
validUntilUtc   int
usable          bool        within validity window and balance > 0
state           string      "usable" | "empty" | "expired" | "closed"
openedAtUtc     int
openedBy        string      admin who opened it
closedAtUtc     int | null  set when a transfer closes the account
```

`state` is derived and ordered: `closed` wins, then `expired`, then
`empty`, then `usable`.

### CreditEntry

```
id                  int
accountId           string(8)
membername          string
amount              int       signed; negative is a deduction
entryType           string    see below
eventId             int | null
occurrenceTimeUtc   int | null
reason              string
actorUsername       string
createdAtUtc        int
offsetsEntryId      int | null  the entry this one reverses
balanceAfter        int       the account's balance after this entry
totalAfter          int       the member's credit, all accounts, after it
```

`balanceAfter` and `totalAfter` are running sums in ledger order,
`(createdAtUtc, id)`: `balanceAfter` over the entry's account, `totalAfter`
over every account the member holds. `transferOut` and `transferIn` move
credit between two of the member's own accounts, so they leave
`totalAfter` unchanged; a departure's `penalty` is what lowers it. Both
figures belong to the entry, computed over the whole ledger, so they are
the same whichever filter, order or page the entry appears on. A
statement read newest first therefore still shows what was left after
each line, without reading back to the first entry.

`entryType` is one of `grant`, `grantReversal`, `sessionDeduction`,
`sessionRefund`, `penalty`, `transferOut`, `transferIn`,
`validityExtended`. The last carries `amount` 0 — a validity change is
not a movement of credit, but it belongs on the statement.

### MemberCreditStatus

```
membername          string
usableCredits       int
boundCredits        int                credit a departure must resolve
payingAccountId     string(8) | null   which account would pay next
blocked             bool               no usable credit
nextExpiryUtc       int | null
```

`usableCredits` answers "can this member be marked" (R90). `boundCredits`
answers "does removing them, or approving their withdrawal, need a
`creditDisposition`" (R71, R73): the balance of every open account bound
to the programme, expired ones included (R72a), and no general account.
The two diverge both ways. An expired package is usable 0 and bound 5, so
the member is blocked yet a removal needs a disposition; general credit
alone is usable 3 and bound 0, so the member can be marked and a removal
needs none. A client asks for a disposition exactly when `boundCredits > 0`.

## Endpoints

### Account lifecycle — `/credits`

**`POST /credits/accounts`** — open an account. Admin.
Body: `membername`, `credits`, `validFromUtc`, `validUntilUtc`,
`eventId?`, `isTrial=false`, `reason`. Returns 201 `CreditAccount`.
Any event in a programme's chain may be named; the server stores the
chain root. The member may be any existing user whatever their account
status (`registered`, `pending`, `active`, `blocked`, `left`); an unknown
username is 404 `USER_NOT_FOUND`. Rules R18–R24, R18a, R6, R8.

**`GET /credits/accounts`** — search. Admin or coach.
Query: `membername?`, `eventId?`, `kind?`, `state?`, `isTrial?`,
`expiringBeforeUtc?`, `offset`, `limit`. Returns
`PaginatedResponse[CreditAccount]`. Rules R89, R90.

**`GET /credits/accounts/{accountId}`** — look up by code alone,
without knowing the owner. Admin. Returns `CreditAccount`. Rule R91.

**`POST /credits/accounts/{accountId}/extend`** — move the validity
end date. Admin. Body: `validUntilUtc`, `reason`. Returns
`CreditAccount`. Rule R62.

**`POST /credits/accounts/{accountId}/reverse`** — undo a mistaken
grant. Admin. Body: `credits?` (defaults to the whole remaining
balance), `reason`. Returns `CreditAccount`. Rules R58, R59.

**`POST /credits/accounts/{accountId}/transfer`** — close the account
and move what survives a penalty into a new general account. Admin.
Body: `penalty`, `validFromUtc`, `validUntilUtc`, `reason`. Returns
`{"source": CreditAccount, "created": CreditAccount | null}`.
There is no destination to choose: the operation creates the account
it transfers into, because accounts are never topped up. `created` is
null when the penalty consumes the balance. Rules R63, R65–R69, R75.

This one endpoint serves three stories — expiry disposition,
voluntary drop-out, and admin removal. They differ only in the penalty.

### Ledger — `/credits`

**`GET /credits/entries`** — the ledger across accounts. Admin or
coach. Query: `membername?`, `accountId?`, `eventId?`, `entryType?`,
`occurrenceTimeUtc?`, `fromTs?`, `toTs?`, `order`, `offset`, `limit`.
`order` is `asc` (oldest first, the default) or `desc` (newest first).
Returns `PaginatedResponse[CreditEntry]`. Rules R79–R81, R87.

### Member view — `/mycredits`

**`GET /mycredits/by_id/{username}`** — the member's accounts.
Self or staff. Query: `state?`, `includeClosed?`. Returns
`list[CreditAccount]`. Rules R86, R88.

**`GET /mycredits/by_id/{username}/accounts/{accountId}`** — one of
them. Self or staff. Returns `CreditAccount`.

**`GET /mycredits/by_id/{username}/entries`** — the member's
statement. Self or staff. Query: `accountId?`, `eventId?`, `fromTs?`,
`toTs?`, `order` (`asc` default, or `desc`), `offset`, `limit`. Returns
`PaginatedResponse[CreditEntry]`, paged in SQL. This is the endpoint
that serves R87: each line carries `balanceAfter` and `totalAfter`.

### Programme roster — `/events`

**`GET /events/by_id/{event_id}/credits`** — who is on this programme
and can be charged: members who accepted, were assigned or trial-assigned,
and those with a pending withdrawal, who are still enrolled and still
charged (R74). Invitations and pending requests are not listed. Admin or
coach. Query: `state?`,
`expiringBeforeUtc?`, `offset`, `limit`. Returns
`PaginatedResponse[MemberCreditStatus]`. Rule R90.

This is the only endpoint where an event id belongs in the path,
because it is the only collection an event owns.

## No endpoints

Deduction and refund have no HTTP surface. They are driven from
`AttendanceService` through a single reconciliation call (R44a), never
by a client. There is likewise no PUT, PATCH, or DELETE anywhere in
the subsystem: an account's balance is the sum of an append-only
ledger, so there is nothing to edit and nothing to delete.

## Changes to existing endpoints

| Endpoint | Change | Rules |
|---|---|---|
| `POST /events/by_id/{id}/enrollments/assign` | new 422 `INSUFFICIENT_CREDIT` | R35–R38 |
| `.../enrollments/assign-trial` | same | R35–R38 |
| `.../enrollments/approve` | same | R35–R38 |
| `POST /myevents/by_id/{username}/{id}/request` | same | R35–R38 |
| `POST /myevents/by_id/{username}/{id}/accept` | same | R35–R38 |
| `POST .../occurrences/{t}/attendance` | **breaking**: 204 → 200 with a per-member report | R41b, R41c |
| `POST .../enrollments/remove` | **breaking**: body gains a required `creditDisposition` when the member holds a bound balance | R71 |
| `POST .../enrollments/approve-withdraw` | same | R73 |

Bulk attendance marking currently returns 204 and aborts the whole
batch on any failure, because the session dependency rolls back on any
exception. Since a lapsed package must not fail nineteen other members'
attendance (R41b), the response becomes:

```
{
  "marked":  [{"membername": ..., "status": ...}],
  "refused": [{"membername": ..., "code": "INSUFFICIENT_CREDIT",
               "message": ...}],
  "trialEnded": [{"membername": ...}]
}
```

`trialEnded` lists the members whose trial this request ended: their
mark spent the last of their trial credit, which removed them from the
programme (R52). Each of them is also in `marked`. The removal sets the
enrollment's `withdrawnAtUtc` and gives it `withdrawalReason`
`trialCreditExhausted`; the member receives an `enrollment.trial_ended`
notification carrying `eventId`, `eventTitle`, `eventType`,
`enrolledAtUtc` and `withdrawnAtUtc`; and the audit log gets an
`enrollment_removed` row with actor `system`, whose `details` carry
`reason`, `triggeredBy` (the marking coach) and `occurrenceTimeUtc`.
Where the deployment does not run on credits the list is always empty,
like `refused` (R97).

`creditDisposition` on removal and withdrawal-approval carries
`penalty`, `validFromUtc`, `validUntilUtc`, `reason` — the same fields
the transfer endpoint takes, because it is the same operation.

The penalty is one figure for the departure, not one per account. Where a
member holds two bound accounts on the same programme it is taken from them
oldest-first until it is used up, so buying two packages instead of one does
not cost the member twice the penalty. Expired bound accounts are included:
expiry never destroys credit, so a lapsed balance is exactly the kind a
removal would otherwise strand.

## Error codes

| Code | Status | Meaning |
|---|---|---|
| `CREDIT_ACCOUNT_NOT_FOUND` | 404 | no account with that id |
| `CREDIT_NOT_APPLICABLE` | 422 | the event is a camp or one-off (R6) |
| `INVALID_CREDIT_AMOUNT` | 422 | not a positive whole number (R19) |
| `INVALID_VALIDITY_WINDOW` | 422 | end not after start, or window wholly past (R23) |
| `INSUFFICIENT_CREDIT` | 422 | no usable credit for this programme (R35, R41) |
| `INSUFFICIENT_BALANCE` | 422 | reversal exceeds what remains unspent (R59) |
| `ACCOUNT_CLOSED` | 422 | operation on an already-closed account |
| `CREDIT_DISPOSITION_REQUIRED` | 422 | removal or withdrawal with a bound balance and no disposition (R71) |
| `CREDIT_DISPOSITION_NOT_APPLICABLE` | 422 | a disposition sent where credit is off, or to a non-programme (R98) |
| `REASON_REQUIRED` | 422 | a movement with no stated reason (R60) |
| `INSUFFICIENT_PERMISSION` | 403 | existing code, reused |
| `CREDIT_SYSTEM_DISABLED` | 503 | the deployment does not run on credits (R94) |

## Storage

Three new tables, all additive. Every foreign key points outward at
`events` and `users`; no existing table is altered.

- **`credit_accounts`** — one row per account. Carries the 8-character
  code, the owner, the chain-root event id or null, the trial flag, the
  validity window, and the close timestamp.
- **`credit_entries`** — the append-only ledger. Balance is
  `SUM(amount)` per account, so there is no stored balance to drift.
- **`credit_session_charges`** — one row per
  `(event_id, occurrence_time_utc, membername)`, mirroring the unique
  constraint on `attendance_records` exactly. Entries hang off it.

The third table is what makes R83 structural rather than a rule to
remember: a charge may split across several accounts (R29), so
uniqueness cannot live on the entries. It also makes the exact-source
refund in R49 a lookup rather than a reconstruction.
