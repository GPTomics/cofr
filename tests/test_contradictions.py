from cofr.synthesis import (
    compute_computed_risks, compute_contradictions, compute_falsification_review,
    generate_contradictions, generate_next_decision, rank_next_decisions,
)


def _state(**kw):
    base = {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []}
    base.update(kw)
    return base


def test_empty_state_returns_empty_rule_lists():
    contradictions, warnings = compute_contradictions(_state())
    assert contradictions == {
        'claim_unchanged': [], 'decision_basis_eroded': [],
        'evidence_conflict': [], 'eroded_confidence': [], 'orphaned_assumption': [],
    }
    assert warnings == []


def test_rule3_flags_opposite_polarity_on_same_claim():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    conflicts = contradictions['evidence_conflict']
    assert len(conflicts) == 1
    rec = conflicts[0]
    assert rec['rule'] == 'evidence_conflict'
    assert rec['claim_id'] == 'claim_a'
    assert rec['evidence_ids'] == ['ev_con', 'ev_pro']
    assert set(rec['cited_ids']) == {'claim_a', 'ev_pro', 'ev_con'}
    assert rec['reason']


def test_rule3_no_conflict_when_all_same_polarity():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_1', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_2', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['evidence_conflict'] == []


def test_rule3_excludes_non_live_evidence():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'deprecated', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['evidence_conflict'] == []


def test_rule3_groups_one_record_per_claim():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_p1', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_p2', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_c1', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    assert len(contradictions['evidence_conflict']) == 1
    assert contradictions['evidence_conflict'][0]['evidence_ids'] == ['ev_c1', 'ev_p1', 'ev_p2']


def test_rule3_does_not_fire_on_single_self_contradicting_evidence():
    '''Audit finding 4: one evidence record self-linking a claim with both
    polarities is not "two live Evidence objects" -- rule 3 must not fire, and
    no spurious computed risk is minted.'''
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[{'id': 'ev_x', 'status': 'active', 'claim_links': [
            {'claim_id': 'claim_a', 'polarity': 'supports'},
            {'claim_id': 'claim_a', 'polarity': 'opposes'},
        ]}],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['evidence_conflict'] == []
    risks, _ = compute_computed_risks(state)
    assert not any('evidence_conflict' in r['id'] for r in risks)


def test_rule3_still_fires_when_a_second_distinct_record_conflicts():
    '''Audit finding 4: a genuine conflict across two distinct records, even when
    one record self-links both polarities, must still fire.'''
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_x', 'status': 'active', 'claim_links': [
                {'claim_id': 'claim_a', 'polarity': 'supports'},
                {'claim_id': 'claim_a', 'polarity': 'opposes'},
            ]},
            {'id': 'ev_y', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    assert len(contradictions['evidence_conflict']) == 1
    assert contradictions['evidence_conflict'][0]['evidence_ids'] == ['ev_x', 'ev_y']


def test_rule4_flags_high_confidence_claim_with_newer_opposing_evidence():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'high', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[{'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]}],
    )
    contradictions, _ = compute_contradictions(state)
    eroded = contradictions['eroded_confidence']
    assert len(eroded) == 1
    assert eroded[0]['claim_id'] == 'claim_a'
    assert eroded[0]['evidence_ids'] == ['ev_con']
    assert eroded[0]['rule'] == 'eroded_confidence'
    assert set(eroded[0]['cited_ids']) == {'claim_a', 'ev_con'}


def test_rule4_no_flag_when_confidence_not_high():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'medium', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[{'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]}],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['eroded_confidence'] == []


def test_rule4_no_flag_when_opposing_evidence_older_than_claim():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'high', 'last_updated': '2026-05-01T00:00:00Z'}],
        evidence=[{'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]}],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['eroded_confidence'] == []


def test_rule4_ignores_supporting_evidence():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'high', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[{'id': 'ev_pro', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]}],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['eroded_confidence'] == []


def test_rule4_warns_on_unparseable_claim_timestamp():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'high', 'last_updated': 'not-a-date'}],
        evidence=[{'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]}],
    )
    contradictions, warnings = compute_contradictions(state)
    assert contradictions['eroded_confidence'] == []
    assert any('claim_a' in w for w in warnings)


def test_rule5_flags_supported_claim_with_zero_evidence():
    state = _state(claims=[{'id': 'claim_a', 'status': 'supported'}])
    contradictions, _ = compute_contradictions(state)
    orphaned = contradictions['orphaned_assumption']
    assert len(orphaned) == 1
    assert orphaned[0]['claim_id'] == 'claim_a'
    assert orphaned[0]['cited_ids'] == ['claim_a']
    assert orphaned[0]['rule'] == 'orphaned_assumption'


def test_rule5_no_flag_when_claim_has_evidence():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'supported'}],
        evidence=[{'id': 'ev_1', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]}],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['orphaned_assumption'] == []


