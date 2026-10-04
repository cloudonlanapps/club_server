# Venues — Requirements

This document is the testable specification for venues, written as
plain-English use cases. Like the event documents it avoids naming HTTP
endpoints or source files, so the API surface can evolve without
invalidating the requirements.

A **venue** is a place an event happens at. It belongs to event
management: every event names exactly one, an occurrence may override it
for a single session, and it is the resource the conflict rules are
mostly about (`programme_requirements.md` R30,
`camps_requirements.md` R27–R39).

Venues are mostly CRUD with metadata. What makes them worth specifying is
the deletion rules — a venue that events point at cannot simply go away —
and the two flags that carry meaning beyond storage.

**Status:** written from the implementation, which was built before any
requirement document existed. Rules marked [✅] and [X] were verified
against the code and its tests; [GAP] marks something the code does not
do.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.

## Vocabulary

- **Referenced** — an event names this venue, either through the event's
  own venue or through an occurrence override that moves one session to
  it.
- **Active event** — an event that has not been soft-deleted. Whether it
  has ended does not matter unless a rule says so.
- **Default venue** — the venue offered when none is chosen. At most one
  exists.

---

## Shape

- **R1** [✅] A venue has a name, and optionally an address, a
  description and a map link.
- **R2** [✅] A venue carries two flags: **default** and **featured**.
- **R3** [X] At most one venue is the default. Creating or updating a
  venue as default while another holds the flag is rejected on create
  (→ 409 `DEFAULT_VENUE_EXISTS`) and transfers the flag on update — the
  previous holder loses it in the same operation.
- **R4** Featured is a presentation flag with no scheduling meaning. It
  says a venue is worth showing, nothing more.

## Authorization

- **R5** [✅] Creating, updating, soft-deleting and restoring a venue
  require the `admin` role.
- **R6** [X] Hard-deleting a venue requires super-admin.
- **R7** Venues are **read by members, changed by admins, and published
  to the website through a separate public surface**. The member reads
  under the ordinary venue endpoints require a login; the website never
  calls them, so what it can see is an explicit allow-list (R22–R25)
  rather than whatever happens to be readable. #387 had opened the member
  listing to anonymous callers; #307 reversed that.
- **R8** [X] Reading a single venue by its integer id requires an
  authenticated user → 401 otherwise. Any role may read.
- **R9** [X] Listing venues requires an authenticated user → 401
  otherwise. Any role may list, and every role sees the same live set.
- **R10** [X] Listing **deleted** venues stays admin-only. A soft-deleted
  venue is an administrative state, not a place anyone can go to.
- **R11** [X] Every mutation rejects an anonymous or non-admin caller
  → 401 / 403.

## Public surface

The website reads venues only here. Venues are addressed by an opaque
**public id**, an HMAC of the integer id keyed on the deployment secret,
so anonymous callers cannot walk ids; the integer id never appears.

- **R22** [✅] Anyone, without authentication, can list the live venues:
  name, address, description, map link, default and featured flags, the
  primary-venue badge, the public id and the image.
- **R23** [✅] Anyone can read one live venue by its public id. An
  unknown public id, or an integer id, is 404.
- **R24** [X] A soft-deleted venue is absent from the public list and 404
  by public id.
- **R25** [✅] The public image is the newest `venue_image` link whose
  media is anonymously viewable. A private current image yields no image;
  an older public one is never resurfaced.

## Deletion

The rules that make a venue more than a row.

- **R12** [X] A venue cannot be soft-deleted while any **active event**
  references it → 422 `VENUE_HAS_EVENTS`, naming the count. Both routes
  count: the event's own venue, and an occurrence override that moves a
  single session there.
- **R13** [X] A venue cannot be hard-deleted until it has been
  soft-deleted first → 422 `HARD_DELETE_NEEDS_SOFT_DELETE` (#526).
- **R14** [X] A venue cannot be hard-deleted while **any** event
  references it, soft-deleted and ended events included → 422
  `VENUE_HAS_EVENTS`.

  R12 asks whether the venue is in use; R14 asks whether it was ever
  used. A past event still says where it happened, and hard-delete would make
  that unanswerable.
- **R15** [✅] A soft-deleted venue can be restored, returning it to the
  active list.
- **R15a** [X] Restoring a venue that is not deleted → 422
  `NOTHING_TO_RESTORE`, and the venue is unchanged (#526).
- **R16** [✅] Soft-deleted venues are listable separately, so an admin
  can find one to restore.
- **R17** [✅] A soft-deleted venue is not offered for new events and
  does not appear in the ordinary listing.

## Renaming

- **R18** [✅] Renaming a venue notifies admins when the venue has
  **future events** at it, naming the old name, the new name and the
  affected events.

  A venue's name appears in what members have already been told. Changing
  it silently would leave an event whose stated location no longer exists
  by that name. Past events are not counted — nobody is going to them.
- **R19** Renaming is a correction, not a move. It does not change where
  anything happens, and no event is rescheduled by it. Moving an event to
  a different place is an event operation, not a venue one.

## Conflicts

- **R20** Venue is the resource the venue gate compares
  (`programme_requirements.md` R31b). A venue owns no conflict rules of
  its own: whether two events may share it, and whether that blocks or is
  merely reported, is decided by the event types involved (programme
  R30, R30c).

## Audit

- **R21** [GAP] Venue creation, update, deletion, restore and hard-delete
  each write an audit row recording the actor and what changed. Today the
  update path computes a change set for its response but no venue
  operation writes to the audit log.

---

## See also

- [`programme_requirements.md`](programme_requirements.md) — R30–R31c,
  the conflict rules and the gate module.
- [`camps_requirements.md`](camps_requirements.md) — R27–R39, the
  flag-don't-block model.
