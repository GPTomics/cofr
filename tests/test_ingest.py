import json
import subprocess

from cofr.ingest import (
    _page_count_from_pdf_bytes,
    _page_count_from_text,
    assign_id,
    classify_file,
    extract_pdf_text,
    parse_field_value,
    parse_sections,
    parse_structured_record,
    scan_and_parse,
    scan_for_id_mentions,
    split_frontmatter,
    walk_project,
)
from conftest import run_cofr


def test_split_frontmatter_valid_yaml():
    text = '---\ntype: claim\nid: claim_x\nstatus: supported\n---\n\n## Title\n\nHello.\n'
    metadata, body = split_frontmatter(text)
    assert metadata == {'type': 'claim', 'id': 'claim_x', 'status': 'supported'}
    assert body.startswith('## Title')
    assert 'Hello.' in body


def test_split_frontmatter_no_frontmatter():
    text = '# Plain markdown\n\nNo frontmatter at all.\n'
    metadata, body = split_frontmatter(text)
    assert metadata == {}
    assert body.rstrip() == text.rstrip()


def test_split_frontmatter_malformed_yaml_returns_error_key():
    text = '---\ntype: claim\nthis is: not: valid: yaml: here\n---\n\n## Title\n'
    metadata, body = split_frontmatter(text)
    assert '_yaml_error' in metadata
    assert isinstance(metadata['_yaml_error'], str)


def test_split_frontmatter_crlf_normalization():
    text = '---\r\ntype: claim\r\nid: claim_crlf\r\n---\r\n\r\n## Body\r\n'
    metadata, body = split_frontmatter(text)
    assert metadata == {'type': 'claim', 'id': 'claim_crlf'}
    assert '## Body' in body


def test_split_frontmatter_empty_frontmatter_block():
    text = '---\n---\n\n## Body only\n'
    metadata, body = split_frontmatter(text)
    assert metadata == {}
    assert '## Body only' in body


def test_split_frontmatter_clean_fixture_file(clean_project_path):
    text = (clean_project_path / 'claims' / 'claim_action_conditioning.md').read_text(encoding='utf-8')
    metadata, body = split_frontmatter(text)
    assert metadata['type'] == 'claim'
    assert metadata['id'] == 'claim_action_conditioning'
    assert metadata['status'] == 'provisionally_supported'
    assert metadata['confidence'] == 'medium'
    assert '## Title' in body
    assert '## What would change my mind' in body


def test_split_frontmatter_error_fixture_bad_yaml(error_project_path):
    text = (error_project_path / 'bad_yaml.md').read_text(encoding='utf-8')
    metadata, body = split_frontmatter(text)
    assert '_yaml_error' in metadata


def test_parse_sections_simple_h2():
    body = '## Title\n\nHello world\n\n## Statement\n\nA second section.\n'
    sections = parse_sections(body)
    assert sections['title'] == 'Hello world'
    assert sections['statement'] == 'A second section.'


def test_parse_sections_ignores_h2_inside_backtick_fence():
    body = '## Title\n\nNormal text.\n\n```\n## Not a section\nstill in code\n```\n\nMore text.\n'
    sections = parse_sections(body)
    assert 'title' in sections
    assert 'not_a_section' not in sections
    assert '```' in sections['title']


def test_parse_sections_ignores_h2_inside_tilde_fence():
    body = '## Title\n\n~~~\n## Not a section\n~~~\n\nAfter fence.\n'
    sections = parse_sections(body)
    assert 'title' in sections
    assert 'not_a_section' not in sections


def test_parse_sections_heading_normalization():
    body = '## Main Support\n\na\n\n## What would change my mind?\n\nb\n\n## Affects Claims:\n\nc\n'
    sections = parse_sections(body)
    assert sections['main_support'] == 'a'
    assert sections['what_would_change_my_mind'] == 'b'
    assert sections['affects_claims'] == 'c'


def test_parse_sections_ignores_pre_first_heading_content():
    body = 'Preamble text before any heading.\n\n## Title\n\nOnly this is captured.\n'
    sections = parse_sections(body)
    assert sections == {'title': 'Only this is captured.'}


def test_parse_sections_empty_section_value():
    body = '## Title\n\n## Statement\n\nbody\n'
    sections = parse_sections(body)
    assert sections['title'] == ''
    assert sections['statement'] == 'body'


