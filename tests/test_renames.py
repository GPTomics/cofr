import json
import subprocess
from pathlib import Path

import pytest

from cofr.domain import Claim, Decision, Evidence
from cofr.renames import (
    _pre_load_pending_pack_fixup,
    append_renames_log,
    apply_rename_cascade,
    clear_pending_renames,
    compute_fingerprint,
    detect_renames,
    load_pending_renames,
    write_pending_renames,
)


from conftest import run_cofr


def _init_project(tmp_path):
    subprocess.run(['cofr', 'init', str(tmp_path)], capture_output=True, text=True)


def test_compute_fingerprint_excludes_id_and_timestamps():
    c1 = Claim(id='claim_a', title='X', statement='S', confidence='medium', last_updated='2026-01-01', first_seen='2026-01-01')
    c2 = Claim(id='claim_b', title='X', statement='S', confidence='medium', last_updated='2099-01-01', first_seen='2099-01-01')
    assert compute_fingerprint(c1, 'claim') == compute_fingerprint(c2, 'claim')


def test_fingerprint_claim_includes_title_statement():
    c1 = Claim(id='a', title='X', statement='S')
    c2 = Claim(id='a', title='Different', statement='S')
    assert compute_fingerprint(c1, 'claim') != compute_fingerprint(c2, 'claim')


def test_fingerprint_excludes_typed_references():
    c1 = Evidence(id='e1', summary='ev', claim_links=[{'claim_id': 'claim_a', 'polarity': 'supports'}])
    c2 = Evidence(id='e1', summary='ev', claim_links=[{'claim_id': 'claim_b', 'polarity': 'supports'}])
    assert compute_fingerprint(c1, 'evidence') == compute_fingerprint(c2, 'evidence')


def test_fingerprint_decision_excludes_status():
    d1 = Decision(id='d1', title='X', decision_statement='S', status='active')
    d2 = Decision(id='d1', title='X', decision_statement='S', status='deprecated')
    assert compute_fingerprint(d1, 'decision') == compute_fingerprint(d2, 'decision')


