import json
import sys
import types
from unittest.mock import patch

import pytest

from cofr.ingest import classify_file, scan_and_parse, walk_project
from cofr.packs import pack_dump
from cofr.state import CorruptStateError, _pre_load_migration_check, init, load_state
from conftest import run_cofr


def test_long_frontmatter_markdown_is_classified_and_ingested(tmp_path):
    init(tmp_path)
    claim_dir = tmp_path / 'claims'
    claim_dir.mkdir()
    long_title = 'A' * 5000
    path = claim_dir / 'claim_long.md'
    path.write_text(
        f'---\ntype: claim\nid: claim_long\ntitle: {long_title}\n'
        'status: supported\nconfidence: high\n---\n\n## Statement\n\nLong frontmatter still counts.\n',
        encoding='utf-8',
    )

    classification, fm_type, yaml_error = classify_file(path, path.stat().st_size, project_path=tmp_path)
    assert (classification, fm_type, yaml_error) == ('structured', 'claim', None)

    _index, records, warnings, *_ = scan_and_parse(tmp_path, {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []})
    assert [r.id for r in records] == ['claim_long']
    assert warnings == []


def test_load_state_wraps_missing_id_as_corrupt_state(tmp_path):
    init(tmp_path)
    bad = {
        'schema_version': 2,
        'cofr_version': '0.1.0',
        'claims': [{'title': 'missing id', 'status': 'supported'}],
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(bad), encoding='utf-8')

    with pytest.raises(CorruptStateError) as excinfo:
        load_state(tmp_path)
    assert 'claims' in str(excinfo.value)
    assert 'item 0' in str(excinfo.value)
    assert 'id' in str(excinfo.value)


def test_add_force_refuses_duplicate_ids_inside_target_pack(tmp_path):
    init(tmp_path)
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_dup\n  type: claim\n  title: One\n'
        '- id: claim_dup\n  type: claim\n  title: Two\n',
        encoding='utf-8',
    )
    body = '---\ntype: claim\nid: claim_dup\ntitle: Replacement\n---\n'

    result = run_cofr(['add', '--force', str(tmp_path)], stdin=body)

    assert result.returncode != 0
    assert 'duplicate id' in result.stderr.lower()
    text = (tmp_path / 'claims.yaml').read_text(encoding='utf-8')
    assert text.count('id: claim_dup') == 2
    assert 'Replacement' not in text


def test_migration_seeds_claim_timelines_from_v1_last_updated(tmp_path):
    init(tmp_path)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_a.md').write_text(
        '---\ntype: claim\nid: claim_a\ntitle: A\nstatus: supported\nconfidence: high\n---\n',
        encoding='utf-8',
    )
    v1_state = {
        'schema_version': 1,
        'cofr_version': '0.1.0',
        'last_refresh': '2026-02-01T00:00:00Z',
        'claims': [{
            'id': 'claim_a',
            'title': 'A',
            'status': 'supported',
            'confidence': 'high',
            'parsed_from': 'claims/claim_a.md',
            'first_seen': '2026-01-01T00:00:00Z',
            'last_updated': '2026-01-02T00:00:00Z',
        }],
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(v1_state), encoding='utf-8')

    result = run_cofr(['refresh', str(tmp_path)])

    assert result.returncode in (0, 1), result.stderr
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text(encoding='utf-8'))
    claim = next(c for c in state['claims'] if c['id'] == 'claim_a')
    assert {'t': '2026-01-02T00:00:00Z', 'c': 'high'} in claim['_timeline']
    assert {'t': '2026-01-02T00:00:00Z', 's': 'supported'} in claim['_status_timeline']


def test_pre_load_migration_check_cleans_marker_for_evidence_only_schema_bump(tmp_path):
    init(tmp_path)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text(encoding='utf-8'))
    state['schema_version'] = 2
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (tmp_path / 'evidences').mkdir()
    (tmp_path / 'evidences' / 'source.yaml').write_text('- id: ev_a\n  type: evidence\n', encoding='utf-8')
    (tmp_path / '.cofr' / 'migration_in_progress').write_text('snapshot:.cofr/history/pre.json\n', encoding='utf-8')
    (tmp_path / '.cofr' / 'migration_manifest.json').write_text(json.dumps({
        'schema_version': 1,
        'operations': [{'kind': 'schema_bump', 'src': '1', 'dst': '2', 'status': 'done'}],
    }), encoding='utf-8')

    result = _pre_load_migration_check(tmp_path)

    assert result['action'] == 'cleanup_marker'
    assert not (tmp_path / '.cofr' / 'migration_in_progress').exists()


def test_add_warns_when_stale_frontmatter_is_ignored(tmp_path):
    init(tmp_path)
    body = '---\ntype: claim\nid: claim_stale\nstale: true\n---\n'

    result = run_cofr(['add', str(tmp_path)], stdin=body)

    assert result.returncode == 1
    assert 'stale is system-managed' in result.stderr