def test_parse_sections_clean_fixture_claim(clean_project_path):
    text = (clean_project_path / 'claims' / 'claim_action_conditioning.md').read_text(encoding='utf-8')
    _, body = split_frontmatter(text)
    sections = parse_sections(body)
    assert 'title' in sections
    assert 'statement' in sections
    assert 'main_support' in sections
    assert 'main_weakness' in sections
    assert 'what_would_change_my_mind' in sections
    assert sections['title'].startswith('Action-conditioning')


def test_parse_field_value_prose():
    value, warnings = parse_field_value('prose', '  Hello world  \n\nMore prose.\n')
    assert value == 'Hello world  \n\nMore prose.'
    assert warnings == []


def test_parse_field_value_reference_list():
    content = '- claim_a\n- claim_b\n- claim_c\n'
    value, warnings = parse_field_value('reference_list', content)
    assert value == ['claim_a', 'claim_b', 'claim_c']
    assert warnings == []


def test_parse_field_value_reference_list_strips_extra_whitespace():
    value, warnings = parse_field_value('reference_list', '-   claim_a  \n-claim_b\n')
    assert value == ['claim_a', 'claim_b']


def test_parse_field_value_polarity_list_default_supports():
    content = '- claim_a\n- claim_b: opposes\n- claim_c: supports\n'
    value, warnings = parse_field_value('polarity_list', content)
    assert value == [
        {'claim_id': 'claim_a', 'polarity': 'supports'},
        {'claim_id': 'claim_b', 'polarity': 'opposes'},
        {'claim_id': 'claim_c', 'polarity': 'supports'},
    ]


def test_parse_field_value_polarity_list_splits_on_last_colon():
    value, _ = parse_field_value('polarity_list', '- weird: claim: with: colons: supports\n')
    assert value == [{'claim_id': 'weird: claim: with: colons', 'polarity': 'supports'}]


def test_parse_field_value_key_value_dict_splits_first_colon():
    content = '- accuracy: 0.92\n- description: a:b:c\n'
    value, warnings = parse_field_value('key_value_dict', content)
    assert value == {'accuracy': '0.92', 'description': 'a:b:c'}


def test_parse_field_value_string_list():
    content = '- first question\n- second question\n'
    value, warnings = parse_field_value('string_list', content)
    assert value == ['first question', 'second question']


def test_parse_field_value_returns_none_and_warning_on_malformed_reference_list():
    value, warnings = parse_field_value('reference_list', 'not a bullet list, just prose\n')
    assert value is None
    assert len(warnings) == 1


def test_parse_field_value_returns_none_on_empty_polarity_list_content():
    value, warnings = parse_field_value('polarity_list', '   \n\n   \n')
    assert value is None
    assert len(warnings) == 1


def test_parse_field_value_key_value_dict_rejects_bullet_without_colon():
    value, warnings = parse_field_value('key_value_dict', '- no colon here\n- valid: 1\n')
    assert value is None
    assert len(warnings) == 1


def test_assign_id_uses_explicit_id_when_unique():
    new_id, collision, warning, generated = assign_id({'id': 'claim_x'}, 'claims/x.md', {}, 'claim')
    assert new_id == 'claim_x'
    assert collision is False
    assert warning is None
    assert generated is False


def test_assign_id_generates_uuid_when_absent():
    new_id, collision, warning, generated = assign_id({}, 'claims/x.md', {}, 'claim')
    assert new_id.startswith('claim_')
    assert len(new_id) > len('claim_')
    assert collision is False
    assert generated is True


def test_assign_id_allows_same_path_reuse():
    existing = {'claim_x': 'claims/x.md'}
    seen = {}
    new_id, collision, warning, generated = assign_id({'id': 'claim_x'}, 'claims/x.md', seen, 'claim')
    assert new_id == 'claim_x'
    assert collision is False
    assert warning is None


def test_assign_id_accepts_state_same_id_at_new_path_as_relocation_not_duplicate():
    existing = {'claim_x': 'claims/x.md'}
    seen = {}
    new_id, collision, warning, _ = assign_id({'id': 'claim_x'}, 'claims/y.md', seen, 'claim')
    assert collision is False
    assert warning is None


def test_assign_id_still_rejects_seen_this_refresh_same_id_at_different_path():
    seen = {'claim_x': 'claims/early.md'}
    new_id, collision, warning, _ = assign_id({'id': 'claim_x'}, 'claims/later.md', seen, 'claim')
    assert collision is True
    assert 'claim_x' in warning
    assert 'claims/early.md' in warning


