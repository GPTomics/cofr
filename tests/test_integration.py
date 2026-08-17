import json
import subprocess

from conftest import GOLDEN_DIR, normalize_for_golden, run_cofr


def test_version_runs():
    result = run_cofr(['--version'])
    assert result.returncode == 0
    assert 'cofr' in result.stdout


def test_top_level_help_contains_concepts_section():
    result = run_cofr(['--help'])
    assert result.returncode == 0
    assert 'Concepts' in result.stdout
    assert 'claim' in result.stdout
    assert 'evidence' in result.stdout


def test_top_level_help_teaches_research_manager_workflows():
    result = run_cofr(['--help'])
    assert result.returncode == 0
    for workflow in ('Onboard:', 'Ingest:', 'Review:', 'Evolve:'):
        assert workflow in result.stdout
    assert 'cofr show state --json' in result.stdout
    assert 'cat .cofr/index.json' in result.stdout
    assert 'cofr show diff --json' in result.stdout
    assert 'durable tracking change' in result.stdout
    assert 'structured record' in result.stdout


def test_top_level_help_states_human_agent_manager_ownership():
    result = run_cofr(['--help'])
    assert result.returncode == 0
    assert 'The human directs and corrects judgment' in result.stdout
    assert 'The agent reads sources' in result.stdout
    assert 'cofr validates, tracks history' in result.stdout


def test_subcommand_help_runs():
    for sub in ('init', 'refresh'):
        result = run_cofr([sub, '--help'])
        assert result.returncode == 0


def test_add_help_preserves_available_technical_depth():
    result = run_cofr(['add', '--help'])
    assert result.returncode == 0
    assert 'Preserve technical depth' in result.stdout
    assert 'Markdown/LaTeX' in result.stdout
    assert 'define every symbol' in result.stdout
    assert 'Never invent precision' in result.stdout


def test_add_help_describes_dry_run_as_record_preview():
    result = run_cofr(['add', '--help'])
    assert result.returncode == 0
    assert 'Preview the normalized candidate record' in result.stdout
    assert 'routed pack, warnings, and collisions' in result.stdout
    assert 'cofr add --dry-run' in result.stdout


def test_add_help_evidence_example_points_back_to_source():
    result = run_cofr(['add', '--help'])
    assert result.returncode == 0
    assert 'source_path: reports/v4_holdout.md' in result.stdout
    assert 'source_anchors:' in result.stdout


def test_m2_help_mentions_repair_and_all_flags():
    refresh_help = run_cofr(['refresh', '--help'])
    assert refresh_help.returncode == 0
    assert '--rebuild-timelines' in refresh_help.stdout
    assert '--rebuild-renames-log' in refresh_help.stdout

    claims_help = run_cofr(['show', 'claims', '--help'])
    assert claims_help.returncode == 0
    assert '--all' in claims_help.stdout

    questions_help = run_cofr(['show', 'questions', '--help'])
    assert questions_help.returncode == 0
    assert '--all' in questions_help.stdout
    assert '--summary' in questions_help.stdout


def test_m3_subcommand_help_includes_example_invocation():
    '''Bug #12: each M3 show subcommand help carries a concrete example invocation.'''
    for sub in (['show', 'risks'], ['show', 'contradictions'], ['show', 'review']):
        result = run_cofr([*sub, '--help'])
        assert result.returncode == 0
        assert 'Example:' in result.stdout, f"{' '.join(sub)} help lacks an Example: line"
        assert f"cofr {' '.join(sub)}" in result.stdout, f"{' '.join(sub)} help example lacks an invocation"


def test_m3_subcommand_help_documents_warning_visibility():
    '''Codex follow-up: each M3 show block documents that its computed/detection
    warnings print to stderr in non-JSON mode and to data.warnings under --json,
    so the exit-1 cause is never undocumented. The three blocks must agree.'''
    for sub in (['show', 'risks'], ['show', 'contradictions'], ['show', 'review']):
        result = run_cofr([*sub, '--help'])
        assert result.returncode == 0
        assert 'stderr in non-JSON mode' in result.stdout, \
            f"{' '.join(sub)} help omits the stderr-visibility note"
        assert 'data.warnings under --json' in result.stdout, \
            f"{' '.join(sub)} help omits the --json visibility note"


def test_m3_and_questions_help_blocks_have_when_sections():
    '''Audit finding 3: show risks/contradictions/review/questions --help each
    carry When to run / When NOT to run, matching the M1 blocks and design
    principle #7's what/when/when-not briefing contract.'''
    for sub in (['show', 'risks'], ['show', 'contradictions'], ['show', 'review'], ['show', 'questions']):
        result = run_cofr([*sub, '--help'])
        assert result.returncode == 0
        assert 'When to run:' in result.stdout, f"{' '.join(sub)} help lacks 'When to run:'"
        assert 'When NOT to run:' in result.stdout, f"{' '.join(sub)} help lacks 'When NOT to run:'"


def test_init_creates_cofr_dir(tmp_path):
    result = run_cofr(['init', str(tmp_path)])
    assert result.returncode == 0
    assert (tmp_path / '.cofr').is_dir()
    assert (tmp_path / '.cofr' / 'state.json').is_file()
    assert (tmp_path / 'artifacts').is_dir()


def test_init_idempotent(tmp_path):
    run_cofr(['init', str(tmp_path)])
    state_path = tmp_path / '.cofr' / 'state.json'
    marker = '{"marker": "do-not-touch"}'
    state_path.write_text(marker)
    result = run_cofr(['init', str(tmp_path)])
    assert result.returncode == 0
    assert state_path.read_text() == marker


def test_show_state_uninitialized_exits_3(tmp_path):
    result = run_cofr(['show', 'state', str(tmp_path)])
    assert result.returncode == 3
    assert 'not initialized' in result.stderr.lower() or 'cofr init' in result.stderr.lower()


