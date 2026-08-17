'''State seam: .cofr/state.json, .cofr/index.json, .cofr/config.yaml I/O;
history snapshots; record-merge (apply_parsed_records); reference validation;
v1 -> v2 migration and rollback.'''
import json
import os
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from cofr import __version__
from cofr.domain import CLASS_TO_TYPE_STR, DOMAIN_TYPES, TYPE_TO_COLLECTION, from_dict, to_dict, validate_and_normalize


SCHEMA_VERSION = 2
EMPTY_STATE_COLLECTIONS = ('claims', 'evidence', 'experiments', 'decisions', 'open_questions', 'risks', 'artifacts')
_EXPECTED_V3_PACK_PATHS = ('claims.yaml', 'decisions.yaml', 'questions.yaml', 'risks.yaml', 'experiments.yaml')

_USER_FIELDS_COMPARE_EXCLUDED = frozenset({
    'parsed_from', 'first_seen', 'last_updated', 'stale', 'source_missing',
    '_timeline', '_status_timeline',
})
_MERGE_INTO_STATE_PROTECTED = frozenset({
    'first_seen', 'last_updated', '_timeline', '_status_timeline',
})
_CLAIM_STALE_STATUS = 'retired'
_VALID_STATE_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')


class CofrNotInitialized(Exception):
    pass


class CorruptStateError(Exception):
    pass


def _iso_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _history_timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')


def _cofr_dir(project_path):
    return Path(project_path) / '.cofr'


def project_local_path(project_path, value, label='path'):
    '''Resolve a persisted/user path and reject escapes, including symlinks.'''
    root = Path(project_path).resolve()
    raw = Path(value)
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise RuntimeError(f'{label} points outside project: {value!r}')
    return resolved


def default_config():
    return {
        'project_name': '',
        'project_objective': '',
        'exclude_patterns': [],
        'timeline_min_entries': 20,
        'timeline_min_days': 180,
    }


def _empty_state():
    state = {'schema_version': SCHEMA_VERSION, 'cofr_version': __version__, 'last_refresh': ''}
    for key in EMPTY_STATE_COLLECTIONS:
        state[key] = []
    return state


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True, default=_json_default, allow_nan=False) + '\n')


def init(project_path):
    project_path = Path(project_path)
    cofr_dir = _cofr_dir(project_path)
    if cofr_dir.exists():
        return False
    cofr_dir.mkdir(parents=True)
    (cofr_dir / 'history').mkdir()
    (project_path / 'artifacts').mkdir(exist_ok=True)
    atomic_write_json(cofr_dir / 'state.json', _empty_state())
    atomic_write_json(cofr_dir / 'index.json', {})
    cfg_text = (
        '# cofr project config. Edit project_objective to a one-sentence statement of\n'
        '# what this project is trying to prove or build. exclude_patterns accepts\n'
        '# additional path globs to skip beyond the .gitignore and built-in excludes.\n'
        + yaml.safe_dump({**default_config(), 'project_name': project_path.name}, sort_keys=True)
    )
    atomic_write_text(cofr_dir / 'config.yaml', cfg_text)
    return True


def load_state(project_path):
    cofr_dir = _cofr_dir(project_path)
    state_path = cofr_dir / 'state.json'
    if not state_path.is_file():
        raise CofrNotInitialized(f'No .cofr/ at {project_path}. Run `cofr init` first.')
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise CorruptStateError(f'.cofr/state.json is unreadable or not valid JSON: {exc}') from exc
    if not isinstance(state, dict):
        raise CorruptStateError(f'.cofr/state.json top-level value must be an object, got {type(state).__name__}')
    persisted_version = state.get('schema_version')
    if persisted_version is None:
        raise CorruptStateError('.cofr/state.json missing schema_version field')
    if persisted_version == 1:
        state['_pending_migration'] = True
        for key in EMPTY_STATE_COLLECTIONS:
            state.setdefault(key, [])
        return state, []
    if persisted_version != SCHEMA_VERSION:
        raise CorruptStateError(f'unsupported schema_version {persisted_version!r} (this cofr expects {SCHEMA_VERSION})')
    warnings = []
    seen_ids = {}
    collections_to_validate = list(TYPE_TO_COLLECTION.items()) + [('artifact', 'artifacts')]
    for type_str, collection_key in collections_to_validate:
        collection_value = state.get(collection_key, [])
        if not isinstance(collection_value, list):
            raise CorruptStateError(f'.cofr/state.json field {collection_key!r} must be a list, got {type(collection_value).__name__}')
        cleaned = []
        for idx, raw in enumerate(collection_value):
            if not isinstance(raw, dict):
                raise CorruptStateError(f'.cofr/state.json field {collection_key!r} item {idx} must be an object, got {type(raw).__name__}')
            try:
                obj = from_dict(type_str, raw)
            except TypeError as exc:
                raise CorruptStateError(f'.cofr/state.json field {collection_key!r} item {idx} is invalid: {exc}') from exc
            _, enum_warnings = validate_and_normalize(obj, user_authored=True)
            if not isinstance(obj.id, str) or not _VALID_STATE_ID_RE.fullmatch(obj.id):
                raise CorruptStateError(f'.cofr/state.json field {collection_key!r} item {idx} has invalid id {obj.id!r}')
            if obj.id in seen_ids:
                raise CorruptStateError(
                    f'.cofr/state.json duplicate id {obj.id!r} in {seen_ids[obj.id]} and {collection_key}[{idx}]'
                )
            seen_ids[obj.id] = f'{collection_key}[{idx}]'
            for w in enum_warnings:
                warnings.append(f'state.json: {w}')
            cleaned.append(to_dict(obj))
        state[collection_key] = cleaned
    return state, warnings


