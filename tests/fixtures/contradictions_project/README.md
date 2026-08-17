# contradictions_project fixture

A V3-pack project that deterministically plants the timestamp-independent M3
contradiction surfaces for golden tests:

- **Evidence conflict (rule 3)** -- `claim_conflict` has `ev_pro` (supports) and `ev_con` (opposes).
- **Orphaned assumption (rule 5)** -- `claim_orphan` has status `supported` with zero evidence.
- **Falsification review (rule 6)** -- `claim_falsifiable` has a `what_would_change_my_mind` and a linked evidence record.
- **User-authored risk** -- `risk_overstatement`.
- **Next-decision ranking** -- `q_critical` and `q_minor` exercise the scoring scaffold.

This fixture's golden also exercises the computed-risk channel: the rule-3 and
rule-5 hits project into the `## Computed risks` section of `contradictions.md`.

Rules 1, 2, and 4 depend on cofr-managed timestamps and cannot be planted in
static fixture files. They are covered by `tests/test_contradictions.py` unit
tests, by multi-refresh behavioral tests in `tests/test_integration.py`, and --
for the rendered `contradictions.md` output -- by the golden
`tests/golden/contradictions_rules_124.md`, locked from a two-refresh sequence
in `test_contradictions_rules_124_match_golden`.