def test_rule5_no_flag_when_status_not_supported():
    state = _state(claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}])
    contradictions, _ = compute_contradictions(state)
    assert contradictions['orphaned_assumption'] == []


def test_retired_claim_excluded_from_all_rules():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'retired', 'confidence': 'high', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['evidence_conflict'] == []
    assert contradictions['eroded_confidence'] == []
    assert contradictions['orphaned_assumption'] == []


def test_rules_1_and_2_carried_from_semantic_staleness():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[{'id': 'ev_new', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]}],
    )
    contradictions, _ = compute_contradictions(state)
    cu = contradictions['claim_unchanged']
    assert len(cu) == 1
    assert cu[0]['claim_id'] == 'claim_a'
    assert cu[0]['rule'] == 'claim_unchanged'
    assert set(cu[0]['cited_ids']) == {'claim_a', 'ev_new'}


def test_rule2_decision_basis_eroded_carried_with_cited_ids():
    state = _state(
        claims=[{
            'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'low',
            'last_updated': '2026-05-01T00:00:00Z',
            '_timeline': [
                {'t': '2026-01-01T00:00:00Z', 'c': 'high'},
                {'t': '2026-05-01T00:00:00Z', 'c': 'low'},
            ],
        }],
        decisions=[{'id': 'dec_a', 'status': 'active', 'timestamp': '2026-02-01T00:00:00Z',
                    'depends_on_claim_ids': ['claim_a']}],
    )
    contradictions, _ = compute_contradictions(state)
    dbe = contradictions['decision_basis_eroded']
    assert len(dbe) >= 1
    assert dbe[0]['decision_id'] == 'dec_a'
    assert dbe[0]['rule'] == 'decision_basis_eroded'
    assert set(dbe[0]['cited_ids']) == {'dec_a', 'claim_a'}


def test_claim_can_trigger_multiple_rules():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'supported', 'confidence': 'high', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'last_updated': '2026-02-01T00:00:00Z', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    assert len(contradictions['evidence_conflict']) == 1
    assert len(contradictions['eroded_confidence']) == 1
    assert len(contradictions['claim_unchanged']) == 1
    assert contradictions['orphaned_assumption'] == []


def test_records_sorted_by_anchor_id():
    state = _state(
        claims=[
            {'id': 'claim_z', 'status': 'supported'},
            {'id': 'claim_a', 'status': 'supported'},
        ],
    )
    contradictions, _ = compute_contradictions(state)
    assert [r['claim_id'] for r in contradictions['orphaned_assumption']] == ['claim_a', 'claim_z']


def test_falsification_review_includes_claim_with_wwcmm_and_evidence():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'title': 'A',
                 'statement': 'stmt', 'what_would_change_my_mind': 'an ablation showing no effect'}],
        evidence=[{'id': 'ev_1', 'status': 'active', 'strength': 'high', 'summary': 'finding',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]}],
    )
    review = compute_falsification_review(state)
    assert len(review) == 1
    rec = review[0]
    assert rec['claim_id'] == 'claim_a'
    assert rec['claim_title'] == 'A'
    assert rec['what_would_change_my_mind'] == 'an ablation showing no effect'
    assert rec['evidence'] == [{'id': 'ev_1', 'polarity': 'opposes', 'strength': 'high', 'summary': 'finding'}]
    assert 'verdict' not in rec
    assert 'falsified' not in rec


def test_falsification_review_excludes_claim_with_wwcmm_but_no_evidence():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'what_would_change_my_mind': 'something'}],
    )
    assert compute_falsification_review(state) == []


def test_falsification_review_excludes_claim_without_wwcmm():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'what_would_change_my_mind': ''}],
        evidence=[{'id': 'ev_1', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]}],
    )
    assert compute_falsification_review(state) == []


def test_falsification_review_excludes_non_live_claim():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'retired', 'what_would_change_my_mind': 'x'}],
        evidence=[{'id': 'ev_1', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]}],
    )
    assert compute_falsification_review(state) == []


