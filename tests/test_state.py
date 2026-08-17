import json
import subprocess
from unittest.mock import patch

import pytest

from cofr.domain import Claim, Evidence
from cofr.state import (
    CofrNotInitialized,
    CorruptStateError,
    _MERGE_INTO_STATE_PROTECTED,
    _USER_FIELDS_COMPARE_EXCLUDED,
    _user_fields_equal,
    apply_parsed_records,
    atomic_write_json,
    atomic_write_text,
    default_config,
    init,
    load_config,
    load_index,
    load_state,
    save_index,
    save_state,
    snapshot_history,
    validate_references,
)
from conftest import run_cofr


def test_user_fields_compare_excluded_set_contents():
    assert 'first_seen' in _USER_FIELDS_COMPARE_EXCLUDED
    assert 'last_updated' in _USER_FIELDS_COMPARE_EXCLUDED
    assert 'parsed_from' in _USER_FIELDS_COMPARE_EXCLUDED
    assert 'stale' in _USER_FIELDS_COMPARE_EXCLUDED
    assert '_timeline' in _USER_FIELDS_COMPARE_EXCLUDED
    assert '_status_timeline' in _USER_FIELDS_COMPARE_EXCLUDED


def test_merge_into_state_protected_set_contents():
    assert 'first_seen' in _MERGE_INTO_STATE_PROTECTED
    assert 'last_updated' in _MERGE_INTO_STATE_PROTECTED
    assert '_timeline' in _MERGE_INTO_STATE_PROTECTED
    assert '_status_timeline' in _MERGE_INTO_STATE_PROTECTED
    assert 'parsed_from' not in _MERGE_INTO_STATE_PROTECTED
    assert 'stale' not in _MERGE_INTO_STATE_PROTECTED


def test_user_fields_equal_uses_user_fields_compare_excluded_set():
    a = {'id': 'c1', 'title': 'X', 'parsed_from': 'p1', 'first_seen': 't1', 'last_updated': 't2', 'stale': False}
    b = {'id': 'c1', 'title': 'X', 'parsed_from': 'p2', 'first_seen': 't9', 'last_updated': 't9', 'stale': True}
    assert _user_fields_equal(a, b) is True
    c = {'id': 'c1', 'title': 'DIFFERENT'}
    assert _user_fields_equal(a, c) is False


def test_init_creates_cofr_dir(tmp_path):
    init(tmp_path)
    cofr_dir = tmp_path / '.cofr'
    assert cofr_dir.is_dir()
    assert (cofr_dir / 'state.json').is_file()
    assert (cofr_dir / 'index.json').is_file()
    assert (cofr_dir / 'config.yaml').is_file()
    assert (cofr_dir / 'history').is_dir()
    assert (tmp_path / 'artifacts').is_dir()


def test_init_idempotent(tmp_path):
    init(tmp_path)
    state_path = tmp_path / '.cofr' / 'state.json'
    custom_content = '{"schema_version": 1, "marker": "untouched"}'
    state_path.write_text(custom_content)
    init(tmp_path)
    assert state_path.read_text() == custom_content


def test_default_config_has_required_keys():
    cfg = default_config()
    assert 'project_name' in cfg
    assert 'project_objective' in cfg
    assert 'exclude_patterns' in cfg
    assert cfg['project_objective'] == ''
    assert cfg['exclude_patterns'] == []


def test_save_load_state_round_trip(tmp_path):
    init(tmp_path)
    state = {
        'schema_version': 2,
        'cofr_version': '0.1.0',
        'last_refresh': '2026-05-13T14:30:00Z',
        'claims': [{'id': 'claim_a', 'title': 'A'}],
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }
    save_state(tmp_path, state)
    loaded, warnings = load_state(tmp_path)
    assert len(loaded['claims']) == 1
    assert loaded['claims'][0]['id'] == 'claim_a'
    assert loaded['claims'][0]['title'] == 'A'
    assert loaded['claims'][0]['status'] == 'provisionally_supported'
    assert loaded['schema_version'] == 2
    assert loaded['cofr_version'] == '0.4.0'
    assert warnings == []


