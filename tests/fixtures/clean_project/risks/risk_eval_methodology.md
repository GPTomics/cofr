---
type: risk
id: risk_eval_methodology
severity: medium
status: open
---

## Statement

If the held-out eval methodology has a systematic flaw that masks distribution
shift, several v3/v4 comparisons (including the action-conditioning one) are
unreliable and would need to be redone with a different evaluation protocol.

## Related claims

- claim_action_conditioning

## Recommended resolution

Audit the held-out splitting procedure against the v3 and v4 input
distributions. If a systematic gap is found, freeze new claims that depend on
the current methodology and redesign the protocol.
