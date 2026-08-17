import json
import re
from pathlib import Path

import yaml

from cofr.state import atomic_write_text


_PACK_EMIT_EXCLUDED = frozenset({
    'parsed_from', 'first_seen', 'last_updated', 'stale', 'source_missing',
    '_timeline', '_status_timeline',
    '_unknown_fields', '_preserved_user_fields',
})

_VALID_SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_VALID_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_INVALID_SLUG_CHAR_RE = re.compile(r'[^A-Za-z0-9_-]')


CANONICAL_KEY_ORDER = {
    'claim': ['id', 'type', 'title', 'status', 'confidence', 'owner', 'statement',
              'what_would_change_my_mind', 'main_support', 'main_weakness', 'extra_sections'],
    'evidence': ['id', 'type', 'evidence_type', 'status', 'strength', 'summary', 'data_source',
                 'source_path', 'source_slug', 'source_title', 'source_anchors',
                 'claim_links', 'created_at', 'extra_sections'],
    'decision': ['id', 'type', 'title', 'status', 'decision_statement', 'rationale',
                 'based_on_evidence_ids', 'depends_on_claim_ids', 'reopen_conditions',
                 'timestamp', 'extra_sections'],
    'question': ['id', 'type', 'question', 'priority', 'blocking_severity', 'status',
                 'blocking_impact', 'proposed_resolution', 'minimum_test',
                 'related_claim_ids', 'extra_sections'],
    'risk': ['id', 'type', 'statement', 'severity', 'status', 'source',
             'recommended_resolution', 'related_claim_ids', 'related_decision_ids',
             'extra_sections'],
    'experiment': ['id', 'type', 'name', 'status', 'intent', 'config_reference', 'result_summary',
                   'key_metrics', 'implications', 'affected_claim_ids',
                   'follow_on_questions', 'timestamp', 'extra_sections'],
}


EXPECTED_PACK_PATHS = {
    'claim': 'claims.yaml',
    'decision': 'decisions.yaml',
    'question': 'questions.yaml',
    'risk': 'risks.yaml',
    'experiment': 'experiments.yaml',
}


_LITERAL_TAG = 'tag:yaml.org,2002:str'


def _str_representer(dumper, data):
    if isinstance(data, str) and '\n' in data:
        return dumper.represent_scalar(_LITERAL_TAG, data, style='|')
    return dumper.represent_scalar(_LITERAL_TAG, data)


class _PackDumper(yaml.SafeDumper):
    pass


_PackDumper.add_representer(str, _str_representer)


def sanitize_slug(raw):
    '''Sanitize a slug to match [A-Za-z0-9_-]+. Returns (clean_slug, was_modified).

    Always collapses runs of `_` and strips leading/trailing `_` (so `foo___bar`
    sanitizes to `foo_bar`). The `was_modified` flag is True iff the output
    differs from the input. The literal `__misc__` is the canonical fallback
    name and round-trips unchanged.
    '''
    if raw is None:
        return '__misc__', True
    s = str(raw)
    if not s:
        return '__misc__', True
    if s == '__misc__':
        return '__misc__', False
    cleaned = _INVALID_SLUG_CHAR_RE.sub('_', s)
    while '__' in cleaned:
        cleaned = cleaned.replace('__', '_')
    cleaned = cleaned.strip('_')
    if not cleaned:
        return '__misc__', True
    return cleaned, cleaned != s


def sort_records(records):
    return sorted(records, key=lambda r: r.get('id', ''))


def _filter_for_emit(record, type_str):
    '''Drop system keys; fold in unknown user keys; re-inject preserved user fields.

    `_preserved_user_fields` holds user-authored values that validation cleared
    in-memory (e.g. an invalid `source_path` that escapes the project). On pack
    rewrite we put those values back on disk so an incidental rewrite (rename
    cascade, source_slug auto-correction) never silently loses user-authored data.
    '''
    unknown = record.get('_unknown_fields') or {}
    preserved = record.get('_preserved_user_fields') or {}
    out = {}
    for k, v in record.items():
        if k in _PACK_EMIT_EXCLUDED:
            continue
        out[k] = v
    for k, v in preserved.items():
        out[k] = v
    return out, unknown