def test_load_state_rejects_unknown_schema_version(tmp_path):
    init(tmp_path)
    bad = {
        'schema_version': 999,
        'cofr_version': '0.1.0',
        'claims': [], 'evidence': [], 'experiments': [], 'decisions': [],
        'open_questions': [], 'risks': [], 'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(bad))
    with pytest.raises(CorruptStateError):
        load_state(tmp_path)


def test_load_state_warns_on_invalid_artifact_enum(tmp_path):
    init(tmp_path)
    bad = {
        'schema_version': 2,
        'cofr_version': '0.1.0',
        'claims': [], 'evidence': [], 'experiments': [], 'decisions': [],
        'open_questions': [], 'risks': [],
        'artifacts': [{'id': 'art_x', 'artifact_type': 'bogus', 'path': 'artifacts/x.md', 'staleness_status': 'weird'}],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(bad))
    loaded, warnings = load_state(tmp_path)
    assert loaded['artifacts'][0]['artifact_type'] == 'current_state'
    assert loaded['artifacts'][0]['staleness_status'] == 'current'
    assert any('bogus' in w for w in warnings)
    assert any('weird' in w for w in warnings)


def test_load_state_warns_on_invalid_persisted_enum(tmp_path):
    init(tmp_path)
    bad = {
        'schema_version': 2,
        'cofr_version': '0.1.0',
        'claims': [{'id': 'claim_x', 'status': 'nonsense', 'confidence': 'medium'}],
        'evidence': [], 'experiments': [], 'decisions': [],
        'open_questions': [], 'risks': [], 'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(bad))
    loaded, warnings = load_state(tmp_path)
    assert loaded['claims'][0]['status'] == 'provisionally_supported'
    assert any('nonsense' in w for w in warnings)


def test_save_state_atomic_under_io_error(tmp_path):
    init(tmp_path)
    state_path = tmp_path / '.cofr' / 'state.json'
    original = state_path.read_text()
    bad_state = {'schema_version': 2, 'claims': [{'id': 'will_not_be_written'}]}
    with patch('cofr.state.os.replace', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            save_state(tmp_path, bad_state)
    assert state_path.read_text() == original


def test_snapshot_history_writes_timestamped_file(tmp_path):
    init(tmp_path)
    snap_path = snapshot_history(tmp_path)
    assert snap_path.is_file()
    assert snap_path.parent == tmp_path / '.cofr' / 'history'
    assert snap_path.name.endswith('.json')


def test_snapshot_history_no_overwrite_on_same_microsecond(tmp_path):
    init(tmp_path)
    a = snapshot_history(tmp_path)
    b = snapshot_history(tmp_path)
    assert a != b
    assert a.is_file()
    assert b.is_file()


def test_load_state_raises_when_not_initialized(tmp_path):
    with pytest.raises(CofrNotInitialized):
        load_state(tmp_path)


def test_load_state_raises_on_corrupt_json(tmp_path):
    init(tmp_path)
    (tmp_path / '.cofr' / 'state.json').write_text('not valid json {{{')
    with pytest.raises(CorruptStateError):
        load_state(tmp_path)


def test_atomic_write_text_creates_file(tmp_path):
    target = tmp_path / 'out.txt'
    atomic_write_text(target, 'hello\n')
    assert target.read_text() == 'hello\n'


def test_atomic_write_text_overwrites_existing(tmp_path):
    target = tmp_path / 'out.txt'
    target.write_text('old')
    atomic_write_text(target, 'new')
    assert target.read_text() == 'new'


def test_atomic_write_json_deterministic_ordering(tmp_path):
    target = tmp_path / 'data.json'
    atomic_write_json(target, {'b': 2, 'a': 1, 'c': 3})
    written = target.read_text()
    assert written.index('"a"') < written.index('"b"') < written.index('"c"')


def test_load_index_returns_empty_dict_when_fresh(tmp_path):
    init(tmp_path)
    idx = load_index(tmp_path)
    assert idx == {}


def test_save_load_index_round_trip(tmp_path):
    init(tmp_path)
    idx = {
        'notes/scratch.md': {
            'mtime': '2026-05-13T14:30:00Z',
            'size': 1234,
            'content_hash': 'abc123',
            'classification': 'unstructured',
            'extension': '.md',
            'frontmatter_type': None,
            'id_mentions': ['claim_a'],
        }
    }
    save_index(tmp_path, idx)
    assert load_index(tmp_path) == idx


def test_load_config_returns_defaults_when_fresh(tmp_path):
    init(tmp_path)
    cfg, warnings = load_config(tmp_path)
    assert cfg == {**default_config(), 'project_name': tmp_path.name}
    assert warnings == []


def test_initial_state_json_has_envelope_keys(tmp_path):
    init(tmp_path)
    state_path = tmp_path / '.cofr' / 'state.json'
    content = json.loads(state_path.read_text())
    for key in ('schema_version', 'cofr_version', 'last_refresh', 'claims', 'evidence', 'experiments', 'decisions', 'open_questions', 'risks', 'artifacts'):
        assert key in content


def _empty_state():
    return {
        'schema_version': 2,
        'cofr_version': '0.1.0',
        'claims': [],
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }


def test_apply_parsed_records_creates_new_object():
    state = _empty_state()
    claim = Claim(id='claim_a', title='A', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim], {'claims/a.md': {}})
    assert len(state['claims']) == 1
    rec = state['claims'][0]
    assert rec['id'] == 'claim_a'
    assert rec['title'] == 'A'
    assert rec['first_seen'] != ''
    assert rec['last_updated'] != ''
    assert rec['first_seen'] == rec['last_updated']


def test_apply_parsed_records_updates_existing_by_parsed_from():
    state = _empty_state()
    claim_v1 = Claim(id='claim_a', title='Old title', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim_v1], {'claims/a.md': {}})
    claim_v2 = Claim(id='claim_a', title='New title', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim_v2], {'claims/a.md': {}})
    assert len(state['claims']) == 1
    assert state['claims'][0]['title'] == 'New title'