def test_parse_structured_record_claim_happy_path(clean_project_path):
    path = clean_project_path / 'claims' / 'claim_action_conditioning.md'
    text = path.read_text(encoding='utf-8')
    obj, warnings, _ = parse_structured_record(str(path), text, {})
    from cofr.domain import Claim
    assert isinstance(obj, Claim)
    assert obj.id == 'claim_action_conditioning'
    assert obj.title.startswith('Action-conditioning')


def test_parse_structured_record_captures_unknown_frontmatter_into_unknown_fields(tmp_path):
    md = tmp_path / 'note.md'
    md.write_text(
        '---\n'
        'type: claim\n'
        'id: claim_unknown_test\n'
        'title: T\n'
        'statement: S\n'
        'project_tag: my-project\n'
        'reviewer: alice\n'
        '---\n'
        '## Statement\nbody\n'
    )
    obj, warnings, _ = parse_structured_record(str(md), md.read_text(encoding='utf-8'), {})
    assert obj is not None
    assert obj._unknown_fields == {'project_tag': 'my-project', 'reviewer': 'alice'}
    assert obj.status == 'provisionally_supported'
    assert obj.confidence == 'medium'


def test_parse_structured_record_evidence_with_claim_links(clean_project_path):
    path = clean_project_path / 'evidence' / 'ev_supporting_eval.md'
    text = path.read_text(encoding='utf-8')
    obj, warnings, _ = parse_structured_record(str(path), text, {})
    from cofr.domain import Evidence
    assert isinstance(obj, Evidence)
    assert obj.claim_links == [{'claim_id': 'claim_action_conditioning', 'polarity': 'supports'}]
    assert obj.evidence_type == 'experiment_result'


def test_parse_structured_record_decision_with_reference_list(clean_project_path):
    path = clean_project_path / 'decisions' / 'dec_deprioritize_scaling.md'
    text = path.read_text(encoding='utf-8')
    obj, warnings, _ = parse_structured_record(str(path), text, {})
    from cofr.domain import Decision
    assert isinstance(obj, Decision)
    assert obj.depends_on_claim_ids == ['claim_scaling_priority']


def test_parse_structured_record_no_frontmatter_returns_none(error_project_path):
    path = error_project_path / 'no_frontmatter.md'
    text = path.read_text(encoding='utf-8')
    obj, warnings, _ = parse_structured_record(str(path), text, {})
    assert obj is None
    assert warnings == []


def test_parse_structured_record_unknown_type_returns_none_with_warning(error_project_path):
    path = error_project_path / 'unknown_type.md'
    text = path.read_text(encoding='utf-8')
    obj, warnings, _ = parse_structured_record(str(path), text, {})
    assert obj is None
    assert any('hypothesis' in w for w in warnings)


def test_parse_structured_record_bad_yaml_returns_none_with_warning(error_project_path):
    path = error_project_path / 'bad_yaml.md'
    text = path.read_text(encoding='utf-8')
    obj, warnings, _ = parse_structured_record(str(path), text, {})
    assert obj is None
    assert any('yaml' in w.lower() for w in warnings)


def test_parse_structured_record_relocation_is_not_a_collision(clean_project_path):
    path = clean_project_path / 'claims' / 'claim_drift_barrier.md'
    text = path.read_text(encoding='utf-8')
    existing = {'claim_drift_barrier': 'some/other/path.md'}
    obj, warnings, _ = parse_structured_record(str(path), text, {})
    assert obj is not None
    assert obj.id == 'claim_drift_barrier'


def test_classify_file_markdown_with_frontmatter_is_structured(clean_project_path):
    path = clean_project_path / 'claims' / 'claim_action_conditioning.md'
    classification, fm_type, _err = classify_file(path, path.stat().st_size)
    assert classification == 'structured'
    assert fm_type == 'claim'


def test_classify_file_markdown_without_frontmatter_is_unstructured(clean_project_path):
    path = clean_project_path / 'notes' / 'scratch.md'
    classification, fm_type, _err = classify_file(path, path.stat().st_size)
    assert classification == 'unstructured'
    assert fm_type is None


