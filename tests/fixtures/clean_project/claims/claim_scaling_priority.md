---
type: claim
id: claim_scaling_priority
status: unsupported
confidence: low
owner: domen
---

## Title

Pure parameter scaling will resolve the eval regressions

## Statement

Doubling parameter count alone (without architectural changes) is sufficient to
close the v4 regression gap.

## Main support

Early scaling-curve extrapolation suggested the regression closes by 1.5x
parameter count.

## Main weakness

The 2x parameter ablation closed less than 10% of the v4 gap. The scaling-curve
extrapolation broke down before reaching the target slice.

## What would change my mind

A 2x or larger scaled run on a recent eval slice that closes the gap by >75%
without any architectural changes.
