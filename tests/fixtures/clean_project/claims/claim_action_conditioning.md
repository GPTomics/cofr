---
type: claim
id: claim_action_conditioning
status: provisionally_supported
confidence: medium
owner: domen
---

## Title

Action-conditioning improves predictive quality

## Statement

Conditioning the model's predictions on the upcoming action sequence improves
predictive quality on the validation slice relative to action-free baselines.

## Main support

Held-out evaluation runs on the v3 dataset show a consistent reduction in
prediction error when the model is given the action context. Two independent
replications reproduce the effect.

## Main weakness

The effect size on the held-out v4 slice is smaller than on v3 and one ablation
showed the gap narrowing under a different sampling regime.

## What would change my mind

A clean held-out evaluation showing no improvement from action conditioning at
v5 scale, or a controlled study showing the v3/v4 gap is attributable to
distribution shift rather than the action signal.
