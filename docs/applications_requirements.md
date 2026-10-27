# Applications — Requirements

This document is the testable specification for **applications**: printable
application forms built from admin-defined templates (#93). A member
downloads their own form as a PDF, filled from their profile (including the
extended profile of #92), prints it, puts a photo on it, signs it and hands
it in. No record is kept of who downloaded or handed in a form.

The design is `club_client/docs/applications_design.md` §2. The PDF — its
blocks, sizes, spacings and the fitting of page 1 — was settled in a
standalone prototype and is ported as it was. The label catalogue (the
form's fixed wording) and the clause library (sentences with gaps for a
template's facts) ship with the server in English, Marathi and Hindi; a
club adds its own texts beside them (#126). The editor pastes a text as
written, gaps and all; a template stores only its text, and the form fills
every gap from the template's facts when it is drawn.

Each rule's test names it with `@pytest.mark.requirement("applications:Rn")`,
and `tests/test_requirement_coverage.py` checks every marked rule has one.
Applications are an optional module: their tests switch it on per test.

"Admin" is a user with the admin role or the super admin. "Member" is any
signed-in user whose account is `registered`, `pending` or `active`
(approved); blocked and departed accounts are refused at sign-in.

## Module

- **R1** [✅] Applications are available only where both
  `APPLICATIONS_ENABLED` (off by default) and
  `IDENTITY_VERIFICATION_REQUIRED` are on; `GET /v1/capabilities` reports
  this as `applications`. Where they are not, every application route stays
  registered and answers 503 `APPLICATIONS_DISABLED`, as the evaluation and
  credit modules do.

## Templates

- **R2** [✅] An admin creates a template; it starts as a `draft`.
  Every field may be left for later. A coach or member → 403, anonymous →
  401, and nothing is created.
- **R3** [✅] An admin edits a draft: each field sent replaces the
  stored one, null clears it, and a field left out keeps its value. A saved
  or published template → 422 `INVALID_STATE`, and nothing is changed. A
  coach or member → 403.
- **R4** [✅] An admin deletes a draft or a saved template (soft):
  it is no longer listed or read (404 `APPLICATION_NOT_FOUND`). A published
  one → 422 `INVALID_STATE`, and it stays.
- **R5** [✅] An admin duplicates any template, in any state: the
  copy is a new draft with the same content and no layout; the source is
  unchanged.
- **R6** [✅] A template prints in `languages`: `["en"]` or `["en",
  <one other>]`, the other a language of the label catalogue → otherwise
  422. Every printed text is custom text (`{language: text}`); a header
  item may be a list of them, one per line. A library clause (`{"clause":
  …}`) or a fact (`{"fact": …}`) in any printed text, a rule or a note →
  422 (#107). The organisation, title, subtitle, header items, selections,
  declaration, footer line, terms (heading and body) and terms notes (text
  and attribution) may hold gaps, `{name}` with `name` matching
  `^[a-z][a-z0-9_]*$`, in any language; any other brace text is literal.
  They are stored exactly as written, and a draft's create and edit accept
  a gap naming no fact (#126). Label overrides, age-category labels and
  fact values print as written.
- **R6a** [✅] A fact is typed: `date` (`value`, a UTC-midnight
  date), `date_range` (`from`, `to`, the end not before the start),
  `time` (`value` "H:MM", 24-hour), `amount` (`value`, a whole number),
  `name` (`value`, text or `{language: text}` with English) or `choices`
  (`value`, a list of the library's choice ids). Any other shape → 422.
- **R7** [✅] An admin reads the clauses, `GET /v1/applications/clauses`:
  the library's languages and choices, and a list of the built-in clauses
  (`source: "builtin"`, `key`, their kind, gaps and the gaps' fact types,
  and text and heading, for a rule, in every language), then the club's
  texts (R7a; `source: "club"`, `id`, `name`), each text as written, gaps
  and all (#126). A fact fills a gap (R15a) in each language:
  a date as "19 November 2026" / "19 नोव्हेंबर 2026", a range as "1–15
  November 2026" / "1 ते 15 नोव्हेंबर 2026", a time with its part of the day
  ("6:00 am" / "सकाळी 6:00"), an amount with Indian grouping (1,50,000),
  choices joined with each language's "and", a name as typed or as given
  for the language.
- **R7a** [✅] An admin keeps the club's library of texts under
  `/v1/applications/library`: club-wide, offered in every template. Each
  text has a `kind` (a kind of the built-in clauses: `header`,
  `selection`, `declaration`, `rule`, `note`), a short `name` (1–100
  characters) and its `text` per catalogue language, gaps allowed; a rule
  may have a `heading`, any other kind → 422. An admin creates (201),
  lists, reads, edits (the fields sent replace the stored ones, null clears
  the heading; a result that is not a valid text → 422 and nothing changes)
  and deletes (204) them; an unknown one → 404
  `APPLICATION_LIBRARY_TEXT_NOT_FOUND`. Deleting a text changes no
  template. A coach or member → 403, anonymous → 401 (#126).
- **R8** [✅] An admin reads the label catalogue, every label in
  every catalogue language. A template's `labelOverrides` replace a label
  on its form; an id the catalogue does not have → 422.

- **R6b** [✅] An age category's `bornOnOrAfterUtc` must not be later
  than its `bornOnOrBeforeUtc` when both are given → 422, and nothing is
  stored. Categories may overlap (U12 inside U14): save accepts them.

## Saving and the lifecycle

- **R9** [✅] Save refuses a template whose title, declaration or
  footer line is missing, or whose title, declaration, footer line, terms, notes,
  selections or label overrides lack a print language (header items and
  age-category names may stay English only) → 422 `APPLICATION_INCOMPLETE`
  naming each text and the print languages it lacks (`missing: [{field,
  languages}]`; a text missing altogether lacks every print language); it
  stays a draft.
- **R9a** [X] `POST /v1/applications/clauses/fill`, which filled one
  library clause for the editor (#107), is removed → 404: the editor
  pastes a text as written and the form fills its gaps (#126).
- **R9b** [✅] Save refuses a template with a gap naming none of its
  facts, in any text of R6 and any language, → 422
  `APPLICATION_UNKNOWN_FACT` with `details.gaps`, a list of `{place,
  language, gap}` in the order printed (places: `organisation`, `title`,
  `subtitle`, `header[i]` — `header[i][j]` for line `j` of a header item
  that is a list of lines — `selections[i]`, `declaration`, `form_footer`,
  `terms[i].heading`, `terms[i].body`, `terms_notes[i].text`,
  `terms_notes[i].attribution`; within a place the print languages first,
  then the text's others; a gap repeated in one text once); it stays a
  draft. It is checked after R9's languages and before the fit (#126).
- **R12** [✅] An admin moves a template: save (draft → saved),
  publish (saved → published), unpublish (published → saved), revert
  (saved → draft). A move from any other state → 422 `INVALID_TRANSITION`,
  and the state is unchanged. A coach or member → 403.
- **R13** [✅] Save decides the layout and stores it on the
  template (`layout`: its name and page 1's spread with and without the
  guardian block), with a frozen copy of the club's profile options it was
  measured with, and of the library's formats and choices its facts were
  printed with (#126); revert drops them.
- **R14** [✅] A saved or published template is drawn only from its
  stored layout, its frozen profile options and its frozen fact formats,
  and is never measured again: a later change to the profile options
  (positions), the label catalogue or the clause library (its clauses,
  formats and choices) changes none of its forms (#96, #97, #107, #126). To pick up such a change the admin unpublishes, reverts, saves and
  publishes again. The fonts ship with the server; the fixtures' measured
  layouts are pinned by a test, so a font change that measures differently
  is caught before release. A draft's preview is measured on the spot
  (R19).

## Who sees what

- **R10** [✅] An admin lists and reads templates in every state
  (`?status=` narrows the list). A member who has uploaded an identity
  document lists and reads the published ones only: another state → 404
  `APPLICATION_NOT_FOUND`. A member without one is offered none: the list is
  empty, and reading one or its form → 422 `IDENTITY_DOCUMENT_REQUIRED`.
- **R11** [✅] A template with age categories gives its form only to
  a member who fits one of them (born on or after / on or before its
  cutoffs, of one of its genders): anyone else, and a member with no date
  of birth → 422 `APPLICATION_NOT_ELIGIBLE` ("not eligible for this
  application"). A template without categories gives it to every member.
- **R11a** [✅] A member's read of a published template carries
  `fittingCategories`, the indexes of the categories they fit (null on an
  admin's read). The form ticks one category, chosen with `?category=<index>`:
  a member who fits one needs no choice; one who fits several and names
  none → 422 `APPLICATION_CATEGORY_REQUIRED` with `details.categories`
  listing the ones they fit; a category they do not fit (or any, on a
  template without categories) → 422 `APPLICATION_CATEGORY_NOT_FITTING`
  with the same details. Nothing is recorded: the member chooses on every
  download.
- **R20** [✅] An admin downloads any member's form of a published
  template, choosing the category as the member does (R11a). A template not published → 422 `APPLICATION_NOT_PUBLISHED`; a
  user who does not exist → 404 `USER_NOT_FOUND`. Every check happens
  before publishing: the download of a published template by a member it
  is offered to never fails for the template's sake (#97).

## The PDF

- **R15** [✅] A form is an `application/pdf` named
  `application-<id>-<username>.pdf`, drawn per request: page 1 holds the
  header (organisation, title, subtitle in a column per print language,
  the header items), the member's personal information beside a photo box,
  their sports details, the additional selections, the declaration and the
  signature row with the download day in the club's time zone, and the
  footer line; the terms and notes follow. The footer of every page carries
  the member ID and the download day. The positions row ticks every
  position the member chose, and the preferred-language field lists every
  language they chose; a template is measured with every position ticked
  and every club language listed. A club position is named in each
  print language its label has, and in English alone when it lacks the
  other one; save is never refused for this.
- **R15a** [✅] The member's form, an admin's member form and the
  preview fill every gap naming a fact from the template's facts, in the
  text's own language as R7 prints them; a gap naming no fact (possible on
  a draft's preview only) prints as written. The layout decided on save
  (R16) is measured on the filled texts. Changing a fact changes every
  text using it on the next form drawn (#126).
- **R16** [✅] The layout is the first of "one page", "one page,
  tighter spacing" and "signing part on page 2" whose page 1 fits, page 1
  then spread to fill the page.
- **R17** [✅] Save is refused only when the details alone do not
  fit page 1 → 422 `APPLICATION_DOES_NOT_FIT` with the overflow in points.
- **R18** [✅] A member's answer stays within its lines: it is set
  smaller (down to 7 pt) and only then cut with "…", so the layout depends
  on the template alone.
- **R19** [✅] An admin previews a template in any state: the form
  drawn for a fictional demo member (`demo.player`, born inside the first
  age category, which is ticked, with a guardian), watermarked "PREVIEW" with "PREVIEW — not
  for submission" in the footer. A draft is measured on the spot; a saved or
  published template uses its stored layout. A coach or member → 403.
- **R21** [✅] The parent / guardian block, and the guardian's
  signature line, print for a member under 18 on the download day, or for
  anyone whose guardian is set.

## Publishing and tracing

- **R22** [✅] Each publish gives the template a new publish ID (a
  UUID): `publishId` on the template read and in the list, null unless the
  template is published. Unpublishing and publishing again replaces it
  (#97).
- **R23** [✅] Every page of a downloaded form prints its template's
  publish ID ("Form <publishId>") in small grey text, rotated, in the left
  margin, so a printed form can be matched to its template (#97).
- **R24** [✅] Save, publish, unpublish and revert are audited
  (`save_application`, `publish_application`, `unpublish_application`,
  `revert_application`; resource `application`) with `applicationId`; a
  publish also records its `publishId` and the `previousPublishId` (null on
  the first), an unpublish the `publishId` it withdraws (#97).