def test_classify_file_pdf_is_content_extracted(clean_project_path):
    path = clean_project_path / 'papers' / 'short_paper.pdf'
    classification, fm_type, _err = classify_file(path, path.stat().st_size)
    assert classification == 'content_extracted'
    assert fm_type is None


def test_classify_file_large_file_over_100mb(tmp_path):
    fake = tmp_path / 'huge.md'
    fake.write_text('x')
    classification, fm_type, _err = classify_file(fake, 200 * 1024 * 1024)
    assert classification == 'large_file'


def test_classify_file_malformed_yaml_classifies_unstructured(error_project_path):
    path = error_project_path / 'bad_yaml.md'
    classification, fm_type, yaml_error = classify_file(path, path.stat().st_size)
    assert classification == 'unstructured'
    assert fm_type is None
    assert yaml_error is not None
    assert 'yaml' in yaml_error.lower() or 'mapping' in yaml_error.lower() or 'expected' in yaml_error.lower()


def test_scan_and_parse_bad_yaml_keeps_classification_unstructured(error_project_path):
    empty_state = _empty_state()
    new_index, _records, warnings, _packs_ok, _packs_rw, _idgen = scan_and_parse(error_project_path, empty_state)
    assert new_index['bad_yaml.md']['classification'] == 'unstructured'
    assert any('bad_yaml.md' in w and 'YAML' in w for w in warnings)


def test_classify_file_binary_is_non_text(tmp_path):
    binary = tmp_path / 'data.bin'
    binary.write_bytes(b'\x00\x01\x02\xff' * 1024)
    classification, fm_type, _err = classify_file(binary, binary.stat().st_size)
    assert classification == 'non_text'


def test_walk_finds_all_clean_fixture_files(clean_project_path):
    entries = list(walk_project(clean_project_path))
    rel_paths = sorted(rel for rel, _abs, _size in entries)
    assert 'claims/claim_action_conditioning.md' in rel_paths
    assert 'evidence/ev_supporting_eval.md' in rel_paths
    assert 'papers/short_paper.pdf' in rel_paths
    assert 'notes/scratch.md' in rel_paths
    assert 'README.md' in rel_paths
    assert len(entries) == 16


def test_walk_respects_hardcoded_excludes(clean_project_path):
    (clean_project_path / '.git').mkdir()
    (clean_project_path / '.git' / 'config').write_text('[core]')
    (clean_project_path / 'node_modules').mkdir()
    (clean_project_path / 'node_modules' / 'pkg.json').write_text('{}')
    (clean_project_path / '__pycache__').mkdir()
    (clean_project_path / '__pycache__' / 'x.pyc').write_bytes(b'\x00')
    (clean_project_path / 'artifacts').mkdir(exist_ok=True)
    (clean_project_path / 'artifacts' / 'current_state.md').write_text('# Generated')

    entries = list(walk_project(clean_project_path))
    rel_paths = [rel for rel, _abs, _size in entries]
    assert not any(p.startswith('.git/') for p in rel_paths)
    assert not any(p.startswith('node_modules/') for p in rel_paths)
    assert not any(p.startswith('__pycache__/') for p in rel_paths)
    assert not any(p.startswith('artifacts/') for p in rel_paths)


def test_walk_respects_gitignore(clean_project_path):
    (clean_project_path / '.gitignore').write_text('*.tmp\nignored_dir/\n')
    (clean_project_path / 'scratch.tmp').write_text('temp')
    (clean_project_path / 'ignored_dir').mkdir()
    (clean_project_path / 'ignored_dir' / 'x.md').write_text('# y')

    entries = list(walk_project(clean_project_path))
    rel_paths = [rel for rel, _abs, _size in entries]
    assert 'scratch.tmp' not in rel_paths
    assert not any(p.startswith('ignored_dir/') for p in rel_paths)


def test_extract_pdf_text_returns_text_for_valid_pdf(clean_project_path):
    path = clean_project_path / 'papers' / 'short_paper.pdf'
    text, page_count = extract_pdf_text(path)
    assert text is not None
    assert 'claim_drift_barrier' in text
    assert 'drift-barrier' in text.lower()


def test_extract_pdf_text_returns_none_for_corrupt_pdf(tmp_path):
    bad = tmp_path / 'corrupt.pdf'
    bad.write_bytes(b'not a real pdf at all')
    text, page_count = extract_pdf_text(bad)
    assert text is None


def test_extract_pdf_text_returns_none_for_missing_file(tmp_path):
    text, page_count = extract_pdf_text(tmp_path / 'nope.pdf')
    assert text is None


