---
type: decision
id: dec_deprioritize_scaling
timestamp: 2026-04-22
---

## Title

Deprioritize parameter scaling for v4 regression work

## Decision statement

We will not pursue further large-parameter ablations for the v4 regression
investigation this quarter. Architecture-side experiments take priority.

## Rationale

The 2x-scaling result (ev_scaling_failure) closed <10% of the gap, contradicting
the early scaling-curve extrapolation that drove the prior plan. Pure scaling no
longer looks like the highest-EV intervention.

## Depends on claims

- claim_scaling_priority

## Reopen conditions

A clean architecture-side win on v4 fails to close the gap, OR a new compute
allocation makes 4x+ scaling cheaply feasible.