def save_state(project_path, state):
    cofr_dir = _cofr_dir(project_path)
    if not cofr_dir.is_dir():
        raise CofrNotInitialized(f'No .cofr/ at {project_path}. Run `cofr init` first.')
    state = dict(state)
    state['last_refresh'] = _iso_now()
    state.setdefault('schema_version', SCHEMA_VERSION)
    state['cofr_version'] = __version__
    seen_ids = {}
    for collection_key in EMPTY_STATE_COLLECTIONS:
        for idx, item in enumerate(state.get(collection_key, [])):
            rid = item.get('id') if isinstance(item, dict) else None
            if not isinstance(rid, str) or not _VALID_STATE_ID_RE.fullmatch(rid):
                raise CorruptStateError(f'cannot save invalid id {rid!r} at {collection_key}[{idx}]')
            if rid in seen_ids:
                raise CorruptStateError(f'cannot save duplicate id {rid!r} in {seen_ids[rid]} and {collection_key}[{idx}]')
            seen_ids[rid] = f'{collection_key}[{idx}]'
    atomic_write_json(cofr_dir / 'state.json', state)


def load_index(project_path):
    cofr_dir = _cofr_dir(project_path)
    idx_path = cofr_dir / 'index.json'
    if not idx_path.is_file():
        raise CofrNotInitialized(f'No .cofr/ at {project_path}.')
    try:
        index = json.loads(idx_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise CorruptStateError(f'.cofr/index.json is unreadable or invalid JSON: {exc}') from exc
    if not isinstance(index, dict):
        raise CorruptStateError(f'.cofr/index.json top-level value must be an object, got {type(index).__name__}')
    for path, entry in index.items():
        if not isinstance(path, str) or not isinstance(entry, dict):
            raise CorruptStateError('.cofr/index.json entries must map text paths to objects')
    return index


def save_index(project_path, index_dict):
    cofr_dir = _cofr_dir(project_path)
    if not cofr_dir.is_dir():
        raise CofrNotInitialized(f'No .cofr/ at {project_path}.')
    atomic_write_json(cofr_dir / 'index.json', index_dict)


def load_config(project_path):
    '''Load config from .cofr/config.yaml. Returns (cfg, warnings).

    Validates timeline_min_entries / timeline_min_days as non-negative ints;
    falls back to default + emits per-key warning when invalid. Malformed YAML
    or a non-mapping top-level value emits a warning and falls back to defaults
    rather than raising a traceback at the tool boundary.
    '''
    cofr_dir = _cofr_dir(project_path)
    cfg_path = cofr_dir / 'config.yaml'
    if not cfg_path.is_file():
        raise CofrNotInitialized(f'No .cofr/ at {project_path}.')
    cfg = default_config()
    warnings = []
    try:
        loaded = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        warnings.append(f'config.yaml: malformed YAML; using defaults ({exc.__class__.__name__}: {exc})')
        return cfg, warnings
    if loaded is None:
        return cfg, warnings
    if not isinstance(loaded, dict):
        warnings.append(f'config.yaml: top-level value must be a mapping; got {type(loaded).__name__}. Using defaults.')
        return cfg, warnings
    for k, v in loaded.items():
        if k not in cfg:
            continue
        if k in ('timeline_min_entries', 'timeline_min_days'):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                warnings.append(f'config.yaml: {k} must be a non-negative integer; using default {cfg[k]} (got {v!r})')
                continue
        if k == 'exclude_patterns':
            if not isinstance(v, list) or not all(isinstance(p, str) for p in v):
                warnings.append(f'config.yaml: exclude_patterns must be a list of strings; using default [] (got {v!r})')
                continue
        if k in ('project_name', 'project_objective') and not isinstance(v, str):
            warnings.append(f'config.yaml: {k} must be text; using default {cfg[k]!r} (got {v!r})')
            continue
        cfg[k] = v
    return cfg, warnings


def _retain_timeline_entries(entries, config):
    if not entries:
        return entries
    min_entries = (config or {}).get('timeline_min_entries', 20)
    min_days = (config or {}).get('timeline_min_days', 180)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(min_days))
        cutoff_iso = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        cutoff_iso = ''
    sorted_entries = sorted(entries, key=lambda e: e.get('t', ''))
    within_window = [e for e in sorted_entries if e.get('t', '') >= cutoff_iso] if cutoff_iso else sorted_entries
    most_recent = sorted_entries[-int(min_entries):] if int(min_entries) > 0 else []
    seen = set()
    retained = []
    for e in within_window + most_recent:
        key = (e.get('t'), str(e))
        if key not in seen:
            seen.add(key)
            retained.append(e)
    retained.sort(key=lambda e: e.get('t', ''))
    return retained


def snapshot_history(project_path):
    cofr_dir = _cofr_dir(project_path)
    state_path = cofr_dir / 'state.json'
    if not state_path.is_file():
        raise CofrNotInitialized(f'No .cofr/state.json at {project_path}.')
    base_name = _history_timestamp()
    snap_path = cofr_dir / 'history' / f'{base_name}.json'
    suffix_n = 1
    while snap_path.exists():
        snap_path = cofr_dir / 'history' / f'{base_name}-{suffix_n}.json'
        suffix_n += 1
    atomic_write_text(snap_path, state_path.read_text(encoding='utf-8'))
    return snap_path