def test_scan_for_id_mentions_finds_substring():
    text = 'We are investigating claim_action_conditioning today.'
    found = scan_for_id_mentions(text, ['claim_action_conditioning', 'claim_unrelated'])
    assert found == ['claim_action_conditioning']


def test_scan_for_id_mentions_finds_multiple_distinct_ids():
    text = 'See claim_action_conditioning and claim_drift_barrier in the writeup.'
    found = scan_for_id_mentions(text, ['claim_action_conditioning', 'claim_drift_barrier', 'unused'])
    assert set(found) == {'claim_action_conditioning', 'claim_drift_barrier'}


def test_scan_for_id_mentions_deduplicates_repeats():
    text = 'claim_x said claim_x and again claim_x.'
    found = scan_for_id_mentions(text, ['claim_x'])
    assert found == ['claim_x']


def test_scan_for_id_mentions_filters_short_ids():
    text = 'We use ev1 as a shorthand throughout this note.'
    found = scan_for_id_mentions(text, ['ev1', 'ev_supporting_eval'])
    assert found == []


def test_scan_for_id_mentions_clean_fixture_scratch(clean_project_path):
    text = (clean_project_path / 'notes' / 'scratch.md').read_text(encoding='utf-8')
    real_ids = ['claim_action_conditioning', 'claim_scaling_priority', 'claim_drift_barrier']
    short_id_in_state = 'ev1'
    found = scan_for_id_mentions(text, real_ids + [short_id_in_state])
    assert 'claim_action_conditioning' in found
    assert 'ev1' not in found
    assert 'claim_phantom' not in found


def test_scan_and_parse_clean_fixture_finds_all_structured(clean_project_path):
    empty_state = _empty_state()
    new_index, parsed_records, warnings, _packs_ok, _packs_rw, _idgen = scan_and_parse(clean_project_path, empty_state)
    assert len(parsed_records) == 12
    ids = sorted(obj.id for obj in parsed_records)
    assert 'claim_action_conditioning' in ids
    assert 'claim_drift_barrier' in ids
    assert 'dec_deprioritize_scaling' in ids
    assert warnings == []


def test_scan_and_parse_indexes_all_16_files(clean_project_path):
    empty_state = _empty_state()
    new_index, _records, _warnings, _packs_ok, _packs_rw, _idgen = scan_and_parse(clean_project_path, empty_state)
    assert len(new_index) == 16
    assert new_index['claims/claim_action_conditioning.md']['classification'] == 'structured'
    assert new_index['claims/claim_action_conditioning.md']['frontmatter_type'] == 'claim'
    assert new_index['papers/short_paper.pdf']['classification'] == 'content_extracted'
    assert new_index['notes/scratch.md']['classification'] == 'unstructured'


def test_scan_and_parse_id_mentions_in_unstructured_notes(clean_project_path):
    empty_state = _empty_state()
    new_index, _records, _warnings, _packs_ok, _packs_rw, _idgen = scan_and_parse(clean_project_path, empty_state)
    scratch_entry = new_index['notes/scratch.md']
    assert 'claim_action_conditioning' in scratch_entry['id_mentions']
    assert 'claim_phantom' not in scratch_entry['id_mentions']


def test_scan_and_parse_id_mentions_in_pdf(clean_project_path):
    empty_state = _empty_state()
    new_index, _records, _warnings, _packs_ok, _packs_rw, _idgen = scan_and_parse(clean_project_path, empty_state)
    pdf_entry = new_index['papers/short_paper.pdf']
    assert 'claim_drift_barrier' in pdf_entry['id_mentions']


def test_scan_and_parse_deterministic_under_dup_ids(error_project_path):
    empty_state = _empty_state()
    new_index_a, records_a, warnings_a, _packs_ok, _packs_rw, _idgen = scan_and_parse(error_project_path, empty_state)
    new_index_b, records_b, warnings_b, _packs_ok, _packs_rw, _idgen = scan_and_parse(error_project_path, _empty_state())
    a_ids = sorted(o.id for o in records_a)
    b_ids = sorted(o.id for o in records_b)
    assert a_ids == b_ids
    assert warnings_a == warnings_b
    assert any('claim_dup' in w for w in warnings_a)
    assert any('duplicate_id_2.md' in w for w in warnings_a)


