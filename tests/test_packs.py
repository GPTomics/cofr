import json

import pytest

from cofr.packs import (
    CANONICAL_KEY_ORDER,
    EXPECTED_PACK_PATHS,
    _PACK_EMIT_EXCLUDED,
    pack_dump,
    pack_load,
    route_pack_path,
    sanitize_slug,
    sort_records,
)
from cofr.state import init, save_index
from conftest import run_cofr


def test_canonical_key_order_per_type_has_id_first():
    for type_str, keys in CANONICAL_KEY_ORDER.items():
        assert keys[0] == 'id', f'{type_str} missing id-first'
        assert keys[1] == 'type', f'{type_str} missing type-second'


def test_canonical_key_order_lists_every_user_authored_dataclass_field_per_type():
    from dataclasses import fields
    from cofr.domain import Claim, Evidence, Decision, OpenQuestion, Risk, Experiment
    type_to_cls = {
        'claim': Claim, 'evidence': Evidence, 'decision': Decision,
        'question': OpenQuestion, 'risk': Risk, 'experiment': Experiment,
    }
    for type_str, cls in type_to_cls.items():
        canonical = set(CANONICAL_KEY_ORDER[type_str])
        for f in fields(cls):
            if f.name in _PACK_EMIT_EXCLUDED:
                continue
            assert f.name in canonical, (
                f'{type_str}: dataclass field {f.name!r} is not in CANONICAL_KEY_ORDER. '
                f'Add it to packs.CANONICAL_KEY_ORDER[{type_str!r}] in its appropriate position '
                f'so pack emit ordering stays deterministic.'
            )


def test_pack_emit_excluded_set_includes_system_fields():
    assert 'parsed_from' in _PACK_EMIT_EXCLUDED
    assert 'first_seen' in _PACK_EMIT_EXCLUDED
    assert 'last_updated' in _PACK_EMIT_EXCLUDED
    assert 'stale' in _PACK_EMIT_EXCLUDED
    assert '_timeline' in _PACK_EMIT_EXCLUDED
    assert '_status_timeline' in _PACK_EMIT_EXCLUDED
    assert '_unknown_fields' in _PACK_EMIT_EXCLUDED
    assert '_preserved_user_fields' in _PACK_EMIT_EXCLUDED


def test_canonical_yaml_round_trip_preserves_key_order(tmp_path):
    records = [{'id': 'claim_a', 'type': 'claim', 'title': 'A claim', 'statement': 'Text.'}]
    path = tmp_path / 'claims.yaml'
    pack_dump(path, records, 'claim')
    loaded = pack_load(path)
    assert loaded[0]['id'] == 'claim_a'
    text = path.read_text()
    assert text.index('id:') < text.index('type:') < text.index('title:')


def test_pack_sort_records_by_id():
    recs = [{'id': 'b'}, {'id': 'a'}, {'id': 'c'}]
    out = sort_records(recs)
    assert [r['id'] for r in out] == ['a', 'b', 'c']


def test_pack_dump_uses_literal_block_for_multiline_strings(tmp_path):
    records = [{'id': 'c1', 'type': 'claim', 'statement': 'line one\nline two\nline three'}]
    path = tmp_path / 'claims.yaml'
    pack_dump(path, records, 'claim')
    text = path.read_text()
    assert 'statement: |' in text or 'statement: |-' in text


def test_pack_dump_load_is_byte_identical_on_round_trip(tmp_path):
    records = [
        {'id': 'claim_a', 'type': 'claim', 'title': 'A', 'statement': 'stmt A\nmore'},
        {'id': 'claim_b', 'type': 'claim', 'title': 'B', 'statement': 'stmt B'},
    ]
    p1 = tmp_path / 'claims.yaml'
    pack_dump(p1, records, 'claim')
    text1 = p1.read_text()
    loaded = pack_load(p1)
    p2 = tmp_path / 'claims2.yaml'
    pack_dump(p2, loaded, 'claim')
    text2 = p2.read_text()
    assert text1 == text2