def _is_default_value(value):
    if value == '' or value == [] or value == {} or value is False or value is None:
        return True
    return False


def _ordered_emit_dict(record, type_str):
    filtered, unknown = _filter_for_emit(record, type_str)
    out = {}
    canonical = CANONICAL_KEY_ORDER.get(type_str, ['id', 'type'])
    if 'type' not in filtered and type_str:
        filtered['type'] = type_str
    for key in canonical:
        if key in filtered:
            value = filtered[key]
            if key in ('id', 'type'):
                out[key] = value
            elif not _is_default_value(value):
                out[key] = value
    canonical_set = set(canonical)
    leftover = {k: v for k, v in filtered.items() if k not in canonical_set}
    for key in sorted(leftover):
        value = leftover[key]
        if not _is_default_value(value):
            out[key] = value
    for key in sorted(unknown):
        out[key] = unknown[key]
    return out


def pack_dump(path, records, type_str):
    '''Write records to a YAML pack file deterministically.

    System fields are excluded. Records are sorted by id. Keys in CANONICAL_KEY_ORDER
    [type_str] emit first in that order; unknown keys emit alphabetically after.
    Multi-line strings use literal block style.
    '''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_recs = sort_records(records)
    emit_list = [_ordered_emit_dict(r, type_str) for r in sorted_recs]
    text = yaml.dump(
        emit_list,
        Dumper=_PackDumper,
        sort_keys=False,
        default_flow_style=False,
        width=10000,
        allow_unicode=True,
    )
    atomic_write_text(path, text)


def pack_dump_preserving_skipped(path, raw_records, replacements_by_id, type_str):
    '''Rewrite valid records by id while preserving skipped/malformed records.

    Used for incidental pack rewrites such as source_slug correction or rename
    cascades. `pack_load` intentionally skips malformed records, but an
    incidental rewrite must not silently delete those user-authored entries.
    '''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    emitted = []
    replaced = set()
    for raw in raw_records:
        if isinstance(raw, dict):
            rid = raw.get('id')
            if rid in replacements_by_id:
                emitted.append(_ordered_emit_dict(replacements_by_id[rid], type_str))
                replaced.add(rid)
                continue
        emitted.append(raw)
    for rid in sorted(set(replacements_by_id) - replaced):
        emitted.append(_ordered_emit_dict(replacements_by_id[rid], type_str))
    text = yaml.dump(
        emitted,
        Dumper=_PackDumper,
        sort_keys=False,
        default_flow_style=False,
        width=10000,
        allow_unicode=True,
    )
    atomic_write_text(path, text)


def pack_load(path, expected_type=None, return_warnings=False):
    '''Load and validate a pack file.

    Returns a list of record dicts (each with `id` and `type`). When `expected_type`
    is given, records whose `type` differs are skipped (with a warning). Records
    missing `id` or `type` are skipped. Set `return_warnings=True` to get
    (records, warnings) tuple instead of just records.

    For evidence packs (path under `evidences/`), the filename stem MUST match
    the slug grammar; otherwise raises ValueError (path-escape prevention).
    '''
    path = Path(path)
    if path.parent.name == 'evidences':
        stem = path.stem
        if not _VALID_SLUG_RE.match(stem):
            raise ValueError(f'evidence pack filename stem {stem!r} does not match [A-Za-z0-9_-]+ (refusing to load: possible path-escape)')
    if not path.is_file():
        if return_warnings:
            return [], []
        return []
    text = path.read_text(encoding='utf-8')
    if not text.strip():
        if return_warnings:
            return [], []
        return []
    loaded = yaml.safe_load(text)
    if loaded is None:
        if return_warnings:
            return [], []
        return []
    if not isinstance(loaded, list):
        raise ValueError(f'pack {path} top-level is not a list (got {type(loaded).__name__})')
    records = []
    warnings = []
    seen_ids = set()
    for idx, raw in enumerate(loaded):
        if not isinstance(raw, dict):
            warnings.append(f'{path}: record index {idx} is not a mapping; skipping')
            continue
        rid = raw.get('id')
        if rid is None or rid == '':
            warnings.append(f'{path}: record index {idx} has no id; skipping')
            continue
        if not isinstance(rid, str) or not _VALID_ID_RE.match(rid):
            warnings.append(f'{path}: record {rid!r} has invalid id; ids must match [A-Za-z0-9_-]+ (no path separators, dots, colons, or whitespace); skipping')
            continue
        rtype = raw.get('type')
        if not rtype:
            warnings.append(f'{path}: record {rid!r} has no type; skipping')
            continue
        if expected_type and rtype != expected_type:
            warnings.append(f'{path}: record {rid!r} has type {rtype!r} but pack expects {expected_type!r}; skipping')
            continue
        if rid in seen_ids:
            warnings.append(f'{path}: duplicate id {rid!r} within pack; keeping first occurrence')
            continue
        seen_ids.add(rid)
        records.append(raw)
    if return_warnings:
        return records, warnings
    return records