def test_scan_and_parse_corrupt_pdf_downgrades_to_non_text(tmp_path):
    bad_pdf = tmp_path / 'bad.pdf'
    bad_pdf.write_bytes(b'not a real pdf')
    empty_state = _empty_state()
    new_index, _records, warnings, _packs_ok, _packs_rw, _idgen = scan_and_parse(tmp_path, empty_state)
    assert new_index['bad.pdf']['classification'] == 'non_text'
    assert any('bad.pdf' in w and 'extraction failed' in w for w in warnings)


def test_scan_and_parse_broken_ref_does_not_block_ingest(broken_refs_path):
    empty_state = _empty_state()
    new_index, records, warnings, _packs_ok, _packs_rw, _idgen = scan_and_parse(broken_refs_path, empty_state)
    assert len(records) == 1
    assert records[0].id == 'ev_dangling_e5'


def _empty_state():
    return {
        'schema_version': 1,
        'cofr_version': '0.1.0',
        'claims': [],
        'evidence': [],
        'experiments': [],
        'decisions': [],
        'open_questions': [],
        'risks': [],
        'artifacts': [],
    }


def test_parse_structured_record_malformed_field_goes_to_extra_sections():
    text = (
        '---\n'
        'type: evidence\n'
        'id: ev_malformed\n'
        '---\n\n'
        '## Summary\n\nsome summary text\n\n'
        '## Affects claims\n\nThis section is prose, not a bullet list.\n'
    )
    obj, warnings, _ = parse_structured_record('evidence/x.md', text, {})
    from cofr.domain import Evidence
    assert isinstance(obj, Evidence)
    assert obj.claim_links == []
    assert 'affects_claims' in obj.extra_sections
    assert 'prose, not a bullet list' in obj.extra_sections['affects_claims']
    assert any('affects_claims' in w or 'polarity_list' in w for w in warnings)


def test_pdf_page_count_uses_markitdown_text_form_feed():
    '''PDF page count: try markitdown text \\f markers FIRST, then regex on raw bytes.'''
    assert _page_count_from_text(None) is None
    assert _page_count_from_text('') is None
    assert _page_count_from_text('no markers here') is None
    assert _page_count_from_text('p1\fp2\fp3') == 3
    assert _page_count_from_text('p1\fp2') == 2


def test_source_slug_mismatch_marks_pack_for_rewrite(tmp_path):
    '''source_slug differing from pack filename stem should auto-correct AND rewrite pack on disk.'''
    run_cofr(['init', str(tmp_path)])
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    pack = ev_dir / 'foo.yaml'
    pack.write_text('- id: ev_a\n  type: evidence\n  summary: hello\n  source_slug: wrong_slug\n')
    r = run_cofr(['refresh', str(tmp_path)])
    assert r.returncode in (0, 1)
    text = pack.read_text()
    assert 'wrong_slug' not in text, 'pack on disk must be rewritten with corrected source_slug'
    assert 'source_slug: foo' in text


def test_invalid_anchor_fields_are_preserved_in_public_json_projection(tmp_path):
    run_cofr(['init', str(tmp_path)])
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    (ev_dir / '__misc__.yaml').write_text(
        '- id: ev_bad\n'
        '  type: evidence\n'
        '  summary: Bad anchor\n'
        '  source_path: ../outside.pdf\n'
        '  source_anchors:\n'
        '    - page: 9999\n'
    )
    r = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    ev = env['data']['state']['evidence'][0]
    assert ev.get('source_path', '') == '../outside.pdf'
    assert ev.get('source_anchors', []) == [{'page': 9999}]
    state_ev = json.loads((tmp_path / '.cofr' / 'state.json').read_text())['evidence'][0]
    assert state_ev.get('source_path', '') == ''
    assert state_ev.get('source_anchors', []) == []
    assert state_ev.get('_preserved_user_fields') == {
        'source_path': '../outside.pdf',
        'source_anchors': [{'page': 9999}],
    }


