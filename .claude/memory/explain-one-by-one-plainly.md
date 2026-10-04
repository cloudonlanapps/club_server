---
name: explain-one-by-one-plainly
description: "When reviewing a list of findings with the user, take one point at a time in plain terms, define terms, and weigh how often a case really happens"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1324a390-ad15-44b1-afdc-b9ca348c172e
  modified: 2026-09-25T16:04:07.725Z
---

When going through findings or design gaps, present **one point at a time**, explain it in plain terms, let the user decide, then file it before moving to the next. Define any code term the first time (the user had to ask what "roster" and "bound credit" meant). Before proposing a design change for a corner case, say how often it actually happens and what breaks if nothing is done.

**Why:** On 2026-09-25 the user asked "explain in simple term", asked for definitions twice, and asked "are you overthinking with extreme corner case?" — which was right: two proposals targeted rare cases the server already handled. A first list of server fixes was also twice too long because it wasn't checked against the requirement docs.

**How to apply:** Check the repo's requirement docs before calling something a gap. Use a small worked example with numbers when explaining behaviour, and double-check the example itself (a statement example once missed a line).
