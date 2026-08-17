from dataclasses import asdict, dataclass, field, fields
import base64
import math
from datetime import date, datetime, timezone


CLAIM_STATUSES = ('supported', 'provisionally_supported', 'mixed', 'unsupported', 'retired')
CLAIM_CONFIDENCES = ('low', 'medium', 'high')
EVIDENCE_TYPES = ('experiment_result', 'paper_note', 'manual_observation', 'report_section', 'agent_summary')
EVIDENCE_STRENGTHS = ('low', 'medium', 'high')
EVIDENCE_STATUSES = ('active', 'deprecated')
DECISION_STATUSES = ('active', 'deprecated')
EXPERIMENT_STATUSES = ('active', 'deprecated')
OPEN_QUESTION_PRIORITIES = ('low', 'medium', 'high', 'critical')
OPEN_QUESTION_SEVERITIES = ('low', 'medium', 'high')
OPEN_QUESTION_STATUSES = ('open', 'in_progress', 'resolved', 'deprioritized')
RISK_SEVERITIES = ('low', 'medium', 'high')
RISK_STATUSES = ('open', 'mitigated', 'accepted', 'resolved')
RISK_SOURCES = ('authored', 'computed')
ARTIFACT_TYPES = ('current_state', 'what_changed', 'contradictions', 'next_decision')
ARTIFACT_STALENESS = ('current', 'stale')


_CONFIDENCE_NUMERIC = {'low': 0.25, 'medium': 0.5, 'high': 0.75}


def confidence_to_numeric(s):
    return _CONFIDENCE_NUMERIC.get(s)


@dataclass
class Claim:
    id: str
    title: str = ''
    statement: str = ''
    status: str = 'provisionally_supported'
    confidence: str = 'medium'
    owner: str = ''
    main_support: str = ''
    main_weakness: str = ''
    what_would_change_my_mind: str = ''
    parsed_from: str = ''
    first_seen: str = ''
    last_updated: str = ''
    stale: bool = False
    source_missing: bool = False
    extra_sections: dict = field(default_factory=dict)
    _timeline: list = field(default_factory=list)
    _status_timeline: list = field(default_factory=list)
    _unknown_fields: dict = field(default_factory=dict)


@dataclass
class Evidence:
    id: str
    evidence_type: str = 'manual_observation'
    data_source: str = ''
    summary: str = ''
    strength: str = 'medium'
    status: str = 'active'
    claim_links: list = field(default_factory=list)
    created_at: str = ''
    source_path: str = ''
    source_slug: str = ''
    source_title: str = ''
    source_anchors: list = field(default_factory=list)
    parsed_from: str = ''
    first_seen: str = ''
    last_updated: str = ''
    stale: bool = False
    extra_sections: dict = field(default_factory=dict)
    _unknown_fields: dict = field(default_factory=dict)
    _preserved_user_fields: dict = field(default_factory=dict)


@dataclass
class Experiment:
    id: str
    name: str = ''
    intent: str = ''
    config_reference: str = ''
    result_summary: str = ''
    key_metrics: dict = field(default_factory=dict)
    implications: str = ''
    affected_claim_ids: list = field(default_factory=list)
    follow_on_questions: list = field(default_factory=list)
    timestamp: str = ''
    status: str = 'active'
    parsed_from: str = ''
    first_seen: str = ''
    last_updated: str = ''
    stale: bool = False
    extra_sections: dict = field(default_factory=dict)
    _unknown_fields: dict = field(default_factory=dict)


@dataclass
class Decision:
    id: str
    title: str = ''
    decision_statement: str = ''
    rationale: str = ''
    based_on_evidence_ids: list = field(default_factory=list)
    depends_on_claim_ids: list = field(default_factory=list)
    reopen_conditions: str = ''
    timestamp: str = ''
    status: str = 'active'
    parsed_from: str = ''
    first_seen: str = ''
    last_updated: str = ''
    stale: bool = False
    extra_sections: dict = field(default_factory=dict)
    _unknown_fields: dict = field(default_factory=dict)


@dataclass
class OpenQuestion:
    id: str
    question: str = ''
    priority: str = 'medium'
    blocking_impact: str = ''
    blocking_severity: str = ''
    related_claim_ids: list = field(default_factory=list)
    proposed_resolution: str = ''
    minimum_test: str = ''
    status: str = 'open'
    parsed_from: str = ''
    first_seen: str = ''
    last_updated: str = ''
    stale: bool = False
    extra_sections: dict = field(default_factory=dict)
    _unknown_fields: dict = field(default_factory=dict)


@dataclass
class Risk:
    id: str
    statement: str = ''
    severity: str = 'medium'
    related_claim_ids: list = field(default_factory=list)
    related_decision_ids: list = field(default_factory=list)
    recommended_resolution: str = ''
    status: str = 'open'
    source: str = 'authored'
    parsed_from: str = ''
    first_seen: str = ''
    last_updated: str = ''
    stale: bool = False
    extra_sections: dict = field(default_factory=dict)
    _unknown_fields: dict = field(default_factory=dict)