def test_apply_parsed_records_marks_stale_when_file_deleted():
    state = _empty_state()
    claim = Claim(id='claim_a', title='A', parsed_from='claims/a.md')
    evidence = Evidence(id='ev_a', summary='E', parsed_from='evidence/a.md')
    apply_parsed_records(state, [claim, evidence], {'claims/a.md': {}, 'evidence/a.md': {}})
    apply_parsed_records(state, [], {})
    assert state['claims'][0]['stale'] is False
    assert state['claims'][0]['status'] == 'retired'
    assert state['claims'][0]['source_missing'] is True
    assert state['evidence'][0]['stale'] is True


def test_apply_parsed_records_preserves_timestamps_when_unchanged():
    state = _empty_state()
    claim = Claim(id='claim_a', title='A', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim], {'claims/a.md': {}})
    first_seen_initial = state['claims'][0]['first_seen']
    last_updated_initial = state['claims'][0]['last_updated']
    claim_again = Claim(id='claim_a', title='A', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim_again], {'claims/a.md': {}})
    assert state['claims'][0]['first_seen'] == first_seen_initial
    assert state['claims'][0]['last_updated'] == last_updated_initial


def test_apply_parsed_records_bumps_last_updated_only_on_field_change():
    import time
    state = _empty_state()
    claim = Claim(id='claim_a', title='Old', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim], {'claims/a.md': {}})
    first_seen_initial = state['claims'][0]['first_seen']
    last_updated_initial = state['claims'][0]['last_updated']
    time.sleep(1.1)
    claim_changed = Claim(id='claim_a', title='New', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim_changed], {'claims/a.md': {}})
    assert state['claims'][0]['first_seen'] == first_seen_initial
    assert state['claims'][0]['last_updated'] != last_updated_initial


def test_apply_parsed_records_revives_stale_when_file_returns():
    state = _empty_state()
    claim = Claim(id='claim_a', title='A', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim], {'claims/a.md': {}})
    apply_parsed_records(state, [], {})
    assert state['claims'][0]['stale'] is False
    assert state['claims'][0]['status'] == 'retired'
    assert state['claims'][0]['source_missing'] is True
    claim_returns = Claim(id='claim_a', title='A', parsed_from='claims/a.md')
    apply_parsed_records(state, [claim_returns], {'claims/a.md': {}})
    assert state['claims'][0]['stale'] is False
    assert state['claims'][0]['status'] == 'provisionally_supported'
    assert state['claims'][0]['source_missing'] is False


