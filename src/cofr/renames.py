'''Renames seam: fingerprint primitive, pending-file I/O and recovery,
detection, cascade, and append/rebuild of .cofr/renames.json.'''
import hashlib
import json
import re
from pathlib import Path

import yaml

from cofr.domain import CLASS_TO_TYPE_STR, TYPE_TO_COLLECTION, is_live


_WS_RE = re.compile(r'\s+')


def _normalize(s):
    if s is None:
        return ''
    return _WS_RE.sub(' ', str(s).strip())


def _basename_stem(s):
    if not s:
        return ''
    return Path(str(s)).stem


def _claim_fingerprint_tuple(rec):
    extra = rec.get('extra_sections') or {}
    extra_keys = tuple(sorted(extra.keys()))
    extra_values = tuple(_normalize(str(extra[k])[:120]) for k in sorted(extra))
    return (
        'claim',
        _normalize(rec.get('title')),
        _normalize(rec.get('statement')),
        _normalize(rec.get('what_would_change_my_mind')),
        _normalize((rec.get('main_support') or '')[:300]),
        _normalize((rec.get('main_weakness') or '')[:300]),
        extra_keys,
        extra_values,
    )


def _decision_fingerprint_tuple(rec):
    return (
        'decision',
        _normalize(rec.get('title')),
        _normalize(rec.get('decision_statement')),
        _normalize((rec.get('rationale') or '')[:300]),
    )


def _evidence_fingerprint_tuple(rec):
    return (
        'evidence',
        rec.get('evidence_type', ''),
        _normalize((rec.get('summary') or '')[:400]),
        _normalize(_basename_stem(rec.get('data_source'))),
    )


def _question_fingerprint_tuple(rec):
    return (
        'question',
        _normalize(rec.get('question')),
        _normalize((rec.get('blocking_impact') or '')[:300]),
    )


def _risk_fingerprint_tuple(rec):
    return (
        'risk',
        _normalize(rec.get('statement')),
        _normalize((rec.get('recommended_resolution') or '')[:300]),
    )


def _experiment_fingerprint_tuple(rec):
    return (
        'experiment',
        _normalize(rec.get('name')),
        _normalize(rec.get('intent')),
        _normalize((rec.get('result_summary') or '')[:300]),
    )


_FINGERPRINT_BUILDERS = {
    'claim': _claim_fingerprint_tuple,
    'decision': _decision_fingerprint_tuple,
    'evidence': _evidence_fingerprint_tuple,
    'question': _question_fingerprint_tuple,
    'risk': _risk_fingerprint_tuple,
    'experiment': _experiment_fingerprint_tuple,
}