def _user_fields_equal(existing_dict, new_dict):
    keys = (set(existing_dict) | set(new_dict)) - _USER_FIELDS_COMPARE_EXCLUDED
    for key in keys:
        if existing_dict.get(key) != new_dict.get(key):
            return False
    return True


def _pack_path_from_parsed_from(parsed_from):
    '''Strip `#id` suffix from a parsed_from to get the pack file path.

    For legacy markdown (`claims/foo.md`) returns the string unchanged.
    For V3 packs (`claims.yaml#claim-foo`) returns `claims.yaml`.
    '''
    if not parsed_from:
        return parsed_from
    if '#' in parsed_from:
        return parsed_from.split('#', 1)[0]
    return parsed_from


def _maybe_append_claim_timelines(new_dict, existing_dict, now, config):
    '''Append to _timeline / _status_timeline when confidence/status changed.'''
    if existing_dict is None:
        new_dict['_timeline'] = [{'t': now, 'c': new_dict.get('confidence', 'medium')}]
        new_dict['_status_timeline'] = [{'t': now, 's': new_dict.get('status', 'provisionally_supported')}]
        return
    existing_timeline = list(existing_dict.get('_timeline') or [])
    existing_status_timeline = list(existing_dict.get('_status_timeline') or [])
    new_conf = new_dict.get('confidence', 'medium')
    new_status = new_dict.get('status', 'provisionally_supported')
    if not existing_timeline:
        existing_timeline.append({'t': now, 'c': new_conf})
    elif existing_timeline[-1].get('c') != new_conf:
        existing_timeline.append({'t': now, 'c': new_conf})
    if not existing_status_timeline:
        existing_status_timeline.append({'t': now, 's': new_status})
    elif existing_status_timeline[-1].get('s') != new_status:
        existing_status_timeline.append({'t': now, 's': new_status})
    new_dict['_timeline'] = _retain_timeline_entries(existing_timeline, config)
    new_dict['_status_timeline'] = _retain_timeline_entries(existing_status_timeline, config)


def _merge_with_existing(existing, new_dict, type_str, now, config):
    '''Merge a new record onto an existing state record. Returns (merged_dict, was_user_changed).

    When user-authored fields are unchanged: keep existing system fields (first_seen,
    last_updated, timelines), overwrite the rest from new. When user-authored fields
    DID change: refresh last_updated, keep first_seen, append timeline entries if claim.
    '''
    if _user_fields_equal(existing, new_dict):
        merged = dict(existing)
        for k, v in new_dict.items():
            if k not in _MERGE_INTO_STATE_PROTECTED:
                merged[k] = v
        if type_str == 'claim':
            if not merged.get('_timeline'):
                _maybe_append_claim_timelines(merged, None, now, config)
            else:
                _maybe_append_claim_timelines(merged, existing, now, config)
        return merged, False
    preserved = {k: existing.get(k) for k in _MERGE_INTO_STATE_PROTECTED if k in existing}
    new_dict['first_seen'] = existing.get('first_seen') or now
    new_dict['last_updated'] = now
    for k, v in preserved.items():
        if k not in ('first_seen', 'last_updated'):
            new_dict[k] = v
    if type_str == 'claim':
        _maybe_append_claim_timelines(new_dict, existing, now, config)
    return new_dict, True


def _stale_unmatched_record(item, type_str, path, new_by_path, indexed_paths, packs_parsed_successfully):
    '''Decide whether an unmatched state record should be stale-marked or kept.

    Returns (action, was_already_stale) where action is 'keep_unchanged',
    'keep_with_stale_mark', or 'skip' (paired with new incoming).
    '''
    if (type_str, path) in new_by_path:
        return 'skip', False
    pack_path = _pack_path_from_parsed_from(path)
    pack_present_in_index = pack_path in indexed_paths
    pack_parsed_ok = pack_path in packs_parsed_successfully
    if '#' in (path or ''):
        if pack_present_in_index and not pack_parsed_ok:
            return 'keep_unchanged', False
    else:
        if pack_present_in_index:
            return 'keep_unchanged', False
    already_stale = (
        item.get('status') == _CLAIM_STALE_STATUS
        if type_str == 'claim'
        else bool(item.get('stale'))
    )
    return 'keep_with_stale_mark', already_stale


def _resolve_idless_uuid_id(new_dict, obj, rec_path, existing_by_path, existing_by_id, id_was_generated_by_path, consumed_ids):
    '''When a parsed record was minted with a fresh UUID, prefer the existing
    state id matched by parsed_from (or by id if no path match). Mutates new_dict in place.'''
    if not id_was_generated_by_path.get(rec_path):
        return existing_by_path.get(rec_path)
    existing = existing_by_path.get(rec_path)
    if existing is not None and existing.get('id') and existing.get('id') not in consumed_ids:
        new_dict['id'] = existing.get('id')
        return existing
    if existing is None:
        existing = existing_by_id.get(obj.id)
        if existing is not None and existing.get('id') not in consumed_ids:
            new_dict['id'] = existing.get('id')
            return existing
    return existing


def _index_incoming(parsed_records, id_was_generated_by_path):
    '''Build (new_by_path, new_by_id) lookup dicts from parsed records.'''
    new_by_path = {}
    new_by_id = {}
    for obj in parsed_records:
        type_str = CLASS_TO_TYPE_STR[type(obj)]
        new_by_path[(type_str, obj.parsed_from)] = obj
        if id_was_generated_by_path.get(obj.parsed_from):
            continue
        new_by_id[(type_str, obj.id)] = obj
    return new_by_path, new_by_id