def test_falsification_review_omits_non_live_evidence():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'what_would_change_my_mind': 'x'}],
        evidence=[
            {'id': 'ev_live', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_dead', 'status': 'deprecated', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    review = compute_falsification_review(state)
    assert [e['id'] for e in review[0]['evidence']] == ['ev_live']


def test_falsification_review_sorted_by_claim_id():
    state = _state(
        claims=[
            {'id': 'claim_z', 'status': 'provisionally_supported', 'what_would_change_my_mind': 'x'},
            {'id': 'claim_a', 'status': 'provisionally_supported', 'what_would_change_my_mind': 'y'},
        ],
        evidence=[
            {'id': 'ev_1', 'status': 'active', 'claim_links': [
                {'claim_id': 'claim_z', 'polarity': 'supports'},
                {'claim_id': 'claim_a', 'polarity': 'supports'},
            ]},
        ],
    )
    review = compute_falsification_review(state)
    assert [r['claim_id'] for r in review] == ['claim_a', 'claim_z']


def test_computed_risks_empty_when_no_contradictions():
    risks, warnings = compute_computed_risks(_state())
    assert risks == []
    assert warnings == []


def test_computed_risk_for_evidence_conflict_is_high_severity():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    risks, _ = compute_computed_risks(state)
    assert len(risks) == 1
    r = risks[0]
    assert r['source'] == 'computed'
    assert r['severity'] == 'high'
    assert r['status'] == 'open'
    assert r['related_claim_ids'] == ['claim_a']
    assert r['id'] == 'risk_computed_evidence_conflict_claim_a'
    assert r['statement']


def test_computed_risk_for_orphaned_assumption_is_low_severity():
    state = _state(claims=[{'id': 'claim_a', 'status': 'supported'}])
    risks, _ = compute_computed_risks(state)
    assert [r['severity'] for r in risks] == ['low']
    assert risks[0]['id'] == 'risk_computed_orphaned_assumption_claim_a'


def test_computed_risk_for_eroded_confidence_is_medium_severity():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'high', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[{'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]}],
    )
    risks, _ = compute_computed_risks(state)
    eroded = [r for r in risks if 'eroded_confidence' in r['id']]
    assert len(eroded) == 1
    assert eroded[0]['severity'] == 'medium'


def test_computed_risk_for_decision_basis_eroded_is_high_and_links_decision():
    state = _state(
        claims=[{
            'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'low',
            'last_updated': '2026-05-01T00:00:00Z',
            '_timeline': [
                {'t': '2026-01-01T00:00:00Z', 'c': 'high'},
                {'t': '2026-05-01T00:00:00Z', 'c': 'low'},
            ],
        }],
        decisions=[{'id': 'dec_a', 'status': 'active', 'timestamp': '2026-02-01T00:00:00Z',
                    'depends_on_claim_ids': ['claim_a']}],
    )
    risks, _ = compute_computed_risks(state)
    dbe = [r for r in risks if 'decision_basis_eroded' in r['id']]
    assert dbe
    assert dbe[0]['severity'] == 'high'
    assert dbe[0]['related_decision_ids'] == ['dec_a']
    assert dbe[0]['related_claim_ids'] == ['claim_a']


def test_computed_risk_ids_deterministic_and_unique():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'supported', 'confidence': 'high', 'last_updated': '2026-01-01T00:00:00Z'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'last_updated': '2026-02-01T00:00:00Z', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'last_updated': '2026-03-01T00:00:00Z', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    risks1, w1 = compute_computed_risks(state)
    risks2, _ = compute_computed_risks(state)
    ids = [r['id'] for r in risks1]
    assert ids == [r['id'] for r in risks2]
    assert len(ids) == len(set(ids))
    assert not any('collision' in w for w in w1)


def test_computed_risk_renders_with_render_risk_block():
    from cofr.synthesis import _render_risk_block
    state = _state(claims=[{'id': 'claim_a', 'status': 'supported'}])
    risks, _ = compute_computed_risks(state)
    block = _render_risk_block(risks[0])
    assert 'risk_computed_orphaned_assumption_claim_a' in block


def test_rank_next_decisions_empty_state():
    ranked, rationale, warnings = rank_next_decisions(_state())
    assert ranked == []
    assert rationale
    assert warnings == []


def test_rank_scores_open_question():
    state = _state(
        claims=[{'id': 'claim_w', 'status': 'mixed'}],
        open_questions=[{'id': 'q_a', 'status': 'open', 'priority': 'high',
                         'blocking_severity': 'high', 'related_claim_ids': ['claim_w']}],
    )
    ranked, _, _ = rank_next_decisions(state)
    assert len(ranked) == 1
    e = ranked[0]
    assert e['kind'] == 'question'
    assert e['id'] == 'q_a'
    assert e['score'] == 7
    assert e['score_breakdown'] == {'priority': 3, 'blocking_severity': 3, 'mixed_unsupported_claims': 1, 'stale_decision_bonus': 0}


def test_rank_excludes_in_progress_and_resolved_questions():
    state = _state(open_questions=[
        {'id': 'q_ip', 'status': 'in_progress', 'priority': 'high', 'blocking_severity': 'high'},
        {'id': 'q_done', 'status': 'resolved', 'priority': 'high', 'blocking_severity': 'high'},
    ])
    ranked, _, _ = rank_next_decisions(state)
    assert ranked == []


def test_rank_stale_decision_gets_plus_two():
    state = _state(
        claims=[{
            'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'low',
            'last_updated': '2026-05-01T00:00:00Z',
            '_timeline': [
                {'t': '2026-01-01T00:00:00Z', 'c': 'high'},
                {'t': '2026-05-01T00:00:00Z', 'c': 'low'},
            ],
        }],
        decisions=[{'id': 'dec_a', 'status': 'active', 'timestamp': '2026-02-01T00:00:00Z',
                    'depends_on_claim_ids': ['claim_a']}],
    )
    ranked, _, _ = rank_next_decisions(state)
    dec_entries = [e for e in ranked if e['kind'] == 'decision']
    assert len(dec_entries) == 1
    assert dec_entries[0]['id'] == 'dec_a'
    assert dec_entries[0]['score'] == 2
    assert dec_entries[0]['score_breakdown']['stale_decision_bonus'] == 2


def test_rank_sorted_by_score_then_id():
    state = _state(open_questions=[
        {'id': 'q_low', 'status': 'open', 'priority': 'low', 'blocking_severity': 'low'},
        {'id': 'q_high', 'status': 'open', 'priority': 'critical', 'blocking_severity': 'high'},
        {'id': 'q_also_low', 'status': 'open', 'priority': 'low', 'blocking_severity': 'low'},
    ])
    ranked, _, _ = rank_next_decisions(state)
    assert [e['id'] for e in ranked] == ['q_high', 'q_also_low', 'q_low']


def test_rank_rationale_lines_describe_formula():
    state = _state(open_questions=[{'id': 'q_a', 'status': 'open', 'priority': 'high', 'blocking_severity': 'medium'}])
    _, rationale, _ = rank_next_decisions(state)
    joined = '\n'.join(rationale)
    assert 'priority' in joined
    assert 'q_a' in joined


def test_generate_contradictions_empty_state():
    md = generate_contradictions(_state())
    assert 'Generated by cofr' in md
    assert '# Contradictions' in md
    assert 'No contradictions detected.' in md
    assert '## Falsification review' in md
    assert 'No user-authored risks.' in md
    assert md.endswith('\n')


def test_generate_contradictions_renders_evidence_conflict():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    md = generate_contradictions(state)
    assert '### Evidence conflicts' in md
    assert 'claim_a' in md
    assert 'No contradictions detected.' not in md


def test_generate_contradictions_renders_falsification_review():
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported', 'title': 'A',
                 'what_would_change_my_mind': 'a clean ablation'}],
        evidence=[{'id': 'ev_1', 'status': 'active', 'summary': 'finding',
                   'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]}],
    )
    md = generate_contradictions(state)
    assert 'a clean ablation' in md
    assert 'ev_1' in md
    assert 'cofr issues no verdict' in md


def test_generate_contradictions_renders_authored_risk():
    state = _state(risks=[{'id': 'risk_a', 'statement': 'overstated narrative',
                           'severity': 'high', 'status': 'open', 'source': 'authored'}])
    md = generate_contradictions(state)
    assert '## User-identified risks' in md
    assert 'risk_a' in md
    assert 'overstated narrative' in md


def test_generate_contradictions_deterministic():
    state = _state(claims=[{'id': 'claim_a', 'status': 'supported'}])
    assert generate_contradictions(state) == generate_contradictions(state)


def test_generate_next_decision_empty_state():
    md = generate_next_decision(_state())
    assert 'Generated by cofr' in md
    assert '# Recommended next decision' in md
    assert 'No decision to recommend' in md
    assert '## Ranking rationale' in md
    assert md.endswith('\n')


def test_generate_next_decision_renders_top_question():
    state = _state(open_questions=[{'id': 'q_a', 'status': 'open', 'priority': 'critical',
                                    'blocking_severity': 'high', 'question': 'Does it hold?'}])
    md = generate_next_decision(state)
    assert '## Top decision' in md
    assert 'q_a' in md
    assert 'Does it hold?' in md


def test_generate_next_decision_deterministic():
    state = _state(open_questions=[{'id': 'q_a', 'status': 'open', 'priority': 'high', 'blocking_severity': 'low'}])
    assert generate_next_decision(state) == generate_next_decision(state)


def _eroded_decision_state():
    return _state(
        claims=[{
            'id': 'claim_a', 'status': 'provisionally_supported', 'confidence': 'low',
            'last_updated': '2026-05-01T00:00:00Z',
            '_timeline': [
                {'t': '2026-01-01T00:00:00Z', 'c': 'high'},
                {'t': '2026-05-01T00:00:00Z', 'c': 'low'},
            ],
        }],
        decisions=[{'id': 'dec_a', 'status': 'active', 'title': 'Decision A',
                    'timestamp': '2026-02-01T00:00:00Z', 'depends_on_claim_ids': ['claim_a']}],
    )


def test_rule2_status_change_does_not_fire_on_lone_seed_timeline():
    '''Bug #3: a backdated decision must not flag status_change off the single seed entry.'''
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'supported',
                 '_status_timeline': [{'t': '2026-05-01T00:00:00Z', 's': 'supported'}]}],
        decisions=[{'id': 'dec_a', 'status': 'active', 'timestamp': '2026-01-01T00:00:00Z',
                    'depends_on_claim_ids': ['claim_a']}],
    )
    contradictions, _ = compute_contradictions(state)
    assert contradictions['decision_basis_eroded'] == []


