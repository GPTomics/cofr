import json
import subprocess
import types
from unittest.mock import patch

from conftest import run_cofr


def test_show_questions_lists_open_questions_with_envelope(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: question\nid: q_open\nquestion: Is X true?\npriority: high\nstatus: open\n---\n\n## Question\n\nIs X true?\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'questions', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    qs = data['data']['questions']
    assert any(q['id'] == 'q_open' for q in qs)


def test_show_questions_filters_resolved_by_default(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: question\nid: q_done\nquestion: Done\nstatus: resolved\n---\n\n## Question\n\nDone.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'questions', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    qs = data['data']['questions']
    assert all(q['id'] != 'q_done' for q in qs)


def test_show_questions_with_all_returns_resolved(tmp_path):
    '''Plan: cofr show questions --all includes resolved/deprioritized/stale.'''
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: question\nid: q_done\nquestion: Done\nstatus: resolved\n---\n\n## Question\n\nDone.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'questions', '--all', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    qs = data['data']['questions']
    assert any(q['id'] == 'q_done' for q in qs)


def test_show_questions_summary_returns_slim_payload(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: question\nid: q_x\nquestion: Tell me?\npriority: high\nstatus: open\n---\n\n## Question\n\nTell me?\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'questions', '--json', '--summary', str(tmp_path)])
    data = json.loads(r.stdout)
    q = data['data']['questions'][0]
    assert 'question_summary' in q
    assert 'question' not in q  # full body stripped


def test_show_diff_json_wraps_sidecar(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'diff', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    assert 'diff' in data['data']
    assert 'added' in data['data']['diff']


def test_show_diff_refuses_when_no_sidecar(tmp_path):
    run_cofr(['init', str(tmp_path)])
    r = run_cofr(['show', 'diff', '--json', str(tmp_path)])
    assert r.returncode != 0


def test_show_overview_emits_structured_data(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'overview', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    d = data['data']
    assert 'project_summary' in d
    assert 'counts' in d['project_summary']
    assert 'claims' in d['project_summary']['counts']
    assert d['project_summary']['counts']['claims']['total'] == 1
    assert 'top_questions' in d
    assert 'top_risks' in d
    assert 'staleness_flags' in d


def test_show_overview_absent_sidecar_uses_note(tmp_path):
    run_cofr(['init', str(tmp_path)])
    r = run_cofr(['show', 'overview', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    assert data['data']['recent_changes'] is None or '_note' in data


def test_show_state_envelope_schema_version_stays_at_1(tmp_path):
    '''Plan: JSON_SCHEMA_VERSION stays at 1 throughout M2. Persisted state schema bumps to 2; envelope does not.'''
    run_cofr(['init', str(tmp_path)])
    r = run_cofr(['show', 'state', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    assert data['schema_version'] == 1


def test_state_on_disk_schema_is_2(tmp_path):
    run_cofr(['init', str(tmp_path)])
    from pathlib import Path
    on_disk = json.loads((Path(tmp_path) / '.cofr' / 'state.json').read_text())
    assert on_disk['schema_version'] == 2


def test_cofr_version_is_0_4_0(tmp_path):
    r = run_cofr(['--version'])
    assert '0.4.0' in r.stdout or '0.4.0' in r.stderr


def test_show_overview_markdown_includes_m2_briefing_sections(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_a\nconfidence: low\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'overview', str(tmp_path)])
    assert r.returncode == 0
    assert '## Recent changes' in r.stdout
    assert '## Confidence trends' in r.stdout
    assert '## Staleness flags' in r.stdout


def test_show_overview_counts_retired_claim_as_non_live(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_retired\nstatus: retired\n---\n\n## Title\n\nT.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'overview', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    counts = data['data']['project_summary']['counts']
    assert counts['claims']['total'] == 1
    assert counts['claims']['live'] == 0
    assert counts['claims']['non_live'] == 1


def test_cofr_show_questions_uses_is_live_helper_for_filter(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body_open = '---\ntype: question\nid: q_open\nquestion: Open?\nstatus: open\n---\n\n## Question\n\nOpen?\n'
    body_resolved = '---\ntype: question\nid: q_done\nquestion: Done?\nstatus: resolved\n---\n\n## Question\n\nDone?\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body_open, capture_output=True, text=True)
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body_resolved, capture_output=True, text=True)
    r = run_cofr(['show', 'questions', '--json', str(tmp_path)])
    data = json.loads(r.stdout)['data']['questions']
    ids = [q['id'] for q in data]
    assert 'q_open' in ids
    assert 'q_done' not in ids


def test_show_diff_json_includes_load_warnings(tmp_path):
    '''cofr show diff --json must include load_warnings in envelope._warnings.'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n')
    run_cofr(['refresh', str(tmp_path)])
    state_path = tmp_path / '.cofr' / 'state.json'
    state = json.loads(state_path.read_text())
    state['claims'].append({'id': 'bad', 'title': 'B', 'statement': 'S', 'confidence': 'invalid_enum_value'})
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    r = run_cofr(['show', 'diff', '--json', str(tmp_path)])
    if r.returncode == 0:
        env = json.loads(r.stdout)
        assert '_warnings' in env and env['_warnings'], 'load_warnings must surface in envelope._warnings'


def test_show_overview_emits_data_warnings(tmp_path):
    '''cofr show overview --json must surface internal warnings (config/semantic) in envelope.data.warnings.'''
    run_cofr(['init', str(tmp_path)])
    cfg = (tmp_path / '.cofr' / 'config.yaml')
    cfg_text = cfg.read_text()
    cfg_text = cfg_text.replace('timeline_min_entries: 20', 'timeline_min_entries: -5')
    cfg.write_text(cfg_text)
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n')
    run_cofr(['refresh', str(tmp_path)])
    r = run_cofr(['show', 'overview', '--json', str(tmp_path)])
    assert r.returncode in (0, 1), r.stderr
    env = json.loads(r.stdout)
    assert 'warnings' in env['data'], 'show overview must emit data.warnings for internal warnings'
    joined = ' '.join(env['data']['warnings'])
    assert 'timeline_min_entries' in joined


def test_top_questions_summary_word_boundary_truncation(tmp_path):
    '''top_questions question_summary should truncate at word boundary (not mid-word).'''
    run_cofr(['init', str(tmp_path)])
    long_q = 'word ' * 30
    body = f'---\ntype: question\nid: q_long\nquestion: |\n  {long_q}\npriority: high\nstatus: open\n---\n\n## Question\n\n{long_q}\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'overview', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    qsum = env['data']['top_questions'][0]['question_summary']
    assert not qsum.endswith('wor'), f'question_summary should not truncate mid-word: {qsum!r}'


def test_show_overview_returns_soft_warn_on_load_warnings(tmp_path):
    '''cofr show overview should return EXIT_SOFT_WARN when load_warnings present (consistent with other show commands).'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n')
    run_cofr(['refresh', str(tmp_path)])
    state_path = tmp_path / '.cofr' / 'state.json'
    state = json.loads(state_path.read_text())
    state['claims'].append({'id': 'bad', 'title': 'B', 'statement': 'S', 'confidence': 'invalid_enum'})
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    r = run_cofr(['show', 'overview', '--json', str(tmp_path)])
    assert r.returncode == 1, f'expected EXIT_SOFT_WARN on load_warnings; got {r.returncode}'


def test_show_questions_full_includes_related_claim_details_and_warn_exit(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_q\n  type: claim\n  title: Claim Q\n  statement: S\n')
    (tmp_path / 'questions.yaml').write_text('- id: q1\n  type: question\n  question: Q?\n  status: open\n  related_claim_ids:\n    - claim_q\n')
    run_cofr(['refresh', str(tmp_path)])
    r = run_cofr(['show', 'questions', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    assert env['data']['questions'][0]['related_claims'][0]['id'] == 'claim_q'
    state_path = tmp_path / '.cofr' / 'state.json'
    state = json.loads(state_path.read_text())
    state['open_questions'][0]['priority'] = 'invalid'
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    r2 = run_cofr(['show', 'questions', '--json', str(tmp_path)])
    assert r2.returncode == 1


def test_show_diff_corrupt_sidecar_returns_controlled_error(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'last_diff.json').write_text('{bad json')
    result = run_cofr(['show', 'diff', '--json', str(tmp_path)])
    assert result.returncode == 4
    assert 'last_diff.json' in result.stderr
    assert 'Traceback' not in result.stderr


def test_show_overview_corrupt_sidecar_returns_controlled_error(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'last_diff.json').write_text('{bad json')
    result = run_cofr(['show', 'overview', '--json', str(tmp_path)])
    assert result.returncode == 4
    assert 'last_diff.json' in result.stderr
    assert 'Traceback' not in result.stderr


def test_show_questions_empty_note_includes_all_flag_hint(tmp_path):
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['show', 'questions', str(tmp_path), '--json'])
    out = json.loads(result.stdout)
    note = out.get('_note', '')
    assert 'pass --all' in note, f'note missing --all hint: {note!r}'
    assert 'resolved/deprioritized/stale' in note, f'note missing deactivated-status hint: {note!r}'


def test_show_state_json_data_warnings_carries_config_warnings(tmp_path):
    run_cofr(['init', str(tmp_path)])
    cfg = tmp_path / '.cofr' / 'config.yaml'
    cfg.write_text('project_name: x\nexclude_patterns: 42\ntimeline_min_entries: 20\ntimeline_min_days: 180\n')
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['show', 'state', str(tmp_path), '--json'])
    out = json.loads(result.stdout)
    warnings = out['data'].get('warnings', [])
    assert any('exclude_patterns' in w for w in warnings), \
        f'show state --json data.warnings missing config validation; got {warnings!r}'


def test_show_claims_json_data_warnings_carries_config_warnings(tmp_path):
    run_cofr(['init', str(tmp_path)])
    cfg = tmp_path / '.cofr' / 'config.yaml'
    cfg.write_text('project_name: x\ntimeline_min_entries: -5\ntimeline_min_days: 180\n')
    (tmp_path / 'claims.yaml').write_text(
        '- id: c1\n  type: claim\n  title: T\n  statement: S\n  status: provisionally_supported\n  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['show', 'claims', str(tmp_path), '--json'])
    out = json.loads(result.stdout)
    warnings = out['data'].get('warnings', [])
    assert any('timeline_min_entries' in w for w in warnings), \
        f'show claims --json data.warnings missing config validation; got {warnings!r}'


def test_show_questions_json_data_warnings_carries_config_warnings(tmp_path):
    run_cofr(['init', str(tmp_path)])
    cfg = tmp_path / '.cofr' / 'config.yaml'
    cfg.write_text('project_name: x\ntimeline_min_days: not_an_int\n')
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['show', 'questions', str(tmp_path), '--json'])
    out = json.loads(result.stdout)
    warnings = out['data'].get('warnings', [])
    assert any('timeline_min_days' in w for w in warnings), \
        f'show questions --json data.warnings missing config validation; got {warnings!r}'


def test_show_diff_json_data_warnings_carries_config_warnings(tmp_path):
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    cfg = tmp_path / '.cofr' / 'config.yaml'
    cfg.write_text('project_name: x\ntimeline_min_entries: foo\n')
    result = run_cofr(['show', 'diff', str(tmp_path), '--json'])
    out = json.loads(result.stdout)
    warnings = out['data'].get('warnings', [])
    assert any('timeline_min_entries' in w for w in warnings), \
        f'show diff --json data.warnings missing config validation; got {warnings!r}'


def test_show_questions_json_empty_exits_one(tmp_path):
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['show', 'questions', '--json', str(tmp_path)])
    assert result.returncode == 1, f'help.txt:522 says empty result exits 1; got returncode={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}'
    env = json.loads(result.stdout)
    assert '_note' in env, 'empty result should still carry the _note signal'


def test_show_state_exposes_timeline_under_unprefixed_key(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_t1\nconfidence: low\n---\n\n## Title\n\nT.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['show', 'state', '--json', str(tmp_path)])
    data = json.loads(r.stdout)
    claim = [c for c in data['data']['claims'] if c['id'] == 'claim_t1'][0]
    assert 'timeline' in claim
    assert 'status_timeline' in claim
    assert '_timeline' not in claim


def test_show_risks_returns_authored_and_computed(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    r = run_cofr(['show', 'risks', '--json', str(contradictions_project_path)])
    risks = json.loads(r.stdout)['data']['risks']
    sources = {x['source'] for x in risks}
    assert 'authored' in sources
    assert 'computed' in sources
    assert any(x['id'] == 'risk_overstatement' for x in risks)
    assert any(x['id'].startswith('risk_computed_') for x in risks)


def test_show_risks_summary_slims(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    r = run_cofr(['show', 'risks', '--json', '--summary', str(contradictions_project_path)])
    risks = json.loads(r.stdout)['data']['risks']
    assert risks
    for x in risks:
        assert 'statement_summary' in x
        assert 'recommended_resolution' not in x


def test_show_risks_empty_note(tmp_path):
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    r = run_cofr(['show', 'risks', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    assert env['data']['risks'] == []
    assert '_note' in env


def test_show_risks_all_includes_resolved_authored(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'risks.yaml').write_text(
        '- id: risk_done\n  type: risk\n  statement: Handled risk.\n  status: resolved\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    default = json.loads(run_cofr(['show', 'risks', '--json', str(tmp_path)]).stdout)['data']['risks']
    assert all(x['id'] != 'risk_done' for x in default)
    with_all = json.loads(run_cofr(['show', 'risks', '--all', '--json', str(tmp_path)]).stdout)['data']['risks']
    assert any(x['id'] == 'risk_done' for x in with_all)


def test_show_contradictions_returns_rule_dict(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    r = run_cofr(['show', 'contradictions', '--json', str(contradictions_project_path)])
    data = json.loads(r.stdout)['data']
    c = data['contradictions']
    assert set(c) == {'claim_unchanged', 'decision_basis_eroded', 'evidence_conflict',
                      'eroded_confidence', 'orphaned_assumption'}
    assert any(x['claim_id'] == 'claim_conflict' for x in c['evidence_conflict'])
    assert any(x['claim_id'] == 'claim_orphan' for x in c['orphaned_assumption'])
    assert any(x['claim_id'] == 'claim_falsifiable' for x in data['falsification_review'])
    assert any(x['id'] == 'risk_overstatement' for x in data['authored_risks'])


def test_show_contradictions_json_includes_computed_risks(contradictions_project_path):
    '''Audit finding 1: show contradictions --json must carry computed_risks,
    matching the ## Computed risks section the rendered (non-JSON) artifact shows.'''
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    data = json.loads(run_cofr(['show', 'contradictions', '--json', str(contradictions_project_path)]).stdout)['data']
    computed = data['computed_risks']
    assert computed
    assert all(r['source'] == 'computed' for r in computed)
    ids = {r['id'] for r in computed}
    assert all(i.startswith('risk_computed_') for i in ids)
    body = run_cofr(['show', 'contradictions', str(contradictions_project_path)]).stdout
    for i in ids:
        assert i in body, f'computed risk {i} in --json but absent from the non-JSON artifact'


def test_show_contradictions_empty_note(tmp_path):
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    r = run_cofr(['show', 'contradictions', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    assert '_note' in env
    assert 'No contradictions detected' in env['_note']


def test_show_contradictions_non_json_prints_artifact(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    r = run_cofr(['show', 'contradictions', str(contradictions_project_path)])
    assert '# Contradictions' in r.stdout
    assert 'Falsification review' in r.stdout


def test_show_review_returns_top_decision(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    r = run_cofr(['show', 'review', '--json', str(contradictions_project_path)])
    data = json.loads(r.stdout)['data']
    assert data['top_decision']['id'] == 'q_critical'
    assert 'score_breakdown' in data['top_decision']
    assert data['ranking_rationale']


def test_show_review_empty_note(tmp_path):
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    r = run_cofr(['show', 'review', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    assert '_note' in env
    assert env['data']['top_decision'] is None


def test_show_risks_empty_note_does_not_misdirect_to_refresh(tmp_path):
    '''Bug #9: computed risks are recomputed live; the empty note must not tell the user to refresh.'''
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    env = json.loads(run_cofr(['show', 'risks', '--json', str(tmp_path)]).stdout)
    assert '_note' in env
    assert 'run cofr refresh' not in env['_note']
    assert 'type: risk' in env['_note']


def test_show_contradictions_exit_code_reflects_detection_warnings(tmp_path):
    '''Bug #7: a detection warning must drive exit code 1 even when the payload is non-empty.'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n  confidence: high\n')
    (tmp_path / 'evidences').mkdir()
    (tmp_path / 'evidences' / 'src.yaml').write_text(
        '- id: ev_pro\n  type: evidence\n  summary: pro\n  claim_links:\n    - claim_id: claim_a\n      polarity: supports\n'
        '- id: ev_con\n  type: evidence\n  summary: con\n  claim_links:\n    - claim_id: claim_a\n      polarity: opposes\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    state_path = tmp_path / '.cofr' / 'state.json'
    state = json.loads(state_path.read_text())
    for c in state['claims']:
        if c['id'] == 'claim_a':
            c['last_updated'] = 'not-a-timestamp'
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    r = run_cofr(['show', 'contradictions', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    assert any('not-a-timestamp' in w for w in env['data']['warnings'])
    assert r.returncode == 1, r.stderr


def test_show_review_non_json_prints_artifact(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    r = run_cofr(['show', 'review', str(contradictions_project_path)])
    assert '# Recommended next decision' in r.stdout


def test_show_contradictions_and_review_non_json_emit_detection_warnings(tmp_path):
    '''Bug #15: a detection warning is an exit-1 cause; non-JSON mode must surface it
    on stderr rather than leaving the exit code unexplained.'''
    run_cofr(['init', str(tmp_path)])
    state_path = tmp_path / '.cofr' / 'state.json'
    state = json.loads(state_path.read_text())
    state['claims'] = [{'id': 'claim_bad', 'type': 'claim', 'status': 'supported',
                        'confidence': 'high', 'last_updated': 'not-a-date'}]
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    for sub in (['show', 'contradictions'], ['show', 'review']):
        result = run_cofr([*sub, str(tmp_path)])
        assert result.returncode == 1, f"{' '.join(sub)}: rc={result.returncode} stderr={result.stderr!r}"
        assert 'unparseable timestamp' in result.stderr, \
            f"{' '.join(sub)} hid the detection warning from non-JSON output"


def test_m3_commands_surface_load_warnings_in_non_json_mode(tmp_path):
    '''Audit finding 1: load_state warnings (state.json enum normalization) drive
    exit code 1; non-JSON mode must surface them on stderr, not exit silently.'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n')
    run_cofr(['refresh', str(tmp_path)])
    state_path = tmp_path / '.cofr' / 'state.json'
    state = json.loads(state_path.read_text())
    for c in state['claims']:
        if c['id'] == 'claim_a':
            c['confidence'] = 'bogus_value'
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    for sub in (['show', 'risks'], ['show', 'contradictions'], ['show', 'review'], ['show', 'questions']):
        result = run_cofr([*sub, str(tmp_path)])
        assert result.returncode == 1, f"{' '.join(sub)}: rc={result.returncode}"
        assert 'bogus_value' in result.stderr, \
            f"{' '.join(sub)} dropped the load_state warning from non-JSON stderr"


def test_show_review_related_claims_excludes_non_live_claims(tmp_path):
    '''Audit finding 2: show review and show questions must agree on related_claims;
    a question's link to a retired (non-live) claim is excluded from both.'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_dead\n  type: claim\n  title: Dead\n  statement: S\n  status: retired\n'
    )
    (tmp_path / 'questions.yaml').write_text(
        '- id: q_a\n  type: question\n  question: Does it hold?\n  priority: high\n'
        '  blocking_severity: high\n  status: open\n  related_claim_ids:\n    - claim_dead\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    review = json.loads(run_cofr(['show', 'review', '--json', str(tmp_path)]).stdout)['data']
    questions = json.loads(run_cofr(['show', 'questions', '--json', str(tmp_path)]).stdout)['data']['questions']
    review_related = review['top_decision']['related_claims']
    q_related = next(q['related_claims'] for q in questions if q['id'] == 'q_a')
    assert review_related == q_related == []


def test_show_contradictions_help_documents_persisted_enum_exit_code():
    '''B1: show contradictions --help must list persisted-enum normalization as
    an exit-1 cause, the way every sibling show subcommand does.'''
    result = run_cofr(['show', 'contradictions', '--help'])
    assert result.returncode == 0
    assert 'persisted enums normalized' in result.stdout


def test_show_review_help_documents_persisted_enum_exit_code():
    '''B1: show review --help must list persisted-enum normalization as an
    exit-1 cause, the way every sibling show subcommand does.'''
    result = run_cofr(['show', 'review', '--help'])
    assert result.returncode == 0
    assert 'persisted enums normalized' in result.stdout


def test_show_contradictions_surfaces_computed_risk_warnings(tmp_path, capsys):
    '''B2: cmd_show_contradictions must surface computed-risk warnings the way
    cmd_show_risks does, not discard them with `_`.'''
    from cofr import cli
    run_cofr(['init', str(tmp_path)])
    run_cofr(['refresh', str(tmp_path)])
    args = types.SimpleNamespace(project_path=str(tmp_path), json=True)
    warning = "computed-risk id collision: 'risk_x' generated more than once; dropping duplicate"
    with patch('cofr.cli.compute_computed_risks', return_value=([], [warning])):
        rc = cli.cmd_show_contradictions(args)
    env = json.loads(capsys.readouterr().out)
    assert warning in env['data']['warnings']
    assert rc == 1