def test_pack_dump_excludes_system_fields_parsed_from_first_seen_last_updated_stale_timeline_status_timeline(tmp_path):
    records = [{
        'id': 'c1', 'type': 'claim', 'title': 'X', 'statement': 'S',
        'parsed_from': 'claims.yaml#c1', 'first_seen': '2026-01-01T00:00:00Z',
        'last_updated': '2026-01-02T00:00:00Z', 'stale': False,
        '_timeline': [{'t': '2026-01-01', 'c': 'medium'}],
        '_status_timeline': [{'t': '2026-01-01', 's': 'provisionally_supported'}],
    }]
    path = tmp_path / 'claims.yaml'
    pack_dump(path, records, 'claim')
    text = path.read_text()
    assert 'parsed_from' not in text
    assert 'first_seen' not in text
    assert 'last_updated' not in text
    assert 'stale' not in text
    assert '_timeline' not in text
    assert '_status_timeline' not in text


def test_pack_dump_never_emits_stale_field_even_when_true(tmp_path):
    records = [{'id': 'c1', 'type': 'claim', 'title': 'X', 'stale': True}]
    path = tmp_path / 'claims.yaml'
    pack_dump(path, records, 'claim')
    text = path.read_text()
    assert 'stale' not in text


def test_pack_load_record_missing_id_warns_and_skips(tmp_path):
    path = tmp_path / 'claims.yaml'
    path.write_text('- type: claim\n  title: missing id\n- id: c2\n  type: claim\n  title: ok\n')
    records, warnings = pack_load(path, expected_type='claim', return_warnings=True)
    assert len(records) == 1
    assert records[0]['id'] == 'c2'
    assert any('missing id' in w.lower() or 'no id' in w.lower() for w in warnings)


def test_pack_load_record_numeric_id_warns_and_skips(tmp_path):
    path = tmp_path / 'claims.yaml'
    path.write_text('- id: 123\n  type: claim\n  title: numeric id\n- id: c2\n  type: claim\n  title: ok\n')
    records, warnings = pack_load(path, expected_type='claim', return_warnings=True)
    assert len(records) == 1
    assert records[0]['id'] == 'c2'
    assert any('invalid id' in w.lower() for w in warnings)


def test_pack_load_record_id_with_slash_warns_and_skips(tmp_path):
    path = tmp_path / 'claims.yaml'
    path.write_text('- id: bad/id\n  type: claim\n  title: bad id\n- id: c2\n  type: claim\n  title: ok\n')
    records, warnings = pack_load(path, expected_type='claim', return_warnings=True)
    assert len(records) == 1
    assert records[0]['id'] == 'c2'
    assert any('invalid id' in w.lower() for w in warnings)


def test_pack_load_record_missing_type_warns_and_skips(tmp_path):
    path = tmp_path / 'claims.yaml'
    path.write_text('- id: c1\n  title: missing type\n- id: c2\n  type: claim\n  title: ok\n')
    records, warnings = pack_load(path, expected_type='claim', return_warnings=True)
    assert len(records) == 1
    assert records[0]['id'] == 'c2'


def test_pack_load_record_type_mismatch_with_pack_warns_and_skips(tmp_path):
    path = tmp_path / 'claims.yaml'
    path.write_text('- id: e1\n  type: evidence\n  summary: wrong pack\n- id: c1\n  type: claim\n  title: ok\n')
    records, warnings = pack_load(path, expected_type='claim', return_warnings=True)
    assert len(records) == 1
    assert records[0]['id'] == 'c1'


def test_pack_load_empty_file_returns_empty_list(tmp_path):
    path = tmp_path / 'claims.yaml'
    path.write_text('')
    assert pack_load(path) == []


def test_pack_load_malformed_yaml_raises(tmp_path):
    path = tmp_path / 'claims.yaml'
    path.write_text('- id: c1\n  type: claim\n  title: [unclosed\n')
    with pytest.raises(Exception):
        pack_load(path)


def test_expected_pack_paths_per_type():
    assert EXPECTED_PACK_PATHS['claim'] == 'claims.yaml'
    assert EXPECTED_PACK_PATHS['decision'] == 'decisions.yaml'
    assert EXPECTED_PACK_PATHS['question'] == 'questions.yaml'
    assert EXPECTED_PACK_PATHS['risk'] == 'risks.yaml'
    assert EXPECTED_PACK_PATHS['experiment'] == 'experiments.yaml'