def test_rule2_status_change_fires_on_genuine_post_decision_change():
    '''Bug #3: a real status change after the decision timestamp still fires.'''
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'mixed',
                 '_status_timeline': [
                     {'t': '2026-01-01T00:00:00Z', 's': 'supported'},
                     {'t': '2026-05-01T00:00:00Z', 's': 'mixed'},
                 ]}],
        decisions=[{'id': 'dec_a', 'status': 'active', 'timestamp': '2026-02-01T00:00:00Z',
                    'depends_on_claim_ids': ['claim_a']}],
    )
    contradictions, _ = compute_contradictions(state)
    sc = [r for r in contradictions['decision_basis_eroded'] if r['mode'] == 'status_change']
    assert len(sc) == 1
    assert sc[0]['historical_value'] == 'supported'
    assert sc[0]['current_value'] == 'mixed'


def test_duplicate_basis_claim_yields_single_contradiction_and_unique_risks():
    '''Bug #1: a duplicated depends_on_claim_ids entry must not double-count.'''
    state = _eroded_decision_state()
    state['decisions'][0]['depends_on_claim_ids'] = ['claim_a', 'claim_a']
    contradictions, _ = compute_contradictions(state)
    assert len(contradictions['decision_basis_eroded']) == 1
    risks, _ = compute_computed_risks(state)
    ids = [r['id'] for r in risks]
    assert len(ids) == len(set(ids))