def _match_by_id_relocation(item, type_str, path, new_by_id, new_by_path,
                             matched_state_keys, consumed_ids, diff, keep, now, config):
    '''Try to match an existing state record to an incoming record by id (relocation case).

    Returns True if matched (caller should `continue`), False if no id match.
    '''
    id_match = new_by_id.get((type_str, item.get('id')))
    if id_match is None or id_match.parsed_from == path:
        return False
    consumed_ids.add(item.get('id'))
    matched_state_keys.add((type_str, id_match.parsed_from))
    merged, user_changed = _merge_with_existing(item, to_dict(id_match), type_str, now, config)
    keep.append(merged)
    if user_changed:
        diff['updated'].append({'type': type_str, 'id': merged.get('id', '')})
    return True


def _handle_unmatched_state_record(item, type_str, path, new_by_path, indexed_paths,
                                    packs_parsed_successfully, diff, keep, now, config):
    '''Stale-mark or keep an existing state record that has no incoming match.

    Mutates `item` in place when stale-marking. Appends to `keep` and `diff` as needed.
    '''
    action, already_stale = _stale_unmatched_record(item, type_str, path, new_by_path, indexed_paths, packs_parsed_successfully)
    if action == 'skip':
        return
    if action == 'keep_unchanged':
        keep.append(item)
        return
    if type_str == 'claim':
        item['stale'] = False
        item['source_missing'] = True
        if item.get('status') != _CLAIM_STALE_STATUS:
            item['status'] = _CLAIM_STALE_STATUS
            _maybe_append_claim_timelines(item, item, now, config)
    else:
        item['stale'] = True
    keep.append(item)
    if not already_stale:
        diff['stale'].append({'type': type_str, 'id': item.get('id', '')})


def _apply_incoming_record(obj, type_str, rec_path, existing_by_path, existing_by_id,
                            id_was_generated_by_path, consumed_ids, diff, keep, now, config):
    '''Apply one incoming parsed record: either create new or merge with existing.'''
    new_dict = to_dict(obj)
    existing = _resolve_idless_uuid_id(new_dict, obj, rec_path, existing_by_path, existing_by_id, id_was_generated_by_path, consumed_ids)
    if existing is None:
        new_dict['first_seen'] = now
        new_dict['last_updated'] = now
        if type_str == 'claim':
            _maybe_append_claim_timelines(new_dict, None, now, config)
        keep.append(new_dict)
        diff['new'].append({'type': type_str, 'id': new_dict.get('id', '')})
        return
    merged, user_changed = _merge_with_existing(existing, new_dict, type_str, now, config)
    keep.append(merged)
    if user_changed:
        diff['updated'].append({'type': type_str, 'id': merged.get('id', '')})


def _dedup_by_id(records):
    '''Drop duplicate-id entries (keeps first), preserves records without an id.'''
    seen = set()
    out = []
    for r in records:
        rid = r.get('id') if isinstance(r, dict) else None
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        out.append(r)
    return out


def apply_parsed_records(state, parsed_records, new_index, config=None, packs_parsed_successfully=None, id_was_generated_by_path=None):
    '''Apply parsed records to canonical state in place.

    Returns a diff dict: {'new': [...], 'updated': [...], 'stale': [...]}.

    M2 semantics:
    - Match by id FIRST (relocation: incoming id matches state id at a different
      parsed_from -- update in place, preserve first_seen/timelines).
    - Else match by parsed_from (normal refresh case).
    - Stale-mark existing records with no match, distinguishing "pack failed to
      parse" (preserve) from "record removed from successfully-parsed pack" (stale).
    - Idless-markdown UUID-churn fix: a fresh-UUID record at a known path takes
      the existing state id (avoids churn).
    '''
    now = _iso_now()
    packs_parsed_successfully = set(packs_parsed_successfully or [])
    id_was_generated_by_path = id_was_generated_by_path or {}
    new_by_path, new_by_id = _index_incoming(parsed_records, id_was_generated_by_path)
    indexed_paths = set(new_index.keys())
    diff = {'new': [], 'updated': [], 'stale': []}
    matched_state_keys = set()

    for type_str, collection_key in TYPE_TO_COLLECTION.items():
        existing_list = state.get(collection_key, [])
        existing_by_path = {item.get('parsed_from'): item for item in existing_list if item.get('parsed_from')}
        existing_by_id = {item.get('id'): item for item in existing_list if item.get('id')}
        keep = [item for item in existing_list if not item.get('parsed_from')]
        consumed_ids = set()

        for path, item in existing_by_path.items():
            if (type_str, path) in new_by_path:
                continue
            if _match_by_id_relocation(item, type_str, path, new_by_id, new_by_path,
                                       matched_state_keys, consumed_ids, diff, keep, now, config):
                continue
            _handle_unmatched_state_record(item, type_str, path, new_by_path, indexed_paths,
                                           packs_parsed_successfully, diff, keep, now, config)

        for (rec_type, rec_path), obj in new_by_path.items():
            if rec_type != type_str:
                continue
            if (type_str, rec_path) in matched_state_keys:
                continue
            _apply_incoming_record(obj, type_str, rec_path, existing_by_path, existing_by_id,
                                   id_was_generated_by_path, consumed_ids, diff, keep, now, config)

        state[collection_key] = _dedup_by_id(keep)

    return diff


