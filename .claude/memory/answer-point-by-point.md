---
name: answer-point-by-point
description: Answer each question separately and in order; never bury a contradiction in prose
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e281d1b3-f064-432d-9439-4637329baafb
  modified: 2026-09-07T03:10:54.732Z
---

When the user asks several things, answer each one separately and in order.
Do not wrap answers in prose that makes it hard to tell whether I am agreeing,
disagreeing, or contradicting something I said earlier. If an earlier answer
of mine was wrong, say so as its own line, not folded into a paragraph.

**Why:** the user said "you are violating our rule for point by point response
here. and making too confusing statement which makes it to understand if there
is any conflict in the response or not", and later "Please stop sending
proses". Twice in one session. My habit of writing a narrative around an answer
hid a real self-contradiction: I had asserted a design was intentional and then
said it was a bug, without ever marking the reversal.

**How to apply:** one heading or bold label per question, in the order asked.
Show code, payloads or tables instead of describing them. State reversals as
"I said X; that was wrong" on their own line. See [[verify-dont-assert]].
