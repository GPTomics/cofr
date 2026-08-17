---
type: evidence
id: ev_dangling_e5
evidence_type: manual_observation
strength: medium
created_at: 2026-04-25
---

## Summary

E5 fixture: a single evidence file whose claim_links reference a claim that
does not exist in the project. Used to test that broken refs surface in
`refresh --json`, get listed in current_state.md, and resolve cleanly on a
subsequent refresh once the missing claim is added.

## Affects claims

- claim_e5_missing: supports