@dataclass
class Artifact:
    id: str
    artifact_type: str = 'current_state'
    path: str = ''
    covers_claim_ids: list = field(default_factory=list)
    generated_at: str = ''
    staleness_status: str = 'current'
    stale_because: str = ''
    _unknown_fields: dict = field(default_factory=dict)


DOMAIN_TYPES = {
    'claim': Claim,
    'evidence': Evidence,
    'experiment': Experiment,
    'decision': Decision,
    'question': OpenQuestion,
    'risk': Risk,
}


TYPE_TO_COLLECTION = {
    'claim': 'claims',
    'evidence': 'evidence',
    'experiment': 'experiments',
    'decision': 'decisions',
    'question': 'open_questions',
    'risk': 'risks',
}


CLASS_TO_TYPE_STR = {cls: type_str for type_str, cls in DOMAIN_TYPES.items()}


_ENUM_RULES = {
    Claim: {'status': (CLAIM_STATUSES, 'provisionally_supported'), 'confidence': (CLAIM_CONFIDENCES, 'medium')},
    Evidence: {
        'evidence_type': (EVIDENCE_TYPES, 'manual_observation'),
        'strength': (EVIDENCE_STRENGTHS, 'medium'),
        'status': (EVIDENCE_STATUSES, 'active'),
    },
    Decision: {'status': (DECISION_STATUSES, 'active')},
    Experiment: {'status': (EXPERIMENT_STATUSES, 'active')},
    OpenQuestion: {
        'priority': (OPEN_QUESTION_PRIORITIES, 'medium'),
        'blocking_severity': (OPEN_QUESTION_SEVERITIES, 'medium'),
        'status': (OPEN_QUESTION_STATUSES, 'open'),
    },
    Risk: {
        'severity': (RISK_SEVERITIES, 'medium'),
        'status': (RISK_STATUSES, 'open'),
        'source': (RISK_SOURCES, 'authored'),
    },
    Artifact: {
        'artifact_type': (ARTIFACT_TYPES, 'current_state'),
        'staleness_status': (ARTIFACT_STALENESS, 'current'),
    },
}


_PRIORITY_TO_SEVERITY = {'low': 'low', 'medium': 'medium', 'high': 'high', 'critical': 'high'}


_PER_TYPE_LIVE_STATUSES = {
    'claim': lambda s: s != 'retired',
    'question': lambda s: s in ('open', 'in_progress'),
    'risk': lambda s: s in ('open', 'accepted'),
    'evidence': lambda s: s == 'active',
    'decision': lambda s: s == 'active',
    'experiment': lambda s: s == 'active',
}
_PER_TYPE_DEFAULT_STATUS = {
    'claim': 'provisionally_supported',
    'question': 'open',
    'risk': 'open',
    'evidence': 'active',
    'decision': 'active',
    'experiment': 'active',
}


def _record_type_str(record, type_str=None):
    if type_str is not None:
        return type_str
    if hasattr(record, '__class__') and type(record) in CLASS_TO_TYPE_STR:
        return CLASS_TO_TYPE_STR[type(record)]
    if isinstance(record, dict):
        return record.get('type') or record.get('_type')
    return None


def _get(record, name, default=None):
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def has_live_status(record, type_str=None):
    '''True iff the record\'s status is a LIVE value for its type. Ignores stale.

    type_str must be provided when record is a state dict (dicts produced by
    asdict() do not carry a type tag).
    '''
    resolved_type = _record_type_str(record, type_str=type_str)
    status = _get(record, 'status', _PER_TYPE_DEFAULT_STATUS.get(resolved_type))
    if status is None:
        status = _PER_TYPE_DEFAULT_STATUS.get(resolved_type)
    check = _PER_TYPE_LIVE_STATUSES.get(resolved_type)
    if check is None:
        return True
    return check(status)


def is_live(record, type_str=None):
    '''True iff the record is active (not stale, not user-deactivated).

    type_str must be provided when record is a state dict.
    '''
    if _get(record, 'stale', False):
        return False
    return has_live_status(record, type_str=type_str)


_USER_TIMESTAMP_FIELDS = {Evidence: ('created_at',), Experiment: ('timestamp',), Decision: ('timestamp',)}


def _normalize_user_timestamp(value):
    '''Return (normalized, error_or_None). Date-only -> midnight UTC; full ISO accepted; unparseable -> None.'''
    if value is None:
        return None, None
    s = str(value).strip()
    if not s:
        return None, None
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S%z'):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), None
        except ValueError:
            continue
    try:
        dt = datetime.strptime(s, '%Y-%m-%d')
        return dt.replace(tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), None
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), None
    except ValueError:
        return None, f'unparseable timestamp {value!r}'