def test_refresh_rebuild_timelines_checks_pending_renames_first(tmp_path):
    init(tmp_path)
    (tmp_path / '.cofr' / 'pending_renames.json').write_text('{not json', encoding='utf-8')

    result = run_cofr(['refresh', '--rebuild-timelines', str(tmp_path)])

    assert result.returncode != 0
    assert 'pending_renames.json' in result.stderr
    assert 'rebuild-timelines: complete' not in result.stdout


def test_pack_dump_uses_state_atomic_write_text(tmp_path):
    target = tmp_path / 'claims.yaml'
    with patch('cofr.packs.atomic_write_text') as atomic:
        pack_dump(target, [{'id': 'claim_a', 'type': 'claim', 'title': 'A'}], 'claim')

    atomic.assert_called_once()
    assert atomic.call_args.args[0] == target
    assert 'claim_a' in atomic.call_args.args[1]


def test_add_dry_run_does_not_leak_force_recycle_internal_flag(tmp_path):
    init(tmp_path)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text(encoding='utf-8'))
    state['claims'] = [{
        'id': 'claim_old',
        'type': 'claim',
        'title': 'Old',
        'status': 'retired',
        'stale': True,
        'parsed_from': 'claims.yaml#claim_old',
    }]
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    body = '---\ntype: claim\nid: claim_old\ntitle: Replacement\n---\n'

    result = run_cofr(['add', '--force', '--dry-run', str(tmp_path)], stdin=body)

    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert '_force_recycle' not in env['data']['record']


def test_gitignore_negation_reincludes_file(tmp_path):
    (tmp_path / '.gitignore').write_text('*.log\n!keep.log\n', encoding='utf-8')
    (tmp_path / 'drop.log').write_text('drop', encoding='utf-8')
    (tmp_path / 'keep.log').write_text('keep', encoding='utf-8')

    rel_paths = [rel for rel, _abs, _size in walk_project(tmp_path)]

    assert 'drop.log' not in rel_paths
    assert 'keep.log' in rel_paths


def test_gitignore_star_does_not_cross_directory_separator(tmp_path):
    (tmp_path / '.gitignore').write_text('data/*.md\n', encoding='utf-8')
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'top.md').write_text('top', encoding='utf-8')
    (tmp_path / 'data' / 'nested').mkdir()
    (tmp_path / 'data' / 'nested' / 'keep.md').write_text('nested', encoding='utf-8')

    rel_paths = [rel for rel, _abs, _size in walk_project(tmp_path)]

    assert 'data/top.md' not in rel_paths
    assert 'data/nested/keep.md' in rel_paths


def test_gitignore_double_star_matches_across_directories(tmp_path):
    (tmp_path / '.gitignore').write_text('data/**\n', encoding='utf-8')
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'top.md').write_text('top', encoding='utf-8')
    (tmp_path / 'data' / 'nested').mkdir()
    (tmp_path / 'data' / 'nested' / 'drop.md').write_text('nested', encoding='utf-8')

    rel_paths = [rel for rel, _abs, _size in walk_project(tmp_path)]

    assert not any(rel.startswith('data/') for rel in rel_paths)


def test_gitignore_root_anchor_only_matches_project_root(tmp_path):
    (tmp_path / '.gitignore').write_text('/cache\n', encoding='utf-8')
    (tmp_path / 'cache').write_text('root', encoding='utf-8')
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested' / 'cache').write_text('nested', encoding='utf-8')

    rel_paths = [rel for rel, _abs, _size in walk_project(tmp_path)]

    assert 'cache' not in rel_paths
    assert 'nested/cache' in rel_paths


def test_evidence_yml_pack_is_ingested_consistently(tmp_path):
    init(tmp_path)
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    (ev_dir / 'source.yml').write_text(
        '- id: ev_yml\n  type: evidence\n  summary: YML evidence\n',
        encoding='utf-8',
    )

    index, records, warnings, *_ = scan_and_parse(tmp_path, {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []})

    assert index['evidences/source.yml']['classification'] == 'structured_pack'
    assert [r.id for r in records] == ['ev_yml']
    assert warnings == []


def test_show_state_returns_soft_warn_for_staleness_check_warnings(tmp_path):
    init(tmp_path)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text(encoding='utf-8'))
    state['claims'] = [{
        'id': 'claim_bad_time',
        'type': 'claim',
        'status': 'supported',
        'confidence': 'high',
        'last_updated': 'not-a-date',
    }]
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(state), encoding='utf-8')

    result = run_cofr(['show', 'state', '--json', str(tmp_path)])

    assert result.returncode == 1
    assert 'unparseable timestamp' in result.stdout


def test_pdf_extraction_warning_includes_exception_class_and_message(tmp_path, monkeypatch):
    init(tmp_path)
    pdf = tmp_path / 'bad.pdf'
    pdf.write_bytes(b'%PDF-1.4\nnot a parseable pdf\n')

    fake_module = types.ModuleType('markitdown')

    class BrokenMarkItDown:
        def convert(self, _path):
            raise RuntimeError('parser exploded')

    fake_module.MarkItDown = lambda: BrokenMarkItDown()
    monkeypatch.setitem(sys.modules, 'markitdown', fake_module)

    _index, _records, warnings, *_ = scan_and_parse(tmp_path, {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []})

    assert any('RuntimeError: parser exploded' in w for w in warnings)
