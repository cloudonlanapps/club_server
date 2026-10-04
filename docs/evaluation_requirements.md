# Evaluations — Requirements

This document is the testable specification for player evaluations,
written as plain-English use cases. Like the event and venue documents it
avoids naming HTTP endpoints or source files, so the API surface can
evolve without invalidating the requirements.

An **evaluation** is a coach's written assessment of one member against a
**template**: answers to the template's questions, each optionally carrying
a coach note and evidence. It is drafted, saved, and only then published —
publication is the act that makes it visible to the member being assessed.

Crucially, an evaluation is always assessed **of something**. A general
impression of a member and a report on how they did at one camp are
different documents with different readers and different rules about who
may write them. That is the **scope**, and it is the organising idea of
this document.

**The module is implemented (#302, PR #396).** Evaluations existed in the
club generation of this server and were removed wholesale in #9 (commit
`0126c9b`, May 2026); #302 restored them, and soft delete (#298) was built
in from the start. **#535 reshaped the content** — templates became items
and a layout, evaluations became answers — and narrowed who may write and
see an evaluation; the rules it changed or added cite it. The code defines
current behaviour; this document records it.
Each rule's test names it with `@pytest.mark.requirement("evaluation:Rn")`,
and `tests/test_requirement_coverage.py` checks every marked rule has one.
Evaluations are an optional module: their tests switch it on per test
(R61a).

Convention:

- **[✅]** — positive ability is wired and covered by tests.
- **[X]** — prohibition is enforced and covered by tests.
- **[GAP]** — agreed, not yet implemented; cites the issue that adds it.
- **[UNTESTED]** — implemented, but no test proves it yet.

A rule with no mark is a design note or a record of a decision; it states
nothing a test could check on its own. Until #402 each rule instead said
where it came from — carried from the archive, changed from it, or decided
new during triage of #302. That provenance is in this document's history.

---

## Vocabulary

- **Created for** — the member the evaluation is about. The archive called
  this the *player*; until #535, the *subject*.
- **Created by** — the coach who started the evaluation. It never changes.
- **Owner** — the coach the evaluation was transferred to, if any. The
  **effective owner** is the owner, else the creator: the one person who
  sees the evaluation before publication and acts on it.
- **Scope** — what the evaluation is *about*: the member in general, or
  their participation in one named event.
- **Period** — an optional start and end the evaluation covers.
- **Template** — a name, its **items**, and their **layout**. An item is a
  **question** (it takes an answer) or **info** text (it does not).
- **Origin** — for an item copied from another template, the item it was
  first copied from; items sharing an origin are the same question
  (R12b). An item is otherwise identified by its id alone.
- **Layout** — the template's order of items, optionally grouped into
  titled **sections**.
- **Private item** — an item that, with its answer, coach note and
  evidence, never reaches the member.
- **Answer** — the value given to one question, with an optional **coach
  note** and optional **evidence** (images, videos and PDFs).
- **Published** — the terminal visible state. The member can read it; the
  effective owner can no longer edit it without first withdrawing it.

---

## Scope

The part with no precedent in the archive, which knew only of a member
and a coach.

