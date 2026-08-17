---
type: claim
id: claim_bad_yaml
status: provisionally_supported
confidence: medium
owner: domen
this is: not: valid: yaml: because: too: many: colons
---

## Title

Claim with malformed frontmatter

## Statement

This file's frontmatter cannot be parsed as YAML. cofr should classify it as
unstructured and emit a warning, not crash.