def test_load_config_returns_tuple_cfg_and_warnings(tmp_path):
    init(tmp_path)
    cfg, warnings = load_config(tmp_path)
    assert isinstance(cfg, dict)
    assert isinstance(warnings, list)
    assert warnings == []


def test_load_config_reads_timeline_min_entries_when_present(tmp_path):
    init(tmp_path)
    cfg_path = tmp_path / '.cofr' / 'config.yaml'
    cfg_path.write_text('timeline_min_entries: 5\ntimeline_min_days: 30\n')
    cfg, warnings = load_config(tmp_path)
    assert cfg['timeline_min_entries'] == 5
    assert cfg['timeline_min_days'] == 30


def test_load_config_warns_on_non_integer_timeline_min_entries(tmp_path):
    init(tmp_path)
    cfg_path = tmp_path / '.cofr' / 'config.yaml'
    cfg_path.write_text('timeline_min_entries: "five"\n')
    cfg, warnings = load_config(tmp_path)
    assert cfg['timeline_min_entries'] == 20
    assert any('timeline_min_entries' in w for w in warnings)


def test_init_writes_timeline_keys_to_config_yaml(tmp_path):
    init(tmp_path)
    cfg_text = (tmp_path / '.cofr' / 'config.yaml').read_text()
    assert 'timeline_min_entries' in cfg_text
    assert 'timeline_min_days' in cfg_text


def test_validate_references_skips_retired_claim():
    state = {
        'claims': [{'id': 'claim_ret', 'status': 'retired', 'stale': False, 'depends_on_claim_ids': [], 'related_claim_ids': ['claim_missing']}],
        'evidence': [], 'experiments': [], 'decisions': [],
        'open_questions': [], 'risks': [],
    }
    broken = validate_references(state)
    refs = [b for b in broken if b['from_id'] == 'claim_ret']
    assert refs == []


def test_live_reference_to_retired_target_is_broken_reference():
    state = {
        'claims': [{'id': 'claim_old', 'status': 'retired'}],
        'evidence': [{'id': 'ev1', 'status': 'active', 'claim_links': [{'claim_id': 'claim_old', 'polarity': 'supports'}]}],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
    }
    broken = validate_references(state)
    assert broken == [{'from_id': 'ev1', 'missing_id': 'claim_old', 'field_name': 'claim_links'}]


def test_based_on_evidence_ids_accepts_experiment_id_as_valid_reference():
    state = {
        'claims': [],
        'evidence': [],
        'experiments': [{'id': 'exp_x', 'status': 'active'}],
        'decisions': [{'id': 'dec_a', 'status': 'active', 'based_on_evidence_ids': ['exp_x']}],
        'open_questions': [],
        'risks': [],
    }
    broken = validate_references(state)
    assert broken == [], f'based_on_evidence_ids must accept Experiment ids per plan (existence-only check); got {broken!r}'


def test_based_on_evidence_ids_still_reports_genuinely_missing_id():
    state = {
        'claims': [],
        'evidence': [{'id': 'ev_known', 'status': 'active'}],
        'experiments': [],
        'decisions': [{'id': 'dec_a', 'status': 'active', 'based_on_evidence_ids': ['nope_missing']}],
        'open_questions': [],
        'risks': [],
    }
    broken = validate_references(state)
    assert broken == [{'from_id': 'dec_a', 'missing_id': 'nope_missing', 'field_name': 'based_on_evidence_ids'}]


def test_based_on_evidence_ids_accepts_any_live_object_type(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_a\n  type: claim\n  title: T\n  statement: S\n  status: supported\n  confidence: high\n'
    )
    (tmp_path / 'experiments.yaml').write_text(
        '- id: exp_a\n  type: experiment\n  name: N\n  intent: I\n  result_summary: R\n  status: active\n  timestamp: 2026-01-01\n'
    )
    (tmp_path / 'decisions.yaml').write_text(
        '- id: dec_a\n  type: decision\n  title: T\n  decision_statement: DS\n  based_on_evidence_ids:\n    - exp_a\n  depends_on_claim_ids:\n    - claim_a\n  status: active\n  timestamp: 2026-01-15\n'
    )
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    broken = env['data']['broken_references']
    assert broken == [], f'live decision pointing at live experiment via based_on_evidence_ids must not be broken; got {broken!r}'


