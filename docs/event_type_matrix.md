# Event type × operation matrix

**Status: decided 2026-09-03 (#306), implemented with #384.** This is the
design the next restriction is added against. Each cell says whether a
verb is offered, and where the rule that governs it lives; the code that
enforces the matrix is `services/event_types.py`, the one place an event's
type is compared.

The principle behind the shape (`event_lifecycle_requirements.md` L9): each
type offers the verbs that make sense for it, and where a consequence
differs it differs by type, never by which verb was used.

| Operation | programme | camp | oneOff | Rule |
|---|---|---|---|---|
| `POST /events` | ✅ blocks on a programme clash | ✅ reports | ✅ reports | programme R30, R30c; one-off R20 |
| `PATCH /events/by_id/{id}` (metadata) | ✗ `INVALID_EVENT_TYPE` | ✅ | ✅ | programme R25; camp R2; one-off R2 |
| `PATCH …/correction` | ✅ identity, eligibility, presentation | ✗ | ✗ | programme R21–R22a, R25a; camp R3; one-off R15 |
| `PATCH …/future` (split) | ✅ scheduling and staffing | ✗ | ✗ | programme R23–R25a; camp R4; one-off R15 |
| `POST …/terminate` | ✅ | ✗ | ✗ | programme R1–R5 |
| `POST …/extend`, `…/extend-indefinitely` | ✅ | ✗ | ✗ | programme R6–R9 |
| `POST …/cancel`, `…/undo-cancel` | ✗ (terminate) | ✅ | ✗ 422 `INVALID_STATE` (drop) | camp R5–R5d; one-off R4 |
| `POST …/drop`, `…/reinstate` | ✗ | ✗ | ✅ | one-off R3–R8 |
| `POST …/reschedule` (series, in place) | ✗ `EVENT_TYPE_NOT_SUPPORTED` | ✅ before it starts | ✅ postpone-only, 30-min lead | programme R26; camp R2c; one-off R16a, R16b, R21b |
| `POST …/occurrences/{t}/reschedule` | ✅ | ✅ | ✅ | postpone-only and 30-minute lead for every type: programme R19a, camp R81a/R81b, one-off R16a/R16b |
| `POST …/occurrences/{t}/cancel`, `…/undo-cancel` | ✅ | ✅ | ✅ (= drop) | 30-minute lead for every type: programme R17, camp R82a, one-off R5, R17 |
| `POST …/enrollments/assign-trial` | ✅ | ✗ | ✗ | programme R32a; camp R47; enrollment R31a |
| `POST /events/check-conflict`, `…/check-user-conflicts` | ✅ | ✅ | ✅ | camp R31 |
| `GET …/schedules` | ✅ one or more | ✅ exactly one | ✅ exactly one | `event_schedule_model.md` |

## Which field is reached through which path

A field lives either on the **event** — one value for its whole life,
changed by correction (programme) or by the metadata update (camp,
one-off) — or on a **schedule**, changed by a split (programme) or by the
in-place reschedule (camp, one-off).

| Field | Lives on | programme | camp / oneOff |
|---|---|---|---|
| `title`, `description`, `visibility` | event | correction | update |
| `gender`, `dobOnOrAfterUtc`, `dobOnOrBeforeUtc` | event | correction | update |
| `isFeatured`, `galleryUris` | event | correction | update |
| `organizerName`, `coachNames` | schedule | split | update (camp R2: staffing does not move an occurrence) |
| `venueId`, `startTimeUtc`, `endTimeUtc`, `rrule`, `sessions` | schedule | split | reschedule |
| the cutoff (`untilTimeUtc`) | schedule | terminate / extend | cancel / undo-cancel |

Every settable field is reachable for every type through exactly one path,
so no two paths can disagree about one value (programme R25a).

## Decisions #306 asked for

- **A programme is never PATCHed or rescheduled in place.** Deliberate
  (programme R25, R26): a change to its times is a change from a date,
  which is a split, so the occurrences that already ran keep the terms
  they ran under.
- **A camp or one-off has no correction.** Their metadata update already
  applies across the whole event, and they have one schedule, so the
  correction-versus-split distinction has nothing to distinguish.
- **Trial assignment is programme-only by intent.** A trial is a way of
  attending part of a recurring series without joining it (programme
  R32a); a camp is joined whole and a one-off has one occasion.
- **Conflict checks accept every type.** The camp-only restriction was v1
  of #16 and is lifted (camp R31); what differs by pairing is whether a
  finding blocks or is reported.
- **The error code for an unsupported verb** is `INVALID_EVENT_TYPE`
  (400) everywhere except the series reschedule, which keeps its historical
  `EVENT_TYPE_NOT_SUPPORTED`. The camp, one-off and programme documents
  name `EVENT_TYPE_NOT_SUPPORTED` for the correction and split gates; the
  code the server has always sent there is `INVALID_EVENT_TYPE`, and the
  tests that pin it predate those documents. The documents are corrected
  rather than the surface changed.
