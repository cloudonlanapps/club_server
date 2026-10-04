# Public Website Surface — Requirements

This document is the testable specification for what the server publishes
to anonymous readers: the club's public website and anyone else who reads
without logging in. It is written as plain-English use cases and names no
HTTP endpoints or source files.

The public surface is an **explicit allow-list**. Nothing a member
endpoint happens to make readable is public by accident; a thing is public
because a rule here says so. People are addressed by an opaque public id
(an HMAC of the username keyed on the deployment secret), venues and
events likewise by an HMAC of their id, so the integer ids and usernames
never appear and cannot be walked.

Venues are specified in [`venue_requirements.md`](venue_requirements.md)
R22–R25; event presentation fields in
[`event_marketing_requirements.md`](event_marketing_requirements.md).

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.

## Staff listing (#332)

Two facts decide whether a coach is on the website's staff page, and they
belong to different people. **Consent** (`is_public_profile`) is the
coach's own, self-set; it answers *may we show this person*. **Curation**
is the admin's, kept in a listing row separate from the user record; it
answers *how do we present them*: where in the order, whether they are a
guest, whether to withhold them from the page. Curation can only withhold
or order; it can never grant visibility.

- **R1** [✅] The staff page lists the coaches who have consented, in
  curated order: coaches with a position first, ascending; the rest after
  them by name. A coach with no listing row is "public, uncurated".
- **R2** [X] A coach curated as hidden is withheld from the staff page,
  whether or not guests are asked for.
- **R3** [✅] A coach curated as a guest is withheld unless the reader
  asks to include guests, and is marked as a guest in the response either
  way, so an event page can label them.
- **R4** [✅] An admin can read the whole listing, upsert one coach's row
  (any subset of position, guest, hidden; the rest untouched) and remove a
  row. Anonymous → 401; coach or member → 403; a username that is not a
  user → 404; a user without the coach role → 422 `NOT_A_COACH`.
- **R5** [X] Curation is not a profile edit: it sends the coach no
  "profile changed by admin" notice and writes no user-update audit row.
  It writes its own audit row naming the coach.
- **R6** [✅] A guest is an account an admin creates with the guest flag.
  Creating it writes the listing row as a guest and publishes the profile
  on the admin's authority, because a guest never logs in to consent. The
  guest flag is not a field of self-registration → 422 if sent.
- **R7** [X] The user record carries no display order any more. Sending
  one is an unknown field → 422; no user response returns one.

## Event catalogue (#299)

The website is a catalogue, not a schedule: it lists events by type in a
time window and shows one page per event. Occurrences are not expanded.
Eligibility is shown as the event's windows and never matched against a
reader, who has no identity.

- **R8** [✅] Anyone can list the public, live events, paginated. An item
  carries the event's public id and never its integer id, and never a
  username: no organizer, no coach usernames.
- **R9** [✅] The list filters by type, by a featured flag, by venue
  (public id), and by a time window: `from` keeps events with an
  occurrence ending at or after it, `to` keeps events starting at or
  before it. An unknown type → 422.
- **R10** [✅] Anyone can read one public, live event by its public id.
  A private or deleted event, an integer id, or an unknown id → 404.
- **R11** [✅] The event's coaches are embedded as public profiles: the
  consenting ones, guests included (they are published by their admin's
  authority); a coach who has not consented is omitted, not named.
- **R12** [✅] The event's venue is embedded as its public projection,
  and the event's venue id is the venue's public id.
- **R13** [✅] The cover is the newest publicly viewable `event_cover`
  link; the gallery is every publicly viewable `event_gallery` link in
  order of attachment. Private media are omitted.
- **R14** [✅] The event says whether it is past (every occurrence
  finished) and carries its basic marketing block, null when none of the
  four fields is set.
- **R15** [✅] Logged-in event reads carry the same embedded `coaches`
  projection beside `coachNames`, so the member app renders coaches
  without a lookup per coach.

## Club info and site media (#296)