def test_typed_reference_validation_rejects_wrong_target_type(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'evidences').mkdir()
    (tmp_path / 'evidences' / 'note.yaml').write_text(
        '- id: ev_a\n  type: evidence\n  summary: S\n  status: active\n  data_source: note.md\n  source_path: note.md\n  source_slug: note\n  source_title: N\n'
    )
    (tmp_path / 'note.md').write_text('note\n')
    (tmp_path / 'decisions.yaml').write_text(
        '- id: dec_wrong_ref\n'
        '  type: decision\n'
        '  title: D\n'
        '  depends_on_claim_ids:\n'
        '    - ev_a\n'
    )
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    assert result.returncode == 1
    warnings = json.loads(result.stdout)['data']['warnings']
    assert any('depends_on_claim_ids' in w and 'ev_a' in w for w in warnings), warnings


def test_load_state_collection_shape_error_is_corrupt_state(tmp_path):
    run_cofr(['init', str(tmp_path)])
    bad_state = {
        'schema_version': 2,
        'claims': {'not': 'a list'},
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(bad_state))
    result = run_cofr(['show', 'state', str(tmp_path)])
    assert result.returncode == 4
    assert 'corrupt cofr state' in result.stderr.lower()
    assert 'Traceback' not in result.stderr


def test_broken_reference_warning_text_uses_parsed_from_prefix(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_a\n  type: claim\n  title: A\n  statement: S\n  status: provisionally_supported\n  confidence: medium\n'
    )
    (tmp_path / 'decisions.yaml').write_text(
        '- id: dec_x\n  type: decision\n  title: D\n  status: active\n'
        '  depends_on_claim_ids:\n    - claim_missing\n'
    )
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    out = json.loads(result.stdout)
    warnings = out['data']['warnings']
    assert any(w.startswith('decisions.yaml#dec_x:') for w in warnings), \
        f'broken-reference warning missing parsed_from prefix; got {warnings!r}'


def test_malformed_config_yaml_does_not_crash_refresh(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'config.yaml').write_text(': : :\nnot a mapping\n  - one\n  - two\n')
    (tmp_path / 'note.md').write_text('# note\n')
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    assert 'Traceback' not in result.stderr, f'load_config raised: {result.stderr}'
    assert result.returncode in (0, 1), f'unexpected exit {result.returncode}: {result.stderr}'
    env = json.loads(result.stdout)
    warnings = env['data']['warnings']
    assert any('config.yaml' in w.lower() for w in warnings), f'no config warning emitted: {warnings}'


def test_malformed_config_yaml_non_mapping_does_not_crash(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'config.yaml').write_text('- a\n- b\n- c\n')
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    assert 'Traceback' not in result.stderr
    assert result.returncode in (0, 1)
    env = json.loads(result.stdout)
    assert any('config.yaml' in w.lower() for w in env['data']['warnings'])


def test_idless_markdown_preserves_state_id_on_path_match(tmp_path):
    '''Idless markdown re-ingested at the same path keeps its existing state id (no UUID churn).'''
    run_cofr(['init', str(tmp_path)])
    md_dir = tmp_path / 'notes'
    md_dir.mkdir()
    md_path = md_dir / 'observation.md'
    md_path.write_text('---\ntype: claim\ntitle: Obs\n---\n\n## Title\n\nObs.\n\n## Statement\n\nS.\n')
    r1 = run_cofr(['refresh', str(tmp_path)])
    assert r1.returncode in (0, 1)
    state1 = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    claim1 = next((c for c in state1['claims'] if c.get('parsed_from') == 'notes/observation.md'), None)
    assert claim1 is not None, 'first refresh should ingest the idless markdown'
    id_after_first = claim1['id']
    r2 = run_cofr(['refresh', str(tmp_path)])
    assert r2.returncode in (0, 1)
    state2 = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    matching = [c for c in state2['claims'] if c.get('parsed_from') == 'notes/observation.md']
    assert len(matching) == 1
    assert matching[0]['id'] == id_after_first, f'idless md id must be preserved: was {id_after_first!r}, now {matching[0]["id"]!r}'


