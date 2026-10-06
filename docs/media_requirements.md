# Media — Requirements

This document is the testable specification for media: the files members
and staff upload, how they are stored and served, who may see them, and
how they are attached to the things they belong to. It is written as
plain-English rules and names no HTTP endpoints or source files. The code
defines current behaviour; this document records it. Each rule's test
names it with `@pytest.mark.requirement("media:Rn")`, and
`tests/test_requirement_coverage.py` checks every marked rule has one.

What the anonymous website sees of media — event covers and galleries,
venue images, coach avatars, site media slots, the descriptor published in
place of a bare id — is specified in
[`public_requirements.md`](public_requirements.md) (R13, R18, R31–R33) and
[`venue_requirements.md`](venue_requirements.md) (R25). The notice sent
when a video finishes converting is
[`notifications_requirements.md`](notifications_requirements.md) R108.
This document does not restate them.

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[UNTESTED]** — implemented, but no test proves it yet.

## Vocabulary

- A **media item** is one uploaded file: an **image**, a **PDF** or a
  **video**. It is identified by a uuid; staff record screens also use an
  integer id.
- **Access roles** are a media item's own visibility list, drawn from
  `public`, `self`, `admin` and `coach`. `self` means the uploader.
- **Viewable** — a caller may see a media item when its access roles
  admit them (R25–R29). Everything a caller cannot see is answered as if
  it did not exist, unless a rule says otherwise.
- An **owner** is what a media item is attached to: a user, an event, a
  group, a venue, or (when evaluations are enabled) an evaluation.
- A **link** attaches one media item to one owner under a **tag** (a short
  label such as `avatar`, `event_cover`, `venue_image`,
  `identity_document`), with optional free-text **metadata**. A media item
  may be linked many times.
- A media item is **in use** while at least one link of any owner type
  points at it.
- A **variant** is one of the files a download can return: the
  `original` for every type, a `poster` still for PDFs and videos, an
  `animated` preview for videos.
- **Preserve original** is an upload option that keeps the file as sent
  instead of converting it.
- **Conversion status** is `none` (kept as sent), `completed`, `pending`
  or `processing` (a video waiting for or in conversion), or `failed`.

---

## Upload

- **R1** [✅] Any logged-in user can upload an image, a PDF or a video.
  The item records its uploader, and its access roles are those sent with
  the upload, or `public` when none are sent.
- **R2** [X] An anonymous upload → 401.
- **R3** [X] A file that is neither a supported image, video nor PDF, by
  its extension or its declared type → 400 `INVALID_MEDIA_TYPE`.
- **R4** [X] Each media type has its own size limit, set per
  deployment (by default 10 MB for images and PDFs, 50 MB for videos). A
  larger file → 413 `FILE_TOO_LARGE`, naming the media type and the limit.
- **R5** [✅] Images and PDFs are processed while the upload waits, and
  the upload answers 201 with the finished record.
- **R6** [✅] A video upload answers 202 with the record in
  `pending` conversion; the file is converted afterwards by the conversion
  worker (R15–R19).
- **R6a** [✅] An admin, the super admin included, can upload on
  behalf of a user by naming them in `ownerUsername`. The item records that
  user as its uploader, so `self`, the right to change and delete it (R43)
  and the listing of their own uploads (R37) are the named user's, not the
  admin's.
- **R6b** [X] A coach or a member naming anyone but themselves in
  `ownerUsername` → 403 `FORBIDDEN`, and nothing is stored. Naming
  themselves is the same as leaving it out.
- **R6c** [X] An `ownerUsername` that names no user, or a deleted
  one → 404 `USER_NOT_FOUND`, and nothing is stored.
- **R6d** [✅] The audit row of an upload made on behalf of a user
  names the admin as its actor and the owner as its target, with the owner
  in its details.

## Types and conversion

