---
type: question
id: q_holdout_robustness
priority: high
status: open
---

## Question

Is the v3-to-v4 effect-size drop on action conditioning attributable to
distribution shift in the held-out slice, or to a genuine erosion of the
underlying improvement?

## Blocking impact

We cannot confidently promote action conditioning as a load-bearing technique
for production training until the v3/v4 gap is explained.

## Related claims

- claim_action_conditioning

## Minimum test

Construct a matched-distribution v4 subset (re-weighted to match v3's input
distribution on the three covariates we already track) and rerun the action /
no-action comparison. Cost: ~2 days, no new training runs.