def compute_fingerprint(record, type_str):
    '''SHA-256 hex of the per-type canonical content tuple.'''
    builder = _FINGERPRINT_BUILDERS.get(type_str)
    if builder is None:
        return ''
    if hasattr(record, '__dict__'):
        rec = {k: v for k, v in record.__dict__.items()}
    else:
        rec = dict(record)
    tuple_data = builder(rec)
    serialized = json.dumps(tuple_data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def markdown_has_id(text, obj_id):
    pattern = re.compile(r'^[ \t]*id[ \t]*:[ \t]*([\'"]?)' + re.escape(obj_id) + r'\1[ \t]*(?:#.*)?$', re.MULTILINE)
    return bool(pattern.search(text))


def replace_markdown_id(text, old_id, new_id):
    pattern = re.compile(r'^([ \t]*id[ \t]*:[ \t]*)([\'"]?)' + re.escape(old_id) + r'(\2)([ \t]*(?:#.*)?)$', re.MULTILINE)

    def repl(match):
        return f'{match.group(1)}{match.group(2)}{new_id}{match.group(3)}{match.group(4)}'

    new_text, count = pattern.subn(repl, text, count=1)
    return new_text, count == 1


def load_pending_renames(project_path):
    path = Path(project_path) / '.cofr' / 'pending_renames.json'
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get('schema_version') not in (None, 1):
        return None
    entries = data.get('entries')
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        return None
    entry = entries[0]
    if entry.get('type') not in TYPE_TO_COLLECTION:
        return None
    if entry.get('mode', 'standard') not in ('standard', 'fingerprint_confirm'):
        return None
    if not all(isinstance(entry.get(key), str) and entry.get(key) for key in ('old_id', 'new_id', 'pack_path')):
        return None
    try:
        from cofr.state import project_local_path
        project_local_path(project_path, entry['pack_path'], 'pending rename pack_path')
    except RuntimeError:
        return None
    return data


def write_pending_renames(project_path, entry):
    '''Atomically write a single-entry pending_renames.json.'''
    from cofr.state import atomic_write_json
    path = Path(project_path) / '.cofr' / 'pending_renames.json'
    payload = {'schema_version': 1, 'entries': [entry]}
    atomic_write_json(path, payload)


def clear_pending_renames(project_path):
    path = Path(project_path) / '.cofr' / 'pending_renames.json'
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _result(action, applied, reason):
    return {'action': action, 'pre_fixup_applied': applied, 'reason': reason}


def _inspect_pack_or_md(full_path, pack_path_rel, type_str, old_id, new_id):
    '''Return (has_old, has_new, payload, error_result_or_None).

    payload is `text` for markdown, `records` for packs. Used by both standard
    and fingerprint-confirm fixup modes.
    '''
    from cofr.packs import pack_load
    if pack_path_rel.endswith('.md'):
        try:
            text = full_path.read_text(encoding='utf-8')
        except OSError:
            return None, None, None, _result('refuse', False, f'cannot read {pack_path_rel}')
        return markdown_has_id(text, old_id), markdown_has_id(text, new_id), text, None
    try:
        records = pack_load(full_path, expected_type=type_str)
    except Exception as exc:
        return None, None, None, _result('refuse', False, f'cannot parse {pack_path_rel}: {exc}')
    ids = [r.get('id') for r in records]
    return old_id in ids, new_id in ids, records, None


def _redo_pack_edit(full_path, pack_path_rel, type_str, old_id, new_id, payload):
    '''Re-apply the old_id -> new_id edit on disk. Returns result dict.'''
    from cofr.packs import pack_dump
    from cofr.state import atomic_write_text
    if pack_path_rel.endswith('.md'):
        new_text, replaced = replace_markdown_id(payload, old_id, new_id)
        if not replaced:
            return _result('refuse', False, f'could not rewrite id in {pack_path_rel}')
        atomic_write_text(full_path, new_text)
    else:
        for r in payload:
            if r.get('id') == old_id:
                r['id'] = new_id
        pack_dump(full_path, payload, type_str)
    return _result('apply', True, 'pack edit redone')


def _load_state_ids(project_path):
    state_path = Path(project_path) / '.cofr' / 'state.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, FileNotFoundError):
        state = {}
    state_ids = set()
    for ck in ('claims', 'evidence', 'experiments', 'decisions', 'open_questions', 'risks'):
        for it in state.get(ck, []):
            state_ids.add(it.get('id'))
    return state, state_ids


def _fingerprint_confirm_preconditions_hold(state, type_str, old_id, new_id):
    from cofr.domain import has_live_status
    collection_key = {
        'claim': 'claims', 'evidence': 'evidence', 'experiment': 'experiments',
        'decision': 'decisions', 'question': 'open_questions', 'risk': 'risks',
    }.get(type_str)
    coll = state.get(collection_key, []) if collection_key else []
    state_old = next((it for it in coll if it.get('id') == old_id), None)
    state_new = next((it for it in coll if it.get('id') == new_id), None)
    old_is_recoverable = state_old is not None and (
        state_old.get('source_missing') is True
        if type_str == 'claim'
        else state_old.get('stale') is True and has_live_status(state_old, type_str=type_str)
    )
    return (
        old_is_recoverable
        and state_new is not None
        and is_live(state_new, type_str=type_str)
        and compute_fingerprint(state_old, type_str) == compute_fingerprint(state_new, type_str)
    )


def _fixup_standard(has_old, has_new, payload, full_path, pack_path_rel, type_str, old_id, new_id, state_ids):
    if has_new and not has_old:
        if new_id in state_ids and old_id not in state_ids:
            return _result('cleanup_only', False, 'state already committed the rename')
        return _result('apply', False, 'pack edit already done; refresh did not run')
    if has_old and not has_new:
        return _redo_pack_edit(full_path, pack_path_rel, type_str, old_id, new_id, payload)
    return _result('refuse', False, 'unknown shape')


def _fixup_fingerprint_confirm(has_old, has_new, payload, full_path, pack_path_rel, type_str, old_id, new_id, state):
    preconditions_hold = _fingerprint_confirm_preconditions_hold(state, type_str, old_id, new_id)
    if has_new and not has_old:
        if preconditions_hold:
            return _result('apply', False, 'fingerprint-confirm pack edit pending')
        return _result('cleanup_only', False, 'fingerprint-confirm preconditions no longer hold; dropping pending entry')
    if has_old and not has_new:
        if not preconditions_hold:
            return _result('cleanup_only', False, 'fingerprint-confirm preconditions no longer hold; dropping pending entry')
        return _redo_pack_edit(full_path, pack_path_rel, type_str, old_id, new_id, payload)
    return _result('refuse', False, 'unknown shape')


def _pre_load_pending_pack_fixup(project_path, pre_load_pending):
    '''Single-entry recovery routine. Returns {action, pre_fixup_applied, reason}.

    action: 'apply' | 'cleanup_only' | 'refuse'
    '''
    if not pre_load_pending or not isinstance(pre_load_pending.get('entries'), list) or len(pre_load_pending['entries']) != 1 or not isinstance(pre_load_pending['entries'][0], dict):
        return _result('refuse', False, 'pending rename file has invalid structure')
    entry = pre_load_pending['entries'][0]
    if entry.get('type') not in TYPE_TO_COLLECTION or not all(isinstance(entry.get(key), str) and entry.get(key) for key in ('old_id', 'new_id', 'pack_path')):
        return _result('refuse', False, 'pending rename entry is missing required fields')
    old_id, new_id, type_str = entry['old_id'], entry['new_id'], entry['type']
    pack_path_rel = entry.get('pack_path', '')
    mode = entry.get('mode', 'standard')

    try:
        from cofr.state import project_local_path
        full_path = project_local_path(project_path, pack_path_rel, 'pending rename pack_path')
    except RuntimeError as exc:
        return _result('refuse', False, str(exc))
    if not full_path.is_file():
        return _result('refuse', False, f'pending pack {pack_path_rel} not found on disk')

    has_old, has_new, payload, err = _inspect_pack_or_md(full_path, pack_path_rel, type_str, old_id, new_id)
    if err:
        return err

    state, state_ids = _load_state_ids(project_path)

    if has_old and has_new:
        return _result('refuse', False, 'pending rename pack has both old_id and new_id; duplicate id in pack')
    if not has_old and not has_new:
        return _result('refuse', False, 'pending rename pack has neither old_id nor new_id; user manually intervened')

    if mode == 'standard':
        return _fixup_standard(has_old, has_new, payload, full_path, pack_path_rel, type_str, old_id, new_id, state_ids)
    if mode == 'fingerprint_confirm':
        return _fixup_fingerprint_confirm(has_old, has_new, payload, full_path, pack_path_rel, type_str, old_id, new_id, state)
    return _result('refuse', False, f'unknown mode {mode!r}')


def detect_renames(prior_state, current_records, pending=None, pre_fixup_action='none'):
    '''Detect renames. Returns (explicit_renames, warnings).

    Round 8 simplification: fingerprint matches produce WARNINGS only -- never
    auto-cascade. explicit_renames is at-most-one entry from pending.entries[0]
    when pre_fixup_action == 'apply'.
    '''
    explicit = []
    if pending and pre_fixup_action == 'apply':
        entry = pending.get('entries', [None])[0] if pending.get('entries') else None
        if entry:
            explicit.append({'old_id': entry['old_id'], 'new_id': entry['new_id'], 'type': entry['type']})

    warnings = []
    prior_by_type_id = {}
    for type_str, collection_key in TYPE_TO_COLLECTION.items():
        for item in prior_state.get(collection_key, []):
            rid = item.get('id')
            if rid:
                prior_by_type_id[(type_str, rid)] = item

    current_by_type_id = {}
    for obj in current_records:
        type_str = CLASS_TO_TYPE_STR.get(type(obj))
        if not type_str:
            continue
        current_by_type_id[(type_str, obj.id)] = obj

    skip_pairs = set()
    for e in explicit:
        skip_pairs.add((e['type'], e['old_id'], e['new_id']))

    for type_str in ('claim', 'decision', 'evidence', 'question', 'risk', 'experiment'):
        disappeared = []
        appeared = []
        for (t, rid), item in prior_by_type_id.items():
            if t != type_str:
                continue
            if (t, rid) not in current_by_type_id:
                disappeared.append((rid, item))
        for (t, rid), obj in current_by_type_id.items():
            if t != type_str:
                continue
            if (t, rid) not in prior_by_type_id:
                appeared.append((rid, obj))
        if not disappeared or not appeared:
            continue
        for old_id, old_rec in disappeared:
            old_fp = compute_fingerprint(old_rec, type_str)
            if type_str == 'evidence':
                old_slug = old_rec.get('source_slug') or ''
                same_source = [(nid, no) for nid, no in appeared if (getattr(no, 'source_slug', '') or '') == old_slug and old_slug]
                cross_source = [(nid, no) for nid, no in appeared if (getattr(no, 'source_slug', '') or '') != old_slug or not old_slug]
                candidates = same_source if same_source else cross_source
            else:
                candidates = appeared
            for new_id, new_obj in candidates:
                new_fp = compute_fingerprint(new_obj, type_str)
                if new_fp != old_fp:
                    continue
                if (type_str, old_id, new_id) in skip_pairs:
                    continue
                warnings.append(f"possible rename detected: {old_id} → {new_id} (same content fingerprint). Run 'cofr rename {old_id} {new_id}' to confirm.")
    return explicit, warnings


def apply_rename_cascade(state, parsed_records, project_path, explicit_renames, new_index, mode='standard'):
    '''Apply rename cascade for confirmed renames.

    Standard mode: optional pre-delete of non-live new_id state record, then
    rewrite typed pointers + state record id/parsed_from.
    Fingerprint-confirm mode: defense-in-depth re-verify preconditions; if
    preconditions hold, delete live new_id record and revive non-live old by
    clearing stale ONLY (do NOT modify status); then perform cascade.

    Returns packs_to_rewrite set. Raises RuntimeError on fingerprint-confirm
    precondition drift.
    '''
    from cofr.domain import has_live_status
    packs_to_rewrite = set()
    for entry in explicit_renames:
        old_id, new_id, type_str = entry['old_id'], entry['new_id'], entry['type']
        collection_key = TYPE_TO_COLLECTION.get(type_str)
        if not collection_key:
            continue
        coll = state.get(collection_key, [])

        if mode == 'fingerprint_confirm':
            new_rec = None
            old_rec = None
            for item in coll:
                if item.get('id') == new_id:
                    new_rec = item
                if item.get('id') == old_id:
                    old_rec = item
            old_is_recoverable = old_rec is not None and (
                old_rec.get('source_missing') is True
                if type_str == 'claim'
                else old_rec.get('stale') is True and has_live_status(old_rec, type_str=type_str)
            )
            precondition_ok = (
                old_is_recoverable
                and new_rec is not None
                and is_live(new_rec, type_str=type_str)
                and compute_fingerprint(old_rec, type_str) == compute_fingerprint(new_rec, type_str)
            )
            if not precondition_ok:
                raise RuntimeError('fingerprint-confirm mode: preconditions no longer hold; aborting rename')
            new_idx = None
            old_idx = None
            for i, item in enumerate(coll):
                if item.get('id') == new_id:
                    new_idx = i
                if item.get('id') == old_id:
                    old_idx = i
            if new_idx is not None:
                coll.pop(new_idx)
                if old_idx is not None and old_idx > new_idx:
                    old_idx -= 1
            if old_idx is not None:
                coll[old_idx]['stale'] = False
                if type_str == 'claim':
                    coll[old_idx]['source_missing'] = False
        elif mode == 'standard':
            new_idx = None
            for i, item in enumerate(coll):
                if item.get('id') == new_id:
                    new_idx = i
                    break
            if new_idx is not None and not is_live(coll[new_idx], type_str=type_str):
                coll.pop(new_idx)

        for item in coll:
            if item.get('id') == old_id:
                old_pf = item.get('parsed_from', '')
                if '#' in old_pf:
                    pack_path, _ = old_pf.split('#', 1)
                    item['parsed_from'] = f'{pack_path}#{new_id}'
                    packs_to_rewrite.add(pack_path)
                elif old_pf.endswith('.md'):
                    item['parsed_from'] = old_pf
                item['id'] = new_id
                break

        for collection_key2 in ('claims', 'evidence', 'experiments', 'decisions', 'open_questions', 'risks'):
            for item in state.get(collection_key2, []):
                touched = False
                for field_name in ('affected_claim_ids', 'based_on_evidence_ids', 'depends_on_claim_ids', 'related_claim_ids', 'related_decision_ids'):
                    values = item.get(field_name)
                    if values and isinstance(values, list):
                        rewritten = [new_id if v == old_id else v for v in values]
                        if rewritten != values:
                            touched = True
                        item[field_name] = rewritten
                links = item.get('claim_links')
                if links and isinstance(links, list):
                    for link in links:
                        if isinstance(link, dict) and link.get('claim_id') == old_id:
                            link['claim_id'] = new_id
                            touched = True
                if touched:
                    pf = item.get('parsed_from', '')
                    if '#' in pf:
                        packs_to_rewrite.add(pf.split('#', 1)[0])

        for obj in parsed_records:
            if obj.id == old_id:
                obj.id = new_id
            if hasattr(obj, 'claim_links'):
                for link in obj.claim_links:
                    if isinstance(link, dict) and link.get('claim_id') == old_id:
                        link['claim_id'] = new_id
            for attr in ('affected_claim_ids', 'based_on_evidence_ids', 'depends_on_claim_ids', 'related_claim_ids', 'related_decision_ids'):
                values = getattr(obj, attr, None)
                if values:
                    setattr(obj, attr, [new_id if v == old_id else v for v in values])

    return packs_to_rewrite


def append_renames_log(project_path, entries_with_signatures):
    '''Append rename entries to .cofr/renames.json, deduped by signature.'''
    from cofr.state import atomic_write_json
    path = Path(project_path) / '.cofr' / 'renames.json'
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            data = {'schema_version': 1, 'renames': []}
        if isinstance(data, dict) and data.get('schema_version') not in (None, 1):
            raise RuntimeError(f'.cofr/renames.json: unknown schema_version {data.get("schema_version")!r}; expected 1')
    else:
        data = {'schema_version': 1, 'renames': []}
    if not isinstance(data, dict) or 'renames' not in data:
        data = {'schema_version': 1, 'renames': []}
    existing_sigs = {r.get('signature') for r in data.get('renames', [])}
    for entry in entries_with_signatures:
        if entry.get('signature') and entry['signature'] not in existing_sigs:
            data['renames'].append(entry)
            existing_sigs.add(entry['signature'])
    atomic_write_json(path, data)


def rebuild_renames_log(project_path, state):
    '''Walk history snapshots and re-derive missing rename entries.

    Returns (appended_entries, warnings). For each consecutive snapshot pair,
    enumerates disappeared/appeared/identity_replaced buckets per type; matches
    require same first_seen AND fingerprint. Ambiguous matches skip with a
    warning. Idempotent -- entries already in .cofr/renames.json are skipped
    via signature dedup. Per plan: round 8 simplification replaces the
    pending_log_appends crash anchor with user-invoked repair.
    '''
    project_path = Path(project_path)
    history_dir = project_path / '.cofr' / 'history'
    snapshots = sorted(history_dir.glob('*.json')) if history_dir.is_dir() else []
    appended = []
    warnings = []
    log_path = project_path / '.cofr' / 'renames.json'
    if log_path.is_file():
        try:
            log = json.loads(log_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            log = {'schema_version': 1, 'renames': []}
        if isinstance(log, dict) and log.get('schema_version') not in (None, 1):
            raise RuntimeError(f'.cofr/renames.json: unknown schema_version {log.get("schema_version")!r}; expected 1')
        if not isinstance(log, dict) or 'renames' not in log:
            log = {'schema_version': 1, 'renames': []}
    else:
        log = {'schema_version': 1, 'renames': []}
    existing_sigs = {r.get('signature') for r in log.get('renames', [])}

    def _bucket(prev, curr, type_str, ck):
        prev_by_id = {r.get('id'): r for r in prev.get(ck, []) if r.get('id')}
        curr_by_id = {r.get('id'): r for r in curr.get(ck, []) if r.get('id')}
        disappeared = {rid: rec for rid, rec in prev_by_id.items() if rid not in curr_by_id}
        appeared = {rid: rec for rid, rec in curr_by_id.items() if rid not in prev_by_id}
        identity_replaced = {
            rid: (prev_by_id[rid], curr_by_id[rid])
            for rid in (set(prev_by_id) & set(curr_by_id))
            if prev_by_id[rid].get('first_seen') != curr_by_id[rid].get('first_seen')
        }
        return disappeared, appeared, identity_replaced

    snap_data_pairs = []
    for snap in snapshots:
        try:
            snap_data_pairs.append((snap.name, json.loads(snap.read_text(encoding='utf-8'))))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
    snap_data_pairs.append(('current', state))

    for i in range(1, len(snap_data_pairs)):
        prev_name, prev = snap_data_pairs[i - 1]
        curr_name, curr = snap_data_pairs[i]
        for type_str, ck in TYPE_TO_COLLECTION.items():
            disappeared, appeared, identity_replaced = _bucket(prev, curr, type_str, ck)
            for old_id, old_rec in disappeared.items():
                old_fs = old_rec.get('first_seen')
                old_fp = compute_fingerprint(old_rec, type_str)
                matches = []
                for new_id, new_rec in appeared.items():
                    if new_rec.get('first_seen') == old_fs and compute_fingerprint(new_rec, type_str) == old_fp:
                        matches.append(new_id)
                for rid, (prev_rec, curr_rec) in identity_replaced.items():
                    if curr_rec.get('first_seen') == old_fs and compute_fingerprint(curr_rec, type_str) == old_fp:
                        matches.append(rid)
                if len(matches) == 0:
                    continue
                if len(matches) > 1:
                    warnings.append(f"rebuild-renames-log: ambiguous match for {type_str}/{old_id} at snapshot pair ({prev_name}, {curr_name}); candidates: {matches}. Skipping. Resolve manually by editing .cofr/renames.json.")
                    continue
                new_id = matches[0]
                sig = hashlib.sha256(json.dumps({'type': type_str, 'old_id': old_id, 'new_id': new_id, 'first_seen': old_fs}, sort_keys=True).encode()).hexdigest()
                if sig in existing_sigs:
                    continue
                entry = {
                    'type': type_str, 'old_id': old_id, 'new_id': new_id,
                    'detected_at': curr.get('last_refresh', '') or '',
                    'refresh_snapshot': curr_name,
                    'mode': 'explicit', 'signature': sig,
                }
                log['renames'].append(entry)
                appended.append(entry)
                existing_sigs.add(sig)

    from cofr.state import atomic_write_json
    atomic_write_json(log_path, log)
    return appended, warnings