def test_ranked_decision_entry_carries_eroded_basis():
    '''Bug #8: a decision-kind ranked entry must carry the eroded-basis records.'''
    ranked, _, _ = rank_next_decisions(_eroded_decision_state())
    dec = [e for e in ranked if e['kind'] == 'decision'][0]
    assert dec['eroded_basis']
    assert dec['eroded_basis'][0]['depended_on_claim_id'] == 'claim_a'


def test_next_decision_renders_stale_basis_for_decision_kind_top():
    '''Bug #8: a decision-kind top item must state which claim eroded and how.'''
    md = generate_next_decision(_eroded_decision_state())
    assert 'stale basis: claim claim_a (confidence_drop)' in md


def test_generate_contradictions_renders_computed_risks_section():
    '''Bug #10: contradictions.md renders computed Risk records in a source-labeled section.'''
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'claim_links': [{'claim_id': 'claim_a', 'polarity': 'opposes'}]},
        ],
    )
    md = generate_contradictions(state)
    assert '## Computed risks' in md
    computed_section = md.split('## Computed risks', 1)[1].split('## User-identified risks', 1)[0]
    risks, _ = compute_computed_risks(state)
    assert risks
    for r in risks:
        assert r['id'] in computed_section


def test_generate_contradictions_computed_risks_none_when_clean():
    '''Bug #10: the Computed risks section reads None when no contradictions are computed.'''
    md = generate_contradictions(_state())
    computed_section = md.split('## Computed risks', 1)[1].split('## User-identified risks', 1)[0]
    assert 'No computed risks.' in computed_section