- **R7** [✅] The recorded type of an upload is decided by the file's own
  leading bytes first, then the declared type unless it is a generic
  "binary" type, then the filename extension, and last a default for the
  media type. A wrong or generic declared type is never stored.
- **R8** [✅] An image uploaded without preserve original is converted to
  WebP. Its record names WebP as the stored type, and keeps the uploaded
  type and filename beside it; every image format the upload accepts can
  be converted.
- **R9** [✅] An item kept as sent is stored and served exactly as
  uploaded, under its uploaded type.
- **R10** [✅] A PDF uploaded without preserve original also gets a
  first-page still, served as its `poster` variant. A PDF kept as sent has
  none (see `public_requirements.md` R33).
- **R10a** [X] An image or PDF uploaded without preserve original that
  the converter cannot process → 422 `MEDIA_CONVERSION_FAILED`; no media
  item is created and no file stays on disk. A PDF whose first page cannot
  be rendered counts as such a failure even when the renderer reports
  success. Kept as sent, the same file is stored as uploaded (#521).
- **R11** [✅] The record's type is the type a download of the original
  carries, and its filename is the name a download is served under: the
  first eight characters of the uuid, the uploaded name, and the stored
  extension.

## Download

- **R12** [✅] A viewable media item can be downloaded by its uuid; a
  public one needs no login.
- **R13** [X] Each media type offers only its own variants — images the
  original; PDFs the original and poster; videos the original, poster and
  animated preview. Any other variant → 400 `INVALID_VARIANT`.
- **R14** [X] An unknown uuid, or a soft-deleted media item →
  404 `MEDIA_NOT_FOUND`.

The download's decorative filename, its headers-only form and the refusal
of a preview that was never produced are `public_requirements.md`
R32–R33.

## Video conversion

A background worker converts videos one at a time, after the upload has
answered.

- **R15** [✅] The worker takes the oldest pending, live video, marks it
  `processing`, converts it, and moves on; with nothing pending it waits
  and polls again.
- **R16** [✅] A successful conversion stores an MP4 (or, with preserve
  original, keeps the file as sent and discards the MP4), writes a poster
  still and an animated preview, removes the staged upload, and marks the
  item `completed`.
- **R17** [X] A converter error, or a staged upload that has gone missing,
  marks the item `failed` with the error recorded.
- **R18** [X] A video soft-deleted while pending, or one no longer pending,
  is skipped and left as it is.
- **R19** [✅] Videos left `processing` by a stopped or crashed server are
  put back to `pending` when the server starts, so they are converted
  again.
- **R20** [X] Downloading a video still pending or processing →
  409 `CONVERSION_IN_PROGRESS`; one whose conversion failed → 422
  `CONVERSION_FAILED`.

## Access roles

- **R21** [X] Access roles must be a non-empty list of known roles. An
  unknown role → 422 `INVALID_ACCESS_ROLES`.
- **R22** [X] An empty list → 422 `INVALID_ACCESS_ROLES`, on upload
  and on change.
- **R23** [X] An upload whose access roles are not a list at all
  (malformed, or a single value) → 422 `INVALID_ACCESS_ROLES`.
- **R24** [✅] `public` absorbs every other role: a list containing
  it is stored as `public` alone. Repeated roles are stored once.
- **R25** [✅] A `public` item is viewable by everyone, anonymous callers
  included.
- **R26** [✅] `self` admits the uploader.
- **R27** [✅] `admin` admits users with the admin role; `coach` admits
  users with the coach role.
- **R28** [X] A caller the list does not admit cannot view the item: a
  coach does not see an item listed for `self` and `admin`.
- **R29** [✅] A super-admin can view every item, whatever its
  access roles.
- **R30** [X] Downloading an item that is not viewable anonymously,
  without logging in → 401 `AUTHENTICATION_REQUIRED`.
- **R31** [X] Downloading it logged in as someone it does not admit
  → 403 `FORBIDDEN`.
- **R32** [X] A download presenting a refresh token is treated as
  anonymous.
- **R33** [X] A download presenting the token of a blocked user, or
  of one who has left, is treated as anonymous.

## Media records

- **R34** [✅] Admins and coaches can list every media item, paged.
  Soft-deleted items are left out unless asked for.
- **R35** [✅] That listing is newest first, and can be narrowed by
  media type and by conversion status.
- **R36** [X] A member cannot list every media item → 403.
- **R37** [✅] Any logged-in user can list their own live uploads.
- **R38** [✅] A media record can be read by its uploader or by an
  admin.
- **R39** [X] Anyone else reading a media record → 404 `MEDIA_NOT_FOUND`.
- **R40** [✅] An admin or coach reads a record whatever its access
  roles, and a soft-deleted record is still readable.
- **R41** [✅] Only a media item's access roles can be changed. Other
  fields sent with the change are ignored, not refused.
- **R42** [✅] Uploads outlive their uploaders: permanently deleting
  a user keeps their media items, with no uploader recorded.

## Changing and deleting media

- **R43** [✅] The uploader, an admin and the super-admin can always
  change an item's access roles, soft-delete it and restore it, whatever
  its access roles.
- **R44** [X] Nobody else can, coaches included: a caller who can view
  the item is refused with 403 `INSUFFICIENT_PERMISSION` (#503).
- **R45** [X] A caller who cannot view the item is refused with
  404 `MEDIA_NOT_FOUND`, not 403, so its existence is not revealed.
- **R46** [X] A `public` item is no exception: it admits everyone as a
  viewer, but a logged-in user who did not upload it and is not an admin
  is refused with 403 (#503).
- **R47** [X] A media item cannot be soft-deleted while it is in use →
  409 `MEDIA_IN_USE`, listing the links; every owner type counts,
  evaluations included.
- **R48** [✅] A soft-deleted item leaves the listings, and its files stay
  on disk.
- **R49** [✅] A soft-deleted item can be restored.
- **R50** [X] Restoring an item that is not deleted → 422
  `NOTHING_TO_RESTORE`; the item is unchanged and no audit row is
  written (#520).
- **R51** [X] Only a super-admin can permanently delete a media item →
  403 otherwise.
- **R52** [X] A media item must be soft-deleted before it is permanently
  deleted → 422 `HARD_DELETE_NEEDS_SOFT_DELETE` (#526).
- **R53** [✅] Permanent deletion removes the record and every file it
  owns.
- **R54** [✅] Uploading, changing access roles (when they actually
  change), encrypting (when it actually encrypts), soft-deleting,
  restoring and permanently deleting a media item each write an audit row.

## Links

The same rules hold for every owner type; each owner type chooses its own
tags. Evaluation links follow them too, with the additions under
"Evaluation media".

- **R55** [✅] A media item can be linked to a user, an event, a group or
  a venue under a tag, with optional metadata. The same item can be linked
  to several owners and under several tags.
- **R56** [X] A tag is 1–64 letters, digits, `_` or `-`; metadata
  is at most 2048 characters; a link carries nothing else. Anything else
  → 422.
- **R57** [X] Linking the same item to the same owner under the same tag
  twice → 409 `MEDIA_LINK_EXISTS`.
- **R58** [X] Linking an unknown item, or one the caller cannot view, →
  404 `MEDIA_NOT_FOUND`, answered identically so a link cannot probe
  whether a uuid exists.
- **R59** [X] Linking a soft-deleted item → 404 `MEDIA_NOT_FOUND`.
- **R60** [X] An unknown owner → 404 naming the owner type
  (`USER_NOT_FOUND`, `EVENT_NOT_FOUND`, `GROUP_NOT_FOUND`,
  `VENUE_NOT_FOUND`).
- **R61** [X] The links of a soft-deleted event, group or venue stay
  listed, and every link row — on the owner's own listings, on a media
  item's reverse lookup and on the cross-owner search — carries
  `ownerDeleted`, true while its owner is soft-deleted. Those links are
  read-only until the owner is restored: adding, changing or removing one
  → 422 `OWNER_DELETED` (#517).
- **R62** [X] One owner holds at most 100 links under one tag, and at
  most 50 distinct tags, both set per deployment → 422
  `MEDIA_LINK_TAG_FULL` / `MEDIA_LINK_TOO_MANY_TAGS`.
- **R63** [✅] A link's metadata can be changed; its tag and media item
  are its identity, so changing those means removing it and linking again.
- **R64** [✅] One link, or every link under a tag, can be removed. The
  media items themselves are untouched.
- **R65** [X] Changing or removing a link that does not exist →
  404 `MEDIA_LINK_NOT_FOUND`.
- **R66** [✅] An owner's links can be listed grouped by tag, or for one
  tag, and one link can be read by tag and item.
  Every row carries the media item's descriptor
  (`public_requirements.md` R31).
- **R67** [X] Rows whose media item the caller cannot view are left out
  of every listing, and reading one directly → 404.
- **R68** [✅] Linking, changing and removing links write audit rows
  naming the owner, the tag and the media item.
- **R69** [✅] Permanently deleting an owner removes its links; the
  media items stay.

### Who may read and write links

- **R70** [✅] A user's links can be read by that user and by any admin or
  coach, and written by that user and by any admin.
- **R71** [X] Another member writing a user's links → 403.
- **R72** [X] Another member reading a user's links → 403, and a
  coach writing another user's links → 403.
- **R73** [✅] An event's, group's or venue's links can be read by
  any logged-in user.
- **R74** [✅] An event's, group's or venue's links can be written
  by an admin or a coach.
- **R75** [X] A member writing an event's, group's or venue's links →
  403.
- **R76** [X] Every link read and write refuses an anonymous caller
  → 401.

### Identity documents

- **R77** [✅] With identity verification on, a newly registered user can
  submit for review only once a live media item is linked to them under
  `identity_document`; otherwise → 422. With verification off no document
  is needed.

## Where media is used

- **R78** [✅] A logged-in caller who can view a media item can list
  every link that points at it — owner type, owner id, tag and
  metadata — across all owner types. An item that is not in use lists
  nothing.
- **R79** [X] The list is empty when the caller cannot view the media item
  itself.
- **R80** [✅] Admins and coaches can search every link of live media
  items across owners, paged, narrowed by owner type, tag and encryption
  state.
- **R81** [✅] The search answers newest link first, and can also be
  narrowed by media type.
- **R82** [X] A member cannot search → 403; an unknown owner type → 422
  `INVALID_OWNER_TYPE`.
- **R83** [X] Search rows whose media item the caller cannot view are left
  out, and the total counts only what is shown.

## Encryption at rest

Encryption is opt-in per item and one-way: there is no operation that
decrypts an item back to plain storage.

- **R84** [✅] An upload can ask to be encrypted. The stored file is
  encrypted, the record says so, and a download returns the original bytes
  under the stored type.
- **R85** [X] Asking to encrypt a video upload → 422
  `ENCRYPTION_NOT_SUPPORTED_FOR_VIDEO`.
- **R86** [X] With no encryption key configured, an encrypted
  upload, and a download of an encrypted item → 503
  `ENCRYPTION_NOT_CONFIGURED`.
- **R87** [X] An encrypted upload is also held to its own size
  limit, set per deployment (10 MB by default) → 422
  `ENCRYPTED_FILE_TOO_LARGE`, naming the limit.
- **R88** [✅] A super-admin can encrypt an existing plain item in place:
  the plain files, a PDF's poster included, are replaced by encrypted
  ones, the record says so, and downloads return the same bytes as before.
- **R89** [✅] Encrypting an item already encrypted changes nothing and
  succeeds.
- **R90** [X] Encrypting in place is refused for a regular admin (403), an
  anonymous caller, an unknown item (404), a video (422
  `ENCRYPTION_NOT_SUPPORTED_FOR_VIDEO`), with no key configured (503
  `ENCRYPTION_NOT_CONFIGURED`), and when the file is missing from disk; a
  refusal leaves the item plain.
- **R91** [X] An encrypted item whose encryption details are missing is
  never served as ciphertext; the download fails instead.

## Cache headers on downloads

- **R92** [✅] A download of a plain item anonymous callers can view is
  marked cacheable by anyone for a day (`public, max-age=86400`). A plain
  item restricted by its access roles is marked for the requesting browser
  alone (`private, max-age=86400`), so shared caches never store it
  (#504).
- **R93** [✅] A download of an encrypted item is marked
  `private, no-store`.
- **R94** [✅] Every download allows any origin, so a web client on
  another domain can play or show it.

The public surface's own cache rules are `public_requirements.md`
R20–R22.

## Evaluation media (applies when evaluations are enabled)

Media attached to an evaluation follows the link rules above, with its
own authority and a second, member-facing view
(`evaluation_requirements.md` R55–R56c). What happens to evaluation media
when the module is switched off after use is out of scope here.

- **R95** [✅] The evaluation's effective owner can attach media to a
  draft evaluation as evidence, under the tag of the question it
  justifies, change a link's metadata, and detach it; detaching leaves the
  media item in place. Attaching the same item under the same tag twice →
  409 `MEDIA_LINK_EXISTS`. The tag and file rules are
  `evaluation_requirements.md` R56a (#535).
- **R96** [X] Anyone but the effective owner attaching media: for a coach
  or an admin the evaluation does not exist → 404 `EVALUATION_NOT_FOUND`;
  a plain member → 403; an unknown evaluation → 404 (#535).
- **R97** [X] Attaching an item the author cannot view → 404
  `MEDIA_NOT_FOUND`.
- **R98** [✅] The effective owner can read an evaluation's media (#535).
- **R99** [X] Anyone else reading it on the staff surface — another coach
  or an admin → 404 `EVALUATION_NOT_FOUND` (#535).
- **R100** [✅] The member an evaluation is about sees the evidence on its
  public questions, and its member copy (tag `member_copy`,
  `evaluation_requirements.md` R63), once the evaluation is published
  (#535).
- **R101** [X] Evidence on a private question stays with the effective
  owner: the member never sees it (#535).
- **R102** [X] Before publication the member's request → 404
  `EVALUATION_NOT_FOUND`; another member's request → 403.
- **R103** [✅] The cross-owner search accepts `evaluation` as an owner
  type, whether or not the module is switched on, so leftover evaluation
  media stays findable.
- **R104** [X] With the module switched off, every evaluation media
  operation answers 503 while staying registered.
- **R105** [X] Attaching, changing and detaching evaluation media write no
  audit row: evidence is draft content, and an evaluation enters the audit
  trail when it is first saved (`evaluation_requirements.md` R52, #535).

## Retiring the legacy store

- **R106** There is no sweep of unreferenced media. An item that is not in
  use stays until its uploader or staff delete it.
- **R107** [✅] The one-time migration that retires the legacy upload
  store removes its rows and its files only when every one of them already
  has a counterpart media item; otherwise it removes nothing and fails.
  Running it again on a clean store does nothing.

---

## Future work

- A sweep of media items that have not been in use for a set time, and of
  staged video uploads left behind by a crash.
- Serving downloads from the web server rather than the application,
  once it carries the cross-origin headers browsers need for video.

## See also

- [`public_requirements.md`](public_requirements.md) — R13, R18, R20–R22,
  R31–R33: media on the public surface.
- [`venue_requirements.md`](venue_requirements.md) — R25: the public venue
  image.
- [`evaluation_requirements.md`](evaluation_requirements.md) — R55–R56c:
  why evaluation media is private by default.
- [`notifications_requirements.md`](notifications_requirements.md) — R108:
  conversion notices.
