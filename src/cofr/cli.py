'''cofr CLI: argparse parser, dispatch, every command handler, and the
shared envelope / exit-code / projection helpers they call. One file for the
entire CLI surface -- `cofr --help` discovery starts here, command bodies
follow in source order.'''
import argparse
import copy
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import yaml

from cofr import __version__
from cofr.domain import (
    CLASS_TO_TYPE_STR, DOMAIN_TYPES, TYPE_TO_COLLECTION,
    confidence_to_numeric, has_live_status, is_live,
)
from cofr.ingest import (
    SECTION_MAP, _VALID_ID_RE,
    _stale_warning_for_type, parse_field_value, parse_sections,
    scan_and_parse, scan_existing_ids, split_frontmatter,
    validate_anchors_against_index,
)
from cofr.packs import (
    EXPECTED_PACK_PATHS,
    pack_dump, pack_dump_preserving_skipped, pack_load,
    route_pack_path, validate_at_path,
)
from cofr.renames import (
    _pre_load_pending_pack_fixup,
    append_renames_log,
    apply_rename_cascade,
    clear_pending_renames,
    compute_fingerprint,
    detect_renames,
    load_pending_renames,
    rebuild_renames_log,
    replace_markdown_id,
    write_pending_renames,
)
from cofr.state import (
    CofrNotInitialized, CorruptStateError,
    _cofr_dir, _json_default, _pre_load_migration_check, _refuse_if_pending_migration,
    apply_parsed_records,
    atomic_write_json, atomic_write_text,
    init as state_init,
    load_config, load_index, load_state,
    migrate_rollback, migrate_v1_to_v2,
    save_index, save_state, snapshot_history,
    validate_references,
)
from cofr.synthesis import (
    compute_claim_evidence_counts, compute_claim_mentions,
    compute_computed_risks, compute_contradictions, compute_diff,
    compute_falsification_review, compute_semantic_staleness,
    generate_contradictions, generate_current_state, generate_next_decision,
    generate_what_changed, rank_next_decisions, rebuild_all_timelines,
)


JSON_SCHEMA_VERSION = 1
EXIT_OK = 0
EXIT_SOFT_WARN = 1
EXIT_USAGE = 2
EXIT_NOT_INITIALIZED = 3
EXIT_CORRUPT = 4

NOTE_NO_STATE = 'No structured state found. Create files with YAML frontmatter (type: claim, evidence, experiment, decision, question, risk) in this directory.'
NOTE_NO_CLAIMS = 'No claims found. Create structured markdown files with type: claim in frontmatter.'

ERR_NOT_INITIALIZED_TEMPLATE = 'Project not initialized: {path}. Run `cofr init {path}` first.'
ERR_CORRUPT_STATE_TEMPLATE = 'Corrupt cofr state at {path}: {detail}. Investigate .cofr/state.json manually.'

_PUBLIC_RENAME_KEYS = {'_timeline': 'timeline', '_status_timeline': 'status_timeline'}

_COLLECTION_BY_TYPE = {
    'claim': 'claims', 'evidence': 'evidence', 'experiment': 'experiments',
    'decision': 'decisions', 'question': 'open_questions', 'risk': 'risks',
}
_TYPE_BY_PACK = {'claims.yaml': 'claim', 'decisions.yaml': 'decision', 'questions.yaml': 'question', 'risks.yaml': 'risk', 'experiments.yaml': 'experiment'}
_NON_EVIDENCE_PACKS = ('claims.yaml', 'decisions.yaml', 'questions.yaml', 'risks.yaml', 'experiments.yaml')
_TYPE_PRIMARY_FIELD = {
    'question': 'question', 'evidence': 'summary', 'experiment': 'name',
    'decision': 'title', 'risk': 'statement',
}

_PRIORITY_RANK = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
_SEVERITY_RANK = {'high': 3, 'medium': 2, 'low': 1}
_STATUS_RANK = {'open': 2, 'accepted': 1}


def iso_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def resolve_project_path(args):
    return Path(args.project_path).resolve()


def load_help_text(key):
    raw = files('cofr').joinpath('help.txt').read_text(encoding='utf-8')
    marker = f'=== {key} ==='
    if marker not in raw:
        return ''
    after_marker = raw.split(marker, 1)[1]
    next_marker_idx = after_marker.find('\n=== ')
    body = after_marker if next_marker_idx == -1 else after_marker[:next_marker_idx]
    return body.strip('\n')


def envelope(project_path, data, note=None, warnings=None, generated_at=None):
    env = {
        'schema_version': JSON_SCHEMA_VERSION,
        'cofr_version': __version__,
        'generated_at': generated_at if generated_at is not None else iso_now(),
        'project_path': str(Path(project_path).resolve()),
        'data': data,
    }
    if note is not None:
        env['_note'] = note
    if warnings:
        env['_warnings'] = list(warnings)
    return env


_envelope = envelope


def emit_json(env):
    indent = 2 if sys.stdout.isatty() else None
    json.dump(env, sys.stdout, indent=indent, sort_keys=True, default=_json_default, allow_nan=False)
    sys.stdout.write('\n')


