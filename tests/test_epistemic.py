'''Epistemic-correctness tests (spec §9.5, E1-E9).

These are the validity tests. Each scenario stages a known ground truth in
the clean fixture, runs cofr end-to-end, and asserts that the cofr output
unambiguously communicates that truth to a downstream consumer (agent,
artifact reader, future cofr module). If any scenario fails, M1's contract
is broken even when every unit test passes.
'''
import json
import subprocess
import time

from conftest import run_cofr


def init_and_refresh(project_path):
    run_cofr(['init', str(project_path)])
    result = run_cofr(['refresh', '--json', str(project_path)])
    return json.loads(result.stdout)


def get_state_json(project_path):
    result = run_cofr(['show', 'state', '--json', str(project_path)])
    return json.loads(result.stdout)['data']


def get_claims_json(project_path):
    result = run_cofr(['show', 'claims', '--json', str(project_path)])
    return json.loads(result.stdout)['data']['claims']


def reconstruct_from_json(state):
    '''Given show state --json's `data`, rebuild the full epistemic graph.

    Returns {claim_id: {status, confidence, supporters: [ev_ids],
    opposers: [ev_ids], depended_on_by: [dec_ids], mentioned_in: [paths]}}.
    The helper is the deterministic stand-in for an agent reading cofr's
    output and inferring relationships. Uses only `data` from show state --json.
    '''
    graph = {}
    for claim in state.get('claims', []):
        graph[claim['id']] = {
            'status': claim.get('status'),
            'confidence': claim.get('confidence'),
            'supporters': [],
            'opposers': [],
            'depended_on_by': [],
            'mentioned_in': list(claim.get('mentioned_in', [])),
        }
    for ev in state.get('evidence', []):
        if ev.get('stale'):
            continue
        for link in ev.get('claim_links', []) or []:
            cid = link.get('claim_id')
            polarity = link.get('polarity', 'supports')
            if cid in graph:
                if polarity == 'supports':
                    graph[cid]['supporters'].append(ev['id'])
                elif polarity == 'opposes':
                    graph[cid]['opposers'].append(ev['id'])
    for dec in state.get('decisions', []):
        for cid in dec.get('depends_on_claim_ids', []) or []:
            if cid in graph:
                graph[cid]['depended_on_by'].append(dec['id'])
    return graph


def test_e1_polarity_graph_integrity(clean_project_path):
    init_and_refresh(clean_project_path)
    claims = {c['id']: c for c in get_claims_json(clean_project_path)}
    assert claims['claim_action_conditioning']['supporting_evidence_count'] == 2
    assert claims['claim_action_conditioning']['counter_evidence_count'] == 1
    assert claims['claim_drift_barrier']['supporting_evidence_count'] == 2
    assert claims['claim_drift_barrier']['counter_evidence_count'] == 0
    assert claims['claim_scaling_priority']['supporting_evidence_count'] == 0
    assert claims['claim_scaling_priority']['counter_evidence_count'] == 1

    state = get_state_json(clean_project_path)
    holdout = next(e for e in state['evidence'] if e['id'] == 'ev_holdout_drop')
    assert holdout['claim_links'] == [{'claim_id': 'claim_action_conditioning', 'polarity': 'opposes'}]


def test_e1_negated_control_flipping_polarity_changes_counts(clean_project_path):
    init_and_refresh(clean_project_path)
    holdout_path = clean_project_path / 'evidence' / 'ev_holdout_drop.md'
    original = holdout_path.read_text()
    flipped = original.replace('claim_action_conditioning: opposes', 'claim_action_conditioning: supports')
    holdout_path.write_text(flipped)
    init_and_refresh(clean_project_path)
    claims = {c['id']: c for c in get_claims_json(clean_project_path)}
    assert claims['claim_action_conditioning']['supporting_evidence_count'] == 3
    assert claims['claim_action_conditioning']['counter_evidence_count'] == 0
    holdout_path.write_text(original)


def test_e2_decision_claim_dependency_reachable_from_json(clean_project_path):
    init_and_refresh(clean_project_path)
    state = get_state_json(clean_project_path)
    dec = next(d for d in state['decisions'] if d['id'] == 'dec_deprioritize_scaling')
    assert 'claim_scaling_priority' in dec['depends_on_claim_ids']
    claim = next(c for c in state['claims'] if c['id'] == 'claim_scaling_priority')
    assert claim['status'] == 'unsupported'


def test_e3_id_mention_scanning_rejects_phantom_and_short_ids(clean_project_path):
    init_and_refresh(clean_project_path)
    cofr_dir = clean_project_path / '.cofr'
    index = json.loads((cofr_dir / 'index.json').read_text())
    scratch_entry = index['notes/scratch.md']
    assert 'claim_action_conditioning' in scratch_entry['id_mentions']
    assert 'claim_phantom' not in scratch_entry['id_mentions']
    assert 'ev1' not in scratch_entry['id_mentions']

    claims = {c['id']: c for c in get_claims_json(clean_project_path)}
    assert 'notes/scratch.md' in claims['claim_action_conditioning']['mentioned_in']


