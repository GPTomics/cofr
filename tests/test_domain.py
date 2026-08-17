from cofr.domain import (
    Artifact,
    Claim,
    Decision,
    DOMAIN_TYPES,
    Evidence,
    Experiment,
    OpenQuestion,
    Risk,
    from_dict,
    has_live_status,
    is_live,
    to_dict,
    validate_and_normalize,
)


def test_claim_defaults():
    c = Claim(id='claim_x')
    assert c.id == 'claim_x'
    assert c.status == 'provisionally_supported'
    assert c.confidence == 'medium'
    assert c.title == ''
    assert c.extra_sections == {}


def test_evidence_defaults():
    e = Evidence(id='ev_x')
    assert e.evidence_type == 'manual_observation'
    assert e.strength == 'medium'
    assert e.claim_links == []
    assert e.stale is False


def test_experiment_defaults():
    x = Experiment(id='exp_x')
    assert x.key_metrics == {}
    assert x.affected_claim_ids == []
    assert x.follow_on_questions == []


def test_decision_defaults():
    d = Decision(id='dec_x')
    assert d.based_on_evidence_ids == []
    assert d.depends_on_claim_ids == []


def test_open_question_defaults():
    q = OpenQuestion(id='q_x')
    assert q.priority == 'medium'
    assert q.status == 'open'
    assert q.related_claim_ids == []


def test_risk_defaults():
    r = Risk(id='risk_x')
    assert r.severity == 'medium'
    assert r.status == 'open'
    assert r.source == 'authored'


def test_artifact_minimal():
    a = Artifact(id='a_x', artifact_type='current_state', path='artifacts/current_state.md')
    assert a.covers_claim_ids == []
    assert a.staleness_status == 'current'


def test_claim_round_trip_to_dict_from_dict():
    original = Claim(
        id='claim_rt',
        title='Round-trip test',
        statement='Goes out, comes back.',
        status='supported',
        confidence='high',
        main_support='Strong evidence',
        what_would_change_my_mind='A clean counter-experiment.',
        extra_sections={'notes': 'side commentary'},
    )
    d = to_dict(original)
    rebuilt = from_dict('claim', d)
    assert rebuilt == original


def test_evidence_round_trip_with_claim_links():
    original = Evidence(
        id='ev_rt',
        evidence_type='experiment_result',
        summary='Round-trip test',
        strength='high',
        claim_links=[{'claim_id': 'claim_a', 'polarity': 'supports'}, {'claim_id': 'claim_b', 'polarity': 'opposes'}],
    )
    d = to_dict(original)
    rebuilt = from_dict('evidence', d)
    assert rebuilt == original


def test_validate_normalizes_invalid_status_to_default():
    c = Claim(id='claim_bad', status='not_a_real_status')
    validated, warnings = validate_and_normalize(c)
    assert validated.status == 'provisionally_supported'
    assert len(warnings) == 1
    assert 'not_a_real_status' in warnings[0]
    assert 'status' in warnings[0]


def test_validate_accepts_all_enum_values():
    for status in ('supported', 'provisionally_supported', 'mixed', 'unsupported', 'retired'):
        c = Claim(id=f'claim_{status}', status=status)
        validated, warnings = validate_and_normalize(c)
        assert validated.status == status
        assert warnings == []


def test_validate_evidence_strength_enum():
    e = Evidence(id='ev_bad', strength='very-strong')
    validated, warnings = validate_and_normalize(e)
    assert validated.strength == 'medium'
    assert len(warnings) == 1


def test_validate_open_question_priority_enum():
    q = OpenQuestion(id='q_bad', priority='urgent')
    validated, warnings = validate_and_normalize(q)
    assert validated.priority == 'medium'
    assert len(warnings) == 1


def test_validate_open_question_blocking_severity_defaults_to_priority():
    q = OpenQuestion(id='q_a', priority='high', blocking_severity='')
    validated, _ = validate_and_normalize(q)
    assert validated.blocking_severity == 'high'


def test_validate_open_question_critical_priority_maps_to_high_severity():
    q = OpenQuestion(id='q_a', priority='critical', blocking_severity='')
    validated, _ = validate_and_normalize(q)
    assert validated.blocking_severity == 'high'


def test_validate_open_question_explicit_blocking_severity_preserved():
    q = OpenQuestion(id='q_a', priority='high', blocking_severity='low')
    validated, _ = validate_and_normalize(q)
    assert validated.blocking_severity == 'low'


def test_validate_multiple_invalid_enums_returns_multiple_warnings():
    r = Risk(id='r_bad', severity='catastrophic', status='exploding', source='god_said_so')
    validated, warnings = validate_and_normalize(r)
    assert validated.severity == 'medium'
    assert validated.status == 'open'
    assert validated.source == 'authored'
    assert len(warnings) == 3


def test_domain_types_dict_complete():
    assert set(DOMAIN_TYPES.keys()) == {'claim', 'evidence', 'experiment', 'decision', 'question', 'risk'}
    assert DOMAIN_TYPES['claim'] is Claim
    assert DOMAIN_TYPES['evidence'] is Evidence
    assert DOMAIN_TYPES['experiment'] is Experiment
    assert DOMAIN_TYPES['decision'] is Decision
    assert DOMAIN_TYPES['question'] is OpenQuestion
    assert DOMAIN_TYPES['risk'] is Risk


def test_claim_dataclass_has_timeline_and_status_timeline_fields_with_empty_defaults():
    c = Claim(id='claim_x')
    assert c._timeline == []
    assert c._status_timeline == []