- **R1** [✅] Every evaluation carries exactly one scope: it is about one
  event, or, naming none, it is general. There is no third kind (#535).
- **R2** [✅] **General scope.** An assessment of the member overall, tied
  to no event. This is the closest thing to what the archive supported.
  A general evaluation may carry a period (R4) (#535).
- **R3** [✅] **Event scope.** An assessment of the member's
  participation in one named event. The event may be a one-off, a camp,
  or a programme — the three types specified in
  `oneoff_requirements.md`, `camps_requirements.md` and
  `programme_requirements.md`. Naming an event that does not exist → 404
  `EVENT_NOT_FOUND` (#535).
- **R4** [✅] Any evaluation, general or event-scoped, may be narrowed to
  a **period**: a start and an end. This is what makes a report on one
  term of a long-running programme, or a quarterly general review,
  possible without inventing a separate entity for it. A period gives
  both bounds or neither, and its end is on or after its start — a
  one-day period may end where it starts → 422 (request validation)
  otherwise. **A review looks back:** a period's end lies in the past,
  at or before the server's clock when the period is written; a period
  ending later → 422 `PERIOD_IN_FUTURE`. Both apply on create and
  whenever a draft's period changes (#535).
- **R5** [✅] A period may collapse to a **single occurrence** — one
  session, assessed on its own, expressed as a window tight around it.
  For a one-off, where event and occurrence are the same thing
  (`oneoff_requirements.md`), the period is redundant and is not
  required; no event type requires one.
- **R6** [✅] A period is **any start and end** — it need not align to
  occurrence boundaries. The occurrences it covers are **derived from the
  time frame** rather than enumerated and stored. This is what makes
  monthly, quarterly or termly reports fall out of the existing model
  without inventing a reporting entity for each: the period is the
  question, and the sessions inside it are the answer. A period covering
  no occurrences is not rejected on its own terms — no member can satisfy
  R33 within it, so the emptiness surfaces as an eligibility failure
  (422 `NOT_ELIGIBLE`) rather than as a separate validation rule.
- **R7** [X] **One review per coach, member, template and period.**
  Among the live (not soft-deleted) evaluations of one effective owner,
  at most one is for a given member, template and period — the exact
  start and end, or no period at all, which counts as a value: one
  period-less review of a member on a template per coach. Overlapping
  periods are different periods, and the event is not part of the key.
  A write that would make a second → 422 `DUPLICATE_EVALUATION`:
  creating one, changing a draft's period or event (R23), transferring
  one to a coach who already owns its twin (R36), and restoring a deleted
  one. A member may still hold several evaluations of one scope — from
  different coaches, on different templates, or over different periods.
  Consequence: nothing reconciles two coaches' reports that disagree,
  and any reader — the member included — sees both (#535).

## Shape

- **R8** [✅] An evaluation names exactly one member it is created for,
  the coach who created it, and optionally an owner; it carries a status,
  and records when it was created and when it was published (#535).
- **R9** [✅] An evaluation's content is its **answers**: at most one per
  question of its template, each a value of the question's kind, an
  optional coach note, and optional evidence (R56a). An answer may carry
  only a note or only evidence. There is no free-standing comment and no
  staff-only note: a coach's private notes are a private question (R39)
  (#535).
- **R10** [X] Every answer is **validated against its item**: a rating
  within that item's scale or levels, yes or no, a number, a single choice
  among its choices, one or more of its choices, or written text for a
  Q & A. An answer of the wrong kind, outside the item's domain, or for an
  item that is not a question of the template → 422 `INVALID_ANSWER`. This
  replaces the archive's opaque JSON blob, which the server never looked
  inside (#535).
- **R10a** [X] Every evaluation **names exactly one template**, and the
  reference is required rather than optional: creating one without it
  → 422. R10 makes the template the contract answers are validated
  against, and a contract that may be absent is not a contract — an
  evaluation without one could carry anything at all, which is the
  opaque blob R10 replaced under a different name. The archive allowed
  template-less evaluations because it validated nothing.

- **R11** Because R10 makes answers real data rather than a string,
  progress over time becomes answerable — the same member, the same
  question, across scopes, and across templates by origin (R12b). That is not
  a feature of this document, but R10 is the decision that permits it,
  and it is the reason R10 was chosen over the cheaper options.
- **R12** [X] A template's items are the validation contract. Each item
  has an id assigned by the server; a type — `rating`, `yesNo`, `singleChoice`,
  `multipleChoice`, `number`, `qa` or `info`; question text in markdown
  (info carries markdown text instead, and takes no answer); a private
  flag; and the properties of its type and no others — an unknown type or
  a property its type does not allow → 422 (#535).
- **R12a** [X] An item's properties are consistent: a rating has a star
  or range scale (minimum below maximum) or labelled levels, never both; a
  choice question has at least one choice and unique choice values; the
  answers that require a coach note are answers the question can take,
  and need its comment area shown → 422 otherwise (#535).
- **R12b** [X] **A copy is the same question as its origin.** An item
  may be added as a copy of an item of any template; it records that
  item's origin, or that item when it has none, so a copy of a copy points
  at the first. A copy keeps its origin's type and answer domain — the
  same scale or level values, the same choice values; wording, labels,
  required, private and comment settings may differ. A write breaking
  this → 422 `ORIGIN_MISMATCH`; an origin that does not exist → 404
  `ITEM_NOT_FOUND` (#535).
- **R12c** [X] A template's **layout** is an ordered list of items and
  titled sections, each section holding items only — sections do not
  nest. Every item of the template appears in it exactly once, and it
  names no other → 422 (#535).
- **R13** [✅] Timestamps and soft-deletion follow the current
  conventions rather than the archive's ad-hoc integers (#298):
  millisecond UTC instants stamped by the server.
- **R13a** [✅] The server caps no text length — names, questions,
  answers and notes. Clients may restrict their own forms (#535).

## Lifecycle

The states and the only moves between them.

- **R14** [✅] An evaluation is created in **draft**.
- **R15** [X] Draft → **saved**. The effective owner declares it
  complete: every required question is answered, and every answer whose
  value requires a coach note has one → 422 `INCOMPLETE` naming the items
  otherwise. A draft may have gaps. Nothing about visibility changes
  (#535).
- **R16** [✅] Saved → **published**. This is the only step that exposes
  the evaluation to its subject, and it stamps the publication time.
- **R17** [✅] Publication is reversible: published → saved withdraws it
  from the subject's view.
- **R18** [✅] Saved → draft returns it to editing.
- **R19** [X] No other transition exists. In particular a draft cannot
  be published directly — it must pass through saved → 422
  `INVALID_TRANSITION`.
- **R20** [✅] Withdrawing an evaluation **clears the publication
  timestamp**. The field then means exactly "currently published since",
  and a client may read its presence as the fact of publication. The
  archive left the timestamp set, so a withdrawn evaluation still
  appeared to carry a publication time. The fact that it was once
  visible is not lost — the audit log records the publication and the
  withdrawal both (R52).

## Editing and deletion

- **R21** [✅] Only a **draft** may be edited, and only by its effective
  owner: one answer at a time — written, replaced or cleared — its
  period, and its event (R23) (#535).
- **R22** [X] A saved or published evaluation cannot be edited → 422
  `INVALID_STATE`, with the remedy named: revert a saved one to draft;
  withdraw a published one first. Saving is a declaration that the content
  is final, so a saved evaluation is read-only (#535).
- **R22a** [✅] Clearing an answer detaches its evidence (#535).
- **R23** [✅] The member an evaluation is created for is fixed at
  creation and cannot be edited → 422. A **draft's event may change** —
  to another event, or to none, which makes it general — with or without
  its period. Eligibility (R28–R33) is checked again for its effective
  owner and member against the resulting event and period, exactly as on
  create → 422 `NOT_ELIGIBLE`, 404 `EVENT_NOT_FOUND`; so is R7. A saved or
  published evaluation → 422 `INVALID_STATE` (R22). The answers stay: the
  template, which they are validated against, does not change (#535).
- **R24** [X] Only a **draft** may be deleted. A saved or published
  evaluation must be reverted to draft first → 422 `INVALID_STATE`.
- **R25** [✅] Deletion is **soft** and follows the same shape as every
  other entity here — soft delete returns the updated entity, hard delete
  is a separate destructive action, a soft-deleted evaluation can be
  restored, and soft-deleted evaluations can be listed. Restoring one
  that is not deleted → 422 `NOTHING_TO_RESTORE`; hard-deleting one that
  is not soft-deleted → 422 `HARD_DELETE_NEEDS_SOFT_DELETE` (#526). The
  archive had hard delete only.
- **R26** [✅] The same six operations apply to templates (#298). The
  archive hard-deleted templates with no check at all.
- **R26a** [✅] A template is edited a piece at a time: renamed,
  re-laid out, or one item added, changed or removed — adding places the
  item in the layout, removing takes it out. An item's type is fixed once
  it is added → 422 `ITEM_TYPE_FIXED`; an item named through a template it
  does not belong to → 404 `ITEM_NOT_FOUND` (#535).
- **R27** [X] A template is referenced by the evaluations validated
  against it, so it carries a "referenced" guard like venues'
  (`venue_requirements.md` R12) → 422 `TEMPLATE_IN_USE` with the count.
  Soft-deleting it is refused while a live evaluation references it;
  hard-deleting it, or changing its items or layout, is refused while any
  evaluation does, soft-deleted ones included, since a restored
  evaluation needs the contract it was written against (#486). Renaming
  stays allowed (#535).
- **R27a** [✅] Every read of a template says whether it is **in use** —
  whether any evaluation, soft-deleted included, is written against it — so
  a client can show it frozen before an edit is refused (#535).

## Eligibility — who may evaluate whom

The archive required no relationship at all: any coach could be assigned
any member. That is now scope-dependent.

- **R28** [✅] **General scope: any coach may evaluate any member.** No
  relationship is required. This is a deliberate choice, not an
  oversight — a general impression is exactly the document that does not
  presuppose one.
- **R29** [X] **Event scope: the author must have coached the event, and
  the subject must have attended it.** Both halves are required → 422
  `NOT_ELIGIBLE`.
- **R30** [✅] "Attended" is drawn from the attendance record, which is
  kept per occurrence and per member (`attendance_requirements.md`). One
  qualifying occurrence is enough; the subject need not have attended
  every session.
- **R31** [✅] **Any attendance record counts**, whatever its status —
  `present`, `late`, `absent`, `onLeave` or `onLeaveRequested`. The test
  is that the member was tracked for that occurrence at all, not that
  they turned up. A coach may therefore report on a member who was
  recorded absent throughout, which is deliberate: a report saying
  someone did not attend is a legitimate report.
- **R32** [X] The author qualifies by being **named on the event's coach
  list**; an author who is not → 422 `NOT_ELIGIBLE`. The stated intent
  was that a coach should qualify by having coached at least one session,
  but coaches are recorded on the event, not the occurrence — the event
  carries a flat list of names and nothing says which of them was present
  when. Adding per-occurrence coach records would support the stronger
  rule and overlaps the event-coach tier work in **#247**. That is not
  tracked as follow-up work: the weaker rule is accepted as the answer,
  in full knowledge of what it permits (see R33a): a coach who took one
  session of a year-long programme may report on any member with any
  attendance record for any part of it.
- **R33** [✅] When a period narrows the scope (R4), the **subject** half
  of eligibility is evaluated within that period — the member must have
  an attendance record falling inside it. The **author** half cannot be
  narrowed, because R32 knows only that a coach belongs to the event.
  So a coach who took only the autumn term may still report on the
  spring one. This asymmetry is a direct cost of R32 and disappears if
  per-occurrence coach records are ever added.
- **R33a** The looseness of R31 and R32 **together** is deliberate,
  and is the settled answer rather than a gap awaiting a fix. Combined,
  they permit any coach named on an event to write event-scoped
  evaluations about any member ever tracked on it, including one
  recorded absent throughout.

  Two tightenings were available without new data — requiring an
  attending status, and one evaluation per coach per subject per scope.
  The first was declined in favour of flexibility; #535 adopted a form of
  the second, keyed on the template and period rather than the scope
  (R7). The reasoning for declining the first is that nobody yet knows
  whether real use wants it, and a constraint added now would have to be
  guessed; one added later can be shaped by what actually goes wrong.
  Revisit only if field use shows a need.

  A future reader should not treat this as an oversight and quietly
  narrow it. If it needs narrowing, that is a decision to take again,
  with evidence.

- **R34** [X] Only a **coach** creates evaluations, and the evaluation
  is created by and owned by the caller. The admin role does not qualify:
  an admin may write templates (R46) and evaluates no one → 403 (#535).
- **R35** [X] Only the effective owner acts on an evaluation — edits it,
  moves it through its lifecycle, deletes or restores it. For anyone else
  it does not exist → not-found (R38a), except a transfer by an admin
  (R36) and hard deletion (R47) (#535).
- **R36** [✅] An evaluation may be **transferred** to another coach by
  its effective owner, or by an admin who names it by id; the admin's
  transfer returns none of the evaluation's content. Only before
  publication → 422 `INVALID_STATE` once published. A transfer sets the
  owner; who created it never changes. The receiving coach must not
  already own its twin (R7) → 422 `DUPLICATE_EVALUATION` (#535).
- **R37** [X] A transfer target must satisfy the same eligibility the
  original author had to (R28–R33, R45) → 422 `NOT_ELIGIBLE`. Transfer
  is not a way around the scope rules.

## Visibility

The rules that make an evaluation more than a row, and the reason the
lifecycle exists at all.

- **R38** [X] The subject sees an evaluation **only once it is
  published**. A draft or saved evaluation does not exist as far as the
  subject is concerned — asking for one by id returns not-found, not
  forbidden, so a member cannot detect that a coach is drafting
  something about them. Nor is the subject notified before publication.
- **R38a** [X] Before publication an evaluation exists **only for its
  effective owner**. Any other caller — another coach, an admin — gets
  not-found for it, and finds it in no listing (#535).
- **R39** [X] The member never sees a **private item** — not its
  question, answer, coach note or evidence — in any state. This is
  enforced by serving the member a different projection, not by filtering
  after the fact: the projection carries the template's name, its public
  items, and its layout with the private items removed (#535).
- **R39a** [✅] The **coach note** on a public item is part of the
  member's view. It is written for the member; anything meant only for
  staff belongs in a private item (#535).
- **R40** [X] A member can only read evaluations about themselves.
  Reading someone else's → 403.
- **R41** [✅] The effective owner sees every field, private items
  included (#535).
- **R42** [✅] On the staff surface a coach sees only the evaluations
  they own, and an admin sees none. Any coach may also read any member's
  **published** evaluations through the member's own surface, in the
  member projection, so without private items (#535).
- **R43** [X] Anonymous callers see nothing. Evaluations are private in
  every state → 401.
- **R44** Guardians reading a dependent's evaluations is **out of
  scope for v1**. Guardian/dependent links were removed in the same
  commit as evaluations (#9) and have not returned to this generation, so
  the capability does not exist to build on. Revisit when they do.

## Authorization

- **R45** [X] Creating, editing and moving an evaluation through its
  lifecycle requires the `coach` role → 403 otherwise. Whoever owns it
  must hold that role too, in every scope → 422 `NOT_ELIGIBLE` (#488,
  #535). Eligibility (R28–R33) applies on top of the role, not instead of
  it.
- **R46** [✅] Templates are readable **and writable by any staff
  member** — an admin or a coach: creating one, renaming or re-laying it
  out, adding, changing or removing an item, soft-deleting and restoring
  it, and listing the deleted ones. Any staff member may edit any
  template, whoever created it. Anyone else → 403. The in-use freeze
  (R27) applies to every writer alike, and hard deletion stays the super
  admin's (R47). Writing templates does not make an admin an evaluator
  (R34) (#535).
- **R46a** [✅] Any staff member may search the items of every template
  by text and by type, to copy an existing question into a template
  (R12b) (#535).
- **R47** [X] Hard deletion requires super-admin, matching every other
  entity → 403 for a plain admin.
- **R48** The archive checked the admin requirement on templates twice
  by two different mechanisms — a route guard admitting admin or coach,
  then a second check inside the handler body. Correct outcome, reached
  redundantly. Template writes are now guarded once, by the route.

## Templates

- **R49** [✅] A template has a name, its items (R12), their layout
  (R12c), and records who created it; it is created whole, in one write.
  A template with no question → 422 (#535).
- **R49a** [X] A live template's **name is unique**, compared without
  regard to case or surrounding whitespace. Creating a template, or
  renaming one, to a name another live template holds → 422
  `TEMPLATE_NAME_TAKEN`; so does restoring a deleted template whose name a
  live one has taken meanwhile. A soft-deleted template holds no name, and
  a template may be renamed to its own name in another case (#535).
- **R50** [✅] Creating an evaluation names its template, the member,
  and optionally an event and a period. It starts as a **draft** with no
  answers, created by the caller (#535).
- **R51** Until #535 a template declared which scopes it was valid for,
  and applying it outside them was refused. That is gone: a template is a
  set of questions, and any template suits a general or an event
  evaluation. What an evaluation is about is its scope, not its template.

## Trace

- **R52** [✅] Saving, publishing, withdrawing, reverting and
  transferring an evaluation each write an audit entry naming the actor,
  the evaluation and the actor's IP. Creating it, editing a draft and
  deleting a draft are not logged: an evaluation enters the trail when it
  is first saved, which is when it starts to matter to anyone but its
  writer. Restoring a deleted draft is not logged either; hard deletion,
  a super-admin act, is logged as for every entity (#535).
- **R53** [✅] Lifecycle events emit through the notification pipeline.
  The archive emitted nothing at all: a member was never told an
  evaluation about them had been published. Publication notifies the
  subject, and transfer notifies the receiving coach.
- **R54** [✅] Withdrawing a published evaluation **notifies the
  subject**. An assessment that silently vanishes from a member's view
  leaves them with no account of what happened; the retraction is told
  plainly instead.

## Attachments

- **R55** [✅] If an evaluation carries media — a clip, a photo, a marked
  drill sheet — it links through the v2 media tables (#162), not the
  legacy upload record the archive used, with a link table of its own
  following the four that already exist. The link rules are
  `media_requirements.md` R95–R105.
- **R56a** [X] Media is **evidence for one answer**: its tag is the
  question's id. Attaching is refused unless the tag is a question of
  the evaluation's template that allows evidence and the file is an image,
  a video or a PDF → 422 `INVALID_EVIDENCE`; attaching, changing or
  detaching evidence once the evaluation is no longer a draft → 422
  `INVALID_STATE`. An answer may consist of evidence alone. Evidence reaches
  the member exactly when its question does: with a published evaluation,
  and never on a private item (R39). The `shared_` tag prefix is gone
  (#535).

- **R56d** [✅] The owner may **upload a file as evidence** for one
  question of a draft in a single step: the server stores it as a media item
  whose uploader is the member the evaluation is about, with the access
  roles `self`, `coach` and `admin`, and links it under the question's id.
  The member and staff can download it; nobody else can, and it is never
  public. The tag, file-kind and draft rules of R56a apply (#535).

- **R56b** [X] The subject sees shared media only once the evaluation is
  **published**, and asking earlier answers not-found rather than
  forbidden, for the same reason the evaluation body does (R38).

- **R56c** [✅] Per-file privacy (`accessRoles`) stays the **app's**
  decision, not the server's: attaching media to an evaluation leaves its
  access roles as they were. A club may legitimately want a clip public —
  to publish it on the website — so the server does not force evaluation
  media into a restricted role. Note the consequence: `accessRoles`
  defaults to public, and a public item downloads without logging in, so
  R56a governs *discovery* of an attachment through the evaluation, not
  access to the bytes; those follow the item's own access roles
  (`media_requirements.md` R25–R33).

- **R56** [✅] Evaluations **carry media from the first version**. A clip
  or a marked drill sheet is often the substance of an assessment rather
  than an ornament to it. This costs one link table and a tag now,
  following the four that already exist, which is cheaper than the
  migration that adding it later would need.

## Listing and paging

- **R57** [✅] A coach lists the evaluations they own, filtered by status
  and by the member they are for (#535).
- **R58** [✅] Listings can be filtered by scope: general only, or those
  belonging to a named event. Because R7 allows several reviews of one
  member in one scope — on other templates or over other periods — a
  filtered listing may legitimately return several, and ordering is stable rather than arbitrary: most recently
  created first.
- **R59** [✅] A member lists their own published evaluations, most
  recently published first.
- **R60** [✅] Listing is paginated using the current shared pagination
  contract, and the total counts the filtered set. The archive's staff
  listing computed its total by counting *every* evaluation in the
  system regardless of the filters applied.

## Member copy

- **R63** [✅] Publishing an evaluation stores its **member copy**: a PDF of
  the member projection — no private items — kept as an ordinary media
  item and linked to the evaluation under the tag `member_copy`. It is
  listed with the member's view of the evaluation's media, and the member
  and staff may download it; another member may not. Like the rest of the
  evaluation it reaches the member only while published (#535).
- **R63a** [✅] The effective owner can **preview** the member copy as a
  PDF at any status, draft included; a preview is generated on request and
  never stored. For anyone else the evaluation does not exist → not-found
  (R38a) (#535).
- **R63b** [✅] Publishing again — after withdrawing and reverting —
  **replaces** the member copy: the new PDF is linked, and the old one is
  detached and soft-deleted, so only the current copy is offered and the
  old file stays recoverable (#535).
- **R64** [✅] In the PDF the word *Evidence* links to each file of an
  answer. The page format and look are not specified here beyond one look
  per deployment, not per template (#535).
- **R64a** [✅] A written (Q & A) answer prints in its row of the
  question table, in layout order and handwritten like every other
  answer. Only the Q & A questions that close the layout at its top
  level — a run of them after the last section or other item — print
  after the table instead, each as its question over the written answer,
  or ruled lines when it is unanswered (#535).

---

## Product gating

- **R61** [✅] Evaluations ship **behind a product flag**. One codebase
  serves more than one club; one removed
  evaluations deliberately, another wants them. #302 argued that porting the
  feature already gated is cheaper than retrofitting a switch, and that
  argument was accepted.
- **R61a** [X] The setting is `EVALUATIONS_ENABLED`, supplied through the
  deploy conf's `EXTRA_ENV` passthrough alongside the rest of the product
  identity, exactly as `CREDIT_SYSTEM_ENABLED` is. It defaults to **false**,
  so an existing deployment that says nothing keeps today's behaviour. Every
  evaluation endpoint stays registered on every deployment; where the setting
  is false they all answer:

  ```
  503  {"detail": {"code": "EVALUATIONS_DISABLED",
                   "message": "Evaluations are not enabled on this deployment"}}
  ```

  `GET /v1/capabilities` reports it as `evaluations`, so a client learns the
  answer without provoking an error.

- **R61b** Introducing the setting moved the deployment contract, so
  `VERSION` took a **minor** bump (0.1.0 → 0.2.0) per `CONTRIBUTING.md`. A
  patch release may not introduce a setting: the installer re-asks only on
  minor or major, so a setting shipped under a patch reaches no existing
  deployment and the resulting 503s look exactly like nothing being wrong.
  The other half of the change belongs in the product deploy repo, which must
  declare the setting's tier and default — if the two drift, the upgrade
  either prompts for nothing or prompts for something this server ignores.

- **R62** When R61 was accepted the server had no feature-flag mechanism,
  so product configuration became a prerequisite of this work rather
  than something settled inside it. It now exists as the pattern R61a
  describes: one boolean setting per optional module, every route
  registered and answering 503 while off, and the module reported in the
  capabilities object — shared by credit, evaluations and event
  marketing.

---

## What is deliberately not specified

- **Answer semantics.** Nothing here says what a rating *means* or what
  scale a club should use. R10 says the server enforces whatever the
  template declares; choosing the questions is the club's business.
- **Aggregation and progress reporting.** R10 and R12b make it possible;
  how it is surfaced is a separate feature.
- **Self-evaluation and peer evaluation.** Out of scope. An evaluation
  has one writer at a time, and the writer is a coach.
- **Migrating earlier evaluations.** #535 replaces the category and score
  storage outright; no deployment held real evaluation data.