def test_text_anchor_line_must_be_integer(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'source.md').write_text('# Intro\nbody\n')
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    (ev_dir / '__misc__.yaml').write_text(
        '- id: ev_bad_line\n'
        '  type: evidence\n'
        '  summary: Bad line\n'
        '  source_path: source.md\n'
        '  source_anchors:\n'
        '    - section: Intro\n'
        '      line: not-an-int\n'
    )
    r = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    ev = env['data']['state']['evidence'][0]
    assert ev.get('source_anchors', []) == [{'section': 'Intro', 'line': 'not-an-int'}]
    state_ev = json.loads((tmp_path / '.cofr' / 'state.json').read_text())['evidence'][0]
    assert state_ev.get('source_anchors', []) == []
    assert state_ev.get('_preserved_user_fields') == {
        'source_anchors': [{'section': 'Intro', 'line': 'not-an-int'}],
    }
    assert any('anchor line must be int' in w for w in env['data']['warnings'])


def test_idless_markdown_at_refresh_falls_through_to_uuid_per_plan(tmp_path):
    '''Plan: refresh-time ingestion of idless markdown does NOT attempt title-slug
    derivation (refresh is a read path). Full UUID is used.'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'untitled.md').write_text('---\ntype: claim\ntitle: Hello World Claim\n---\n\n## Statement\n\nS.\n')
    run_cofr(['refresh', str(tmp_path)])
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert state['claims'][0]['id'].startswith('claim_')
    assert state['claims'][0]['id'] != 'claim_hello_world_claim'
    assert len(state['claims'][0]['id'].split('_')[-1]) == 32


def test_exclude_patterns_skips_matching_files_during_refresh(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'config.yaml').write_text(
        'project_name: ""\nproject_objective: ""\nexclude_patterns:\n  - "ignored/**"\n'
    )
    (tmp_path / 'ignored').mkdir()
    (tmp_path / 'ignored' / 'secret.md').write_text('# secret\n')
    (tmp_path / 'visible.md').write_text('# visible\n')
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    assert result.returncode in (0, 1), result.stderr
    env = json.loads(result.stdout)
    state = env['data']['state']
    index_path = tmp_path / '.cofr' / 'index.json'
    index = json.loads(index_path.read_text())
    assert 'ignored/secret.md' not in index, f'exclude_patterns ignored; index has: {list(index)}'
    assert 'visible.md' in index


def test_exclude_patterns_glob_matches_nested_paths(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'config.yaml').write_text(
        'project_name: ""\nproject_objective: ""\nexclude_patterns:\n  - "data/**"\n'
    )
    nested = tmp_path / 'data' / 'sub' / 'deep'
    nested.mkdir(parents=True)
    (nested / 'buried.txt').write_text('buried\n')
    result = run_cofr(['refresh', str(tmp_path)])
    assert result.returncode in (0, 1), result.stderr
    index = json.loads((tmp_path / '.cofr' / 'index.json').read_text())
    for p in index:
        assert not p.startswith('data/'), f'exclude pattern data/** did not skip {p}'


def test_frontmatter_title_wins_over_body_title_section(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '''---
type: claim
id: claim_title_precedence
title: FrontmatterWins
status: provisionally_supported
confidence: medium
---

## Title

BodyShouldLose

## Statement

stmt
'''
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    c = next(c for c in state['claims'] if c['id'] == 'claim_title_precedence')
    assert c['title'] == 'FrontmatterWins', f'body section overwrote frontmatter: got {c["title"]!r}'


def test_frontmatter_statement_wins_over_body_statement_section(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '''---
type: claim
id: claim_stmt_precedence
title: T
status: provisionally_supported
confidence: medium
statement: FRONTMATTER_STMT
---

## Statement

BODY_STMT_SHOULD_LOSE
'''
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    c = next(c for c in state['claims'] if c['id'] == 'claim_stmt_precedence')
    assert c['statement'] == 'FRONTMATTER_STMT', f'body overwrote frontmatter: got {c["statement"]!r}'


def test_refresh_path_also_honors_frontmatter_precedence(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'claims' / 'claim_pref.md').write_text(
        '---\n'
        'type: claim\n'
        'id: claim_refresh_pref\n'
        'title: FromFrontmatter\n'
        'status: provisionally_supported\n'
        'confidence: medium\n'
        '---\n\n'
        '## Title\n\n'
        'FromBody\n\n'
        '## Statement\n\nx\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    c = next(c for c in state['claims'] if c['id'] == 'claim_refresh_pref')
    assert c['title'] == 'FromFrontmatter'


def test_invalid_polarity_normalizes_with_warning(tmp_path):
    run_cofr(['init', str(tmp_path)])
    setup = '''---
