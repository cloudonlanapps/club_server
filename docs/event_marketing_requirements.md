# Event Marketing — Requirements

This document is the testable specification for what an event says about
itself to the public, beyond its timetable. It is written as plain-English
use cases and names no HTTP endpoints or source files.

There are two blocks. The **basic block** is four presentation fields on
the event itself: every club's public site puts them on an event card, so
they are core and always available. The **extended block** is the
commercial detail one club wants and another does not (fees, deadlines,
offers…); it is a deployment-gated module, off by default, whose routes
answer 503 while off so the published API does not vary.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.

## Basic block (#409)

- **R1** [✅] An event carries `shortDescription`, `stamp`, `highlights`
  and `includes`, supplied on creation and returned on every event read,
  single or listed.
- **R2** [✅] Each of the four is optional and reads back as null when
  never set; a client falls back to its own presentation (the first words
  of the description, no badge).
- **R3** [✅] They change through the same paths as the event's other
  presentation fields: the metadata update for a camp or one-off, the
  correction for a programme. Sending null clears a field; omitting it
  leaves it alone.
- **R4** [X] Malformed values are rejected → 422: a list field that is
  not a list of strings, a short description over 300 characters, a stamp
  over 60, more than 20 bullets.

## Extended block — the Event Marketing module (#410)

A deployment-gated module in the manner of credit and evaluations, off by
default. Holds the commercial detail one club wants and another does not:
fees, deadlines, offers, facilities, a per-event contact, and free-text
overrides the client uses instead of deriving from the timetable.

- **R5** [X] While the module is off every marketing route answers 503
  `EVENT_MARKETING_DISABLED`, and the capabilities document says so; the
  routes stay registered so the published API does not vary.
- **R6** [✅] An admin can read, replace whole and remove an event's
  extended block. Replace is a whole-row write: fields not sent are
  cleared. Every write and removal is audited.
- **R7** [✅] The event's organizer may write and remove like an admin;
  a coach may read; a member may not read; anonymous → 401.
- **R8** [X] Malformed values are rejected → 422 and nothing is stored:
  a negative fee, a fee-structure, package, offer, membership or facility
  entry missing its required parts, an unknown field. `currency` is not a
  field of the request: it is stored as `INR` and never exposed.
- **R9** [✅] `fee` and `feeStructure` are stored as sent, both allowed
  at once. Precedence is the client's: it shows the structure when both
  are set. The server does not reconcile or refuse.
- **R10** [✅] Anyone can read the extended block of a public, live event
  by the event's public id. A private or deleted event, an event without
  a block, or an unknown id → 404. The public projection carries the
  public id, never the integer id or the currency.
- **R11** [✅] Anyone can read the extended blocks of up to 50 public
  events at once by public id, for listing cards; unknown and private ids
  are silently absent. More than 50 → 422.
- **R12** [✅] The block belongs to its event: a soft-deleted event's
  block is unreadable publicly, and hard-deleting the event removes it.
- **R13** [✅] The block carries its own `version`, `updatedAt` and
  `updatedBy`, returned on every staff read and write and never on the
  public projection (#13). An event with no block counts as version 1
  with no author, so the first replace writes the block at 2, and every
  replace after bumps it and records who made it. The event's own version
  is not touched by a replace or a removal, and does not protect the
  block.
- **R13a** [X] Replace and removal **require** the block version the
  client last saw → 422 when missing. A version the block has moved past
  → 409 `STALE_VERSION`, whose body carries the current `version`,
  `updatedAt` and `updatedBy`. Nothing is written. Removing a block that
  does not exist is still not an error at version 1.

  Without it, two editors working on the same event's page each wiped
  out the other's fields: replace is a whole-row write.

## See also

- [`camps_requirements.md`](camps_requirements.md), [`programme_requirements.md`](programme_requirements.md),
  [`oneoff_requirements.md`](oneoff_requirements.md) — the events these
  fields decorate.