def load_json_file_or_corrupt(path, label, expect_shape=dict):
    try:
        loaded = json.loads(Path(path).read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f'Corrupt {label} at {path}: not valid JSON ({exc})'
    except OSError as exc:
        return None, f'Corrupt {label} at {path}: cannot read file ({exc})'
    if expect_shape and not isinstance(loaded, expect_shape):
        return None, f'Corrupt {label} at {path}: expected {expect_shape.__name__} at top level, got {type(loaded).__name__}'
    return loaded, None


def safe_load_config_warnings(project_path):
    try:
        _, config_warnings = load_config(project_path)
        return list(config_warnings)
    except CofrNotInitialized:
        return []


def emit_warnings_to_stderr(warnings):
    for w in warnings:
        print(f'  - {w}', file=sys.stderr)


def compute_index_summary(index):
    summary = {}
    for entry in index.values():
        cls = entry.get('classification', 'unknown')
        summary[cls] = summary.get(cls, 0) + 1
    return summary


def project_record_for_public_json(record):
    '''Project a state record dict into the shape exposed at public JSON surfaces.

    - Drops literal `_unknown_fields` and `_preserved_user_fields` keys.
    - Re-injects `_preserved_user_fields` contents at their canonical keys
      (overriding any cleared in-memory empty value), so agents reading
      `show state` see the user-authored on-disk value rather than the
      validation-cleared in-memory value.
    - Merges `_unknown_fields` contents as additional record keys.
    - Renames `_timeline` to `timeline`, `_status_timeline` to `status_timeline`.
    - On-disk state.json retains the underscore-prefixed keys; this projection
      runs only when emitting public envelopes.
    '''
    if not isinstance(record, dict):
        return record
    unknown = record.get('_unknown_fields') or {}
    preserved = record.get('_preserved_user_fields') or {}
    out = {}
    for k, v in record.items():
        if k in ('_unknown_fields', '_preserved_user_fields'):
            continue
        if k in _PUBLIC_RENAME_KEYS:
            out[_PUBLIC_RENAME_KEYS[k]] = v
            continue
        if v == '' or v == [] or v == {} or v is False:
            if k in unknown or k in preserved:
                continue
        out[k] = v
    for k, v in preserved.items():
        out[k] = v
    for k, v in unknown.items():
        if k not in out:
            out[k] = v
    return out


def project_records_list(records):
    return [project_record_for_public_json(r) for r in records]


def attach_computed_claim_fields(state, index, include_retired=False):
    evidence = state.get('evidence', [])
    enriched = []
    for claim in state.get('claims', []):
        if not include_retired and not is_live(claim, type_str='claim'):
            continue
        counts = compute_claim_evidence_counts(claim['id'], evidence)
        mentions = compute_claim_mentions(claim['id'], index)
        projected = project_record_for_public_json(claim)
        enriched.append({**projected, 'supporting_evidence_count': counts['supports'], 'counter_evidence_count': counts['opposes'], 'mentioned_in': mentions})
    return enriched


def agent_complete_state(state, index):
    '''Build the agent-complete state payload used by both `show state --json` and `refresh --json`.'''
    data = dict(state)
    data['claims'] = attach_computed_claim_fields(state, index, include_retired=True)
    for key in ('evidence', 'experiments', 'decisions', 'open_questions', 'risks', 'artifacts'):
        if key in data:
            data[key] = project_records_list(data[key])
    data['_index_summary'] = compute_index_summary(index)
    data['_index_mentions'] = {path: entry.get('id_mentions', []) for path, entry in index.items() if entry.get('id_mentions')}
    return data


def cmd_init(args):
    project_path = resolve_project_path(args)
    if not project_path.exists():
        print(f'Project path does not exist: {project_path}', file=sys.stderr)
        return EXIT_USAGE
    created = state_init(project_path)
    if created:
        print(f'Initialized cofr in {project_path}')
    else:
        print(f'Already initialized: {project_path}')
    return EXIT_OK


def cmd_migrate_rollback(args):
    project_path = resolve_project_path(args)
    if not args.rollback:
        print('cofr migrate: no action specified. Use `cofr migrate --rollback`.', file=sys.stderr)
        return EXIT_USAGE
    try:
        migrate_rollback(project_path, from_history=args.from_history, confirm=args.yes_i_know_what_im_doing)
        print(f'rolled back migration at {project_path}')
        return EXIT_OK
    except Exception as exc:
        print(f'cofr migrate --rollback: {exc}', file=sys.stderr)
        return EXIT_USAGE


def _handle_rebuild_flags(args, project_path, state):
    rebuild_log_flag = getattr(args, 'rebuild_renames_log', False)
    rebuild_timelines_flag = getattr(args, 'rebuild_timelines', False)
    if not (rebuild_log_flag or rebuild_timelines_flag):
        return None
    rebuild_warnings = []
    if rebuild_timelines_flag:
        try:
            snapshot_history(project_path)
        except Exception as exc:
            print(f'cofr refresh --rebuild-timelines: pre-rebuild snapshot failed ({exc}); refusing to mutate state without recovery point.', file=sys.stderr)
            return EXIT_CORRUPT
    if rebuild_log_flag:
        appended, warnings_log = rebuild_renames_log(project_path, state)
        print(f'rebuild-renames-log: appended {len(appended)} entries')
        for w in warnings_log:
            print(f'  - {w}', file=sys.stderr)
        rebuild_warnings.extend(warnings_log)
    if rebuild_timelines_flag:
        _, rebuild_cfg_warnings = rebuild_all_timelines(state, project_path)
        for w in rebuild_cfg_warnings:
            print(f'  - {w}', file=sys.stderr)
        rebuild_warnings.extend(rebuild_cfg_warnings)
        save_state(project_path, state)
        try:
            config_for_md, cw_for_md = load_config(project_path)
            for w in cw_for_md:
                print(f'  - {w}', file=sys.stderr)
            rebuild_warnings.extend(cw_for_md)
        except CofrNotInitialized:
            config_for_md = {}
        try:
            index_for_md = load_index(project_path)
        except CofrNotInitialized:
            index_for_md = {}
        sidecar_for_md = None
        sidecar_path = project_path / '.cofr' / 'last_diff.json'
        if sidecar_path.is_file():
            sidecar_for_md, _ = load_json_file_or_corrupt(sidecar_path, '.cofr/last_diff.json')
        rebuild_artifacts = [
            ('artifacts/current_state.md', lambda: generate_current_state(state, index_for_md, config_for_md, last_diff=sidecar_for_md)),
            ('artifacts/contradictions.md', lambda: generate_contradictions(state)),
            ('artifacts/next_decision.md', lambda: generate_next_decision(state)),
        ]
        for rel, render in rebuild_artifacts:
            try:
                atomic_write_text(project_path / rel, render())
            except OSError as exc:
                print(f'  - artifact-write failure at {rel}: {exc}', file=sys.stderr)
                rebuild_warnings.append(str(exc))
        print('rebuild-timelines: complete')
    return EXIT_SOFT_WARN if rebuild_warnings else EXIT_OK


def _check_pending_renames(project_path):
    '''Return (pre_load_pending, pre_fixup_result, error_exit_or_None).'''
    pending_path = project_path / '.cofr' / 'pending_renames.json'
    pre_load_pending = load_pending_renames(project_path)
    if pending_path.is_file() and pre_load_pending is None:
        print('cofr refresh: malformed .cofr/pending_renames.json; resolve or remove it manually before refreshing.', file=sys.stderr)
        return None, None, EXIT_CORRUPT
    if pre_load_pending is None:
        return None, {'action': 'none', 'pre_fixup_applied': False, 'reason': 'no pending rename'}, None
    pre_fixup_result = _pre_load_pending_pack_fixup(project_path, pre_load_pending)
    if pre_fixup_result['action'] == 'refuse':
        print(f"cofr refresh: {pre_fixup_result['reason']}", file=sys.stderr)
        return None, None, EXIT_USAGE
    return pre_load_pending, pre_fixup_result, None


def _rewrite_packs(project_path, packs_to_rewrite, state, parsed_record_keys_by_pack, new_index):
    '''Rewrite packs flagged for incidental edits (slug correction, rename cascade).

    Preserves malformed records via pack_dump_preserving_skipped when pack_load
    reported skipped records.
    '''
    for pack_rel in packs_to_rewrite:
        pack_full = project_path / pack_rel
        if not pack_full.is_file():
            continue
        if pack_rel.startswith('evidences/'):
            t = 'evidence'
        else:
            t = _TYPE_BY_PACK.get(pack_rel)
            if not t:
                continue
        collection_key = _COLLECTION_BY_TYPE[t]
        parsed_ids = parsed_record_keys_by_pack.get(pack_rel, set())
        records_to_emit = []
        for item in state.get(collection_key, []):
            pf = item.get('parsed_from', '')
            pack_of_pf = pf.split('#', 1)[0] if '#' in pf else pf
            if pack_of_pf == pack_rel and item.get('id') in parsed_ids:
                records_to_emit.append(dict(item))
        _, pack_rewrite_warnings = pack_load(pack_full, expected_type=t, return_warnings=True)
        if pack_rewrite_warnings:
            try:
                loaded_raw = yaml.safe_load(pack_full.read_text(encoding='utf-8'))
            except yaml.YAMLError:
                loaded_raw = None
            if isinstance(loaded_raw, list):
                replacements = {r.get('id'): r for r in records_to_emit if r.get('id')}
                pack_dump_preserving_skipped(pack_full, loaded_raw, replacements, t)
            else:
                pack_dump(pack_full, records_to_emit, t)
        else:
            pack_dump(pack_full, records_to_emit, t)
        try:
            with open(pack_full, 'rb') as fh:
                new_hash = hashlib.sha256(fh.read()).hexdigest()
            new_mtime = pack_full.stat().st_mtime
            if pack_rel in new_index:
                new_index[pack_rel]['content_hash'] = new_hash
                new_index[pack_rel]['mtime'] = datetime.fromtimestamp(new_mtime, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                new_index[pack_rel]['size'] = pack_full.stat().st_size
        except OSError:
            pass


_RAW_RECORD_LINE_RE = None


def _raw_textual_record_count(text):
    '''Best-effort top-level record count for unparseable YAML packs.

    Counts lines that begin a top-level list item: `- id: ...` or `- type: ...`
    (with optional leading whitespace = 0 to match top-level indentation).
    A malformed YAML pack with many records typically still has well-formed
    list-marker lines; this gives the guardrail something to bite on.
    '''
    global _RAW_RECORD_LINE_RE
    if _RAW_RECORD_LINE_RE is None:
        import re
        _RAW_RECORD_LINE_RE = re.compile(r'^- (?:id|type)\s*:', re.MULTILINE)
    return len(_RAW_RECORD_LINE_RE.findall(text))


def _pack_size_warnings(project_path, parsed_record_keys_by_pack, new_index):
    warnings = []
    for pack_rel in _NON_EVIDENCE_PACKS:
        pack_full = project_path / pack_rel
        if not pack_full.is_file():
            continue
        try:
            size_bytes = pack_full.stat().st_size
        except OSError:
            continue
        entry = new_index.get(pack_rel) or {}
        if entry.get('classification') != 'structured_pack':
            continue
        rec_count = len(parsed_record_keys_by_pack.get(pack_rel, set()))
        count_source = 'parsed'
        if rec_count == 0:
            try:
                text = pack_full.read_text(encoding='utf-8')
            except OSError:
                text = None
            if text is not None:
                try:
                    raw = yaml.safe_load(text)
                    if isinstance(raw, list):
                        rec_count = len(raw)
                        count_source = 'yaml-loaded'
                except yaml.YAMLError:
                    rec_count = _raw_textual_record_count(text)
                    count_source = 'textual-fallback' if rec_count else 'malformed-zero'
        if rec_count > 100 or size_bytes > 50 * 1024:
            note = f' [{count_source}]' if count_source != 'parsed' else ''
            warnings.append(
                f'pack-size guardrail: {pack_rel} has {rec_count} records / {size_bytes} bytes{note} '
                f'(threshold: 100 records or 50 KB). Consider splitting or pruning.'
            )
    return warnings


def _build_artifact_records(generated_at, live_claim_ids, reserved_ids=None):
    records = [
        {
            'id': 'artifact_current_state',
            'artifact_type': 'current_state',
            'path': 'artifacts/current_state.md',
            'covers_claim_ids': live_claim_ids,
            'generated_at': generated_at,
            'staleness_status': 'current',
            'stale_because': '',
        },
        {
            'id': 'artifact_what_changed',
            'artifact_type': 'what_changed',
            'path': 'artifacts/what_changed.md',
            'covers_claim_ids': live_claim_ids,
            'generated_at': generated_at,
            'staleness_status': 'current',
            'stale_because': '',
        },
        {
            'id': 'artifact_contradictions',
            'artifact_type': 'contradictions',
            'path': 'artifacts/contradictions.md',
            'covers_claim_ids': live_claim_ids,
            'generated_at': generated_at,
            'staleness_status': 'current',
            'stale_because': '',
        },
        {
            'id': 'artifact_next_decision',
            'artifact_type': 'next_decision',
            'path': 'artifacts/next_decision.md',
            'covers_claim_ids': live_claim_ids,
            'generated_at': generated_at,
            'staleness_status': 'current',
            'stale_because': '',
        },
    ]
    reserved_ids = set(reserved_ids or ())
    warnings = []
    kept = []
    for record in records:
        if record['id'] in reserved_ids:
            warnings.append(f"generated artifact id collision with authored state: {record['id']!r}; omitting artifact record")
        else:
            kept.append(record)
    return kept, warnings


def cmd_refresh(args):
    project_path = resolve_project_path(args)
    pre = _pre_load_migration_check(project_path)
    if pre['action'] == 'refuse':
        print(pre['message'], file=sys.stderr)
        return pre.get('exit_code', EXIT_CORRUPT)
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT

    migrated = False
    migration_extra_warnings = []
    if state.get('_pending_migration'):
        try:
            migration_extra_warnings = migrate_v1_to_v2(project_path, state) or []
            migrated = True
        except Exception as exc:
            print(f'cofr refresh: migration failed: {exc}', file=sys.stderr)
            return EXIT_CORRUPT
        state, load_warnings = load_state(project_path)

    rebuild_requested = getattr(args, 'rebuild_renames_log', False) or getattr(args, 'rebuild_timelines', False)
    if rebuild_requested:
        pending_path = project_path / '.cofr' / 'pending_renames.json'
        pending = load_pending_renames(project_path)
        if pending_path.is_file() and pending is None:
            print('cofr refresh: malformed .cofr/pending_renames.json; resolve or remove it manually before refreshing.', file=sys.stderr)
            return EXIT_CORRUPT
        if pending is not None:
            print("cofr refresh: pending rename exists; run 'cofr refresh' without rebuild flags to recover it before rebuilding derived caches.", file=sys.stderr)
            return EXIT_USAGE
        rebuild_result = _handle_rebuild_flags(args, project_path, state)
        if rebuild_result is not None:
            return rebuild_result

    pre_load_pending, pre_fixup_result, error_exit = _check_pending_renames(project_path)
    if error_exit is not None:
        return error_exit

    snapshot_filename = ''
    if not migrated:
        try:
            snap_path = snapshot_history(project_path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f'cofr refresh: cannot create pre-refresh history snapshot: {exc}', file=sys.stderr)
            return EXIT_CORRUPT
        snapshot_filename = Path(snap_path).name if snap_path else ''
    prior_state_for_diff = copy.deepcopy(state)
    config, config_warnings = load_config(project_path)
    exclude_patterns = config.get('exclude_patterns') or []
    new_index, parsed_records, scan_warnings, packs_parsed_successfully, packs_to_rewrite, id_was_generated_by_path = scan_and_parse(project_path, state, exclude_patterns=exclude_patterns)
    anchor_warnings = validate_anchors_against_index(parsed_records, new_index, project_path)
    explicit_renames, rename_warnings = detect_renames(prior_state_for_diff, parsed_records, pending=pre_load_pending, pre_fixup_action=pre_fixup_result['action'])
    cascade_mode = 'standard'
    if pre_load_pending and pre_load_pending.get('entries') and pre_fixup_result['action'] == 'apply':
        cascade_mode = pre_load_pending['entries'][0].get('mode', 'standard')
    try:
        new_packs = apply_rename_cascade(state, parsed_records, project_path, explicit_renames, new_index, mode=cascade_mode)
    except RuntimeError as exc:
        print(f'cofr refresh: {exc}', file=sys.stderr)
        return EXIT_USAGE
    packs_to_rewrite = packs_to_rewrite | new_packs
    diff = apply_parsed_records(state, parsed_records, new_index, config=config, packs_parsed_successfully=packs_parsed_successfully, id_was_generated_by_path=id_was_generated_by_path)

    parsed_record_keys_by_pack = {}
    for obj in parsed_records:
        pf = obj.parsed_from or ''
        if '#' in pf:
            pack_of_pf = pf.split('#', 1)[0]
            parsed_record_keys_by_pack.setdefault(pack_of_pf, set()).add(obj.id)

    if packs_to_rewrite:
        _rewrite_packs(project_path, packs_to_rewrite, state, parsed_record_keys_by_pack, new_index)

    structured_refs = validate_references(state)
    id_to_parsed_from = {}
    for ck in ('claims', 'evidence', 'experiments', 'decisions', 'open_questions', 'risks'):
        for it in state.get(ck, []):
            if it.get('id'):
                id_to_parsed_from[it['id']] = it.get('parsed_from', '')
    ref_warnings = [
        f"{id_to_parsed_from.get(r['from_id'], r['from_id'])}: broken reference in {r.get('field_name', '?')} -- {r['missing_id']!r} not found"
        for r in structured_refs
    ]

    staleness, staleness_warnings = compute_semantic_staleness(state)
    staleness_strings = []
    for entry in staleness.get('claim_unchanged', []):
        staleness_strings.append(f"staleness: claim {entry['claim_id']} unchanged despite {entry['newer_count']} newer evidence record(s): {entry['newer_evidence_ids']}")
    for entry in staleness.get('decision_basis_eroded', []):
        staleness_strings.append(f"staleness: decision {entry['decision_id']} basis eroded on {entry['depended_on_claim_id']} ({entry['mode']}: {entry['reason']})")

    contradictions, contradiction_warnings = compute_contradictions(state)
    contradiction_extra_warnings = [w for w in contradiction_warnings if w not in staleness_warnings]

    pack_size_warnings = _pack_size_warnings(project_path, parsed_record_keys_by_pack, new_index)

    migration_warnings = []
    if migrated:
        migration_warnings.append('migration: schema v1 project migrated to schema v2 V3 packs; legacy markdown moved to .cofr/legacy_markdown/')
        migration_warnings.extend(migration_extra_warnings)
    warnings = migration_warnings + list(load_warnings) + list(scan_warnings) + list(anchor_warnings) + list(rename_warnings) + ref_warnings + staleness_strings + staleness_warnings + contradiction_extra_warnings + list(config_warnings) + pack_size_warnings
    broken_refs = ref_warnings
    late_warnings = []

    diff_rich = compute_diff(prior_state_for_diff, state, explicit_renames=explicit_renames)
    generated_at = iso_now()
    live_claim_ids = sorted(c['id'] for c in state.get('claims', []) if is_live(c, type_str='claim'))
    authored_ids = {
        item.get('id')
        for collection_key in _COLLECTION_BY_TYPE.values()
        for item in state.get(collection_key, [])
        if item.get('id')
    }
    artifact_records, artifact_id_warnings = _build_artifact_records(generated_at, live_claim_ids, authored_ids)
    warnings.extend(artifact_id_warnings)

    summary_for_refresh = {
        'files_scanned': len(new_index),
        'structured': sum(1 for e in new_index.values() if e.get('classification') in ('structured', 'structured_pack')),
        'skipped': sum(1 for e in new_index.values() if e.get('classification') not in ('structured', 'structured_pack', 'unstructured', 'content_extracted')),
        'pack_files': sum(1 for e in new_index.values() if e.get('classification') == 'structured_pack'),
        'structured_records': sum(len(state.get(k, [])) for k in ('claims', 'evidence', 'experiments', 'decisions', 'open_questions', 'risks')),
        'warnings_count': len(warnings),
    }
    last_diff = dict(diff_rich)
    last_diff['broken_references'] = structured_refs
    last_diff['refresh_summary'] = summary_for_refresh
    last_diff['generated_at'] = generated_at

    state['artifacts'] = artifact_records

    try:
        save_state(project_path, state)
        save_index(project_path, new_index)
    except (OSError, TypeError, ValueError, CorruptStateError) as exc:
        print(f'cofr refresh: failed to persist derived state/index: {exc}', file=sys.stderr)
        return EXIT_CORRUPT

    if explicit_renames:
        log_entries = []
        for e in explicit_renames:
            first_seen = ''
            ck = _COLLECTION_BY_TYPE[e['type']]
            for item in prior_state_for_diff.get(ck, []):
                if item.get('id') == e['old_id']:
                    first_seen = item.get('first_seen', '')
                    break
            signature = hashlib.sha256(json.dumps({'type': e['type'], 'old_id': e['old_id'], 'new_id': e['new_id'], 'first_seen': first_seen}, sort_keys=True).encode()).hexdigest()
            log_entries.append({
                'type': e['type'], 'old_id': e['old_id'], 'new_id': e['new_id'],
                'detected_at': generated_at,
                'refresh_snapshot': snapshot_filename,
                'mode': e.get('detection_mode', 'explicit'), 'signature': signature,
            })
        append_renames_log(project_path, log_entries)

    if pre_fixup_result['action'] in ('apply', 'cleanup_only'):
        try:
            clear_pending_renames(project_path)
        except OSError as exc:
            late_warnings.append(f'pending-file cleanup failure: {exc}')

    try:
        atomic_write_json(project_path / '.cofr' / 'last_diff.json', last_diff)
    except OSError as exc:
        late_warnings.append(f'artifact-write failure at .cofr/last_diff.json: {exc}')

    try:
        atomic_write_text(project_path / 'artifacts' / 'what_changed.md', generate_what_changed(last_diff, project_path))
    except OSError as exc:
        late_warnings.append(f'artifact-write failure at artifacts/what_changed.md: {exc}')

    try:
        artifact_md = generate_current_state(state, new_index, config, broken_references=broken_refs, last_diff=last_diff)
        atomic_write_text(project_path / 'artifacts' / 'current_state.md', artifact_md)
    except OSError as exc:
        late_warnings.append(f'artifact-write failure at artifacts/current_state.md: {exc}')

    try:
        atomic_write_text(project_path / 'artifacts' / 'contradictions.md', generate_contradictions(state, contradictions=contradictions))
    except OSError as exc:
        late_warnings.append(f'artifact-write failure at artifacts/contradictions.md: {exc}')

    try:
        atomic_write_text(project_path / 'artifacts' / 'next_decision.md', generate_next_decision(state, contradictions=contradictions))
    except OSError as exc:
        late_warnings.append(f'artifact-write failure at artifacts/next_decision.md: {exc}')

    enriched_state = agent_complete_state(state, new_index)

    summary = dict(summary_for_refresh)

    composed_updated = list(diff['updated'])
    existing_pairs = {(u['type'], u['id']) for u in composed_updated}
    for e in explicit_renames:
        key = (e['type'], e['new_id'])
        if key not in existing_pairs:
            composed_updated.append({'type': e['type'], 'id': e['new_id']})
            existing_pairs.add(key)
    for m in diff_rich.get('modified', []):
        if m.get('fields_updated') == ['parsed_from']:
            key = (m['type'], m['id'])
            if key not in existing_pairs:
                composed_updated.append({'type': m['type'], 'id': m['id']})
                existing_pairs.add(key)

    if args.json:
        data = {
            'summary': summary,
            'changes': {'new': diff['new'], 'updated': composed_updated, 'removed': diff['stale']},
            'warnings': warnings,
            'broken_references': broken_refs,
            'state': enriched_state,
            'diff': last_diff,
            'late_warnings': late_warnings,
        }
        emit_json(envelope(project_path, data, generated_at=generated_at))
    else:
        print(f'Refreshed {project_path}')
        print(f'  files scanned: {summary['files_scanned']} (structured: {summary['structured']})')
        print(f'  new: {len(diff['new'])}, updated: {len(composed_updated)}, removed: {len(diff['stale'])}')
        if warnings:
            print(f'  warnings: {len(warnings)}')
            for w in warnings:
                print(f'    - {w}', file=sys.stderr)

    return EXIT_SOFT_WARN if (warnings or late_warnings) else EXIT_OK


def _state_rec_for_id(state, rid):
    for ck in ('claims', 'evidence', 'experiments', 'decisions', 'open_questions', 'risks'):
        for it in state.get(ck, []):
            if it.get('id') == rid:
                return it
    return None


def cmd_rename(args):
    project_path = resolve_project_path(args)
    pre = _pre_load_migration_check(project_path)
    if pre['action'] == 'refuse':
        print(pre['message'], file=sys.stderr)
        return pre.get('exit_code', EXIT_CORRUPT)
    try:
        state, _ = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT

    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE

    old_id = args.old_id
    new_id = args.new_id
    if not _VALID_ID_RE.match(new_id):
        print(f'cofr rename: invalid new_id {new_id!r}: must match [A-Za-z0-9_-]+', file=sys.stderr)
        return EXIT_USAGE

    pending_path = project_path / '.cofr' / 'pending_renames.json'
    if pending_path.is_file():
        existing = load_pending_renames(project_path)
        if existing is None:
            print('cofr rename: malformed .cofr/pending_renames.json; resolve or remove it manually before renaming.', file=sys.stderr)
            return EXIT_CORRUPT
        if existing and existing.get('entries'):
            e = existing['entries'][0]
            print(f"Pending rename detected at .cofr/pending_renames.json ({e['old_id']} -> {e['new_id']}). Run 'cofr refresh' to apply the pending rename before renaming again.", file=sys.stderr)
            return EXIT_USAGE

    rename_config, rename_config_warnings = load_config(project_path)
    id_map, malformed_packs, _scan_warnings = scan_existing_ids(project_path, state, exclude_patterns=rename_config.get('exclude_patterns') or [])
    for w in rename_config_warnings:
        print(f'cofr rename: {w}', file=sys.stderr)
    if malformed_packs:
        for p, exc in malformed_packs:
            print(f'cofr rename: malformed pack {p}: {exc}', file=sys.stderr)
        return EXIT_USAGE

    old_occs = id_map.get(old_id, [])
    if not old_occs or not any(o['in_state'] for o in old_occs):
        print(f'cofr rename: unknown old_id: {old_id!r} not found in state', file=sys.stderr)
        return EXIT_USAGE
    if len(old_occs) > 1:
        print(f'cofr rename: id {old_id!r} has cross-source occurrences; resolve manually.', file=sys.stderr)
        return EXIT_USAGE
    old_occ = old_occs[0]
    if len(old_occ['disk_locations']) > 1:
        print(f'cofr rename: id {old_id!r} appears multiple times on disk; resolve duplicates before renaming.', file=sys.stderr)
        return EXIT_USAGE

    state_rec = None
    type_str = old_occ['record_type']
    collection_key = _COLLECTION_BY_TYPE.get(type_str)
    if collection_key:
        for it in state.get(collection_key, []):
            if it.get('id') == old_id:
                state_rec = it
                break

    new_occs = id_map.get(new_id, [])
    new_in_state_live = False
    fp_old = compute_fingerprint(state_rec, type_str) if state_rec else None
    fp_new = None
    if new_occs and new_occs[0]['in_state']:
        for it in state.get(collection_key, []):
            if it.get('id') == new_id:
                new_in_state_live = is_live(it, type_str=type_str)
                fp_new = compute_fingerprint(it, type_str)
                break

    cp1 = bool(new_occs)
    cp2 = any(o['in_state'] for o in new_occs)
    cp3 = state_rec is not None and not is_live(state_rec, type_str=type_str)
    is_fp_confirm_candidate = cp1 and cp2 and cp3

    if is_fp_confirm_candidate:
        if len(new_occs) != 1 or len(new_occs[0]['disk_locations']) > 1:
            print(f'cofr rename: fingerprint-confirm: new_id {new_id!r} has duplicates across packs or within-pack. Resolve manually.', file=sys.stderr)
            return EXIT_USAGE
        old_is_recoverable = bool(state_rec) and (
            state_rec.get('source_missing') is True
            if type_str == 'claim'
            else bool(state_rec.get('stale')) and has_live_status(state_rec, type_str=type_str)
        )
        if not old_is_recoverable:
            old_status = state_rec.get('status', '') if state_rec else ''
            print(f'cofr rename: fingerprint-confirm: old_id {old_id!r} has user-deactivated status {old_status!r}; fingerprint-confirm cannot resurrect an intentionally deactivated record. Revert the pack edit, then change status to a live value, then run cofr rename.', file=sys.stderr)
            return EXIT_USAGE
        if not new_in_state_live:
            print(f'cofr rename: fingerprint-confirm: new_id {new_id!r} must be live in state.', file=sys.stderr)
            return EXIT_USAGE
        if fp_old != fp_new:
            print(f'cofr rename: fingerprint-confirm: fingerprints differ between {old_id!r} and {new_id!r}.', file=sys.stderr)
            return EXIT_USAGE
        mode = 'fingerprint_confirm'
        old_source = old_occ['state_parsed_from']
        if not old_source:
            old_source = new_occs[0]['disk_locations'][0]['path'] if new_occs[0]['disk_locations'] else ''
        if '#' in old_source:
            old_source = old_source.split('#', 1)[0]
    else:
        if state_rec is None or not is_live(state_rec, type_str=type_str):
            print(f'cofr rename: old_id {old_id!r} is non-live (stale or deactivated); cannot rename. Revive or delete first.', file=sys.stderr)
            return EXIT_USAGE
        if len(old_occ['disk_locations']) == 1:
            old_source = old_occ['disk_locations'][0]['path']
            if new_occs:
                if len(new_occs) > 1:
                    print(f'cofr rename: new_id {new_id!r} has cross-source occurrences.', file=sys.stderr)
                    return EXIT_USAGE
                new_occ = new_occs[0]
                new_state_rec = _state_rec_for_id(state, new_id)
                if not (new_occ['in_state'] and not new_occ['disk_locations'] and new_state_rec is not None and not is_live(new_state_rec, type_str=type_str)):
                    print(f'cofr rename: new_id {new_id!r} is not in an accepted shape.', file=sys.stderr)
                    return EXIT_USAGE
            mode = 'standard'
        elif len(old_occ['disk_locations']) == 0:
            sp = old_occ['state_parsed_from']
            old_source = sp.split('#', 1)[0] if '#' in sp else sp
            if not new_occs:
                print(f'cofr rename: manual-edit recovery requires new_id present on disk at {old_source}; new_id {new_id!r} not found.', file=sys.stderr)
                return EXIT_USAGE
            if len(new_occs) > 1:
                print(f'cofr rename: new_id {new_id!r} has cross-source occurrences.', file=sys.stderr)
                return EXIT_USAGE
            new_occ = new_occs[0]
            if new_occ['in_state']:
                print(f'cofr rename: manual-edit recovery requires new_id absent from state; {new_id!r} is in state at {new_occ.get("state_parsed_from")}.', file=sys.stderr)
                return EXIT_USAGE
            if len(new_occ['disk_locations']) != 1:
                print(f'cofr rename: manual-edit recovery: new_id {new_id!r} has unexpected disk_locations.', file=sys.stderr)
                return EXIT_USAGE
            new_disk_path = new_occ['disk_locations'][0]['path']
            if new_disk_path != old_source:
                print(f'cofr rename: manual-edit recovery requires new_id at {old_source}; found at {new_disk_path}.', file=sys.stderr)
                return EXIT_USAGE
            mode = 'standard'
        else:
            print(f'cofr rename: old_id {old_id!r} appears multiple times on disk', file=sys.stderr)
            return EXIT_USAGE

    full_old = project_path / old_source
    if not full_old.is_file():
        print(f'cofr rename: source file {old_source!r} not found on disk; cannot rename a state-only record without a source pack. Resolve manually (delete the state entry, or restore the source file) before retrying.', file=sys.stderr)
        return EXIT_USAGE
    markdown_rewrite = None
    pack_rewrite_records = None
    if old_source.endswith('.md'):
        text = full_old.read_text(encoding='utf-8')
        new_text, replaced = replace_markdown_id(text, old_id, new_id)
        if not replaced:
            print(f'cofr rename: could not rewrite id in {old_source}', file=sys.stderr)
            return EXIT_USAGE
        markdown_rewrite = new_text
    else:
        try:
            raw_records = yaml.safe_load(full_old.read_text(encoding='utf-8')) or []
        except Exception as exc:
            print(f'cofr rename: cannot read {old_source}: {exc}', file=sys.stderr)
            return EXIT_USAGE
        if not isinstance(raw_records, list):
            print(f'cofr rename: pack {old_source} top-level is not a list ({type(raw_records).__name__}); cannot rewrite safely.', file=sys.stderr)
            return EXIT_USAGE
        _, src_pack_warnings = pack_load(full_old, expected_type=type_str, return_warnings=True)
        for w in src_pack_warnings:
            print(f'cofr rename: pack {old_source}: {w}', file=sys.stderr)
        for r in raw_records:
            if isinstance(r, dict) and r.get('id') == old_id:
                renamed = dict(r)
                renamed['id'] = new_id
                pack_rewrite_records = (raw_records, {old_id: renamed})
                break

    pending_entry = {'type': type_str, 'old_id': old_id, 'new_id': new_id, 'pack_path': old_source, 'mode': mode}
    write_pending_renames(project_path, pending_entry)

    if markdown_rewrite is not None:
        atomic_write_text(full_old, markdown_rewrite)
    if pack_rewrite_records is not None:
        raw_records, replacements = pack_rewrite_records
        pack_dump_preserving_skipped(full_old, raw_records, replacements, type_str)

    args_ns = argparse.Namespace(project_path=str(project_path), json=False)
    refresh_rc = cmd_refresh(args_ns)
    if refresh_rc not in (EXIT_OK, 1):
        return refresh_rc
    print(f'Renamed {type_str} {old_id} -> {new_id} in {old_source}')
    return refresh_rc


def _slug_for_add(s):
    out = ''.join(ch if (ch.isascii() and ch.isalnum()) or ch == '_' else '_' for ch in s.strip().lower())
    while '__' in out:
        out = out.replace('__', '_')
    return out.strip('_')


def cmd_add(args):
    project_path = resolve_project_path(args)
    if not (project_path / '.cofr').is_dir():
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    try:
        state, _ = load_state(project_path)
        refuse_msg = _refuse_if_pending_migration(state)
        if refuse_msg:
            print(refuse_msg, file=sys.stderr)
            return EXIT_USAGE
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT

    if sys.stdin.isatty():
        print('cofr add: no markdown received on stdin. Pipe a complete frontmatter+body markdown document.', file=sys.stderr)
        return EXIT_USAGE

    raw = sys.stdin.read()
    if not raw.strip():
        print('cofr add: stdin was empty.', file=sys.stderr)
        return EXIT_USAGE

    metadata, body = split_frontmatter(raw)
    if '_yaml_error' in metadata:
        print(f'cofr add: malformed YAML frontmatter: {metadata['_yaml_error']}', file=sys.stderr)
        return EXIT_USAGE
    type_str = metadata.get('type')
    if not type_str:
        print('cofr add: stdin missing required `type:` field in YAML frontmatter.', file=sys.stderr)
        return EXIT_USAGE
    if type_str not in DOMAIN_TYPES:
        valid = ', '.join(sorted(DOMAIN_TYPES))
        print(f'cofr add: unknown type {type_str!r}. Valid types: {valid}', file=sys.stderr)
        return EXIT_USAGE

    sections = parse_sections(body)
    explicit_id = metadata.get('id')
    if 'id' in metadata:
        if not isinstance(explicit_id, str) or not _VALID_ID_RE.match(explicit_id):
            print(f'cofr add: invalid id {explicit_id!r}: ids must match [A-Za-z0-9_-]+ (no path separators, dots, colons, or whitespace).', file=sys.stderr)
            return EXIT_USAGE
        obj_id = explicit_id
    else:
        hint = metadata.get('title') or ''
        if not hint:
            primary_field = _TYPE_PRIMARY_FIELD.get(type_str)
            if primary_field:
                hint = metadata.get(primary_field) or ''
        if hint:
            slug = _slug_for_add(str(hint)[:60])
            obj_id = f'{type_str}_{slug}' if slug else f'{type_str}_{uuid.uuid4().hex[:8]}'
        else:
            obj_id = f'{type_str}_{uuid.uuid4().hex[:8]}'

    record_dict = {'id': obj_id, 'type': type_str}
    fields_from_frontmatter = set()
    input_warnings = []
    for k, v in metadata.items():
        if k in ('id', 'type'):
            continue
        if k == 'stale':
            input_warnings.append(_stale_warning_for_type(type_str))
            continue
        record_dict[k] = v
        fields_from_frontmatter.add(k)
    section_map = SECTION_MAP.get(type_str, {})
    extra_sections = {}
    section_warnings = []
    for heading, content in sections.items():
        mapped = section_map.get(heading)
        if mapped is None:
            extra_sections[heading] = content
            continue
        field_name, mode = mapped
        if field_name in fields_from_frontmatter:
            extra_sections[heading] = content
            continue
        value, parse_warnings = parse_field_value(mode, content)
        for w in parse_warnings:
            section_warnings.append(f'section {heading!r}: {w}')
        if value is None:
            extra_sections[heading] = content
            continue
        record_dict[field_name] = value
    if extra_sections:
        record_dict['extra_sections'] = extra_sections

    placement_warnings = []
    if args.at:
        validated, err = validate_at_path(args.at, type_str, project_path)
        if err:
            print(f'cofr add: {err}', file=sys.stderr)
            return EXIT_USAGE
        target_pack = validated
        if type_str == 'evidence':
            slug = target_pack.stem
            if record_dict.get('source_slug') and record_dict['source_slug'] != slug:
                print(f"cofr add: source_slug {record_dict['source_slug']!r} differs from --at filename stem {slug!r}; either drop source_slug or change --at.", file=sys.stderr)
                return EXIT_USAGE
            record_dict['source_slug'] = slug
    else:
        target_pack, route_warnings = route_pack_path(project_path, type_str, record_dict)
        if target_pack is None:
            for w in route_warnings:
                print(f'cofr add: {w}', file=sys.stderr)
            return EXIT_USAGE
        placement_warnings.extend(route_warnings)

    add_config, add_config_warnings = load_config(project_path)
    id_map, malformed_packs, scan_warnings = scan_existing_ids(project_path, state, exclude_patterns=add_config.get('exclude_patterns') or [])
    for w in add_config_warnings:
        print(f'cofr add: {w}', file=sys.stderr)
    for w in input_warnings:
        print(f'cofr add: warning: {w}', file=sys.stderr)
    for w in section_warnings:
        print(f'cofr add: {w}', file=sys.stderr)
    if malformed_packs:
        for p, exc in malformed_packs:
            print(f'cofr add: malformed pack {p}: {exc}', file=sys.stderr)
        return EXIT_USAGE

    occurrences = id_map.get(obj_id, [])
    target_rel = str(target_pack.relative_to(project_path)) if target_pack.is_absolute() else str(target_pack)
    has_collision = len(occurrences) >= 1
    force_recycle = False
    if has_collision:
        if not args.force:
            print(f'cofr add: id {obj_id!r} already exists. Occurrences:', file=sys.stderr)
            for occ in occurrences:
                print(f'  - in_state={occ["in_state"]}, state_parsed_from={occ["state_parsed_from"]}, disk_locations={occ["disk_locations"]}', file=sys.stderr)
            print('  Pass --force to replace (only when collision is in target pack), or use cofr rename.', file=sys.stderr)
            return EXIT_USAGE
        single = len(occurrences) == 1
        if not single:
            print(f'cofr add --force: id {obj_id!r} has multiple cross-source occurrences; resolve manually.', file=sys.stderr)
            return EXIT_USAGE
        occ = occurrences[0]
        disk_locs = occ['disk_locations']
        if len(disk_locs) > 1:
            print(f'cofr add --force: duplicate id {obj_id!r} has multiple disk locations within one pack; resolve manually.', file=sys.stderr)
            return EXIT_USAGE
        if disk_locs:
            disk_path = disk_locs[0]['path']
            if disk_path != target_rel:
                print(f'cofr add --force: id {obj_id!r} exists at {disk_path}, not at target {target_rel}. Use cofr rename or delete first.', file=sys.stderr)
                return EXIT_USAGE
        else:
            state_rec = None
            state_rec_type = None
            for t, ck in _COLLECTION_BY_TYPE.items():
                for it in state.get(ck, []):
                    if it.get('id') == obj_id:
                        state_rec = it
                        state_rec_type = t
                        break
                if state_rec:
                    break
            if state_rec is None or is_live(state_rec, type_str=state_rec_type):
                print(f'cofr add --force: id {obj_id!r} is live in state with no disk presence; cannot recycle a live record.', file=sys.stderr)
                return EXIT_USAGE
            force_recycle = True

    if args.dry_run:
        env = envelope(project_path, {
            'routed_pack_path': target_rel,
            'record': record_dict,
            'would_collide': has_collision,
            'occurrences': occurrences,
            'warnings': input_warnings + placement_warnings + scan_warnings,
        })
        emit_json(env)
        return EXIT_OK

    if force_recycle:
        for ck in _COLLECTION_BY_TYPE.values():
            state[ck] = [it for it in state.get(ck, []) if it.get('id') != obj_id]
        save_state(project_path, state)

    raw_existing = []
    if target_pack.is_file():
        try:
            raw_existing = yaml.safe_load(target_pack.read_text(encoding='utf-8')) or []
        except Exception as exc:
            print(f'cofr add: cannot read target pack {target_rel}: {exc}', file=sys.stderr)
            return EXIT_USAGE
        if not isinstance(raw_existing, list):
            print(f'cofr add: target pack {target_rel} top-level is not a list ({type(raw_existing).__name__}); cannot append safely.', file=sys.stderr)
            return EXIT_USAGE
        _, target_pack_warnings = pack_load(target_pack, expected_type=type_str, return_warnings=True)
        for w in target_pack_warnings:
            print(f'cofr add: target pack {target_rel}: {w}', file=sys.stderr)

    pack_dump_preserving_skipped(target_pack, raw_existing, {obj_id: record_dict}, type_str)

    args_ns = argparse.Namespace(project_path=str(project_path), json=False)
    refresh_rc = cmd_refresh(args_ns)
    for w in input_warnings + placement_warnings:
        print(f'cofr add: warning: {w}', file=sys.stderr)
    print(f'wrote {target_rel}')
    if (input_warnings or placement_warnings) and refresh_rc == EXIT_OK:
        return EXIT_SOFT_WARN
    return refresh_rc


def _slim_claim(claim):
    return {
        'id': claim.get('id', ''),
        'title': claim.get('title', ''),
        'status': claim.get('status', ''),
        'confidence': claim.get('confidence', ''),
        'supporting_evidence_count': claim.get('supporting_evidence_count', 0),
        'counter_evidence_count': claim.get('counter_evidence_count', 0),
    }


def _slim_question(q):
    qtext = q.get('question', '') or ''
    return {
        'id': q.get('id', ''),
        'question_summary': _truncate_word(qtext),
        'priority': q.get('priority', ''),
        'blocking_severity': q.get('blocking_severity', ''),
        'status': q.get('status', ''),
        'stale': q.get('stale', False),
        'related_claim_ids': q.get('related_claim_ids', []),
    }


def _truncate_word(s, n=120):
    if not s or len(s) <= n:
        return s or ''
    cut = s[:n]
    boundary = cut.rfind(' ')
    if boundary > 0:
        return cut[:boundary]
    return cut


def _slim_risk(r):
    return {
        'id': r.get('id', ''),
        'statement_summary': _truncate_word(r.get('statement', '') or ''),
        'severity': r.get('severity', ''),
        'status': r.get('status', ''),
        'source': r.get('source', ''),
        'related_claim_ids': r.get('related_claim_ids', []),
        'related_decision_ids': r.get('related_decision_ids', []),
    }


def cmd_show_state(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT

    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE

    try:
        index = load_index(project_path)
    except CofrNotInitialized:
        index = {}
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT

    config_warnings = safe_load_config_warnings(project_path)
    has_any_state = any(state.get(collection_key) for collection_key in TYPE_TO_COLLECTION.values())

    staleness, staleness_check_warnings = compute_semantic_staleness(state)
    staleness_strings = []
    for entry in staleness.get('claim_unchanged', []):
        staleness_strings.append(f"staleness: claim {entry['claim_id']} unchanged despite {entry['newer_count']} newer evidence record(s): {entry['newer_evidence_ids']}")
    for entry in staleness.get('decision_basis_eroded', []):
        staleness_strings.append(f"staleness: decision {entry['decision_id']} basis eroded on {entry['depended_on_claim_id']} ({entry['mode']}: {entry['reason']})")
    if args.json:
        data = agent_complete_state(state, index)
        data['warnings'] = config_warnings + staleness_strings + list(staleness_check_warnings)
        note = None if has_any_state else NOTE_NO_STATE
        emit_json(envelope(project_path, data, note=note, warnings=load_warnings))
    else:
        if not has_any_state:
            print(NOTE_NO_STATE)
            return EXIT_SOFT_WARN
        print(json.dumps(agent_complete_state(state, index), indent=2, sort_keys=True, default=_json_default))
        if load_warnings:
            print(f'  warnings: {len(load_warnings)}', file=sys.stderr)
            emit_warnings_to_stderr(load_warnings)
        if config_warnings:
            emit_warnings_to_stderr(config_warnings)
        if staleness_strings:
            emit_warnings_to_stderr(staleness_strings)
        if staleness_check_warnings:
            emit_warnings_to_stderr(staleness_check_warnings)

    if not has_any_state or load_warnings or config_warnings or staleness_strings or staleness_check_warnings:
        return EXIT_SOFT_WARN
    return EXIT_OK


def cmd_show_claims(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT

    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE

    try:
        index = load_index(project_path)
    except CofrNotInitialized:
        index = {}
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT

    config_warnings = safe_load_config_warnings(project_path)
    enriched = attach_computed_claim_fields(state, index, include_retired=getattr(args, 'all', False))
    if args.summary:
        enriched = [_slim_claim(c) for c in enriched]

    has_claims = len(enriched) > 0

    if args.json:
        note = None if has_claims else NOTE_NO_CLAIMS
        emit_json(envelope(project_path, {'claims': enriched, 'warnings': config_warnings}, note=note, warnings=load_warnings))
    else:
        if not has_claims:
            print(NOTE_NO_CLAIMS)
            return EXIT_SOFT_WARN
        for c in enriched:
            print(f'{c.get('id', '?')}  [{c.get('status', '?')}, {c.get('confidence', '?')}]  {c.get('title', '')}')
        if load_warnings:
            print(f'  warnings: {len(load_warnings)}', file=sys.stderr)
            emit_warnings_to_stderr(load_warnings)
        if config_warnings:
            emit_warnings_to_stderr(config_warnings)

    if not has_claims or load_warnings or config_warnings:
        return EXIT_SOFT_WARN
    return EXIT_OK


def cmd_show_questions(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT
    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE
    questions = state.get('open_questions', [])
    if not getattr(args, 'all', False):
        questions = [q for q in questions if is_live(q, type_str='question')]
    projected = project_records_list(questions)
    if not args.summary:
        live_claims_by_id = {
            c.get('id'): c for c in state.get('claims', [])
            if c.get('id') and is_live(c, type_str='claim')
        }
        for q in projected:
            q['related_claims'] = [
                {
                    'id': cid,
                    'title': live_claims_by_id[cid].get('title', ''),
                    'status': live_claims_by_id[cid].get('status', ''),
                    'confidence': live_claims_by_id[cid].get('confidence', ''),
                }
                for cid in q.get('related_claim_ids', [])
                if cid in live_claims_by_id
            ]
    if args.summary:
        projected = [_slim_question(q) for q in projected]
    config_warnings = safe_load_config_warnings(project_path)
    if args.json:
        note = None if projected else 'No live questions (status in {open, in_progress}). Create records with type: question in questions.yaml to populate this list, or pass --all to include resolved/deprioritized/stale.'
        emit_json(envelope(project_path, {'questions': projected, 'warnings': config_warnings}, note=note, warnings=load_warnings))
    else:
        cmd_warnings = list(load_warnings) + config_warnings
        if not projected:
            print('No live questions.')
            if cmd_warnings:
                emit_warnings_to_stderr(cmd_warnings)
            return EXIT_SOFT_WARN
        for q in projected:
            print(f"{q.get('id', '?')}  [{q.get('priority', '?')}, {q.get('status', '?')}]  {q.get('question_summary', q.get('question', ''))[:80]}")
        if cmd_warnings:
            emit_warnings_to_stderr(cmd_warnings)
    if not projected or load_warnings or config_warnings:
        return EXIT_SOFT_WARN
    return EXIT_OK


def cmd_show_diff(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT
    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE
    config_warnings = safe_load_config_warnings(project_path)
    sidecar_path = project_path / '.cofr' / 'last_diff.json'
    artifact_path = project_path / 'artifacts' / 'what_changed.md'
    if args.json:
        if not sidecar_path.is_file():
            print('No diff sidecar found at .cofr/last_diff.json. Run `cofr refresh` to populate it.', file=sys.stderr)
            return EXIT_USAGE
        sidecar, sidecar_error = load_json_file_or_corrupt(sidecar_path, '.cofr/last_diff.json')
        if sidecar_error:
            print(sidecar_error, file=sys.stderr)
            return EXIT_CORRUPT
        emit_json(envelope(project_path, {'diff': sidecar, 'warnings': config_warnings}, warnings=load_warnings))
        return EXIT_SOFT_WARN if (load_warnings or config_warnings) else EXIT_OK
    if artifact_path.is_file():
        print(artifact_path.read_text(encoding='utf-8'), end='')
        return EXIT_OK
    if sidecar_path.is_file():
        sidecar, sidecar_error = load_json_file_or_corrupt(sidecar_path, '.cofr/last_diff.json')
        if sidecar_error:
            print(sidecar_error, file=sys.stderr)
            return EXIT_CORRUPT
        md = generate_what_changed(sidecar, project_path)
        atomic_write_text(artifact_path, md)
        print(md, end='')
        print('cofr show diff: regenerated artifacts/what_changed.md from .cofr/last_diff.json', file=sys.stderr)
        return EXIT_OK
    print('No diff sidecar found at .cofr/last_diff.json. Run `cofr refresh` to populate it.', file=sys.stderr)
    return EXIT_USAGE


def cmd_show_overview(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT
    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE
    config, config_warnings = load_config(project_path)
    sidecar_path = project_path / '.cofr' / 'last_diff.json'
    recent_changes = None
    note = None
    if sidecar_path.is_file():
        sidecar, sidecar_error = load_json_file_or_corrupt(sidecar_path, '.cofr/last_diff.json')
        if sidecar_error:
            print(sidecar_error, file=sys.stderr)
            return EXIT_CORRUPT
        recent_changes = {
            'added_count': len(sidecar.get('added', [])),
            'removed_count': len(sidecar.get('removed', [])),
            'modified_count': len(sidecar.get('modified', [])),
            'renamed_count': len(sidecar.get('renamed', [])),
        }
    else:
        note = 'No diff sidecar at .cofr/last_diff.json; run cofr refresh to populate recent_changes.'

    counts = {}
    type_to_collection = {'claims': ('claims', 'claim'), 'evidence': ('evidence', 'evidence'), 'decisions': ('decisions', 'decision'), 'questions': ('open_questions', 'question'), 'risks': ('risks', 'risk'), 'experiments': ('experiments', 'experiment')}
    for label, (ck, type_str) in type_to_collection.items():
        recs = state.get(ck, [])
        live_count = sum(1 for r in recs if is_live(r, type_str=type_str))
        counts[label] = {'live': live_count, 'non_live': len(recs) - live_count, 'total': len(recs)}

    staleness, semantic_warnings = compute_semantic_staleness(state)
    staleness_flags = {
        'claims_unchanged_with_new_evidence': [e['claim_id'] for e in staleness['claim_unchanged']],
        'decisions_with_eroded_basis': sorted({e['decision_id'] for e in staleness['decision_basis_eroded']}),
    }
    internal_warnings = list(config_warnings) + list(semantic_warnings)

    live_questions = [q for q in state.get('open_questions', []) if is_live(q, type_str='question')]
    live_questions.sort(key=lambda q: (-_PRIORITY_RANK.get(q.get('priority', ''), 0), -_SEVERITY_RANK.get(q.get('blocking_severity', ''), 0), q.get('id', '')))
    top_questions = [{
        'id': q.get('id', ''),
        'priority': q.get('priority', ''),
        'blocking_severity': q.get('blocking_severity', ''),
        'question_summary': _truncate_word(q.get('question', '') or ''),
    } for q in live_questions[:10]]

    live_risks = [r for r in state.get('risks', []) if r.get('source', 'authored') == 'authored' and is_live(r, type_str='risk')]
    live_risks.sort(key=lambda r: (-_SEVERITY_RANK.get(r.get('severity', ''), 0), -_STATUS_RANK.get(r.get('status', ''), 0), r.get('id', '')))
    top_risks = [{
        'id': r.get('id', ''),
        'severity': r.get('severity', ''),
        'status': r.get('status', ''),
        'statement_summary': _truncate_word(r.get('statement', '') or ''),
    } for r in live_risks[:10]]

    confidence_trends = []
    status_trends = []
    for c in state.get('claims', []):
        tl = c.get('_timeline') or []
        stl = c.get('_status_timeline') or []
        if len(tl) >= 2:
            total_delta = 0.0
            prev_num = None
            for e in tl:
                n = confidence_to_numeric(e.get('c'))
                if n is None:
                    continue
                if prev_num is not None:
                    total_delta += abs(n - prev_num)
                prev_num = n
            confidence_trends.append({
                'claim_id': c.get('id', ''),
                'title': c.get('title', ''),
                'current_confidence': c.get('confidence', ''),
                'timeline': tl,
                '_rank': total_delta,
            })
        if len(stl) >= 2:
            transitions = sum(1 for i in range(1, len(stl)) if stl[i].get('s') != stl[i - 1].get('s'))
            status_trends.append({
                'claim_id': c.get('id', ''),
                'title': c.get('title', ''),
                'current_status': c.get('status', ''),
                'status_timeline': stl,
                '_rank': transitions,
            })
    confidence_trends.sort(key=lambda x: (-x['_rank'], x['claim_id']))
    status_trends.sort(key=lambda x: (-x['_rank'], x['claim_id']))
    for t in confidence_trends:
        t.pop('_rank', None)
    for t in status_trends:
        t.pop('_rank', None)
    confidence_trends = confidence_trends[:10]
    status_trends = status_trends[:10]

    data = {
        'project_summary': {
            'project_name': config.get('project_name', ''),
            'project_objective': config.get('project_objective', ''),
            'counts': counts,
            'last_refresh': state.get('last_refresh', ''),
        },
        'recent_changes': recent_changes,
        'confidence_trends': confidence_trends,
        'status_trends': status_trends,
        'staleness_flags': staleness_flags,
        'top_questions': top_questions,
        'top_risks': top_risks,
        'warnings': internal_warnings,
    }
    if args.json:
        emit_json(envelope(project_path, data, note=note, warnings=load_warnings))
    else:
        print(f"# Overview: {config.get('project_name', '<unnamed>')}")
        print()
        print(f"Objective: {config.get('project_objective', '')}")
        print()
        print('## Recent changes')
        if recent_changes is None:
            print('- No diff sidecar found. Run `cofr refresh` to populate recent changes.')
        else:
            print(f"- added: {recent_changes['added_count']}")
            print(f"- removed: {recent_changes['removed_count']}")
            print(f"- modified: {recent_changes['modified_count']}")
            print(f"- renamed: {recent_changes['renamed_count']}")
        print()
        print('## Counts')
        for label, c in counts.items():
            print(f"- {label}: {c['live']} live / {c['total']} total")
        print()
        print('## Confidence trends')
        if not confidence_trends:
            print('- None.')
        for t in confidence_trends:
            values = ' -> '.join(e.get('c', '') for e in t.get('timeline', []) if e.get('c'))
            print(f"- {t['claim_id']}: {values}")
        print()
        print('## Status trends')
        if not status_trends:
            print('- None.')
        for t in status_trends:
            values = ' -> '.join(e.get('s', '') for e in t.get('status_timeline', []) if e.get('s'))
            print(f"- {t['claim_id']}: {values}")
        print()
        print('## Staleness flags')
        any_stale = False
        for cid in staleness_flags['claims_unchanged_with_new_evidence']:
            print(f'- claim unchanged despite new evidence: {cid}')
            any_stale = True
        for did in staleness_flags['decisions_with_eroded_basis']:
            print(f'- decision basis eroded: {did}')
            any_stale = True
        if not any_stale:
            print('- None.')
        print()
        print('## Top questions')
        for q in top_questions:
            print(f"- {q['id']} [{q['priority']}/{q['blocking_severity']}]: {q['question_summary']}")
        if not top_questions:
            print('- None.')
        print()
        print('## Top risks')
        for r in top_risks:
            print(f"- {r['id']} [{r['severity']}/{r['status']}]: {r['statement_summary']}")
        if not top_risks:
            print('- None.')
    if load_warnings or internal_warnings:
        return EXIT_SOFT_WARN
    return EXIT_OK


def cmd_show_risks(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT
    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE

    authored = [r for r in state.get('risks', []) if r.get('source', 'authored') == 'authored']
    if not getattr(args, 'all', False):
        authored = [r for r in authored if is_live(r, type_str='risk')]
    computed, computed_warnings = compute_computed_risks(state)
    combined = project_records_list(authored) + [project_record_for_public_json(r) for r in computed]
    combined.sort(key=lambda r: (r.get('source', 'authored'), r.get('id', '')))
    if args.summary:
        combined = [_slim_risk(r) for r in combined]

    config_warnings = safe_load_config_warnings(project_path)
    if args.json:
        note = None if combined else 'No risks. No user-authored risks and no computed risks (the current state has no contradictions). Author risks with type: risk.'
        emit_json(envelope(project_path, {'risks': combined, 'warnings': config_warnings + computed_warnings}, note=note, warnings=load_warnings))
    else:
        cmd_warnings = list(load_warnings) + config_warnings + computed_warnings
        if not combined:
            print('No risks.')
            if cmd_warnings:
                emit_warnings_to_stderr(cmd_warnings)
            return EXIT_SOFT_WARN
        for r in combined:
            stmt = r.get('statement_summary', r.get('statement', '')) or ''
            print(f"{r.get('id', '?')}  [{r.get('source', '?')}, {r.get('severity', '?')}, {r.get('status', '?')}]  {stmt[:80]}")
        if cmd_warnings:
            emit_warnings_to_stderr(cmd_warnings)
    if not combined or load_warnings or config_warnings or computed_warnings:
        return EXIT_SOFT_WARN
    return EXIT_OK


def cmd_show_contradictions(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT
    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE

    contradictions, contradiction_warnings = compute_contradictions(state)
    review = compute_falsification_review(state)
    computed_risks, computed_warnings = compute_computed_risks(state, contradictions=contradictions)
    authored_risks = project_records_list([
        r for r in state.get('risks', [])
        if r.get('source', 'authored') == 'authored' and is_live(r, type_str='risk')
    ])
    config_warnings = safe_load_config_warnings(project_path)
    empty = sum(len(v) for v in contradictions.values()) == 0 and not review and not authored_risks

    if args.json:
        note = 'No contradictions detected. The current state has no flagged tensions.' if empty else None
        data = {
            'contradictions': contradictions,
            'falsification_review': review,
            'computed_risks': [project_record_for_public_json(r) for r in computed_risks],
            'authored_risks': authored_risks,
            'warnings': config_warnings + contradiction_warnings + computed_warnings,
        }
        emit_json(envelope(project_path, data, note=note, warnings=load_warnings))
    else:
        print(generate_contradictions(state, contradictions=contradictions,
                                      falsification_review=review,
                                      computed_risks=computed_risks), end='')
        cmd_warnings = list(load_warnings) + config_warnings + contradiction_warnings + computed_warnings
        if cmd_warnings:
            emit_warnings_to_stderr(cmd_warnings)
    if empty or load_warnings or config_warnings or contradiction_warnings or computed_warnings:
        return EXIT_SOFT_WARN
    return EXIT_OK


def _project_ranked_entry(entry):
    out = dict(entry)
    out['record'] = project_record_for_public_json(entry['record'])
    return out


def cmd_show_review(args):
    project_path = Path(args.project_path).resolve()
    try:
        state, load_warnings = load_state(project_path)
    except CofrNotInitialized:
        print(ERR_NOT_INITIALIZED_TEMPLATE.format(path=project_path), file=sys.stderr)
        return EXIT_NOT_INITIALIZED
    except CorruptStateError as exc:
        print(ERR_CORRUPT_STATE_TEMPLATE.format(path=project_path, detail=exc), file=sys.stderr)
        return EXIT_CORRUPT
    refuse_msg = _refuse_if_pending_migration(state)
    if refuse_msg:
        print(refuse_msg, file=sys.stderr)
        return EXIT_USAGE

    contradictions, contradiction_warnings = compute_contradictions(state)
    ranked, rationale_lines, _ = rank_next_decisions(state, contradictions=contradictions)
    config_warnings = safe_load_config_warnings(project_path)
    if args.json:
        note = None if ranked else 'No decision to recommend. No open questions and no decisions with eroded basis.'
        data = {
            'top_decision': _project_ranked_entry(ranked[0]) if ranked else None,
            'runner_ups': [_project_ranked_entry(e) for e in ranked[1:4]],
            'ranking_rationale': rationale_lines,
            'warnings': config_warnings + contradiction_warnings,
        }
        emit_json(envelope(project_path, data, note=note, warnings=load_warnings))
    else:
        print(generate_next_decision(state, contradictions=contradictions), end='')
        cmd_warnings = list(load_warnings) + config_warnings + contradiction_warnings
        if cmd_warnings:
            emit_warnings_to_stderr(cmd_warnings)
    if not ranked or load_warnings or config_warnings or contradiction_warnings:
        return EXIT_SOFT_WARN
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog='cofr',
        description=load_help_text('top'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version', action='version', version=f'cofr {__version__}')
    subparsers = parser.add_subparsers(dest='command', required=True)

    p_init = subparsers.add_parser(
        'init',
        help='Initialize cofr in a project directory',
        description=load_help_text('init'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_init.add_argument('project_path', nargs='?', default='.', help='Project directory (default: cwd)')
    p_init.set_defaults(func=cmd_init)

    p_refresh = subparsers.add_parser(
        'refresh',
        help='Re-scan project, update state, regenerate artifacts',
        description=load_help_text('refresh'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_refresh.add_argument('project_path', nargs='?', default='.')
    p_refresh.add_argument('--json', action='store_true', help='Emit machine-readable JSON envelope to stdout')
    p_refresh.add_argument('--rebuild-timelines', action='store_true', help='Rebuild claim timelines from history snapshots')
    p_refresh.add_argument('--rebuild-renames-log', action='store_true', help='Re-derive .cofr/renames.json from history snapshots (crash recovery)')
    p_refresh.set_defaults(func=cmd_refresh)

    p_rename = subparsers.add_parser(
        'rename',
        help='Rename an id (cascades typed pointers + pack rewrites)',
        description=load_help_text('rename'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_rename.add_argument('old_id')
    p_rename.add_argument('new_id')
    p_rename.add_argument('project_path', nargs='?', default='.')
    p_rename.set_defaults(func=cmd_rename)

    p_show = subparsers.add_parser(
        'show',
        help='Print state, claims, etc.',
        description=load_help_text('show'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_sub = p_show.add_subparsers(dest='show_target', required=True)

    p_show_state = show_sub.add_parser(
        'state',
        description=load_help_text('show_state'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_state.add_argument('project_path', nargs='?', default='.')
    p_show_state.add_argument('--json', action='store_true')
    p_show_state.set_defaults(func=cmd_show_state)

    p_show_claims = show_sub.add_parser(
        'claims',
        description=load_help_text('show_claims'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_claims.add_argument('project_path', nargs='?', default='.')
    p_show_claims.add_argument('--json', action='store_true')
    p_show_claims.add_argument('--summary', action='store_true')
    p_show_claims.add_argument('--all', action='store_true', help='Include retired claims (default: live only)')
    p_show_claims.set_defaults(func=cmd_show_claims)

    p_show_questions = show_sub.add_parser(
        'questions',
        description=load_help_text('show_questions'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_questions.add_argument('project_path', nargs='?', default='.')
    p_show_questions.add_argument('--json', action='store_true')
    p_show_questions.add_argument('--summary', action='store_true')
    p_show_questions.add_argument('--all', action='store_true', help='Include resolved/deprioritized/stale questions (default: live only)')
    p_show_questions.set_defaults(func=cmd_show_questions)

    p_show_diff = show_sub.add_parser(
        'diff',
        description=load_help_text('show_diff'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_diff.add_argument('project_path', nargs='?', default='.')
    p_show_diff.add_argument('--json', action='store_true')
    p_show_diff.set_defaults(func=cmd_show_diff)

    p_show_overview = show_sub.add_parser(
        'overview',
        description=load_help_text('show_overview'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_overview.add_argument('project_path', nargs='?', default='.')
    p_show_overview.add_argument('--json', action='store_true')
    p_show_overview.set_defaults(func=cmd_show_overview)

    p_show_risks = show_sub.add_parser(
        'risks',
        description=load_help_text('show_risks'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_risks.add_argument('project_path', nargs='?', default='.')
    p_show_risks.add_argument('--json', action='store_true')
    p_show_risks.add_argument('--summary', action='store_true')
    p_show_risks.add_argument('--all', action='store_true', help='Include mitigated/resolved/stale authored risks (default: live only)')
    p_show_risks.set_defaults(func=cmd_show_risks)

    p_show_contradictions = show_sub.add_parser(
        'contradictions',
        description=load_help_text('show_contradictions'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_contradictions.add_argument('project_path', nargs='?', default='.')
    p_show_contradictions.add_argument('--json', action='store_true')
    p_show_contradictions.set_defaults(func=cmd_show_contradictions)

    p_show_review = show_sub.add_parser(
        'review',
        description=load_help_text('show_review'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show_review.add_argument('project_path', nargs='?', default='.')
    p_show_review.add_argument('--json', action='store_true')
    p_show_review.set_defaults(func=cmd_show_review)

    p_migrate = subparsers.add_parser(
        'migrate',
        help='Migration management commands',
        description=load_help_text('migrate'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_migrate.add_argument('--rollback', action='store_true', help='Rollback a migration')
    p_migrate.add_argument('--from-history', default=None, help='Snapshot path for post-commit rollback (case B/C)')
    p_migrate.add_argument('--yes-i-know-what-im-doing', action='store_true', help='Confirm destructive rollback (case B/C)')
    p_migrate.add_argument('project_path', nargs='?', default='.')
    p_migrate.set_defaults(func=cmd_migrate_rollback)

    p_add = subparsers.add_parser(
        'add',
        help='Write a structured object (claim/evidence/...) into the project and validate',
        description=load_help_text('add'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_add.add_argument('project_path', nargs='?', default='.')
    p_add.add_argument('--at', default=None, help='Override placement (path relative to project)')
    p_add.add_argument(
        '--dry-run', action='store_true',
        help='Preview normalized candidate record, routed pack, warnings, and collisions without writing',
    )
    p_add.add_argument('--force', action='store_true', help='Overwrite an existing record with the same id when collision is in the same target pack, or recycle a non-live state-only record')
    p_add.set_defaults(func=cmd_add)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
