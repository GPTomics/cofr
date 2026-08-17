---
type: hypothesis
id: hyp_unknown_type
---

## Title

Object with an unrecognized type

## Statement

`type: hypothesis` is not in cofr's known type set (claim, evidence, experiment,
decision, question, risk). cofr should emit a warning and skip the file, not
silently accept it.