def test_sanitize_slug_replaces_dot_dot_with_underscore():
    clean, modified = sanitize_slug('../etc/passwd')
    assert '..' not in clean
    assert '/' not in clean
    assert modified is True


def test_sanitize_slug_replaces_path_separator_with_underscore():
    clean, modified = sanitize_slug('foo/bar')
    assert '/' not in clean
    assert modified is True


def test_sanitize_slug_replaces_whitespace_with_underscore():
    clean, modified = sanitize_slug('foo bar baz')
    assert ' ' not in clean
    assert modified is True


def test_sanitize_slug_collapses_runs_of_underscore():
    clean, _ = sanitize_slug('foo___bar')
    assert '__' not in clean


def test_sanitize_slug_returns_misc_for_empty_input():
    clean, modified = sanitize_slug('')
    assert clean == '__misc__'
    assert modified is True


def test_sanitize_slug_valid_input_returns_unmodified():
    clean, modified = sanitize_slug('foo-bar_baz')
    assert clean == 'foo-bar_baz'
    assert modified is False


def test_sanitize_slug_strips_leading_trailing_underscores():
    clean, _ = sanitize_slug('_foo_')
    assert clean == 'foo'


def test_pack_dump_unknown_fields_emit_alphabetically_after_canonical(tmp_path):
    records = [{
        'id': 'c1', 'type': 'claim', 'title': 'X',
        '_unknown_fields': {'zebra': 'last', 'alpha': 'first'},
    }]
    path = tmp_path / 'claims.yaml'
    pack_dump(path, records, 'claim')
    text = path.read_text()
    assert text.index('title') < text.index('alpha') < text.index('zebra')
    assert '_unknown_fields:' not in text


def test_pack_dump_reinjects_preserved_user_fields_so_pack_rewrites_dont_lose_user_data(tmp_path):
    '''Plan: pack_dump re-injects _preserved_user_fields content under canonical keys.

    The mechanism preserves user-authored on-disk values even when validation
    cleared the in-memory copy (e.g. an invalid source_path). Incidental
    rewrites (rename cascade, source_slug auto-correction) must not silently
    drop user data. The literal _preserved_user_fields key never appears.
    '''
    records = [{
        'id': 'e1', 'type': 'evidence', 'evidence_type': 'paper_note',
        'source_path': '',
        'source_anchors': [],
        '_preserved_user_fields': {'source_path': '../leaky.pdf', 'source_anchors': [{'page': 1}]},
    }]
    path = tmp_path / 'evidences' / 'foo.yaml'
    path.parent.mkdir(parents=True, exist_ok=True)
    pack_dump(path, records, 'evidence')
    text = path.read_text()
    assert 'source_path: ../leaky.pdf' in text
    assert 'source_anchors:' in text
    assert '_preserved_user_fields:' not in text


def test_pack_dump_no_record_ever_emits_literal_underscore_preserved_user_fields_key(tmp_path):
    records = [{'id': 'e1', 'type': 'evidence', '_preserved_user_fields': {'source_path': 'x.pdf'}}]
    path = tmp_path / 'evidences' / 'foo.yaml'
    path.parent.mkdir(parents=True, exist_ok=True)
    pack_dump(path, records, 'evidence')
    text = path.read_text()
    for line in text.splitlines():
        assert not line.lstrip().startswith('_preserved_user_fields:')


def test_pack_dump_no_record_ever_emits_literal_underscore_unknown_fields_key(tmp_path):
    records = [{'id': 'c1', 'type': 'claim', '_unknown_fields': {'custom_key': 'value'}}]
    path = tmp_path / 'claims.yaml'
    pack_dump(path, records, 'claim')
    text = path.read_text()
    for line in text.splitlines():
        assert not line.lstrip().startswith('_unknown_fields:')