def validate_references(state):
    '''Return list of broken references in state.

    Each entry is {from_id, missing_id, field_name}. Skips references FROM
    non-live records (per is_live). Live records pointing AT non-live records
    are reported.
    '''
    from cofr.domain import is_live
    known_live_ids = set()
    known_live_ids_by_type = {type_str: set() for type_str in TYPE_TO_COLLECTION}
    for type_str, collection_key in TYPE_TO_COLLECTION.items():
        for item in state.get(collection_key, []):
            if item.get('id') and is_live(item, type_str=type_str):
                known_live_ids.add(item['id'])
                known_live_ids_by_type.setdefault(type_str, set()).add(item['id'])
    broken = []
    field_groups = [
        ('claim_links', 'claim_id', 'claim'),
        ('affected_claim_ids', None, 'claim'),
        ('based_on_evidence_ids', None, None),
        ('depends_on_claim_ids', None, 'claim'),
        ('related_claim_ids', None, 'claim'),
        ('related_decision_ids', None, 'decision'),
    ]
    for type_str, collection_key in TYPE_TO_COLLECTION.items():
        for item in state.get(collection_key, []):
            if not is_live(item, type_str=type_str):
                continue
            for field_name, sub_key, target_type in field_groups:
                values = item.get(field_name)
                if not values:
                    continue
                for entry in values:
                    if sub_key and isinstance(entry, dict):
                        ref_id = entry.get(sub_key)
                    elif isinstance(entry, str):
                        ref_id = entry
                    else:
                        continue
                    if not ref_id:
                        continue
                    if target_type is None:
                        if ref_id not in known_live_ids:
                            broken.append({'from_id': item.get('id', ''), 'missing_id': ref_id, 'field_name': field_name})
                    elif (
                        ref_id not in known_live_ids
                        or ref_id not in known_live_ids_by_type.get(target_type, set())
                    ):
                        broken.append({'from_id': item.get('id', ''), 'missing_id': ref_id, 'field_name': field_name})
    return broken


def _refuse_if_pending_migration(state):
    '''Return error string if state has _pending_migration set, else None.'''
    if state.get('_pending_migration'):
        return 'Project is at schema v1; run `cofr refresh` to migrate to schema v2 first.'
    return None