type: claim
id: claim_polarity_target
title: T
status: provisionally_supported
confidence: medium
---
## Statement
x
'''
    subprocess.run(['cofr', 'add', str(tmp_path)], input=setup, capture_output=True, text=True)
    body = '''---
type: evidence
id: ev_typo
evidence_type: manual_observation
strength: medium
summary: T
data_source: foo
claim_links:
  - {claim_id: claim_polarity_target, polarity: oppose}
---
'''
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert 'polarity' in combined.lower() and ('invalid' in combined.lower() or 'oppose' in combined.lower()), \
        f'no warning for invalid polarity; output:\n{combined}'
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    ev = next(e for e in state['evidence'] if e['id'] == 'ev_typo')
    link = ev['claim_links'][0]
    assert link['polarity'] in ('supports', 'opposes'), f'invalid polarity persisted: {link["polarity"]!r}'


def test_nested_claims_yaml_is_unstructured_not_pack(tmp_path):
    run_cofr(['init', str(tmp_path)])
    notes = tmp_path / 'notes'
    notes.mkdir()
    (notes / 'claims.yaml').write_text('- id: claim_nested\n  type: claim\n  title: Nested\n')
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    assert result.returncode in (0, 1), result.stderr
    env = json.loads(result.stdout)
    assert env['data']['state']['claims'] == []
    index = json.loads((tmp_path / '.cofr' / 'index.json').read_text())
    assert index['notes/claims.yaml']['classification'] == 'unstructured'


def test_nested_evidences_yaml_is_unstructured_not_pack(tmp_path):
    run_cofr(['init', str(tmp_path)])
    nested = tmp_path / 'notes' / 'evidences'
    nested.mkdir(parents=True)
    (nested / 'source.yaml').write_text('- id: ev_nested\n  type: evidence\n  summary: Nested\n')
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    assert result.returncode in (0, 1), result.stderr
    env = json.loads(result.stdout)
    assert env['data']['state']['evidence'] == []
    index = json.loads((tmp_path / '.cofr' / 'index.json').read_text())
    assert index['notes/evidences/source.yaml']['classification'] == 'unstructured'


def test_frontmatter_claim_links_missing_polarity_normalizes_to_supports(tmp_path):
    run_cofr(['init', str(tmp_path)])
    setup = '---\ntype: claim\nid: claim_t\ntitle: T\nstatus: provisionally_supported\nconfidence: medium\n---\n## Statement\nx\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=setup, capture_output=True, text=True)
    body = '''---
type: evidence
id: ev_no_polarity
evidence_type: manual_observation
strength: medium
summary: T
data_source: foo
claim_links:
  - {claim_id: claim_t}
---
'''
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    ev = next(e for e in state['evidence'] if e['id'] == 'ev_no_polarity')
    link = ev['claim_links'][0]
    assert link.get('polarity') == 'supports', f'polarity not normalized to supports; got {link!r}'


def test_id_derivation_does_not_walk_body_when_frontmatter_title_absent(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '''---
type: claim
status: provisionally_supported
confidence: medium
---

## Title

BodyDerivedTitle

## Statement

stmt
'''
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    c = state['claims'][0]
    assert 'bodyderivedtitle' not in c['id'].lower(), \
        f'id derived from body section ## Title (spec says frontmatter ONLY): {c["id"]!r}'
    assert c['id'].startswith('claim_'), f'unexpected id shape: {c["id"]!r}'


def test_legacy_markdown_user_authored_stale_is_warned_and_stripped(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'evidences').mkdir(exist_ok=True)
    (tmp_path / 'evidences' / 'ev_legacy.md').write_text(
        '---\n'
        'type: evidence\n'
        'id: ev_legacy\n'
        'evidence_type: manual_observation\n'
        'strength: medium\n'
        'status: active\n'
        'stale: true\n'
        '---\n'
        '\n'
        '## Summary\n'
        'Should remain live.\n'
    )
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    warnings_blob = json.dumps(env)
    assert 'stale is system-managed' in warnings_blob, f'user-authored stale must produce a warning; got envelope warnings: {env.get("_warnings")!r} / data: {env["data"].get("warnings")!r}'
    state = env['data']['state']
    ev = [e for e in state.get('evidence', []) if e['id'] == 'ev_legacy']
    assert ev, 'ev_legacy should be ingested'
    assert ev[0].get('stale') is False, f'stale must be stripped (system-managed); got stale={ev[0].get("stale")!r}'