_STRING_FIELDS = {
    Claim: ('title', 'statement', 'owner', 'main_support', 'main_weakness', 'what_would_change_my_mind', 'parsed_from', 'first_seen', 'last_updated'),
    Evidence: ('data_source', 'summary', 'source_path', 'source_slug', 'source_title', 'parsed_from', 'first_seen', 'last_updated'),
    Experiment: ('name', 'intent', 'config_reference', 'result_summary', 'implications', 'parsed_from', 'first_seen', 'last_updated'),
    Decision: ('title', 'decision_statement', 'rationale', 'reopen_conditions', 'parsed_from', 'first_seen', 'last_updated'),
    OpenQuestion: ('question', 'blocking_impact', 'proposed_resolution', 'minimum_test', 'parsed_from', 'first_seen', 'last_updated'),
    Risk: ('statement', 'recommended_resolution', 'parsed_from', 'first_seen', 'last_updated'),
    Artifact: ('path', 'generated_at', 'stale_because'),
}
_STRING_LIST_FIELDS = {
    Experiment: ('affected_claim_ids', 'follow_on_questions'),
    Decision: ('based_on_evidence_ids', 'depends_on_claim_ids'),
    OpenQuestion: ('related_claim_ids',),
    Risk: ('related_claim_ids', 'related_decision_ids'),
    Artifact: ('covers_claim_ids',),
}
_DICT_FIELDS = {
    Claim: ('extra_sections', '_unknown_fields'),
    Evidence: ('extra_sections', '_unknown_fields', '_preserved_user_fields'),
    Experiment: ('key_metrics', 'extra_sections', '_unknown_fields'),
    Decision: ('extra_sections', '_unknown_fields'),
    OpenQuestion: ('extra_sections', '_unknown_fields'),
    Risk: ('extra_sections', '_unknown_fields'),
    Artifact: ('_unknown_fields',),
}


def _json_safe(value, path, warnings):
    '''Recursively preserve safe YAML values while guaranteeing JSON output.'''
    if isinstance(value, float) and not math.isfinite(value):
        warnings.append(f'{path}: non-finite number replaced with null')
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        warnings.append(f'{path}: converted YAML date/time to ISO string')
        return value.isoformat()
    if isinstance(value, bytes):
        warnings.append(f'{path}: converted YAML binary value to base64 text')
        return base64.b64encode(value).decode('ascii')
    if isinstance(value, dict):
        out = {}
        for key, nested in value.items():
            safe_key = key if isinstance(key, str) else str(key)
            if safe_key != key:
                warnings.append(f'{path}: converted non-string mapping key {key!r} to text')
            out[safe_key] = _json_safe(nested, f'{path}.{safe_key}', warnings)
        return out
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested, f'{path}[{idx}]', warnings) for idx, nested in enumerate(value)]
    warnings.append(f'{path}: unsupported {type(value).__name__} value replaced with text')
    return str(value)


def _normalize_shapes(obj, warnings):
    cls = type(obj)
    for field_name in _STRING_FIELDS.get(cls, ()):
        value = getattr(obj, field_name)
        if value is None:
            setattr(obj, field_name, '')
        elif not isinstance(value, str):
            warnings.append(f'{cls.__name__} {obj.id!r}: field {field_name!r} must be text; defaulting to empty text')
            setattr(obj, field_name, '')
    for field_name in _STRING_LIST_FIELDS.get(cls, ()):
        value = getattr(obj, field_name)
        if not isinstance(value, list):
            warnings.append(f'{cls.__name__} {obj.id!r}: field {field_name!r} must be a list; defaulting to []')
            setattr(obj, field_name, [])
            continue
        kept = [entry for entry in value if isinstance(entry, str)]
        if len(kept) != len(value):
            warnings.append(f'{cls.__name__} {obj.id!r}: field {field_name!r} contained non-text entries; dropping them')
        setattr(obj, field_name, kept)
    for field_name in _DICT_FIELDS.get(cls, ()):
        value = getattr(obj, field_name)
        if not isinstance(value, dict):
            warnings.append(f'{cls.__name__} {obj.id!r}: field {field_name!r} must be a mapping; defaulting to {{}}')
            setattr(obj, field_name, {})
            continue
        setattr(obj, field_name, _json_safe(value, f'{cls.__name__} {obj.id!r}.{field_name}', warnings))
    if hasattr(obj, 'stale') and not isinstance(obj.stale, bool):
        warnings.append(f'{cls.__name__} {obj.id!r}: field \'stale\' must be boolean; defaulting to false')
        obj.stale = False
    if isinstance(obj, Claim) and not isinstance(obj.source_missing, bool):
        warnings.append(f'Claim {obj.id!r}: field \'source_missing\' must be boolean; defaulting to false')
        obj.source_missing = False