def test_detect_renames_warning_only_for_fingerprint_match():
    '''Plan: round 8 — fingerprint matching is warning-only; never auto-cascades.'''
    prior = {'claims': [{'id': 'claim_old', 'title': 'X', 'statement': 'S', 'parsed_from': 'claims.yaml#claim_old'}],
             'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []}
    new_obj = Claim(id='claim_new', title='X', statement='S', parsed_from='claims.yaml#claim_new')
    explicit, warnings = detect_renames(prior, [new_obj])
    assert explicit == []
    assert any('claim_old' in w and 'claim_new' in w for w in warnings)


def test_refresh_does_not_auto_cascade_fingerprint_match(tmp_path):
    '''Plan: round 8 — hand-edit of id does NOT auto-rename; emits warning only.'''
    _init_project(tmp_path)
    (tmp_path / 'claims.yaml').write_text('- id: claim_old\n  type: claim\n  title: T\n  statement: S\n')
    r1 = run_cofr(['refresh', str(tmp_path)])
    assert r1.returncode in (0, 1), r1.stderr
    (tmp_path / 'claims.yaml').write_text('- id: claim_new\n  type: claim\n  title: T\n  statement: S\n')
    r2 = run_cofr(['refresh', '--json', str(tmp_path)])
    assert r2.returncode in (0, 1)
    env = json.loads(r2.stdout)
    diff = env['data']['diff']
    assert diff['renamed'] == []
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    ids = sorted(c['id'] for c in state['claims'])
    assert 'claim_new' in ids


def test_cofr_rename_confirms_warning_only_fingerprint_match_for_pack_record(tmp_path):
    '''After warning-only fingerprint detection, cofr rename should confirm and clear pending state.'''
    _init_project(tmp_path)
    (tmp_path / 'claims.yaml').write_text('- id: claim_old\n  type: claim\n  title: T\n  statement: S\n')
    (tmp_path / 'evidences').mkdir()
    (tmp_path / 'evidences' / 'src.yaml').write_text(
        '- id: ev1\n  type: evidence\n  summary: E\n'
        '  claim_links:\n    - claim_id: claim_old\n      polarity: supports\n'
    )
    r1 = run_cofr(['refresh', str(tmp_path)])
    assert r1.returncode in (0, 1), r1.stderr

    (tmp_path / 'claims.yaml').write_text('- id: claim_new\n  type: claim\n  title: T\n  statement: S\n')
    r2 = run_cofr(['refresh', str(tmp_path)])
    assert r2.returncode in (0, 1)
    assert 'possible rename detected' in r2.stderr

    r3 = run_cofr(['rename', 'claim_old', 'claim_new', str(tmp_path)])
    assert r3.returncode in (0, 1), r3.stderr
    assert not (tmp_path / '.cofr' / 'pending_renames.json').exists()

    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    claim_ids = [c['id'] for c in state['claims']]
    assert claim_ids == ['claim_new']
    ev = next(e for e in state['evidence'] if e['id'] == 'ev1')
    assert ev['claim_links'][0]['claim_id'] == 'claim_new'


def test_pending_renames_round_trip(tmp_path):
    _init_project(tmp_path)
    write_pending_renames(tmp_path, {'type': 'claim', 'old_id': 'a', 'new_id': 'b', 'pack_path': 'claims.yaml', 'mode': 'standard'})
    data = load_pending_renames(tmp_path)
    assert data['entries'][0]['old_id'] == 'a'
    clear_pending_renames(tmp_path)
    assert load_pending_renames(tmp_path) is None


def test_apply_rename_cascade_rewrites_typed_pointers():
    state = {
        'claims': [{'id': 'claim_old', 'parsed_from': 'claims.yaml#claim_old'}],
        'evidence': [{'id': 'ev1', 'claim_links': [{'claim_id': 'claim_old', 'polarity': 'supports'}], 'parsed_from': 'evidences/foo.yaml#ev1'}],
        'experiments': [], 'decisions': [], 'open_questions': [], 'risks': [],
    }
    apply_rename_cascade(state, [], None, [{'old_id': 'claim_old', 'new_id': 'claim_new', 'type': 'claim'}], {})
    assert state['claims'][0]['id'] == 'claim_new'
    assert state['claims'][0]['parsed_from'] == 'claims.yaml#claim_new'
    assert state['evidence'][0]['claim_links'][0]['claim_id'] == 'claim_new'


def test_cofr_rename_refuses_second_invocation_while_pending(tmp_path):
    _init_project(tmp_path)
    body = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    write_pending_renames(tmp_path, {'type': 'claim', 'old_id': 'claim_a', 'new_id': 'claim_b', 'pack_path': 'claims.yaml', 'mode': 'standard'})
    r = run_cofr(['rename', 'claim_a', 'claim_c', str(tmp_path)])
    assert r.returncode != 0
    assert 'pending' in r.stderr.lower()


def test_cofr_rename_unknown_old_id_refuses(tmp_path):
    _init_project(tmp_path)
    r = run_cofr(['rename', 'claim_missing', 'claim_new', str(tmp_path)])
    assert r.returncode != 0


def test_cofr_rename_basic_flow_standard_mode(tmp_path):
    _init_project(tmp_path)
    body = '---\ntype: claim\nid: claim_orig\n---\n\n## Title\n\nT.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['rename', 'claim_orig', 'claim_renamed', str(tmp_path)])
    assert r.returncode in (0, 1), r.stderr
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    ids = [c['id'] for c in state['claims']]
    assert 'claim_renamed' in ids
    assert 'claim_orig' not in ids
    log = json.loads((tmp_path / '.cofr' / 'renames.json').read_text())
    sigs = [e for e in log['renames'] if e['old_id'] == 'claim_orig' and e['new_id'] == 'claim_renamed']
    assert sigs


def test_append_renames_log_signature_dedupes(tmp_path):
    _init_project(tmp_path)
    entry = {'type': 'claim', 'old_id': 'a', 'new_id': 'b', 'detected_at': '2026-01-01T00:00:00Z', 'refresh_snapshot': '', 'mode': 'explicit', 'signature': 'sig1'}
    append_renames_log(tmp_path, [entry])
    append_renames_log(tmp_path, [entry])
    log = json.loads((tmp_path / '.cofr' / 'renames.json').read_text())
    assert len(log['renames']) == 1


def test_apply_rename_cascade_fingerprint_confirm_refuses_on_drift():
    '''Plan: defense-in-depth re-verify preconditions; raise if drift.'''
    state = {
        'claims': [
            {'id': 'a', 'status': 'provisionally_supported', 'stale': False, 'title': 'X', 'parsed_from': 'claims.yaml#a'},
            {'id': 'b', 'status': 'provisionally_supported', 'stale': False, 'title': 'X', 'parsed_from': 'claims.yaml#b'},
        ],
        'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': [],
    }
    with pytest.raises(RuntimeError, match='preconditions no longer hold'):
        apply_rename_cascade(state, [], None, [{'old_id': 'a', 'new_id': 'b', 'type': 'claim'}], {}, mode='fingerprint_confirm')


def test_cofr_rename_branch_m_refuses_when_new_id_at_different_pack(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body_a = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body_a, capture_output=True, text=True)
    pack_path = tmp_path / 'claims.yaml'
    pack_path.write_text('[]\n')
    r = run_cofr(['rename', 'claim_a', 'claim_new_id', str(tmp_path)])
    assert r.returncode != 0


def test_refresh_snapshot_field_populated(tmp_path):
    '''Live refresh rename log entries should populate refresh_snapshot (not hardcoded to "").'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: T\n  statement: S\n')
    run_cofr(['refresh', str(tmp_path)])
    r = run_cofr(['rename', 'claim_a', 'claim_b', str(tmp_path)])
    assert r.returncode in (0, 1)
    log = json.loads((tmp_path / '.cofr' / 'renames.json').read_text())
    entry = next((e for e in log['renames'] if e['old_id'] == 'claim_a'), None)
    assert entry is not None
    assert entry.get('refresh_snapshot'), 'refresh_snapshot must be populated, not empty string'


def test_fingerprint_warning_text_uses_unicode_arrow(tmp_path):
    '''Plan: round 8 -- fingerprint matches are warning-only. Warning text uses Unicode arrow.'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_old\n  type: claim\n  title: T\n  statement: S\n')
    run_cofr(['refresh', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_new\n  type: claim\n  title: T\n  statement: S\n')
    r = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    joined = ' '.join(env['data']['warnings'])
    assert 'possible rename detected' in joined
    assert '→' in joined, 'fingerprint warning should use Unicode → per plan'
    assert env['data']['diff']['renamed'] == [], 'fingerprint matches must NOT auto-cascade (round 8)'


def test_malformed_pending_rename_refuses_refresh(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'pending_renames.json').write_text('{not json')
    r = run_cofr(['refresh', str(tmp_path)])
    assert r.returncode == 4
    assert 'malformed .cofr/pending_renames.json' in r.stderr


def test_rename_handles_quoted_markdown_id(tmp_path):
    run_cofr(['init', str(tmp_path)])
    claims_dir = tmp_path / 'claims'
    claims_dir.mkdir()
    (claims_dir / 'c.md').write_text('---\ntype: claim\nid: "claim_old"\n---\n\n## Title\n\nT\n## Statement\n\nS\n')
    run_cofr(['refresh', str(tmp_path)])
    r = run_cofr(['rename', 'claim_old', 'claim_new', str(tmp_path)])
    assert r.returncode in (0, 1), r.stderr
    text = (claims_dir / 'c.md').read_text()
    assert 'id: "claim_new"' in text
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert [c['id'] for c in state['claims']] == ['claim_new']


def test_failed_rename_validation_does_not_leave_pending_rename(tmp_path):
    '''A validation failure (invalid new_id format) must not leave .cofr/pending_renames.json.'''
    run_cofr(['init', str(tmp_path)])
    pack = tmp_path / 'claims.yaml'
    pack.write_text(
        '- id: claim_rename_src\n'
        '  type: claim\n'
        '  title: V\n'
        '  statement: S\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['rename', 'claim_rename_src', 'bad/new id', str(tmp_path)])
    assert result.returncode == 2
    assert not (tmp_path / '.cofr' / 'pending_renames.json').exists()
    assert 'bad/new id' not in pack.read_text()


def test_fingerprint_evidence_excludes_source_anchors_so_anchor_refinements_do_not_break_rename_detection():
    rec_no_anchor = {'id': 'ev_a', 'evidence_type': 'manual_observation', 'summary': 'S', 'data_source': 'foo.pdf'}
    rec_with_anchor = {'id': 'ev_a', 'evidence_type': 'manual_observation', 'summary': 'S', 'data_source': 'foo.pdf', 'source_anchors': [{'page': 1, 'quote': 'q'}]}
    assert compute_fingerprint(rec_no_anchor, 'evidence') == compute_fingerprint(rec_with_anchor, 'evidence')


def test_evidence_fingerprint_ignores_source_anchor_page_value_changes():
    rec_p1 = {'id': 'ev_a', 'evidence_type': 'manual_observation', 'summary': 'S', 'data_source': 'foo.pdf', 'source_anchors': [{'page': 1}]}
    rec_p2 = {'id': 'ev_a', 'evidence_type': 'manual_observation', 'summary': 'S', 'data_source': 'foo.pdf', 'source_anchors': [{'page': 5}]}
    assert compute_fingerprint(rec_p1, 'evidence') == compute_fingerprint(rec_p2, 'evidence')


def test_evidence_fingerprint_ignores_pdf_vs_text_anchor_shape_differences():
    rec_pdf = {'id': 'ev', 'evidence_type': 'manual_observation', 'summary': 'S', 'data_source': 'foo.pdf', 'source_anchors': [{'page': 1}]}
    rec_text = {'id': 'ev', 'evidence_type': 'manual_observation', 'summary': 'S', 'data_source': 'foo.pdf', 'source_anchors': [{'section': 'A', 'line': 3}]}
    assert compute_fingerprint(rec_pdf, 'evidence') == compute_fingerprint(rec_text, 'evidence')


def test_pre_load_pending_pack_fixup_fingerprint_confirm_returns_cleanup_only_when_state_drifted(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_new\n  type: claim\n  title: T\n  statement: S\n'
        '  status: provisionally_supported\n  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    write_pending_renames(tmp_path, {
        'type': 'claim',
        'old_id': 'claim_old',
        'new_id': 'claim_new',
        'pack_path': 'claims.yaml',
        'mode': 'fingerprint_confirm',
    })
    pending = json.loads((tmp_path / '.cofr' / 'pending_renames.json').read_text())
    result = _pre_load_pending_pack_fixup(tmp_path, pending)
    assert result['action'] == 'cleanup_only', \
        f'expected cleanup_only when state lacks fingerprint-confirm shape; got {result!r}'


def test_pre_load_pending_pack_fixup_fingerprint_confirm_returns_apply_when_state_holds(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_old\n  type: claim\n  title: T\n  statement: S\n'
        '  status: provisionally_supported\n  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_new\n  type: claim\n  title: T\n  statement: S\n'
        '  status: provisionally_supported\n  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    write_pending_renames(tmp_path, {
        'type': 'claim',
        'old_id': 'claim_old',
        'new_id': 'claim_new',
        'pack_path': 'claims.yaml',
        'mode': 'fingerprint_confirm',
    })
    pending = json.loads((tmp_path / '.cofr' / 'pending_renames.json').read_text())
    result = _pre_load_pending_pack_fixup(tmp_path, pending)
    assert result['action'] == 'apply', \
        f'expected apply when fingerprint-confirm preconditions hold; got {result!r}'