def test_same_id_relocation_appends_timeline_on_confidence_change(tmp_path):
    '''Same-id relocation that ALSO changes confidence must append a timeline entry.'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n  confidence: low\n')
    run_cofr(['refresh', str(tmp_path)])
    state1 = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    timeline_len_1 = len(next(c for c in state1['claims'] if c['id'] == 'claim_a')['_timeline'])
    (tmp_path / 'claims.yaml').write_text('')
    new_dir = tmp_path / 'subdir'
    new_dir.mkdir()
    (new_dir / 'other.yaml').write_text('')
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n  confidence: high\n')
    run_cofr(['refresh', str(tmp_path)])
    state2 = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    timeline_2 = next(c for c in state2['claims'] if c['id'] == 'claim_a')['_timeline']
    assert len(timeline_2) > timeline_len_1, 'confidence change should append timeline entry'


def test_timeline_first_entry_seeds_on_new_claim(tmp_path):
    init(tmp_path)
    state = {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []}
    c = Claim(id='c1', confidence='medium', parsed_from='claims.yaml#c1')
    apply_parsed_records(state, [c], {'claims.yaml': {}}, config={}, packs_parsed_successfully={'claims.yaml'})
    assert len(state['claims']) == 1
    rec = state['claims'][0]
    assert len(rec.get('_timeline', [])) == 1
    assert rec['_timeline'][0]['c'] == 'medium'


def test_status_timeline_first_entry_seeds_on_new_claim(tmp_path):
    init(tmp_path)
    state = {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []}
    c = Claim(id='c1', status='provisionally_supported', parsed_from='claims.yaml#c1')
    apply_parsed_records(state, [c], {'claims.yaml': {}}, config={}, packs_parsed_successfully={'claims.yaml'})
    rec = state['claims'][0]
    assert len(rec.get('_status_timeline', [])) == 1
    assert rec['_status_timeline'][0]['s'] == 'provisionally_supported'


def test_timeline_appends_only_on_confidence_change(tmp_path):
    init(tmp_path)
    state = {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []}
    c1 = Claim(id='c1', confidence='medium', parsed_from='claims.yaml#c1')
    apply_parsed_records(state, [c1], {'claims.yaml': {}}, config={}, packs_parsed_successfully={'claims.yaml'})
    c1 = Claim(id='c1', confidence='medium', parsed_from='claims.yaml#c1')
    apply_parsed_records(state, [c1], {'claims.yaml': {}}, config={}, packs_parsed_successfully={'claims.yaml'})
    assert len(state['claims'][0]['_timeline']) == 1
    c1 = Claim(id='c1', confidence='high', parsed_from='claims.yaml#c1', title='change')
    apply_parsed_records(state, [c1], {'claims.yaml': {}}, config={}, packs_parsed_successfully={'claims.yaml'})
    assert len(state['claims'][0]['_timeline']) == 2
    assert state['claims'][0]['_timeline'][-1]['c'] == 'high'


def test_status_timeline_appends_only_on_status_change(tmp_path):
    init(tmp_path)
    state = {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []}
    c1 = Claim(id='c1', status='provisionally_supported', parsed_from='claims.yaml#c1')
    apply_parsed_records(state, [c1], {'claims.yaml': {}}, config={}, packs_parsed_successfully={'claims.yaml'})
    assert len(state['claims'][0]['_status_timeline']) == 1
    c2 = Claim(id='c1', status='supported', parsed_from='claims.yaml#c1', title='change')
    apply_parsed_records(state, [c2], {'claims.yaml': {}}, config={}, packs_parsed_successfully={'claims.yaml'})
    assert len(state['claims'][0]['_status_timeline']) == 2
    assert state['claims'][0]['_status_timeline'][-1]['s'] == 'supported'


def test_state_json_on_disk_stores_underscore_prefixed_timeline(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_disk\nconfidence: medium\n---\n\n## Title\n\nT.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    on_disk = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    rec = [c for c in on_disk['claims'] if c['id'] == 'claim_disk'][0]
    assert '_timeline' in rec
