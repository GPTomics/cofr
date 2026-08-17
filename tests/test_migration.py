import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cofr.state import (
    SCHEMA_VERSION,
    _pre_load_migration_check,
    init,
    load_state,
    migrate_v1_to_v2,
)


FIXTURES_DIR = Path(__file__).parent / 'fixtures'


def _v1_state_skeleton():
    return {
        'schema_version': 1,
        'cofr_version': '0.1.0',
        'last_refresh': '',
        'claims': [],
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }


def _write_v1_state(project_path, state):
    (project_path / '.cofr').mkdir(exist_ok=True)
    (project_path / '.cofr' / 'history').mkdir(exist_ok=True)
    (project_path / '.cofr' / 'state.json').write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    (project_path / '.cofr' / 'index.json').write_text('{}')
    (project_path / '.cofr' / 'config.yaml').write_text('project_name: ""\nproject_objective: ""\nexclude_patterns: []\n')
    (project_path / 'artifacts').mkdir(exist_ok=True)


def test_pre_load_migration_check_runs_before_load_state_marker_absent(tmp_path):
    init(tmp_path)
    result = _pre_load_migration_check(tmp_path)
    assert result['action'] == 'proceed'


def test_pre_load_migration_check_marker_plus_v1_state_refuses(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    (tmp_path / '.cofr' / 'migration_in_progress').write_text('snapshot:.cofr/history/foo.json')
    result = _pre_load_migration_check(tmp_path)
    assert result['action'] == 'refuse'
    assert 'Interrupted migration' in result['message']


def test_pre_load_migration_check_marker_plus_v2_with_valid_manifest_and_packs_cleans_up(tmp_path):
    state = _v1_state_skeleton()
    state['schema_version'] = 2
    _write_v1_state(tmp_path, state)
    (tmp_path / '.cofr' / 'migration_in_progress').write_text('snapshot:.cofr/history/foo.json')
    (tmp_path / '.cofr' / 'migration_manifest.json').write_text(json.dumps({
        'schema_version': 1, 'snapshot': '.cofr/history/foo.json', 'operations': []
    }))
    (tmp_path / 'claims.yaml').write_text('[]\n')
    result = _pre_load_migration_check(tmp_path)
    assert result['action'] == 'cleanup_marker'
    assert not (tmp_path / '.cofr' / 'migration_in_progress').exists()


def test_pre_load_migration_check_marker_plus_v2_missing_manifest_refuses(tmp_path):
    state = _v1_state_skeleton()
    state['schema_version'] = 2
    _write_v1_state(tmp_path, state)
    (tmp_path / '.cofr' / 'migration_in_progress').write_text('snapshot:.cofr/history/foo.json')
    result = _pre_load_migration_check(tmp_path)
    assert result['action'] == 'refuse'


def test_migrate_writes_pre_migration_snapshot_to_history(tmp_path):
    state = _v1_state_skeleton()
    state['claims'].append({'id': 'claim_x', 'title': 'X', 'parsed_from': 'claims/claim_x.md', 'first_seen': '2026-01-01T00:00:00Z', 'last_updated': '2026-01-01T00:00:00Z'})
    _write_v1_state(tmp_path, state)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_x.md').write_text('---\nid: claim_x\ntype: claim\ntitle: X\n---\n')
    migrate_v1_to_v2(tmp_path, state)
    snaps = list((tmp_path / '.cofr' / 'history').glob('*pre-migration*.json'))
    assert len(snaps) == 1


def test_migrate_writes_manifest_with_operations_list(tmp_path):
    state = _v1_state_skeleton()
    state['claims'].append({'id': 'claim_x', 'title': 'X', 'parsed_from': 'claims/claim_x.md', 'first_seen': '2026-01-01T00:00:00Z', 'last_updated': '2026-01-01T00:00:00Z'})
    _write_v1_state(tmp_path, state)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_x.md').write_text('---\nid: claim_x\ntype: claim\ntitle: X\n---\n')
    migrate_v1_to_v2(tmp_path, state)
    manifest_text = (tmp_path / '.cofr' / 'migration_manifest.json').read_text()
    manifest = json.loads(manifest_text)
    assert manifest['schema_version'] == 1
    assert 'snapshot' in manifest
    assert isinstance(manifest['operations'], list)
    assert len(manifest['operations']) > 0
    for op in manifest['operations']:
        assert 'kind' in op
        assert 'src' in op
        assert 'dst' in op
        assert 'status' in op


def test_migrate_moves_legacy_markdown_into_legacy_dir(tmp_path):
    state = _v1_state_skeleton()
    state['claims'].append({'id': 'claim_x', 'title': 'X', 'parsed_from': 'claims/claim_x.md', 'first_seen': '2026-01-01T00:00:00Z', 'last_updated': '2026-01-01T00:00:00Z'})
    _write_v1_state(tmp_path, state)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_x.md').write_text('---\nid: claim_x\ntype: claim\ntitle: X\n---\n')
    migrate_v1_to_v2(tmp_path, state)
    assert (tmp_path / '.cofr' / 'legacy_markdown' / 'claims' / 'claim_x.md').is_file()
    assert not (tmp_path / 'claims' / 'claim_x.md').exists()


def test_migrate_creates_v3_pack_at_project_root(tmp_path):
    state = _v1_state_skeleton()
    state['claims'].append({'id': 'claim_x', 'title': 'X', 'parsed_from': 'claims/claim_x.md', 'first_seen': '2026-01-01T00:00:00Z', 'last_updated': '2026-01-01T00:00:00Z'})
    _write_v1_state(tmp_path, state)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_x.md').write_text('---\nid: claim_x\ntype: claim\ntitle: X\n---\n')
    migrate_v1_to_v2(tmp_path, state)
    assert (tmp_path / 'claims.yaml').is_file()
    text = (tmp_path / 'claims.yaml').read_text()
    assert 'claim_x' in text


def test_migrate_bumps_schema_version_to_2(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    migrate_v1_to_v2(tmp_path, state)
    after = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert after['schema_version'] == 2


def test_migrate_removes_marker_on_success(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    migrate_v1_to_v2(tmp_path, state)
    assert not (tmp_path / '.cofr' / 'migration_in_progress').exists()


def test_migrate_refuses_when_v1_project_already_has_pack_files(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    (tmp_path / 'claims.yaml').write_text('- id: c1\n  type: claim\n')
    with pytest.raises(Exception):
        migrate_v1_to_v2(tmp_path, state)


def test_load_state_v1_returns_pending_migration_marker(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    loaded_state, warnings = load_state(tmp_path)
    assert loaded_state.get('_pending_migration') is True


def test_cofr_init_creates_schema_version_2_state(tmp_path):
    init(tmp_path)
    after = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert after['schema_version'] == 2


from conftest import run_cofr


def test_refresh_auto_migrates_on_first_v1_load(tmp_path):
    state = _v1_state_skeleton()
    state['claims'].append({'id': 'claim_x', 'title': 'X', 'parsed_from': 'claims/claim_x.md', 'first_seen': '2026-01-01T00:00:00Z', 'last_updated': '2026-01-01T00:00:00Z'})
    _write_v1_state(tmp_path, state)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_x.md').write_text('---\nid: claim_x\ntype: claim\ntitle: X\n---\n')
    r = run_cofr(['refresh', str(tmp_path)])
    assert r.returncode in (0, 1), r.stderr
    after = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert after['schema_version'] == 2
    assert (tmp_path / 'claims.yaml').is_file()


def test_show_state_on_v1_project_refuses(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    r = run_cofr(['show', 'state', str(tmp_path)])
    assert r.returncode != 0
    assert 'v1' in r.stderr.lower() or 'migration' in r.stderr.lower() or 'refresh' in r.stderr.lower()


def test_refresh_with_marker_and_schema_v1_refuses(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    (tmp_path / '.cofr' / 'migration_in_progress').write_text('snapshot:foo')
    r = run_cofr(['refresh', str(tmp_path)])
    assert r.returncode != 0
    assert 'interrupt' in r.stderr.lower() or 'rollback' in r.stderr.lower()


def test_migrate_rollback_plain_works_when_marker_and_state_present(tmp_path):
    state = _v1_state_skeleton()
    state['claims'].append({'id': 'claim_x', 'parsed_from': 'claims/claim_x.md', 'first_seen': '2026-01-01T00:00:00Z', 'last_updated': '2026-01-01T00:00:00Z'})
    _write_v1_state(tmp_path, state)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_x.md').write_text('---\nid: claim_x\ntype: claim\n---\n')
    snap = tmp_path / '.cofr' / 'history' / '2026-01-01T000000Z-pre-migration.json'
    snap.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    (tmp_path / '.cofr' / 'migration_in_progress').write_text(f'snapshot:.cofr/history/{snap.name}\n')
    (tmp_path / '.cofr' / 'migration_manifest.json').write_text(json.dumps({
        'schema_version': 1, 'snapshot': f'.cofr/history/{snap.name}',
        'operations': []
    }))
    r = run_cofr(['migrate', '--rollback', str(tmp_path)])
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / '.cofr' / 'migration_in_progress').exists()


def test_migrate_rollback_requires_explicit_flags_when_no_marker(tmp_path):
    state = _v1_state_skeleton()
    _write_v1_state(tmp_path, state)
    r = run_cofr(['migrate', '--rollback', str(tmp_path)])
    assert r.returncode != 0


def test_completed_migration_rollback_restores_markdown_and_removes_packs(tmp_path):
    state = {
        'schema_version': 1,
        'cofr_version': '0.1.0',
        'last_refresh': '',
        'claims': [],
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }
    state['claims'].append({
        'id': 'claim_x',
        'title': 'X',
        'parsed_from': 'claims/claim_x.md',
        'first_seen': '2026-01-01T00:00:00Z',
        'last_updated': '2026-01-01T00:00:00Z',
    })
    (tmp_path / '.cofr').mkdir()
    (tmp_path / '.cofr' / 'history').mkdir()
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    (tmp_path / '.cofr' / 'index.json').write_text('{}\n')
    (tmp_path / '.cofr' / 'config.yaml').write_text('project_name: ""\nproject_objective: ""\nexclude_patterns: []\n')
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_x.md').write_text('---\nid: claim_x\ntype: claim\ntitle: X\n---\n')
    r1 = run_cofr(['refresh', str(tmp_path)])
    assert r1.returncode in (0, 1), r1.stderr
    snap = next((tmp_path / '.cofr' / 'history').glob('*pre-migration*.json'))
    r2 = run_cofr(['migrate', '--rollback', '--from-history', str(snap), '--yes-i-know-what-im-doing', str(tmp_path)])
    assert r2.returncode == 0, r2.stderr
    assert (tmp_path / 'claims' / 'claim_x.md').is_file()
    assert not (tmp_path / 'claims.yaml').exists()


def test_migrate_without_rollback_flag_refuses(tmp_path):
    run_cofr(['init', str(tmp_path)])
    r = run_cofr(['migrate', str(tmp_path)])
    assert r.returncode != 0
    assert '--rollback' in r.stderr


def test_migration_evidence_uses_index_for_slug(tmp_path):
    '''Migration's evidence source_slug derivation must consult .cofr/index.json.'''
    run_cofr(['init', str(tmp_path)])
    pdf_path = tmp_path / 'papers' / 'foo.pdf'
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b'%PDF-1.4\n%dummy\n%%EOF\n')
    ev_dir = tmp_path / 'evidence'
    ev_dir.mkdir()
    md_path = ev_dir / 'ev_a.md'
    md_path.write_text('---\ntype: evidence\nid: ev_a\nevidence_type: paper_note\ndata_source: papers/foo.pdf\n---\n\n## Summary\n\nSummary.\n')
    state_path = tmp_path / '.cofr' / 'state.json'
    state = json.loads(state_path.read_text())
    state['schema_version'] = 1
    state.setdefault('evidence', []).append({
        'id': 'ev_a',
        'evidence_type': 'paper_note',
        'data_source': 'papers/foo.pdf',
        'summary': 'Summary.',
        'parsed_from': 'evidence/ev_a.md',
        'first_seen': '2026-01-01T00:00:00Z',
        'last_updated': '2026-01-01T00:00:00Z',
    })
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    index_path = tmp_path / '.cofr' / 'index.json'
    index_path.write_text(json.dumps({'papers/foo.pdf': {'classification': 'content_extracted', 'extension': '.pdf'}}))
    r = run_cofr(['refresh', str(tmp_path)])
    assert r.returncode in (0, 1), r.stderr
    foo_pack = tmp_path / 'evidences' / 'foo.yaml'
    assert foo_pack.is_file(), 'evidence should route to evidences/foo.yaml (stem from data_source resolved via index)'


def test_refresh_migration_emits_warning(tmp_path):
    (tmp_path / '.cofr' / 'history').mkdir(parents=True)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'artifacts').mkdir()
    state = {
        'schema_version': 1, 'cofr_version': '0.1.0', 'last_refresh': '',
        'claims': [{'id': 'claim_a', 'title': 'A', 'statement': 'S', 'parsed_from': 'claims/claim_a.md'}],
        'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': [], 'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(state))
    (tmp_path / '.cofr' / 'index.json').write_text('{}')
    (tmp_path / '.cofr' / 'config.yaml').write_text('project_name: ""\nproject_objective: ""\nexclude_patterns: []\n')
    (tmp_path / 'claims' / 'claim_a.md').write_text('---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA\n## Statement\n\nS\n')
    r = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    assert any('migrated to schema v2' in w for w in env['data']['warnings'])