def validate_and_normalize(obj, *, user_authored=False):
    '''Validate enum fields against allowlists; replace invalid values with the field default.

    Returns (validated_obj, [warning_str]). The object is mutated in place and also returned.
    Special case: OpenQuestion.blocking_severity, when empty, falls back to the severity
    matching `priority` (critical priority maps to high severity, since severity has no
    `critical` value). Evidence.claim_links entries with off-enum polarity normalize to
    'supports' with a per-link warning. User-supplied timestamps (Evidence.created_at,
    Experiment.timestamp, Decision.timestamp) are normalized to UTC ISO 8601; date-only
    strings become midnight UTC; absent or unparseable values become None (the latter
    with a warning).
    '''
    warnings = []
    _normalize_shapes(obj, warnings)
    if isinstance(obj, OpenQuestion) and obj.priority not in OPEN_QUESTION_PRIORITIES:
        warnings.append(f'{type(obj).__name__} {obj.id!r}: invalid value {obj.priority!r} for field \'priority\'; defaulting to \'medium\'')
        obj.priority = 'medium'
    if isinstance(obj, OpenQuestion) and obj.blocking_severity == '':
        obj.blocking_severity = _PRIORITY_TO_SEVERITY.get(obj.priority, 'medium')
    rules = _ENUM_RULES.get(type(obj), {})
    for field_name, (allowed, default) in rules.items():
        value = getattr(obj, field_name)
        if value not in allowed:
            warnings.append(f'{type(obj).__name__} {obj.id!r}: invalid value {value!r} for field {field_name!r}; defaulting to {default!r}')
            setattr(obj, field_name, default)
    for ts_field in _USER_TIMESTAMP_FIELDS.get(type(obj), ()):
        normalized, err = _normalize_user_timestamp(getattr(obj, ts_field))
        if err:
            warnings.append(f'{type(obj).__name__} {obj.id!r}: invalid {ts_field}: {err}; storing as None')
        setattr(obj, ts_field, normalized)
    if isinstance(obj, Risk) and user_authored and obj.source != 'authored':
        warnings.append(f"Risk {obj.id!r}: source 'computed' is reserved for transient cofr analysis; treating this user-authored record as 'authored'")
        obj.source = 'authored'
    if isinstance(obj, Evidence):
        if not isinstance(obj.claim_links, list):
            warnings.append(f"Evidence {obj.id!r}: field 'claim_links' must be a list; defaulting to []")
            obj.claim_links = []
        kept_links = []
        for idx, link in enumerate(obj.claim_links):
            if not isinstance(link, dict) or not isinstance(link.get('claim_id'), str) or not link.get('claim_id'):
                warnings.append(f"Evidence {obj.id!r}: claim_links item {idx} must contain a non-empty text claim_id; dropping")
                continue
            safe_link = _json_safe(link, f"Evidence {obj.id!r}.claim_links[{idx}]", warnings)
            pol = safe_link.get('polarity', 'supports')
            if pol not in ('supports', 'opposes'):
                warnings.append(f"Evidence {obj.id!r}: invalid polarity {pol!r} on link to {safe_link.get('claim_id')!r}; defaulting to 'supports' (valid: supports, opposes)")
                pol = 'supports'
            safe_link['polarity'] = pol
            kept_links.append(safe_link)
        obj.claim_links = kept_links
        if not isinstance(obj.source_anchors, list):
            warnings.append(f"Evidence {obj.id!r}: field 'source_anchors' must be a list; defaulting to []")
            obj.source_anchors = []
        else:
            obj.source_anchors = _json_safe(obj.source_anchors, f"Evidence {obj.id!r}.source_anchors", warnings)
    if isinstance(obj, Claim):
        for field_name in ('_timeline', '_status_timeline'):
            value = getattr(obj, field_name)
            if not isinstance(value, list):
                warnings.append(f'Claim {obj.id!r}: field {field_name!r} must be a list; defaulting to []')
                setattr(obj, field_name, [])
            else:
                setattr(obj, field_name, _json_safe(value, f'Claim {obj.id!r}.{field_name}', warnings))
    return obj, warnings


def to_dict(obj):
    return asdict(obj)


def from_dict(type_str, d):
    cls = DOMAIN_TYPES[type_str] if type_str in DOMAIN_TYPES else Artifact
    valid_field_names = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in d.items() if k in valid_field_names}
    instance = cls(**filtered)
    unknown = {k: v for k, v in d.items() if k not in valid_field_names and k not in ('type',)}
    if unknown:
        existing = dict(instance._unknown_fields) if hasattr(instance, '_unknown_fields') and instance._unknown_fields else {}
        existing.update(unknown)
        if hasattr(instance, '_unknown_fields'):
            instance._unknown_fields = existing
    return instance