def test_e4_stance_change_survives_refresh_round_trip(clean_project_path):
    init_and_refresh(clean_project_path)
    state_v1 = get_state_json(clean_project_path)
    claim_v1 = next(c for c in state_v1['claims'] if c['id'] == 'claim_drift_barrier')
    assert claim_v1['confidence'] == 'high'

    drift_path = clean_project_path / 'claims' / 'claim_drift_barrier.md'
    original = drift_path.read_text()
    edited = original.replace('confidence: high', 'confidence: low')
    drift_path.write_text(edited)
    time.sleep(1.1)
    init_and_refresh(clean_project_path)
    state_v2 = get_state_json(clean_project_path)
    claim_v2 = next(c for c in state_v2['claims'] if c['id'] == 'claim_drift_barrier')
    assert claim_v2['confidence'] == 'low'
    assert claim_v2['first_seen'] == claim_v1['first_seen']
    assert claim_v2['last_updated'] != claim_v1['last_updated']
    assert claim_v2['parsed_from'] == claim_v1['parsed_from']
    assert claim_v2['id'] == claim_v1['id']

    other_v1 = next(c for c in state_v1['claims'] if c['id'] == 'claim_action_conditioning')
    other_v2 = next(c for c in state_v2['claims'] if c['id'] == 'claim_action_conditioning')
    assert other_v1['last_updated'] == other_v2['last_updated']

    drift_path.write_text(original)


def test_e5_broken_references_surface_and_resolve(broken_refs_path):
    refresh_env = init_and_refresh(broken_refs_path)
    broken_refs = refresh_env['data']['broken_references']
    assert any('claim_e5_missing' in w for w in broken_refs)

    artifact = (broken_refs_path / 'artifacts' / 'current_state.md').read_text()
    assert '## Broken references' in artifact
    after_header = artifact.split('## Broken references', 1)[1].split('\n## ', 1)[0]
    assert 'claim_e5_missing' in after_header

    (broken_refs_path / 'claim_e5_missing.md').write_text(
        '---\ntype: claim\nid: claim_e5_missing\nstatus: provisionally_supported\n---\n\n## Title\n\nResolves the broken ref.\n'
    )
    refresh_env_after = init_and_refresh(broken_refs_path)
    broken_refs_after = refresh_env_after['data']['broken_references']
    assert not any('claim_e5_missing' in w for w in broken_refs_after)
    artifact_after = (broken_refs_path / 'artifacts' / 'current_state.md').read_text()
    section_after = artifact_after.split('## Broken references', 1)[1].split('\n## ', 1)[0]
    assert 'claim_e5_missing' not in section_after


def test_e6_pdf_content_id_mentions_join_graph(clean_project_path):
    init_and_refresh(clean_project_path)
    index = json.loads((clean_project_path / '.cofr' / 'index.json').read_text())
    pdf_entry = index['papers/short_paper.pdf']
    assert pdf_entry['classification'] == 'content_extracted'
    assert 'claim_drift_barrier' in pdf_entry['id_mentions']

    claims = {c['id']: c for c in get_claims_json(clean_project_path)}
    assert 'papers/short_paper.pdf' in claims['claim_drift_barrier']['mentioned_in']

    artifact = (clean_project_path / 'artifacts' / 'current_state.md').read_text()
    drift_section = artifact.split('claim_drift_barrier', 1)[1] if 'claim_drift_barrier' in artifact else ''
    assert 'papers/short_paper.pdf' in drift_section


def test_e7_json_envelope_is_sufficient_for_state_reconstruction(clean_project_path):
    init_and_refresh(clean_project_path)
    state = get_state_json(clean_project_path)
    graph = reconstruct_from_json(state)

    assert set(graph) == {'claim_action_conditioning', 'claim_drift_barrier', 'claim_scaling_priority'}

    aco = graph['claim_action_conditioning']
    assert aco['status'] == 'provisionally_supported'
    assert aco['confidence'] == 'medium'
    assert set(aco['supporters']) == {'ev_supporting_eval', 'ev_action_replication'}
    assert aco['opposers'] == ['ev_holdout_drop']

    drift = graph['claim_drift_barrier']
    assert set(drift['supporters']) == {'ev_lynch_drift_barrier', 'ev_bergeron_vertebrates'}
    assert drift['opposers'] == []

    scaling = graph['claim_scaling_priority']
    assert scaling['supporters'] == []
    assert scaling['opposers'] == ['ev_scaling_failure']
    assert scaling['depended_on_by'] == ['dec_deprioritize_scaling']

    assert 'notes/scratch.md' in aco['mentioned_in']
    assert 'papers/short_paper.pdf' in drift['mentioned_in']
    assert scaling['mentioned_in'] == []


def test_e8_realistic_prose_survives_pipeline_verbatim(clean_project_path):
    init_and_refresh(clean_project_path)
    state = get_state_json(clean_project_path)
    lynch_ev = next(e for e in state['evidence'] if e['id'] == 'ev_lynch_drift_barrier')
    assert 'Lynch (2011) lower-bound argument' in lynch_ev['summary']
    assert 'Ne^-0.6' in lynch_ev['summary']
    assert 'fitness gains too small to' in lynch_ev['summary']

    src = (clean_project_path / 'evidence' / 'ev_lynch_drift_barrier.md').read_text()
    quoted = 'The Lynch (2011) lower-bound argument predicts an inverse relationship between'
    assert quoted in src
    assert quoted in lynch_ev['summary']


def test_e9_deletion_marks_stale_and_decrements_counts(clean_project_path):
    init_and_refresh(clean_project_path)
    claims_v1 = {c['id']: c for c in get_claims_json(clean_project_path)}
    assert claims_v1['claim_action_conditioning']['supporting_evidence_count'] == 2

    ev_path = clean_project_path / 'evidence' / 'ev_supporting_eval.md'
    backup = ev_path.read_text()
    ev_path.unlink()

    init_and_refresh(clean_project_path)
    state_v2 = get_state_json(clean_project_path)
    deleted_ev = next(e for e in state_v2['evidence'] if e['id'] == 'ev_supporting_eval')
    assert deleted_ev['stale'] is True

    claims_v2 = {c['id']: c for c in get_claims_json(clean_project_path)}
    assert claims_v2['claim_action_conditioning']['supporting_evidence_count'] == 1

    ev_path.write_text(backup)