def test_pack_dump_records_sorted_by_id(tmp_path):
    records = [
        {'id': 'b', 'type': 'claim', 'title': 'B'},
        {'id': 'a', 'type': 'claim', 'title': 'A'},
    ]
    path = tmp_path / 'claims.yaml'
    pack_dump(path, records, 'claim')
    text = path.read_text()
    assert text.index('id: a') < text.index('id: b')


def test_classify_file_recognizes_yaml_packs_at_canonical_paths(tmp_path):
    from cofr.ingest import classify_file
    pack = tmp_path / 'claims.yaml'
    pack.write_text('- id: c1\n  type: claim\n  title: X\n')
    classification, fm_type, yaml_error = classify_file(pack, pack.stat().st_size)
    assert classification == 'structured_pack'


def test_classify_file_recognizes_evidences_subdir_yaml_packs(tmp_path):
    from cofr.ingest import classify_file
    pack = tmp_path / 'evidences'
    pack.mkdir()
    f = pack / 'foo.yaml'
    f.write_text('- id: e1\n  type: evidence\n  summary: X\n')
    classification, fm_type, yaml_error = classify_file(f, f.stat().st_size)
    assert classification == 'structured_pack'


def test_pack_record_parsed_from_uses_pack_hash_id_format(tmp_path):
    from cofr.ingest import scan_and_parse
    from cofr.state import init
    init(tmp_path)
    (tmp_path / 'claims.yaml').write_text('- id: c1\n  type: claim\n  title: X\n')
    _, records, _, _, _, _ = scan_and_parse(tmp_path, {'claims': [], 'evidence': [], 'experiments': [], 'decisions': [], 'open_questions': [], 'risks': []})
    pack_records = [r for r in records if 'claims.yaml' in r.parsed_from]
    assert len(pack_records) == 1
    assert pack_records[0].parsed_from == 'claims.yaml#c1'


def test_pack_load_refuses_evidence_pack_filename_with_dot_dot(tmp_path):
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    bad = ev_dir / 'foo..yaml'
    bad.write_text('- id: e1\n  type: evidence\n  summary: x\n')
    with pytest.raises(ValueError, match='path-escape'):
        pack_load(bad, expected_type='evidence')


def test_pack_load_refuses_evidence_pack_filename_with_spaces(tmp_path):
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    bad = ev_dir / 'foo bar.yaml'
    bad.write_text('- id: e1\n  type: evidence\n  summary: x\n')
    with pytest.raises(ValueError):
        pack_load(bad, expected_type='evidence')


def test_route_pack_path_data_source_misses_index_routes_to_misc(tmp_path):
    init(tmp_path)
    rec = {'id': 'e1', 'data_source': 'nonexistent_file.pdf'}
    pack, warnings = route_pack_path(tmp_path, 'evidence', rec)
    assert pack.name == '__misc__.yaml'


def test_route_pack_path_data_source_in_index_routes_to_stem(tmp_path):
    init(tmp_path)
    save_index(tmp_path, {'papers/foo.pdf': {'classification': 'content_extracted'}})
    rec = {'id': 'e1', 'data_source': 'papers/foo.pdf'}
    pack, warnings = route_pack_path(tmp_path, 'evidence', rec)
    assert pack.name == 'foo.yaml'


def test_sanitize_slug_misc_round_trips():
    '''sanitize_slug("__misc__") must round-trip -- it is the canonical fallback name.'''
    clean, modified = sanitize_slug('__misc__')
    assert clean == '__misc__'
    assert modified is False


def test_pack_size_guardrail_warns_above_threshold(tmp_path):
    '''Pack with > 100 records should produce a guardrail warning on refresh.'''
    run_cofr(['init', str(tmp_path)])
    records = []
    for i in range(105):
        records.append(f'- id: claim_{i:03d}\n  type: claim\n  title: T{i}\n  statement: S{i}\n')
    (tmp_path / 'claims.yaml').write_text(''.join(records))
    r = run_cofr(['refresh', '--json', str(tmp_path)])
    assert r.returncode in (0, 1)
    env = json.loads(r.stdout)
    warnings = env['data'].get('warnings', [])
    joined = ' '.join(warnings)
    assert 'pack' in joined.lower() and ('100' in joined or 'records' in joined.lower())
