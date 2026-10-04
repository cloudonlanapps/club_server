---
name: server-is-generic-fit-before-extending
description: "club_server stays club-neutral, but trivial schema changes (a JSON column, a field) are cheap and preferred over contorting data into existing fields; design output → model → server → client"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 87479688-31db-4c71-b78e-175e51b41441
  modified: 2026-10-01T05:29:06.457Z
---

club_server must stay generic: no club-specific or UI-specific *meaning* in it. But changing its structure is trivial, and a small schema change is preferred over squeezing data into fields meant for something else. Examples of the right kind of change: a JSON column for a form definition or for answers, or a proper field.

Server-side outputs, such as the member's PDF of a published evaluation, belong on the server, not in a client package.

Work in this order:
1. Finalize the output design. Use mockups, not code.
2. Derive the data model from that design.
3. Change the server to store the model directly.
4. Build the client against the stable server.

Discuss one question at a time.

**Why:** On 2026-09-29, the user said a first proposal that extended the server "went too far". I over-applied that as "never touch the server". A day of evaluations work (2026-09-30) then contorted SurveyJS data into existing fields:
- the form JSON in a template's `description`;
- the answers in `comment`;
- coach notes as JSON in `coach_note`, with a 4000-character cap issue.

That work also built the PDF client-side and reshaped the model four or five times. On 2026-10-01 the user reflected that the server changes should have been trivial, that the PDF is the server's job, and that we iterated on the design instead of finishing it first. See [[evaluations-design-surveyjs]].

**How to apply:** Explain how the server stores the data today. If the fit needs workarounds, propose the small server change instead. Do not build client code while the output design and the model are still moving. Do not fan out parallel implementation agents before decisions settle.