def test_evidence_dataclass_has_source_path_source_slug_source_title_source_anchors_fields():
    e = Evidence(id='ev_x')
    assert e.source_path == ''
    assert e.source_slug == ''
    assert e.source_title == ''
    assert e.source_anchors == []


def test_evidence_dataclass_has_status_field_defaulting_to_active():
    e = Evidence(id='ev_x')
    assert e.status == 'active'


def test_decision_dataclass_has_status_field_defaulting_to_active():
    d = Decision(id='dec_x')
    assert d.status == 'active'


def test_experiment_dataclass_has_status_field_defaulting_to_active():
    x = Experiment(id='exp_x')
    assert x.status == 'active'


def test_validate_and_normalize_rejects_unknown_status_on_evidence_decision_experiment():
    from cofr.domain import validate_and_normalize
    e = Evidence(id='ev_x', status='bogus')
    _, w = validate_and_normalize(e)
    assert e.status == 'active'
    assert len(w) >= 1
    d = Decision(id='dec_x', status='bogus')
    _, w = validate_and_normalize(d)
    assert d.status == 'active'
    x = Experiment(id='exp_x', status='bogus')
    _, w = validate_and_normalize(x)
    assert x.status == 'active'


def test_every_dataclass_has_unknown_fields_dict_with_empty_default():
    for cls in (Claim, Evidence, Decision, OpenQuestion, Risk, Experiment, Artifact):
        instance = cls(id='x')
        assert hasattr(instance, '_unknown_fields')
        assert instance._unknown_fields == {}


def test_evidence_has_preserved_user_fields_dict_with_empty_default():
    e = Evidence(id='ev_x')
    assert hasattr(e, '_preserved_user_fields')
    assert e._preserved_user_fields == {}


def test_from_dict_captures_unknown_frontmatter_keys_into_unknown_fields():
    raw = {'id': 'c1', 'title': 'X', 'my_custom_key': 'preserved-value', 'another_unknown': 42}
    rebuilt = from_dict('claim', raw)
    assert rebuilt._unknown_fields == {'my_custom_key': 'preserved-value', 'another_unknown': 42}


def test_confidence_to_numeric_mapping():
    from cofr.domain import confidence_to_numeric
    assert confidence_to_numeric('low') == 0.25
    assert confidence_to_numeric('medium') == 0.5
    assert confidence_to_numeric('high') == 0.75


def test_confidence_to_numeric_unknown_returns_none():
    from cofr.domain import confidence_to_numeric
    assert confidence_to_numeric('xxx') is None
    assert confidence_to_numeric('') is None
    assert confidence_to_numeric(None) is None


def test_is_live_dict_with_explicit_type_str_returns_false_for_retired_claim():
    rec = {'id': 'c1', 'status': 'retired', 'stale': False}
    assert is_live(rec, type_str='claim') is False


def test_is_live_dict_with_explicit_type_str_returns_false_for_deprecated_evidence():
    rec = {'id': 'e1', 'status': 'deprecated', 'stale': False}
    assert is_live(rec, type_str='evidence') is False


def test_is_live_dict_with_explicit_type_str_returns_false_for_resolved_question():
    rec = {'id': 'q1', 'status': 'resolved', 'stale': False}
    assert is_live(rec, type_str='question') is False


def test_is_live_dict_with_explicit_type_str_returns_false_for_mitigated_risk():
    rec = {'id': 'r1', 'status': 'mitigated', 'stale': False}
    assert is_live(rec, type_str='risk') is False


def test_is_live_dict_returns_false_when_stale_regardless_of_status():
    rec = {'id': 'c1', 'status': 'provisionally_supported', 'stale': True}
    assert is_live(rec, type_str='claim') is False


def test_has_live_status_ignores_stale_flag():
    rec = {'id': 'c1', 'status': 'provisionally_supported', 'stale': True}
    assert has_live_status(rec, type_str='claim') is True


def test_validate_and_normalize_normalizes_evidence_created_at_date_to_midnight_utc():
    obj = Evidence(id='ev_a', created_at='2026-02-28')
    obj, warnings = validate_and_normalize(obj)
    assert obj.created_at == '2026-02-28T00:00:00Z', f'date-only must normalize to midnight UTC; got {obj.created_at!r}'


def test_validate_and_normalize_accepts_full_iso_evidence_created_at():
    obj = Evidence(id='ev_a', created_at='2026-02-28T14:30:00Z')
    obj, _ = validate_and_normalize(obj)
    assert obj.created_at == '2026-02-28T14:30:00Z'


def test_validate_and_normalize_unparseable_evidence_created_at_becomes_none():
    obj = Evidence(id='ev_a', created_at='not-a-date')
    obj, warnings = validate_and_normalize(obj)
    assert obj.created_at is None, f'unparseable must become None; got {obj.created_at!r}'
    assert any('created_at' in w for w in warnings), f'expected created_at warning; got {warnings!r}'


def test_validate_and_normalize_empty_evidence_created_at_stays_falsy():
    obj = Evidence(id='ev_a', created_at='')
    obj, warnings = validate_and_normalize(obj)
    assert obj.created_at in (None, '')
    assert not any('created_at' in w for w in warnings), f'empty must be silent; got {warnings!r}'


def test_validate_and_normalize_normalizes_experiment_timestamp():
    obj = Experiment(id='exp_a', timestamp='2026-02-28')
    obj, _ = validate_and_normalize(obj)
    assert obj.timestamp == '2026-02-28T00:00:00Z'


def test_validate_and_normalize_normalizes_decision_timestamp():
    obj = Decision(id='dec_a', timestamp='2026-02-28')
    obj, _ = validate_and_normalize(obj)
    assert obj.timestamp == '2026-02-28T00:00:00Z'