def test_refresh_on_clean_fixture_produces_state(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    result = run_cofr(['refresh', str(clean_project_path)])
    assert result.returncode in (0, 1)
    state = json.loads((clean_project_path / '.cofr' / 'state.json').read_text())
    assert len(state['claims']) == 3
    assert len(state['evidence']) == 6
    assert len(state['decisions']) == 1
    assert len(state['open_questions']) == 1
    assert len(state['risks']) == 1


def test_refresh_writes_current_state_artifact(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    artifact = clean_project_path / 'artifacts' / 'current_state.md'
    assert artifact.is_file()
    content = artifact.read_text()
    assert 'Generated by cofr' in content
    assert 'Action-conditioning' in content
    assert 'drift-barrier' in content.lower()


def test_show_state_json_envelope_shape(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    result = run_cofr(['show', 'state', '--json', str(clean_project_path)])
    assert result.returncode in (0, 1)
    env = json.loads(result.stdout)
    assert env['schema_version'] == 1
    assert env['cofr_version']
    assert env['generated_at']
    assert env['project_path']
    data = env['data']
    assert 'claims' in data
    assert 'evidence' in data
    assert '_index_summary' in data


def test_show_claims_summary_json(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    result = run_cofr(['show', 'claims', '--json', '--summary', str(clean_project_path)])
    assert result.returncode == 0
    env = json.loads(result.stdout)
    claims = env['data']['claims']
    assert len(claims) == 3
    for c in claims:
        assert set(c.keys()) == {'id', 'title', 'status', 'confidence', 'supporting_evidence_count', 'counter_evidence_count'}


def test_show_claims_full_json_has_computed_fields(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    result = run_cofr(['show', 'claims', '--json', str(clean_project_path)])
    env = json.loads(result.stdout)
    claims = {c['id']: c for c in env['data']['claims']}
    assert claims['claim_action_conditioning']['supporting_evidence_count'] == 2
    assert claims['claim_action_conditioning']['counter_evidence_count'] == 1
    assert 'notes/scratch.md' in claims['claim_action_conditioning']['mentioned_in']
    assert claims['claim_drift_barrier']['supporting_evidence_count'] == 2
    assert claims['claim_scaling_priority']['supporting_evidence_count'] == 0


def test_show_state_empty_project_returns_note(tmp_path):
    run_cofr(['init', str(tmp_path)])
    result = run_cofr(['show', 'state', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    assert '_note' in env
    assert 'No structured state' in env['_note']


def test_show_claims_empty_project_returns_note(tmp_path):
    run_cofr(['init', str(tmp_path)])
    result = run_cofr(['show', 'claims', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    assert '_note' in env
    assert 'No claims' in env['_note']


def test_refresh_error_fixture_emits_warnings(error_project_path):
    run_cofr(['init', str(error_project_path)])
    result = run_cofr(['refresh', '--json', str(error_project_path)])
    env = json.loads(result.stdout)
    warnings = env['data']['warnings']
    assert any('claim_dup' in w for w in warnings)
    assert any('hypothesis' in w for w in warnings)
    assert any('claim_does_not_exist' in w for w in warnings)


def test_refresh_json_state_matches_show_state_shape(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    refresh_env = json.loads(run_cofr(['refresh', '--json', str(clean_project_path)]).stdout)
    show_env = json.loads(run_cofr(['show', 'state', '--json', str(clean_project_path)]).stdout)
    refresh_state = refresh_env['data']['state']
    show_state = show_env['data']
    assert '_index_summary' in refresh_state
    assert '_index_mentions' in refresh_state
    refresh_claims = {c['id']: c for c in refresh_state['claims']}
    show_claims = {c['id']: c for c in show_state['claims']}
    for cid, rc in refresh_claims.items():
        assert 'mentioned_in' in rc
        assert 'supporting_evidence_count' in rc
        assert rc['mentioned_in'] == show_claims[cid]['mentioned_in']
        assert rc['supporting_evidence_count'] == show_claims[cid]['supporting_evidence_count']


def test_show_state_surfaces_load_warnings_in_envelope(tmp_path):
    run_cofr(['init', str(tmp_path)])
    bad_state = {
        'schema_version': 2,
        'cofr_version': '0.1.0',
        'last_refresh': '2026-05-13T14:30:00Z',
        'claims': [{'id': 'claim_x', 'status': 'nonsense', 'confidence': 'medium', 'parsed_from': 'claims/x.md'}],
        'evidence': [], 'experiments': [], 'decisions': [],
        'open_questions': [], 'risks': [], 'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(bad_state))
    result = run_cofr(['show', 'state', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    assert '_warnings' in env
    assert any('nonsense' in w for w in env['_warnings'])
    assert result.returncode == 1


def test_show_claims_surfaces_load_warnings_in_envelope(tmp_path):
    run_cofr(['init', str(tmp_path)])
    bad_state = {
        'schema_version': 2,
        'cofr_version': '0.1.0',
        'claims': [{'id': 'claim_x', 'status': 'nonsense', 'confidence': 'medium', 'parsed_from': 'claims/x.md'}],
        'evidence': [], 'experiments': [], 'decisions': [],
        'open_questions': [], 'risks': [], 'artifacts': [],
    }
    (tmp_path / '.cofr' / 'state.json').write_text(json.dumps(bad_state))
    result = run_cofr(['show', 'claims', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    assert '_warnings' in env
    assert any('nonsense' in w for w in env['_warnings'])
    assert result.returncode == 1


def test_show_claims_excludes_retired_by_default(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    (clean_project_path / 'claims' / 'claim_scaling_priority.md').unlink()
    run_cofr(['refresh', str(clean_project_path)])
    result = run_cofr(['show', 'claims', '--json', str(clean_project_path)])
    claims = json.loads(result.stdout)['data']['claims']
    ids = [c['id'] for c in claims]
    assert 'claim_scaling_priority' not in ids
    assert 'claim_action_conditioning' in ids


def test_refresh_changes_populates_updated_on_field_change(clean_project_path):
    import time
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    drift_path = clean_project_path / 'claims' / 'claim_drift_barrier.md'
    drift_path.write_text(drift_path.read_text().replace('confidence: high', 'confidence: low'))
    time.sleep(1.1)
    result = run_cofr(['refresh', '--json', str(clean_project_path)])
    env = json.loads(result.stdout)
    updated_ids = [c['id'] for c in env['data']['changes']['updated']]
    assert 'claim_drift_barrier' in updated_ids


def test_refresh_changes_marks_deleted_files_as_removed(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    (clean_project_path / 'evidence' / 'ev_holdout_drop.md').unlink()
    result = run_cofr(['refresh', '--json', str(clean_project_path)])
    env = json.loads(result.stdout)
    removed_ids = [c['id'] for c in env['data']['changes']['removed']]
    assert 'ev_holdout_drop' in removed_ids


def test_refresh_idempotent_under_explicit_ids(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    state_a = (clean_project_path / '.cofr' / 'state.json').read_text()
    import time
    time.sleep(1.1)
    run_cofr(['refresh', str(clean_project_path)])
    state_b = (clean_project_path / '.cofr' / 'state.json').read_text()
    parsed_a = json.loads(state_a)
    parsed_b = json.loads(state_b)
    parsed_a['last_refresh'] = '<redacted>'
    parsed_b['last_refresh'] = '<redacted>'
    for art in parsed_a.get('artifacts', []) + parsed_b.get('artifacts', []):
        art['generated_at'] = '<redacted>'
    assert parsed_a == parsed_b


def test_add_writes_claim_via_stdin(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_via_add\nstatus: supported\nconfidence: high\n---\n\n## Title\n\nVia add.\n\n## Statement\n\nProseable.\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    pack = tmp_path / 'claims.yaml'
    assert pack.is_file()
    assert 'claim_via_add' in pack.read_text()
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert any(c['id'] == 'claim_via_add' for c in state['claims'])


def test_add_preserves_latex_and_multiline_technical_claim(tmp_path):
    run_cofr(['init', str(tmp_path)])
    statement = (
        'For the modeled regime:\n\n'
        '$$\\mu_{min} \\propto (N_e G)^{-1}$$\n\n'
        'where $N_e$ is effective population size and $G$ is functional genome size.\n'
    )
    body = (
        '---\ntype: claim\nid: claim_technical\n---\n\n'
        '## Title\n\nA technically specific claim\n\n'
        f'## Statement\n\n{statement}'
    )

    result = run_cofr(['add', str(tmp_path)], stdin=body)

    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    claim = next(c for c in state['claims'] if c['id'] == 'claim_technical')
    assert claim['statement'] == statement.strip()
    assert '$$\\mu_{min} \\propto (N_e G)^{-1}$$' in (tmp_path / 'claims.yaml').read_text()


def test_add_refuses_overwrite_without_force(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_dup\n---\n\n## Title\n\nFirst.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    second = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert second.returncode == 2
    assert 'already exists' in second.stderr.lower() or 'collision' in second.stderr.lower()


def test_add_force_overwrites(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_dup\n---\n\n## Title\n\nFirst.\n## Statement\n\nProse.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    body2 = '---\ntype: claim\nid: claim_dup\nstatus: supported\n---\n\n## Title\n\nReplaced.\n## Statement\n\nReplaced statement.\n'
    result = subprocess.run(['cofr', 'add', '--force', str(tmp_path)], input=body2, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    pack_text = (tmp_path / 'claims.yaml').read_text()
    assert 'Replaced.' in pack_text or 'Replaced statement.' in pack_text


def test_add_rejects_unknown_type(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: hypothesis\nid: hyp_x\n---\n\n## Title\n\nNope.\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'unknown type' in result.stderr.lower()


def test_add_rejects_missing_type(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\nid: claim_x\n---\n\n## Title\n\nMissing type.\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'type' in result.stderr.lower()


def test_add_dry_run_does_not_write(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_dry\n---\n\n## Title\n\nDry.\n'
    result = subprocess.run(['cofr', 'add', '--dry-run', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 0
    assert not (tmp_path / 'claims.yaml').exists()


def test_add_evidence_with_polarity(tmp_path):
    run_cofr(['init', str(tmp_path)])
    claim_body = '---\ntype: claim\nid: claim_target\n---\n\n## Title\n\nTarget.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=claim_body, capture_output=True, text=True)
    ev_body = '---\ntype: evidence\nid: ev_a\nstrength: high\n---\n\n## Summary\n\nObserved.\n\n## Affects claims\n\n- claim_target: opposes\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=ev_body, capture_output=True, text=True)
    assert result.returncode in (0, 1), result.stderr
    show_result = subprocess.run(['cofr', 'show', 'claims', '--json', str(tmp_path)], capture_output=True, text=True)
    claims = {c['id']: c for c in json.loads(show_result.stdout)['data']['claims']}
    assert claims['claim_target']['counter_evidence_count'] == 1


def test_add_on_uninitialized_project_exits_3(tmp_path):
    body = '---\ntype: claim\nid: claim_x\n---\n\n## Title\n\nx\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 3


def test_add_idless_is_stable_across_refresh(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\ntitle: drift barrier hypothesis\nstatus: provisionally_supported\nconfidence: medium\n---\n\n## Statement\n\nProse.\n'
    add_result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert add_result.returncode == 0, add_result.stderr
    state_after_add = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    claims_after_add = state_after_add.get('claims', [])
    assert len(claims_after_add) == 1
    derived_id = claims_after_add[0]['id']
    assert derived_id and derived_id != ''
    pack = tmp_path / 'claims.yaml'
    assert pack.is_file()
    assert derived_id in pack.read_text()
    run_cofr(['refresh', str(tmp_path)])
    state_after_refresh = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    ids_after_refresh = [c['id'] for c in state_after_refresh.get('claims', [])]
    assert ids_after_refresh == [derived_id]
    run_cofr(['refresh', str(tmp_path)])
    state_after_second_refresh = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert [c['id'] for c in state_after_second_refresh.get('claims', [])] == [derived_id]


def test_add_question_uses_frontmatter_title_for_derived_id(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '''---
type: question
title: Drift vs sampling temperature
priority: high
---

## Question

Does drift correlate with sampling temperature?
'''
    add_result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert add_result.returncode == 0, add_result.stderr
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert [q['id'] for q in state['open_questions']] == ['question_drift_vs_sampling_temperature']
    assert 'id: question_drift_vs_sampling_temperature' in (tmp_path / 'questions.yaml').read_text()


def test_add_rejects_numeric_explicit_id(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: 123\ntitle: Numeric\n---\n\n## Statement\n\nx\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'invalid id' in result.stderr.lower()
    assert not (tmp_path / 'claims.yaml').exists()


def test_add_at_rejects_path_outside_project(tmp_path):
    project = tmp_path / 'proj'
    project.mkdir()
    run_cofr(['init', str(project)])
    body = '---\ntype: claim\nid: claim_escape\n---\n\n## Title\n\nNope.\n'
    result = subprocess.run(['cofr', 'add', '--at', '../outside.md', str(project)], input=body, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'outside' in result.stderr.lower()
    assert not (tmp_path / 'outside.md').exists()
    absolute_target = tmp_path / 'absolute.md'
    result_abs = subprocess.run(['cofr', 'add', '--at', str(absolute_target), str(project)], input=body, capture_output=True, text=True)
    assert result_abs.returncode == 2
    assert not absolute_target.exists()


def test_add_rejects_explicit_id_with_path_traversal(tmp_path):
    project = tmp_path / 'proj'
    project.mkdir()
    run_cofr(['init', str(project)])
    body = '---\ntype: claim\nid: ../../escaped\n---\n\n## Title\n\nNope.\n## Statement\n\nNope.\n'
    result = subprocess.run(['cofr', 'add', str(project)], input=body, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'invalid id' in result.stderr.lower()
    assert not (tmp_path / 'escaped.md').exists()
    assert not list(tmp_path.glob('*.md'))


def test_add_rejects_explicit_id_with_slash(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claims/inner\n---\n\n## Title\n\nNope.\n## Statement\n\nNope.\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'invalid id' in result.stderr.lower()


def test_refresh_rejects_structured_file_with_bad_id(tmp_path):
    run_cofr(['init', str(tmp_path)])
    claims_dir = tmp_path / 'claims'
    claims_dir.mkdir(exist_ok=True)
    (claims_dir / 'bad.md').write_text('---\ntype: claim\nid: ../../escaped\n---\n\n## Title\n\nx\n')
    result = subprocess.run(['cofr', 'refresh', str(tmp_path)], capture_output=True, text=True)
    assert 'invalid id' in (result.stdout + result.stderr).lower()
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert state.get('claims', []) == []


def test_refresh_rejects_structured_file_with_numeric_id(tmp_path):
    run_cofr(['init', str(tmp_path)])
    claims_dir = tmp_path / 'claims'
    claims_dir.mkdir(exist_ok=True)
    (claims_dir / 'bad.md').write_text('---\ntype: claim\nid: 123\n---\n\n## Title\n\nx\n')
    result = subprocess.run(['cofr', 'refresh', '--json', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    env = json.loads(result.stdout)
    assert any('invalid id' in w.lower() for w in env['data']['warnings'])
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert state.get('claims', []) == []


def test_refresh_skips_pack_record_with_invalid_id(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: bad/id\n  type: claim\n  title: bad\n- id: claim_ok\n  type: claim\n  title: ok\n')
    (tmp_path / 'notes.md').write_text('Mention claim_ok only.\n')
    result = subprocess.run(['cofr', 'refresh', '--json', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    env = json.loads(result.stdout)
    assert any('invalid id' in w.lower() for w in env['data']['warnings'])
    assert [c['id'] for c in env['data']['state']['claims']] == ['claim_ok']


def test_refresh_skips_pack_record_with_numeric_id_without_crashing(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: 123\n  type: claim\n  title: numeric\n- id: claim_ok\n  type: claim\n  title: ok\n')
    (tmp_path / 'notes.md').write_text('Mention claim_ok only.\n')
    result = subprocess.run(['cofr', 'refresh', '--json', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    env = json.loads(result.stdout)
    assert any('invalid id' in w.lower() for w in env['data']['warnings'])
    assert [c['id'] for c in env['data']['state']['claims']] == ['claim_ok']


def test_add_slug_strips_non_ascii_from_derived_id(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\ntitle: Café claim about naïveté\nstatus: provisionally_supported\nconfidence: medium\n---\n\n## Statement\n\nProse.\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert len(state['claims']) == 1
    derived_id = state['claims'][0]['id']
    assert all(c.isascii() for c in derived_id)
    subprocess.run(['cofr', 'refresh', str(tmp_path)], capture_output=True, text=True)
    state_after = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    assert [c['id'] for c in state_after['claims']] == [derived_id]


def test_refresh_detects_in_place_file_edit(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_change_me\nstatus: provisionally_supported\nconfidence: medium\n---\n\n## Title\n\nChange detection target\n\n## Statement\n\nOriginal statement.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state_before = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    claim_before = next(c for c in state_before['claims'] if c['id'] == 'claim_change_me')
    assert claim_before['statement'] == 'Original statement.'
    index_before = json.loads((tmp_path / '.cofr' / 'index.json').read_text())
    assert 'claims.yaml' in index_before

    pack = tmp_path / 'claims.yaml'
    edited = pack.read_text().replace('Original statement.', 'Revised statement after re-analysis.')
    edited = edited.replace('confidence: medium', 'confidence: high')
    pack.write_text(edited)

    import time
    time.sleep(1.1)
    refresh = subprocess.run(['cofr', 'refresh', '--json', str(tmp_path)], capture_output=True, text=True)
    assert refresh.returncode in (0, 1)
    payload = json.loads(refresh.stdout)
    updated_ids = [u['id'] for u in payload['data']['changes']['updated']]
    assert 'claim_change_me' in updated_ids
    state_after = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    claim_after = next(c for c in state_after['claims'] if c['id'] == 'claim_change_me')
    assert claim_after['statement'] == 'Revised statement after re-analysis.'
    assert claim_after['confidence'] == 'high'
    assert claim_after['last_updated'] != claim_before['last_updated']
    assert claim_after['first_seen'] == claim_before['first_seen']


def test_current_state_matches_golden(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    actual = (clean_project_path / 'artifacts' / 'current_state.md').read_text()
    actual_redacted = normalize_for_golden(actual, project_path=clean_project_path)
    expected = (GOLDEN_DIR / 'clean_project_current_state.md').read_text()
    assert actual_redacted == expected


def test_show_state_json_matches_golden(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    result = run_cofr(['show', 'state', '--json', str(clean_project_path)])
    parsed = json.loads(result.stdout)
    actual_text = json.dumps(parsed, indent=2, sort_keys=True) + '\n'
    actual_redacted = normalize_for_golden(actual_text, project_path=clean_project_path)
    expected = (GOLDEN_DIR / 'clean_project_show_state.json').read_text()
    assert actual_redacted == expected


def test_show_claims_summary_matches_golden(clean_project_path):
    run_cofr(['init', str(clean_project_path)])
    run_cofr(['refresh', str(clean_project_path)])
    result = run_cofr(['show', 'claims', '--json', '--summary', str(clean_project_path)])
    parsed = json.loads(result.stdout)
    actual_text = json.dumps(parsed, indent=2, sort_keys=True) + '\n'
    actual_redacted = normalize_for_golden(actual_text, project_path=clean_project_path)
    expected = (GOLDEN_DIR / 'clean_project_show_claims_summary.json').read_text()
    assert actual_redacted == expected


def _assert_show_json_matches_golden(project_path, show_args, golden_name):
    run_cofr(['init', str(project_path)])
    run_cofr(['refresh', str(project_path)])
    result = run_cofr(['show', *show_args, '--json', str(project_path)])
    parsed = json.loads(result.stdout)
    actual_text = json.dumps(parsed, indent=2, sort_keys=True) + '\n'
    actual_redacted = normalize_for_golden(actual_text, project_path=project_path)
    expected = (GOLDEN_DIR / golden_name).read_text()
    assert actual_redacted == expected


def test_show_risks_json_matches_golden(contradictions_project_path):
    '''Audit finding 2: plan line 993 calls for a `show risks --json` envelope golden.'''
    _assert_show_json_matches_golden(contradictions_project_path, ['risks'], 'contradictions_project_show_risks.json')


def test_show_contradictions_json_matches_golden(contradictions_project_path):
    '''Audit finding 2: plan line 993 calls for a `show contradictions --json` golden.'''
    _assert_show_json_matches_golden(contradictions_project_path, ['contradictions'], 'contradictions_project_show_contradictions.json')


def test_show_review_json_matches_golden(contradictions_project_path):
    '''Audit finding 2: plan line 993 calls for a `show review --json` golden.'''
    _assert_show_json_matches_golden(contradictions_project_path, ['review'], 'contradictions_project_show_review.json')


def test_envelope_accepts_optional_generated_at(tmp_path):
    from cofr.cli import _envelope
    env = _envelope(tmp_path, {'x': 1}, generated_at='2026-05-16T18:55:47Z')
    assert env['generated_at'] == '2026-05-16T18:55:47Z'


def test_refresh_sidecar_and_envelope_generated_at_identical(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    sidecar_ts = env['data']['diff']['generated_at']
    envelope_ts = env['generated_at']
    assert sidecar_ts == envelope_ts


def test_envelope_data_summary_has_m2_keys(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    r = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(r.stdout)
    summary = env['data']['summary']
    assert 'pack_files' in summary
    assert 'structured_records' in summary
    assert 'warnings_count' in summary


def test_pack_rewrite_does_not_resurrect_user_deleted_record(tmp_path):
    '''Plan: pack rewrite only emits records found in parsed_records, not user-deleted ones.'''
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    body2 = '---\ntype: claim\nid: claim_b\n---\n\n## Title\n\nB.\n## Statement\n\nT.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body2, capture_output=True, text=True)
    pack_path = tmp_path / 'claims.yaml'
    text = pack_path.read_text()
    import yaml
    records = yaml.safe_load(text)
    records = [r for r in records if r.get('id') != 'claim_b']
    pack_path.write_text(yaml.dump(records))
    r = run_cofr(['rename', 'claim_a', 'claim_a_renamed', str(tmp_path)])
    pack_after = pack_path.read_text()
    assert 'claim_b' not in pack_after, 'pack rewrite resurrected user-deleted record'


def test_cofr_add_force_recycle_resets_first_seen(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_recycle\n---\n\n## Title\n\nA.\n## Statement\n\nS.\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state_path = tmp_path / '.cofr' / 'state.json'
    state_data = json.loads(state_path.read_text())
    for c in state_data['claims']:
        if c['id'] == 'claim_recycle':
            c['status'] = 'retired'
            first_seen_old = c['first_seen']
    state_path.write_text(json.dumps(state_data, indent=2, sort_keys=True) + '\n')
    pack_path = tmp_path / 'claims.yaml'
    pack_path.write_text('[]\n')
    subprocess.run(['cofr', 'refresh', str(tmp_path)], capture_output=True, text=True)
    body2 = '---\ntype: claim\nid: claim_recycle\n---\n\n## Title\n\nRecycled.\n## Statement\n\nNew.\n'
    import time
    time.sleep(1.1)
    r = subprocess.run(['cofr', 'add', '--force', str(tmp_path)], input=body2, capture_output=True, text=True)
    assert r.returncode in (0, 1), r.stderr
    state_after = json.loads(state_path.read_text())
    recycled = [c for c in state_after['claims'] if c['id'] == 'claim_recycle'][0]
    assert recycled['first_seen'] != first_seen_old


def test_help_top_includes_m2_commands_and_workflow():
    '''=== top === must list all M2 subcommands and include agent-workflow guidance.'''
    r = run_cofr(['--help'])
    text = r.stdout + r.stderr
    assert 'show questions' in text or 'show_questions' in text
    assert 'show diff' in text or 'show_diff' in text
    assert 'show overview' in text or 'show_overview' in text
    assert 'rename' in text
    assert 'migrate' in text
    assert 'what_changed' in text or 'show overview' in text, 'workflow guidance should mention post-refresh briefing'
    show_help = run_cofr(['show', '--help'])
    show_text = show_help.stdout + show_help.stderr
    assert 'show questions' in show_text
    assert 'show diff' in show_text
    assert 'show overview' in show_text


def test_help_top_section_does_not_promise_contradictions():
    result = run_cofr(['--help'])
    assert result.returncode == 0
    head = result.stdout.split('Commands:')[0]
    assert 'contradictions' not in head.lower(), f'help top mentions contradictions (M3): {head}'


def test_index_rebuilt_after_pack_rewrites(tmp_path):
    '''After pack rewrites, .cofr/index.json content_hash must reflect new pack content.'''
    run_cofr(['init', str(tmp_path)])
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    pack = ev_dir / 'foo.yaml'
    pack.write_text('- id: ev_a\n  type: evidence\n  summary: hello\n  source_slug: wrong\n')
    r = run_cofr(['refresh', str(tmp_path)])
    assert r.returncode in (0, 1)
    index = json.loads((tmp_path / '.cofr' / 'index.json').read_text())
    import hashlib
    actual_hash = hashlib.sha256(pack.read_bytes()).hexdigest()
    indexed_hash = index['evidences/foo.yaml']['content_hash']
    assert indexed_hash == actual_hash, 'index content_hash must match rewritten pack'


def test_cofr_add_surfaces_misc_evidence_routing_warning(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: evidence\nid: ev_no_source\n---\n\n## Summary\n\nNo source.\n'
    r = run_cofr(['add', str(tmp_path)], stdin=body)
    assert r.returncode == 1
    assert 'routing to evidences/__misc__.yaml' in r.stderr


def test_refresh_populates_state_artifacts_for_current_state_and_what_changed(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\ntitle: T\nstatus: provisionally_supported\nconfidence: medium\n---\n\n## Statement\n\nx\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    artifact_types = {a['artifact_type'] for a in state.get('artifacts', [])}
    assert 'current_state' in artifact_types, f'no current_state artifact record: {state.get("artifacts")}'
    assert 'what_changed' in artifact_types, f'no what_changed artifact record: {state.get("artifacts")}'


def test_artifact_records_include_path_generated_at_and_staleness(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\ntitle: T\nstatus: provisionally_supported\nconfidence: medium\n---\n\n## Statement\n\nx\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    arts = state.get('artifacts', [])
    by_type = {a['artifact_type']: a for a in arts}
    cs = by_type['current_state']
    assert cs['path'] == 'artifacts/current_state.md'
    assert cs['generated_at']
    assert cs['staleness_status'] == 'current'


def test_artifact_records_cover_live_claim_ids_only(tmp_path):
    run_cofr(['init', str(tmp_path)])
    live_body = '---\ntype: claim\ntitle: Live\nstatus: provisionally_supported\nconfidence: medium\n---\n\n## Statement\n\nlive\n'
    retired_body = '---\ntype: claim\ntitle: Retired\nstatus: retired\nconfidence: low\n---\n\n## Statement\n\nretired\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=live_body, capture_output=True, text=True)
    subprocess.run(['cofr', 'add', str(tmp_path)], input=retired_body, capture_output=True, text=True)
    state = json.loads((tmp_path / '.cofr' / 'state.json').read_text())
    cs = next(a for a in state['artifacts'] if a['artifact_type'] == 'current_state')
    live_ids = {c['id'] for c in state['claims'] if c.get('status') != 'retired'}
    retired_ids = {c['id'] for c in state['claims'] if c.get('status') == 'retired'}
    assert set(cs['covers_claim_ids']) == live_ids
    assert not (set(cs['covers_claim_ids']) & retired_ids), 'covers_claim_ids included retired claims'


def test_cmd_add_surfaces_parse_field_value_warning_for_malformed_key_value_section(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '''---
type: experiment
id: exp_bad_metrics
status: active
---
## Name
test
## Intent
testing
## Key metrics
- bullet with no colon here
- another colonless bullet
'''
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert ('key metrics' in combined.lower() or 'key_metrics' in combined.lower()) and ('malformed' in combined.lower() or 'no colon' in combined.lower()), \
        f'no warning surfaced for malformed ## Key metrics; output:\n{combined}'


def test_pack_rewrite_via_source_slug_correction_reemits_preserved_source_path(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'evidences').mkdir()
    pack = tmp_path / 'evidences' / 'real.yaml'
    pack.write_text(
        '- id: ev_slug_fix\n'
        '  type: evidence\n'
        '  evidence_type: manual_observation\n'
        '  strength: medium\n'
        '  status: active\n'
        '  summary: T\n'
        '  data_source: foo\n'
        '  source_slug: wrong_slug\n'
        "  source_path: '/abs/escape.pdf'\n"
    )
    run_cofr(['refresh', str(tmp_path)])
    text = pack.read_text()
    assert '/abs/escape.pdf' in text, f'preserved source_path lost after slug-correction rewrite; pack now:\n{text}'


def test_pack_rewrite_via_rename_cascade_reemits_preserved_source_path(tmp_path):
    run_cofr(['init', str(tmp_path)])
    claim_body = '---\ntype: claim\nid: claim_x\ntitle: T\nstatus: provisionally_supported\nconfidence: medium\n---\n## Statement\nx\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=claim_body, capture_output=True, text=True)
    (tmp_path / 'evidences').mkdir(exist_ok=True)
    pack = tmp_path / 'evidences' / 'p.yaml'
    pack.write_text(
        '- id: ev_for_rename\n'
        '  type: evidence\n'
        '  evidence_type: manual_observation\n'
        '  strength: medium\n'
        '  status: active\n'
        '  summary: T\n'
        '  data_source: foo\n'
        '  source_slug: p\n'
        "  source_path: '/abs/escape.pdf'\n"
        '  claim_links:\n'
        '    - {claim_id: claim_x, polarity: supports}\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    run_cofr(['rename', 'ev_for_rename', 'ev_renamed', str(tmp_path)])
    text = pack.read_text()
    assert '/abs/escape.pdf' in text, f'preserved source_path lost after rename cascade; pack now:\n{text}'


def test_cofr_add_force_does_not_silently_drop_malformed_pack_records(tmp_path):
    run_cofr(['init', str(tmp_path)])
    pack = tmp_path / 'claims.yaml'
    pack.write_text(
        '- id: claim_valid\n'
        '  type: claim\n'
        '  title: V\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
        '- type: claim\n'
        '  title: NoIdRecord\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_valid\ntitle: Replacement\nstatus: provisionally_supported\nconfidence: medium\n---\n## Statement\nnew\n'
    result = subprocess.run(['cofr', 'add', '--force', str(tmp_path)], input=body, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    text = pack.read_text()
    is_warned = 'no id' in combined.lower() or 'missing id' in combined.lower() or 'malformed' in combined.lower()
    has_record = 'NoIdRecord' in text
    assert is_warned or has_record, (
        f'--force silently dropped the no-id record AND did not warn.\n'
        f'output:\n{combined}\n\npack:\n{text}'
    )


def test_cofr_rename_does_not_silently_drop_malformed_pack_records(tmp_path):
    run_cofr(['init', str(tmp_path)])
    pack = tmp_path / 'claims.yaml'
    pack.write_text(
        '- id: claim_rename_src\n'
        '  type: claim\n'
        '  title: V\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
        '- type: claim\n'
        '  title: NoIdRecord\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['rename', 'claim_rename_src', 'claim_rename_dst', str(tmp_path)])
    combined = result.stdout + result.stderr
    text = pack.read_text()
    is_warned = 'no id' in combined.lower() or 'missing id' in combined.lower() or 'malformed' in combined.lower()
    has_record = 'NoIdRecord' in text
    assert is_warned or has_record, (
        f'rename silently dropped the no-id record AND did not warn.\n'
        f'output:\n{combined}\n\npack:\n{text}'
    )


def test_cmd_add_surfaces_load_config_warnings(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'config.yaml').write_text('- not a mapping\n')
    body = '---\ntype: claim\nid: claim_x\ntitle: T\nstatus: provisionally_supported\nconfidence: medium\n---\n## Statement\nx\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert 'config.yaml' in combined.lower(), f'config warning not surfaced via cofr add; output:\n{combined}'


def test_cmd_rename_surfaces_load_config_warnings(tmp_path):
    run_cofr(['init', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_to_rename\ntitle: T\nstatus: provisionally_supported\nconfidence: medium\n---\n## Statement\nx\n'
    subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    (tmp_path / '.cofr' / 'config.yaml').write_text('- not a mapping\n')
    result = run_cofr(['rename', 'claim_to_rename', 'claim_renamed', str(tmp_path)])
    combined = result.stdout + result.stderr
    assert 'config.yaml' in combined.lower(), f'config warning not surfaced via cofr rename; output:\n{combined}'


def test_refresh_pack_rewrite_preserves_malformed_skipped_records(tmp_path):
    run_cofr(['init', str(tmp_path)])
    ev_dir = tmp_path / 'evidences'
    ev_dir.mkdir()
    pack = ev_dir / 'foo.yaml'
    pack.write_text(
        '- id: ev_slug_fix\n'
        '  type: evidence\n'
        '  summary: hello\n'
        '  source_slug: wrong_slug\n'
        '- type: evidence\n'
        '  summary: NoIdRecord\n'
    )
    result = run_cofr(['refresh', str(tmp_path)])
    assert result.returncode in (0, 1), result.stderr
    text = pack.read_text()
    assert 'source_slug: foo' in text
    assert 'NoIdRecord' in text, f'refresh rewrite dropped malformed skipped record:\n{text}'


def test_add_on_corrupt_state_returns_corrupt_exit_code(tmp_path):
    run_cofr(['init', str(tmp_path)])
    (tmp_path / '.cofr' / 'state.json').write_text('{bad json')
    body = '---\ntype: claim\nid: claim_a\n---\n\n## Title\n\nA\n'
    result = subprocess.run(['cofr', 'add', str(tmp_path)], input=body, capture_output=True, text=True)
    assert result.returncode == 4
    assert 'corrupt cofr state' in result.stderr.lower()


def test_cofr_add_force_preserves_malformed_pack_records(tmp_path):
    run_cofr(['init', str(tmp_path)])
    pack = tmp_path / 'claims.yaml'
    pack.write_text(
        '- id: claim_valid\n'
        '  type: claim\n'
        '  title: V\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
        '- type: claim\n'
        '  title: NoIdRecord\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    body = '---\ntype: claim\nid: claim_valid\ntitle: Replacement\nstatus: provisionally_supported\nconfidence: medium\n---\n## Statement\nnew\n'
    result = subprocess.run(['cofr', 'add', '--force', str(tmp_path)], input=body, capture_output=True, text=True)
    text = pack.read_text()
    assert 'NoIdRecord' in text, f'cmd_add --force lost malformed record; pack:\n{text}'
    assert 'Replacement' in text, f'cmd_add --force did not write the replacement; pack:\n{text}'
    combined = result.stdout + result.stderr
    assert 'no id' in combined.lower() or 'missing id' in combined.lower() or 'malformed' in combined.lower(), \
        f'cmd_add --force did not warn about malformed record; output:\n{combined}'


def test_cofr_rename_preserves_malformed_pack_records(tmp_path):
    run_cofr(['init', str(tmp_path)])
    pack = tmp_path / 'claims.yaml'
    pack.write_text(
        '- id: claim_rename_src\n'
        '  type: claim\n'
        '  title: V\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
        '- type: claim\n'
        '  title: NoIdRecord\n'
        '  status: provisionally_supported\n'
        '  confidence: medium\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    result = run_cofr(['rename', 'claim_rename_src', 'claim_rename_dst', str(tmp_path)])
    text = pack.read_text()
    assert 'NoIdRecord' in text, f'cmd_rename lost malformed record; pack:\n{text}'
    assert 'claim_rename_dst' in text, f'cmd_rename did not apply rename; pack:\n{text}'
    combined = result.stdout + result.stderr
    assert 'no id' in combined.lower() or 'missing id' in combined.lower() or 'malformed' in combined.lower(), \
        f'cmd_rename did not warn about malformed record; output:\n{combined}'


def test_refresh_json_envelope_warnings_mirrors_data_warnings(tmp_path):
    '''refresh --json must hoist warnings into envelope `_warnings` (M2 plan line 962).'''
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'decisions.yaml').write_text(
        '- id: dec_a\n  type: decision\n  title: T\n  decision_statement: DS\n  status: active\n  depends_on_claim_ids:\n    - claim_missing\n'
    )
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    data_warnings = env['data'].get('warnings') or []
    top_warnings = env.get('_warnings') or []
    assert data_warnings, f'expected at least one warning in data.warnings; got {data_warnings!r}'
    assert any('claim_missing' in w for w in data_warnings), data_warnings
    assert not top_warnings, f'cofr refresh --json: envelope _warnings MUST be empty per plan line 183 (M2 never populates _warnings with novel warnings); got {top_warnings!r}'


def test_refresh_json_envelope_warnings_carries_semantic_staleness(tmp_path):
    '''When semantic staleness fires, it surfaces in data.warnings only (per plan section "Warnings: envelope vs data").'''
    import time
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(
        '- id: claim_a\n  type: claim\n  title: T\n  statement: S\n  status: supported\n  confidence: high\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    time.sleep(1.1)
    (tmp_path / 'evidences').mkdir(exist_ok=True)
    (tmp_path / 'evidences' / 'note.yaml').write_text(
        '- id: ev_new\n  type: evidence\n  evidence_type: manual_observation\n  summary: newer\n  status: active\n  data_source: note.md\n  source_path: note.md\n  source_slug: note\n  source_title: Note\n  claim_links:\n    - claim_id: claim_a\n      polarity: opposes\n'
    )
    result = run_cofr(['refresh', '--json', str(tmp_path)])
    env = json.loads(result.stdout)
    top_warnings = env.get('_warnings') or []
    data_warnings = env['data'].get('warnings') or []
    has_staleness_data = any('staleness:' in w for w in data_warnings)
    assert has_staleness_data, f'data.warnings must include staleness; got {data_warnings!r}'
    assert not top_warnings, f'cofr refresh --json: envelope _warnings MUST be empty per plan line 183; got {top_warnings!r}'


def test_contradictions_matches_golden(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    actual = (contradictions_project_path / 'artifacts' / 'contradictions.md').read_text()
    actual_redacted = normalize_for_golden(actual, project_path=contradictions_project_path)
    expected = (GOLDEN_DIR / 'contradictions_project_contradictions.md').read_text()
    assert actual_redacted == expected


def test_next_decision_matches_golden(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    actual = (contradictions_project_path / 'artifacts' / 'next_decision.md').read_text()
    actual_redacted = normalize_for_golden(actual, project_path=contradictions_project_path)
    expected = (GOLDEN_DIR / 'contradictions_project_next_decision.md').read_text()
    assert actual_redacted == expected


def test_refresh_writes_contradictions_and_next_decision_artifacts(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    assert (contradictions_project_path / 'artifacts' / 'contradictions.md').is_file()
    assert (contradictions_project_path / 'artifacts' / 'next_decision.md').is_file()
    state = json.loads((contradictions_project_path / '.cofr' / 'state.json').read_text())
    types = {a['artifact_type'] for a in state['artifacts']}
    assert 'contradictions' in types
    assert 'next_decision' in types


def test_contradiction_artifacts_byte_identical_across_refreshes(contradictions_project_path):
    run_cofr(['init', str(contradictions_project_path)])
    run_cofr(['refresh', str(contradictions_project_path)])
    first_c = (contradictions_project_path / 'artifacts' / 'contradictions.md').read_text()
    first_n = (contradictions_project_path / 'artifacts' / 'next_decision.md').read_text()
    run_cofr(['refresh', str(contradictions_project_path)])
    assert (contradictions_project_path / 'artifacts' / 'contradictions.md').read_text() == first_c
    assert (contradictions_project_path / 'artifacts' / 'next_decision.md').read_text() == first_n


def _rules_124_claims(eroded_dec_confidence):
    return (
        '- id: claim_unchanged\n  type: claim\n  title: Unchanged\n  statement: S\n'
        '  status: provisionally_supported\n  confidence: medium\n'
        '- id: claim_eroded_conf\n  type: claim\n  title: Eroded confidence\n  statement: S\n'
        '  status: provisionally_supported\n  confidence: high\n'
        '- id: claim_eroded_dec\n  type: claim\n  title: Eroded basis\n  statement: S\n'
        f'  status: provisionally_supported\n  confidence: {eroded_dec_confidence}\n'
    )


def test_contradictions_rules_124_match_golden(tmp_path):
    '''Bug #13: golden-lock the rendered contradictions.md for the timestamp-dependent
    rules 1 (claim unchanged), 2 (decision basis eroded), and 4 (eroded confidence),
    which the single-refresh static contradictions_project fixture cannot plant.'''
    import time
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text(_rules_124_claims('high'))
    (tmp_path / 'decisions.yaml').write_text(
        '- id: dec_basis\n  type: decision\n  title: D\n  decision_statement: DS\n'
        '  depends_on_claim_ids:\n    - claim_eroded_dec\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    time.sleep(1.1)
    (tmp_path / 'claims.yaml').write_text(_rules_124_claims('low'))
    (tmp_path / 'evidences').mkdir()
    (tmp_path / 'evidences' / '__misc__.yaml').write_text(
        '- id: ev_supporting\n  type: evidence\n  summary: Supports the unchanged claim.\n'
        '  claim_links:\n    - claim_id: claim_unchanged\n      polarity: supports\n'
        '- id: ev_opposing\n  type: evidence\n  summary: Opposes the high-confidence claim.\n'
        '  claim_links:\n    - claim_id: claim_eroded_conf\n      polarity: opposes\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    actual = (tmp_path / 'artifacts' / 'contradictions.md').read_text()
    actual_redacted = normalize_for_golden(actual, project_path=tmp_path)
    expected = (GOLDEN_DIR / 'contradictions_rules_124.md').read_text()
    assert actual_redacted == expected


def test_rule1_claim_unchanged_fires_after_evidence_added(tmp_path):
    import time
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: A\n  statement: S\n')
    run_cofr(['refresh', str(tmp_path)])
    time.sleep(1.1)
    (tmp_path / 'evidences').mkdir()
    (tmp_path / 'evidences' / '__misc__.yaml').write_text(
        '- id: ev_a\n  type: evidence\n  summary: E\n'
        '  claim_links:\n    - claim_id: claim_a\n      polarity: supports\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    text = (tmp_path / 'artifacts' / 'contradictions.md').read_text()
    section = text.split('### Claims unchanged despite new evidence', 1)[1].split('\n## ', 1)[0]
    assert 'claim_a' in section


def test_rule2_decision_basis_eroded_fires_after_confidence_drop(tmp_path):
    import time
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: A\n  statement: S\n  confidence: high\n')
    (tmp_path / 'decisions.yaml').write_text(
        '- id: dec_a\n  type: decision\n  title: D\n  decision_statement: DS\n'
        '  depends_on_claim_ids:\n    - claim_a\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    time.sleep(1.1)
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: A\n  statement: S\n  confidence: low\n')
    run_cofr(['refresh', str(tmp_path)])
    text = (tmp_path / 'artifacts' / 'contradictions.md').read_text()
    section = text.split('### Decision basis eroded', 1)[1].split('###', 1)[0]
    assert 'dec_a' in section


def test_rule4_eroded_confidence_fires_after_opposing_evidence_added(tmp_path):
    import time
    run_cofr(['init', str(tmp_path)])
    (tmp_path / 'claims.yaml').write_text('- id: claim_a\n  type: claim\n  title: A\n  statement: S\n  confidence: high\n')
    run_cofr(['refresh', str(tmp_path)])
    time.sleep(1.1)
    (tmp_path / 'evidences').mkdir()
    (tmp_path / 'evidences' / '__misc__.yaml').write_text(
        '- id: ev_con\n  type: evidence\n  summary: E\n'
        '  claim_links:\n    - claim_id: claim_a\n      polarity: opposes\n'
    )
    run_cofr(['refresh', str(tmp_path)])
    text = (tmp_path / 'artifacts' / 'contradictions.md').read_text()
    section = text.split('### Eroded confidence', 1)[1].split('###', 1)[0]
    assert 'claim_a' in section