The club's public identity and the site's media slots are two system
preferences, written by a super-admin through the ordinary preference
endpoint and read publicly as one document. The site defines the purposes
of its media slots; the server only stores the map.

- **R16** [✅] Anyone can read the club-info document: the `club_info`
  object and the `site_media` map. A key never set reads as `{}`.
- **R17** [✅] `club_info` is stored and published as written, any JSON
  object; a value that is not an object → 422 `INVALID_PREFERENCE_VALUE`.
- **R18** [X] `site_media` is written as a map from a purpose string to
  the uuid of a live, publicly viewable media item; any other value, a
  private or unknown media → 422 `SITE_MEDIA_NOT_PUBLIC` (or
  `INVALID_PREFERENCE_VALUE` for a value that is not such a map), and the
  stored map is untouched. An empty map clears every slot. It is published
  per R31, and a slot whose media has since been deleted or hidden is
  dropped from the published map.
- **R19** [✅] Outgoing email is branded with the club's `name` and
  `shortName` from `club_info` when set, and with the deployment's
  configured names otherwise.

## Cache headers (#297)

- **R20** [✅] Every public read answers with `Cache-Control: public,
  max-age=300` and a strong `ETag` derived from the response content, so
  the same content has the same tag whatever the query string, and
  changed content a different one.
- **R21** [✅] A read whose `If-None-Match` equals the current tag
  answers 304 with no body and the same cache headers.
- **R22** [X] The health probe is `no-store`; error responses and every
  logged-in read carry no cache headers.

## Inquiries (#407)

The first unauthenticated write on this server: a contact-form message or
an "I'm interested" registration from someone who is not a user. Spam
defence is part of the design, and the two kinds share one mechanism.

- **R23** [✅] Anyone can submit an inquiry of kind `contact` or
  `interest` with a name, an email, an optional phone, a message and
  optional extra fields. The answer is 202 with no body, and the record is
  never echoed back. The client's address is kept only as a salted hash.
- **R24** [X] The form carries a server-issued fill-time token. A forged,
  malformed, missing or expired (over an hour old) token → 422
  `INVALID_FORM_TOKEN`: that is a broken form, not a bot. A submission
  under three seconds after the token was issued is accepted and stored
  nowhere.
- **R25** [X] A filled honeypot field, or a second submission from the
  same address and email within ten minutes, is accepted (202) and stored
  nowhere: a bot should not learn it was caught.
- **R26** [X] An unknown kind, an invalid email, an empty or over-long
  name (200), a message over 2000 characters, non-object extras, or an
  unknown field → 422.
- **R27** [✅] A stored inquiry is emailed to the club's inquiry address
  from `club_info` when one is set, and always raises one
  `inquiry.received` notification for every admin. No mail goes to the
  submitter.
- **R28** [✅] An admin can list inquiries newest first, filter by kind
  and by handled state, mark one handled (recording who and when) or
  reopen it, and hard delete it. Anonymous → 401; other roles → 403;
  unknown id → 404. Handling and deleting are audited.
- **R29** [✅] A daily sweep removes handled inquiries after 180 days and
  unhandled ones after 365.
- **R30** [X] Submitting writes nothing to the audit log; it is not a
  user's action.

## Media descriptors (#424)

- **R31** [✅] Wherever the public surface names a media item — an event
  cover or gallery item, a venue image, a coach's avatar, a site media
  slot — it publishes a descriptor and never a bare id: the content type a
  download will carry, and the name to save it under. A reader can choose a
  renderer from the content type alone, without fetching the file to find
  out what it is, and nothing published can be worked out from something
  else published.
- **R32** [✅] The download address accepts the published name as a
  trailing path segment and ignores it for the lookup, so the address ends
  in a real file extension. It also answers a headers-only request, which
  is how a reader asks what a particular preview is, how large it is, and
  whether it exists at all.
- **R33** [X] A preview that was never produced is refused, never answered
  with a different file. A document kept in its original form has no page
  image, and asking for one is a miss rather than the document itself.