def test_precomputed_contradictions_param_is_equivalent():
    '''Audit finding 5: passing a precomputed contradictions dict must produce
    output byte-identical to recomputing it internally.'''
    state = _state(
        claims=[{'id': 'claim_a', 'status': 'supported'},
                {'id': 'claim_b', 'status': 'provisionally_supported'}],
        evidence=[
            {'id': 'ev_pro', 'status': 'active', 'claim_links': [{'claim_id': 'claim_b', 'polarity': 'supports'}]},
            {'id': 'ev_con', 'status': 'active', 'claim_links': [{'claim_id': 'claim_b', 'polarity': 'opposes'}]},
        ],
        open_questions=[{'id': 'q_a', 'status': 'open', 'priority': 'high', 'blocking_severity': 'high'}],
    )
    contradictions, _ = compute_contradictions(state)
    assert generate_contradictions(state, contradictions=contradictions) == generate_contradictions(state)
    assert generate_next_decision(state, contradictions=contradictions) == generate_next_decision(state)
    risks_a, _ = compute_computed_risks(state, contradictions=contradictions)
    risks_b, _ = compute_computed_risks(state)
    assert risks_a == risks_b
    ranked_a, rat_a, _ = rank_next_decisions(state, contradictions=contradictions)
    ranked_b, rat_b, _ = rank_next_decisions(state)
    assert ranked_a == ranked_b and rat_a == rat_b


def test_generate_contradictions_reuses_precomputed_contradictions(monkeypatch):
    '''Audit finding 5: generate_contradictions given a precomputed contradictions
    dict must not recompute it.'''
    import cofr.synthesis as syn
    state = _state(claims=[{'id': 'claim_a', 'status': 'supported'}])
    contradictions, _ = compute_contradictions(state)
    real = syn.compute_contradictions
    calls = []
    monkeypatch.setattr(syn, 'compute_contradictions', lambda s: (calls.append(1), real(s))[1])
    syn.generate_contradictions(state, contradictions=contradictions)
    assert calls == [], 'generate_contradictions recomputed contradictions despite a precomputed arg'


def test_rank_next_decisions_live_filters_decision_basis_claims():
    '''B3: decision candidates must live-filter related_claims the same way
    question candidates do; the retired basis claim still surfaces via
    eroded_basis, so live-filtering related_claims loses nothing.'''
    state = _state(
        claims=[
            {'id': 'claim_dead', 'status': 'retired', 'confidence': 'high'},
            {'id': 'claim_live', 'status': 'supported', 'confidence': 'high'},
        ],
        decisions=[{'id': 'dec_a', 'status': 'active',
                    'depends_on_claim_ids': ['claim_dead', 'claim_live']}],
    )
    contradictions = {
        'claim_unchanged': [], 'evidence_conflict': [], 'eroded_confidence': [],
        'orphaned_assumption': [],
        'decision_basis_eroded': [{
            'rule': 'decision_basis_eroded', 'decision_id': 'dec_a',
            'depended_on_claim_id': 'claim_dead', 'mode': 'status_change', 'reason': 'basis eroded',
        }],
    }
    ranked, _, _ = rank_next_decisions(state, contradictions=contradictions)
    dec = next(e for e in ranked if e['kind'] == 'decision')
    assert [r['id'] for r in dec['related_claims']] == ['claim_live']
    assert dec['eroded_basis'][0]['depended_on_claim_id'] == 'claim_dead'


def test_contradiction_section_keys_match_rule_dict():
    '''B4: the section list and the rule dict must enumerate the same five rules
    from a single source, so a future sixth rule cannot desync them.'''
    from cofr.synthesis import _CONTRADICTION_RULES, _CONTRADICTION_SECTIONS
    contradictions, _ = compute_contradictions(_state())
    section_keys = {k for k, _ in _CONTRADICTION_SECTIONS}
    assert section_keys == set(_CONTRADICTION_RULES) == set(contradictions)
