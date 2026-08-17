---
type: evidence
id: ev_broken_ref_error
evidence_type: manual_observation
strength: low
created_at: 2026-04-25
---

## Summary

This evidence references a claim that does not exist in the project. cofr
should ingest the evidence (forward references are allowed) and emit a
broken-reference warning that names the dangling claim_id.

## Affects claims

- claim_does_not_exist: supports