def route_pack_path(project_path, type_str, record):
    '''Resolve the pack path for a record.

    Non-evidence types → <plural>.yaml at project root.
    Evidence → evidences/<slug>.yaml, with fallback chain on slug derivation.
    Returns (pack_path: Path, warnings: list[str]).
    '''
    project_path = Path(project_path)
    warnings = []
    if type_str != 'evidence':
        if type_str not in EXPECTED_PACK_PATHS:
            raise ValueError(f'unknown type {type_str!r}')
        return project_path / EXPECTED_PACK_PATHS[type_str], warnings
    explicit_slug = record.get('source_slug')
    if explicit_slug:
        clean, modified = sanitize_slug(explicit_slug)
        if modified:
            return None, [f'source_slug {explicit_slug!r} contains invalid characters; valid grammar is [A-Za-z0-9_-]+. Suggested: {clean!r}. Edit the input or use --at evidences/{clean}.yaml to bypass.']
        return project_path / 'evidences' / f'{clean}.yaml', warnings
    source_path = record.get('source_path')
    if source_path:
        stem = Path(str(source_path)).stem
        clean, modified = sanitize_slug(stem)
        if modified:
            warnings.append(f"source_slug derived from source_path stem {stem!r}; sanitized to {clean!r}")
        return project_path / 'evidences' / f'{clean}.yaml', warnings
    data_source = record.get('data_source')
    if data_source:
        index = {}
        try:
            idx_path = project_path / '.cofr' / 'index.json'
            if idx_path.is_file():
                index = json.loads(idx_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            index = {}
        ds_str = str(data_source)
        resolves = ds_str in index or Path(ds_str).name in {Path(k).name for k in index}
        if resolves:
            try:
                stem = Path(ds_str).stem
            except Exception:
                stem = ''
            if stem:
                clean, modified = sanitize_slug(stem)
                if modified:
                    warnings.append(f"source_slug derived from data_source stem {stem!r}; sanitized to {clean!r}")
                return project_path / 'evidences' / f'{clean}.yaml', warnings
    warnings.append(f"evidence record {record.get('id', '?')!r} has no source_slug/source_path/data_source resolving to an indexed file; routing to evidences/__misc__.yaml. Consider setting source_path for clarity.")
    return project_path / 'evidences' / '__misc__.yaml', warnings


def validate_at_path(at_path, type_str, project_path):
    '''Validate that an --at path matches the expected pack location for type_str.

    Returns (resolved_path: Path | None, error_message: str | None).
    '''
    project_path = Path(project_path).resolve()
    candidate = (project_path / at_path).resolve()
    try:
        candidate.relative_to(project_path)
    except ValueError:
        return None, f'--at path {at_path!r} resolves outside the project root'
    rel = candidate.relative_to(project_path)
    rel_str = str(rel)
    if type_str == 'evidence':
        if not rel_str.startswith('evidences/') or not rel_str.endswith('.yaml'):
            return None, f'--at {at_path!r} does not match expected evidence pack pattern evidences/<slug>.yaml'
        slug = rel.stem
        if not _VALID_SLUG_RE.match(slug):
            return None, f'--at filename stem {slug!r} does not match [A-Za-z0-9_-]+'
        return candidate, None
    expected = EXPECTED_PACK_PATHS.get(type_str)
    if expected is None:
        return None, f'unknown type {type_str!r}'
    if rel_str != expected:
        return None, f'--at {at_path!r} does not match expected pack location for type {type_str!r}: {expected}'
    return candidate, None