def _pre_load_migration_check(project_path):
    '''Inspect marker + manifest BEFORE load_state. Returns action dict.

    Returns one of:
      {'action': 'proceed'}
      {'action': 'cleanup_marker'} -- orphaned marker post-commit; remove and proceed
      {'action': 'refuse', 'message': '...', 'exit_code': int}
    '''
    project_path = Path(project_path)
    marker = project_path / '.cofr' / 'migration_in_progress'
    manifest = project_path / '.cofr' / 'migration_manifest.json'
    state_path = project_path / '.cofr' / 'state.json'
    if not marker.exists():
        return {'action': 'proceed'}
    if not state_path.is_file():
        return {
            'action': 'refuse',
            'message': 'Interrupted migration detected with missing/unreadable state.json. Run `cofr migrate --rollback --from-history <snapshot> --yes-i-know-what-im-doing` to revert.',
            'exit_code': 4,
        }
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {
            'action': 'refuse',
            'message': 'Interrupted migration detected with unreadable state.json. Run `cofr migrate --rollback --from-history <snapshot> --yes-i-know-what-im-doing` to revert.',
            'exit_code': 4,
        }
    if state.get('schema_version') == 1:
        return {
            'action': 'refuse',
            'message': "Interrupted migration detected. Run 'cofr migrate --rollback' to revert.",
            'exit_code': 4,
        }
    if state.get('schema_version') == 2:
        if not manifest.is_file():
            return {
                'action': 'refuse',
                'message': "Interrupted migration detected. Run 'cofr migrate --rollback' to revert.",
                'exit_code': 4,
            }
        try:
            mdata = json.loads(manifest.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return {
                'action': 'refuse',
                'message': "Interrupted migration detected. Run 'cofr migrate --rollback' to revert.",
                'exit_code': 4,
            }
        schema_bump_done = any(
            op.get('kind') == 'schema_bump' and op.get('status') == 'done'
            for op in mdata.get('operations', [])
            if isinstance(op, dict)
        )
        committed_pack_exists = False
        for op in mdata.get('operations', []):
            if not isinstance(op, dict) or op.get('kind') != 'commit_pack' or op.get('status') != 'done':
                continue
            dst = op.get('dst')
            if dst and (project_path / dst).exists():
                committed_pack_exists = True
                break
        any_packs = any((project_path / p).exists() for p in _EXPECTED_V3_PACK_PATHS)
        evidence_packs = (project_path / 'evidences').is_dir() and any((project_path / 'evidences').glob('*.y*ml'))
        if schema_bump_done or committed_pack_exists or any_packs or evidence_packs:
            marker.unlink(missing_ok=True)
            return {'action': 'cleanup_marker'}
        return {
            'action': 'refuse',
            'message': "Interrupted migration detected. Run 'cofr migrate --rollback' to revert.",
            'exit_code': 4,
        }
    return {
        'action': 'refuse',
        'message': "Interrupted migration: unrecognized schema_version. Run 'cofr migrate --rollback'.",
        'exit_code': 4,
    }


def _manifest_path(project_path):
    return Path(project_path) / '.cofr' / 'migration_manifest.json'


def _marker_path(project_path):
    return Path(project_path) / '.cofr' / 'migration_in_progress'


def _write_manifest(project_path, manifest):
    atomic_write_json(_manifest_path(project_path), manifest)


def _append_op(manifest, kind, src, dst, status='pending'):
    op = {'kind': kind, 'src': src, 'dst': dst, 'status': status}
    manifest['operations'].append(op)
    return op


def _flip_last_op_done(manifest):
    manifest['operations'][-1]['status'] = 'done'


def _clean_record_for_pack(rec):
    '''Strip system/transient fields before staging a record into a pack.'''
    out = {k: v for k, v in rec.items() if k not in ('parsed_from', 'first_seen', 'last_updated', 'stale', '_timeline', '_status_timeline', '_unknown_fields', '_preserved_user_fields')}
    return out


def _scan_brand_new_idless_markdown(project_path, state, seen_md_paths, type_packs, evidence_packs, legacy_paths_to_move, pre_migration_index, index_basenames):
    '''Scan project for structured markdown not yet in state and pack them.

    Plan line 982: brand-new idless structured markdown (no `id:` field and no
    matching state record) gets a UUID32 minted at migration time and is packed
    alongside known records. Files with explicit `id:` but no state record are
    also packed here.
    '''
    from cofr.ingest import walk_project, parse_structured_record
    from cofr.packs import sanitize_slug
    warnings = []
    seen_this_refresh = {}
    for rel_path, abs_path, _size in walk_project(project_path):
        if not rel_path.endswith('.md'):
            continue
        if rel_path in seen_md_paths:
            continue
        try:
            text = abs_path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if not text.startswith('---'):
            continue
        obj, parse_warnings, id_was_generated = parse_structured_record(rel_path, text, seen_this_refresh)
        for w in parse_warnings:
            warnings.append(f'migration: {w}')
        if obj is None:
            continue
        type_str = type(obj).__name__.lower()
        if type_str == 'openquestion':
            type_str = 'question'
        rec = {k: v for k, v in obj.__dict__.items() if not k.startswith('_') or k in ('_timeline', '_status_timeline', '_unknown_fields', '_preserved_user_fields')}
        rec['type'] = type_str
        seen_this_refresh[obj.id] = rel_path
        if id_was_generated:
            warnings.append(f'migration: brand-new idless markdown {rel_path} assigned UUID id {obj.id}; consider editing the pack record to a meaningful id via cofr rename.')
        if type_str == 'evidence':
            data_source = rec.get('data_source') or ''
            stem = ''
            resolved = ''
            if data_source:
                if data_source in pre_migration_index:
                    resolved = data_source
                elif Path(data_source).name in index_basenames:
                    resolved = index_basenames[Path(data_source).name]
                if resolved:
                    stem = Path(resolved).stem
                else:
                    stem = Path(data_source).stem
            slug = '__misc__' if not stem else sanitize_slug(stem)[0]
            if resolved and not rec.get('source_path'):
                rec['source_path'] = resolved
            evidence_packs.setdefault(slug, []).append(rec)
            legacy_paths_to_move.add(rel_path)
        else:
            pack_filename = {
                'claim': 'claims.yaml', 'decision': 'decisions.yaml', 'question': 'questions.yaml',
                'risk': 'risks.yaml', 'experiment': 'experiments.yaml',
            }.get(type_str)
            if not pack_filename:
                continue
            type_packs.setdefault(type_str, []).append(rec)
            legacy_paths_to_move.add(rel_path)
    return warnings


def migrate_v1_to_v2(project_path, state):
    '''Migrate a v1 project to V3 pack layout. Atomic-ish via manifest write-ahead.

    Steps: pre-flight refuse on existing packs; snapshot v1 state; init manifest;
    write marker; stage packs in .cofr/migrate_pending/; move legacy markdown to
    .cofr/legacy_markdown/; rewrite state record parsed_from; commit packs;
    schema bump; remove marker.
    '''
    from cofr.packs import pack_dump, sanitize_slug
    project_path = Path(project_path)
    cofr_dir = _cofr_dir(project_path)
    for pack_filename in _EXPECTED_V3_PACK_PATHS:
        if (project_path / pack_filename).exists():
            raise RuntimeError(f'cannot migrate: V3 pack {pack_filename!r} already exists at project root. Resolve manually before retrying migration.')
    if (project_path / 'evidences').is_dir() and any((project_path / 'evidences').glob('*.yaml')):
        raise RuntimeError('cannot migrate: evidences/*.yaml already exists. Resolve manually before retrying migration.')

    snap_name = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ') + '-pre-migration.json'
    snap_path = cofr_dir / 'history' / snap_name
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    state_disk = {k: v for k, v in state.items() if k != '_pending_migration'}
    atomic_write_text(snap_path, json.dumps(state_disk, indent=2, sort_keys=True) + '\n')

    manifest = {
        'schema_version': 1,
        'snapshot': str(snap_path.relative_to(project_path)),
        'operations': [],
    }
    _write_manifest(project_path, manifest)
    _marker_path(project_path).write_text(f'snapshot:{snap_path.relative_to(project_path)}\n')

    type_packs = {t: [] for t in ('claim', 'decision', 'question', 'risk', 'experiment')}
    evidence_packs = {}
    claim_timeline_seeds = {}
    legacy_paths_to_move = set()
    parsed_from_rewrites = {}

    pre_migration_index = {}
    idx_path = cofr_dir / 'index.json'
    if idx_path.is_file():
        try:
            pre_migration_index = json.loads(idx_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pre_migration_index = {}
    index_basenames = {Path(k).name: k for k in pre_migration_index}

    seen_md_paths = set()
    for type_str, collection_key in TYPE_TO_COLLECTION.items():
        for item in state.get(collection_key, []):
            parsed = item.get('parsed_from', '')
            if not parsed or parsed.startswith('.cofr/'):
                continue
            if parsed.endswith('.md'):
                seen_md_paths.add(parsed)
            if type_str == 'evidence':
                data_source = item.get('data_source') or ''
                stem = ''
                resolved_source_path = ''
                if data_source:
                    if data_source in pre_migration_index:
                        resolved_source_path = data_source
                    elif Path(data_source).name in index_basenames:
                        resolved_source_path = index_basenames[Path(data_source).name]
                    if resolved_source_path:
                        stem = Path(resolved_source_path).stem
                    else:
                        stem = Path(data_source).stem
                if not stem:
                    slug = '__misc__'
                else:
                    slug, _ = sanitize_slug(stem)
                evidence_rec = dict(item)
                if resolved_source_path and not evidence_rec.get('source_path'):
                    evidence_rec['source_path'] = resolved_source_path
                evidence_packs.setdefault(slug, []).append(evidence_rec)
                if parsed.endswith('.md'):
                    legacy_paths_to_move.add(parsed)
                    parsed_from_rewrites[parsed] = f'evidences/{slug}.yaml#{item["id"]}'
            else:
                if type_str == 'claim':
                    t_value = item.get('last_updated') or item.get('first_seen') or ''
                    claim_timeline_seeds[item.get('id')] = {
                        't': t_value,
                        'confidence': item.get('confidence') or 'medium',
                        'status': item.get('status') or 'provisionally_supported',
                    }
                type_packs.setdefault(type_str, []).append(dict(item))
                if parsed.endswith('.md'):
                    legacy_paths_to_move.add(parsed)
                    pack_filename = {
                        'claim': 'claims.yaml',
                        'decision': 'decisions.yaml',
                        'question': 'questions.yaml',
                        'risk': 'risks.yaml',
                        'experiment': 'experiments.yaml',
                    }[type_str]
                    parsed_from_rewrites[parsed] = f'{pack_filename}#{item["id"]}'

    brand_new_warnings = _scan_brand_new_idless_markdown(
        project_path, state, seen_md_paths, type_packs, evidence_packs,
        legacy_paths_to_move, pre_migration_index, index_basenames,
    )

    pending_dir = cofr_dir / 'migrate_pending'
    pending_dir.mkdir(exist_ok=True)
    pack_filename_for_type = {
        'claim': 'claims.yaml',
        'decision': 'decisions.yaml',
        'question': 'questions.yaml',
        'risk': 'risks.yaml',
        'experiment': 'experiments.yaml',
    }
    for type_str, recs in type_packs.items():
        if not recs:
            continue
        pack_filename = pack_filename_for_type[type_str]
        staged = pending_dir / pack_filename
        _append_op(manifest, 'stage_pack', None, str(staged.relative_to(project_path)))
        _write_manifest(project_path, manifest)
        cleaned = [_clean_record_for_pack(r) for r in recs]
        pack_dump(staged, cleaned, type_str)
        _flip_last_op_done(manifest)
        _write_manifest(project_path, manifest)
    for slug, recs in evidence_packs.items():
        staged = pending_dir / 'evidences' / f'{slug}.yaml'
        _append_op(manifest, 'stage_pack', None, str(staged.relative_to(project_path)))
        _write_manifest(project_path, manifest)
        cleaned = [_clean_record_for_pack(r) for r in recs]
        for r in cleaned:
            r['source_slug'] = slug
        pack_dump(staged, cleaned, 'evidence')
        _flip_last_op_done(manifest)
        _write_manifest(project_path, manifest)

    legacy_root = cofr_dir / 'legacy_markdown'
    for rel in sorted(legacy_paths_to_move):
        src = project_path / rel
        if not src.exists():
            continue
        dst = legacy_root / rel
        _append_op(manifest, 'move_legacy_md', rel, str(dst.relative_to(project_path)))
        _write_manifest(project_path, manifest)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _flip_last_op_done(manifest)
        _write_manifest(project_path, manifest)

    for old, new in parsed_from_rewrites.items():
        _append_op(manifest, 'rewrite_parsed_from', old, new)
        _write_manifest(project_path, manifest)
        for type_str, collection_key in TYPE_TO_COLLECTION.items():
            for item in state.get(collection_key, []):
                if item.get('parsed_from') == old:
                    item['parsed_from'] = new
        _flip_last_op_done(manifest)
        _write_manifest(project_path, manifest)

    for claim in state.get('claims', []):
        seed = claim_timeline_seeds.get(claim.get('id'))
        if not seed:
            continue
        t_value = seed.get('t') or ''
        if not t_value:
            continue
        claim['_timeline'] = [{'t': t_value, 'c': seed.get('confidence') or 'medium'}]
        claim['_status_timeline'] = [{'t': t_value, 's': seed.get('status') or 'provisionally_supported'}]

    for type_str, recs in type_packs.items():
        if not recs:
            continue
        pack_filename = pack_filename_for_type[type_str]
        staged = pending_dir / pack_filename
        target = project_path / pack_filename
        if staged.is_file():
            _append_op(manifest, 'commit_pack', str(staged.relative_to(project_path)), pack_filename)
            _write_manifest(project_path, manifest)
            shutil.move(str(staged), str(target))
            _flip_last_op_done(manifest)
            _write_manifest(project_path, manifest)
    for slug, recs in evidence_packs.items():
        staged = pending_dir / 'evidences' / f'{slug}.yaml'
        target = project_path / 'evidences' / f'{slug}.yaml'
        if staged.is_file():
            _append_op(manifest, 'commit_pack', str(staged.relative_to(project_path)), str(target.relative_to(project_path)))
            _write_manifest(project_path, manifest)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(target))
            _flip_last_op_done(manifest)
            _write_manifest(project_path, manifest)
    try:
        if pending_dir.exists():
            shutil.rmtree(pending_dir)
    except OSError:
        pass

    _append_op(manifest, 'schema_bump', '1', '2')
    _write_manifest(project_path, manifest)
    state.pop('_pending_migration', None)
    state['schema_version'] = 2
    save_state(project_path, state)
    _flip_last_op_done(manifest)
    _write_manifest(project_path, manifest)

    marker = _marker_path(project_path)
    _append_op(manifest, 'marker_remove', str(marker.relative_to(project_path)), None)
    _write_manifest(project_path, manifest)
    marker.unlink(missing_ok=True)
    _flip_last_op_done(manifest)
    _write_manifest(project_path, manifest)
    return brand_new_warnings


def migrate_rollback(project_path, from_history=None, confirm=False):
    '''Rollback a migration. Three operating modes (per Core invariants):
    A) marker + state present -> plain rollback OK.
    B) marker + state missing -> requires --from-history + --confirm.
    C) no marker (post-commit regret) -> requires --from-history + --confirm.
    '''
    project_path = Path(project_path)
    cofr_dir = project_path / '.cofr'
    marker = _marker_path(project_path)
    manifest_path = _manifest_path(project_path)
    state_path = cofr_dir / 'state.json'

    def _validated_manifest(manifest):
        if not isinstance(manifest, dict) or not isinstance(manifest.get('operations', []), list):
            raise RuntimeError('migration manifest has invalid structure')
        for idx, op in enumerate(manifest.get('operations', [])):
            if not isinstance(op, dict):
                raise RuntimeError(f'migration manifest operation {idx} must be an object')
            for key in ('src', 'dst'):
                value = op.get(key)
                if value and op.get('kind') not in ('schema_bump',):
                    project_local_path(project_path, value, f'migration manifest operation {idx} {key}')
        snapshot_value = manifest.get('snapshot')
        if snapshot_value:
            project_local_path(project_path, snapshot_value, 'migration manifest snapshot')
        return manifest

    def _reverse_manifest_files(manifest):
        for op in reversed(manifest.get('operations', [])):
            kind = op.get('kind')
            src = op.get('src')
            dst = op.get('dst')
            try:
                if kind == 'commit_pack' and dst:
                    target = project_local_path(project_path, dst, 'migration manifest dst')
                    if target.exists() and src:
                        staged = project_local_path(project_path, src, 'migration manifest src')
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(target), str(staged))
                elif kind == 'stage_pack' and dst:
                    target = project_local_path(project_path, dst, 'migration manifest dst')
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                elif kind == 'move_legacy_md' and src and dst:
                    dst_path = project_local_path(project_path, dst, 'migration manifest dst')
                    src_path = project_local_path(project_path, src, 'migration manifest src')
                    if dst_path.exists() and not src_path.exists():
                        src_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(dst_path), str(src_path))
            except OSError:
                pass

    def _load_manifest():
        if not manifest_path.is_file():
            return {'operations': []}
        try:
            return _validated_manifest(json.loads(manifest_path.read_text(encoding='utf-8')))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return {'operations': []}

    if not marker.exists():
        if not from_history or not confirm:
            raise RuntimeError('No in-progress migration marker. Use --from-history <snapshot> --yes-i-know-what-im-doing to undo a completed migration.')
        snap = project_local_path(project_path, from_history, 'rollback snapshot')
        if not snap.is_file():
            raise RuntimeError(f'snapshot {from_history!r} not found')
        manifest = _load_manifest()
        _reverse_manifest_files(manifest)
        atomic_write_text(state_path, snap.read_text(encoding='utf-8'))
        manifest_path.unlink(missing_ok=True)
        return

    if not state_path.is_file():
        if not from_history or not confirm:
            raise RuntimeError('Marker present but state.json missing. Use --from-history <snapshot> --yes-i-know-what-im-doing.')
        snap = project_local_path(project_path, from_history, 'rollback snapshot')
        if not snap.is_file():
            raise RuntimeError(f'snapshot {from_history!r} not found; rollback aborted to avoid leaving state.json missing.')
        manifest = _load_manifest() if manifest_path.is_file() else {'operations': []}
        _reverse_manifest_files(manifest)
        atomic_write_text(state_path, snap.read_text(encoding='utf-8'))
        manifest_path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        return

    if manifest_path.is_file():
        manifest = _load_manifest()
        _reverse_manifest_files(manifest)
        snapshot_rel = manifest.get('snapshot')
        if snapshot_rel:
            snap = project_local_path(project_path, snapshot_rel, 'migration manifest snapshot')
            if snap.is_file():
                atomic_write_text(state_path, snap.read_text(encoding='utf-8'))

    elif from_history and confirm:
        snap = project_local_path(project_path, from_history, 'rollback snapshot')
        if snap.is_file():
            atomic_write_text(state_path, snap.read_text(encoding='utf-8'))

    manifest_path.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
