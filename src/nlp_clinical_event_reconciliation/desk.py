"""核对已登记的出院交接资料，区分转述、拟议和已确认的沟通内容。

工作区只做核对，不替任何人作决定：未签署、仍在讨论或无法确定承接对象的
文字不会被拼成患者指令。输出保留每个事项的原句、签署身份和当前沟通状态，
供出院协调员和门诊协调员复核。
"""

import re
import unicodedata
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field

from .text import normalize_text


CONFIRMED = '已确认'
REPORTED = '转述'
PROPOSED = '拟议'

MEDICATION = '用药交接'
FOLLOW_UP = '复诊交接'

WAITING = '等待签署依据'
PREPARE = '制作交接'
SUPPLEMENT = '需要补充通知'
NOTIFIED = '已通知'
REVIEW = '需要核实'

DISCHARGE_KINDS = {
    '出院沟通', '出院交接', '出院医嘱', '出院确认', '交接确认', '签署交接',
}
RECEIPT_KINDS = {
    '预约回执', '改期回执', '预约确认', '改期确认', '预约变更', '复诊回执',
}
MEDICATION_KINDS = {MEDICATION, '用药', '药物交接', '用药沟通', '用药确认'}
FOLLOW_UP_KINDS = {FOLLOW_UP, '复诊', '随访交接', '预约交接', '复诊预约', '随访', '预约变更'}

_PHYSICIAN_SIGNERS = {'医师', '医生', '出院医师', '主治医师', '主管医师'}
_COORDINATOR_SIGNERS = {'门诊协调员', '协调员', '门诊护士', '预约协调员'}

_TYPE_ALIASES = {
    '转述': REPORTED,
    '转述内容': REPORTED,
    'reported': REPORTED,
    'report': REPORTED,
    'paraphrase': REPORTED,
    'paraphrased': REPORTED,
    'description': REPORTED,
    'hearsay': REPORTED,
    'belief': REPORTED,
    'patient_belief': REPORTED,
    'patient_report': REPORTED,
    'intake': REPORTED,
    'history': REPORTED,
    '拟议': PROPOSED,
    '提议': PROPOSED,
    'proposed': PROPOSED,
    'proposal': PROPOSED,
    'proposal_only': PROPOSED,
    'discussion': PROPOSED,
    'discussed': PROPOSED,
    'draft': PROPOSED,
    'planned': PROPOSED,
    'plan': PROPOSED,
    'tentative': PROPOSED,
    'pending': PROPOSED,
    'consideration': PROPOSED,
    'considering': PROPOSED,
    '已确认': CONFIRMED,
    '确认': CONFIRMED,
    'confirmed': CONFIRMED,
    'confirmation': CONFIRMED,
    'signed': CONFIRMED,
    'signed_handover': CONFIRMED,
    'final': CONFIRMED,
    'decision': CONFIRMED,
    'decided': CONFIRMED,
    'handover': CONFIRMED,
}

_TYPE_KEYS = (
    'statement_type', 'statement', 'claim_type', 'claim', 'classification',
    'modality', 'evidence_type', 'speech_act', 'narrative_type', 'type',
    'status', 'state',
)

_REPORTED_MARKERS = (
    '患者以为', '患者认为', '患者自述', '患者称', '患者说', '家属称', '家属说',
    '自述', '自称', '转述', '听说', '传闻', '入院前清单', '入院前', '院前',
    '记录提及', '提及', '病史', '既往记录', '患者相信', 'patient believes',
    'patient reports', 'reported that', 'per patient',
)

_TENTATIVE_MARKERS = (
    '讨论', '考虑', '拟', '计划', '打算', '预备', '建议', '若', '如果', '假如',
    '待确认', '待定', '仍等', '等待', '暂未', '尚未', '暂不', '可能', '草稿',
    '未签署', '未确认', 'discuss', 'consider', 'propose', 'draft', 'pending',
    'tentative',
)

_CONFIRMED_NEGATIONS = (
    '未签署', '未确认', '暂未签署', '暂未确认', '尚未签署', '尚未确认',
    '没有签署', '没有确认', '仍未确认', '仍待确认', '待签署', '待确认',
)

_CONFIRMED_STRONG_MARKERS = (
    '已签署', '已确认', '已经确认', '已经签署', '确认签署', '签署确认',
    '已决定', '最终决定', '决定执行',
)

_FORWARD_PHRASES = (
    '继续此前已经确认的安排', '继续此前确认的安排', '继续之前确认的安排',
    '继续此前已确认的安排', '继续之前已确认的安排', '继续已确认的安排',
    '按此前已经确认的安排', '按此前确认的安排', '按之前确认的安排',
    '按已确认的安排', '依照此前确认的安排', '依照此前已经确认的安排',
    '按此前确认的方案', '继续此前方案', '维持此前安排', '维持原计划',
    '继续原计划', '继续原定计划', '按原计划', '按原定计划', '继续原有安排',
    '继续原有计划', '沿用原计划', '按原有安排',
)

_FORWARD_PATTERN = re.compile(
    r'(?:继续|维持|沿用|遵照|依照|按照|按)\s*'
    r'(?:此前|之前|先前|原来?|原定|以往|已确认的?|此前已确认的?)?\s*[^，。；;！？\n]{0,8}?'
    r'(?:安排|计划|方案|做法|医嘱)'
)

_IDENTITY_KEYS = (
    'matter', 'matter_id', 'appointment_id', 'appointment', 'appointment_key',
    'plan_id', 'original_plan_id', 'reschedule_id', 'change_id',
    'confirmation_id', 'entry_id', 'record_id', 'document_id', 'revision_id',
    'version_id', 'id',
)

# 承接比对只使用指向“某个安排”的编号；记录自身编号不能用来认领别的记录。
_SELECTOR_IDENTITY_KEYS = (
    'matter', 'matter_id', 'appointment_id', 'appointment', 'appointment_key',
    'plan_id', 'original_plan_id', 'reschedule_id', 'change_id',
)

_REFERENCE_KEYS = (
    'references', 'reference', 'refers_to', 'ref', 'prior', 'prior_id',
    'prior_record', 'prior_records', 'continues', 'continues_id', 'based_on',
    'basis', 'source_reference', 'original_plan', 'original_plan_id', 'plan',
    'plan_id', 'reschedule', 'reschedule_id', 'change_id', 'confirmation_id',
    'supersedes', 'supersedes_id', 'version_of', 'version_id', 'carries_forward',
    'carried_forward_from',
)

_DATE_PATTERNS = (
    re.compile(r'\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?'),
    re.compile(r'\d{1,2}\s*月\s*\d{1,2}\s*日'),
    re.compile(r'\d{1,2}\s*[:：]\s*\d{2}'),
)

_ISO_DATE = re.compile(r'(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})')
_MONTH_DAY = re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日')

MatterDecision = namedtuple('MatterDecision', 'current unresolved')
SentMatch = namedtuple('SentMatch', 'items earlier')


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        output = []
        for item in value:
            if isinstance(item, dict):
                identifier = item.get('id') or item.get('record_id') or item.get('reference')
                if identifier is not None and str(identifier) != '':
                    output.append(str(identifier))
            elif item is not None and str(item) != '':
                output.append(str(item))
        return output
    if isinstance(value, dict):
        output = []
        for key, item in value.items():
            if isinstance(item, (list, tuple, set)):
                output.extend(str(part) for part in item if part is not None and str(part) != '')
            elif item is True:
                output.append(str(key))
            elif item is not None and item != '' and item is not False:
                output.append(str(item))
        return output
    return [str(value)]


def _canonical(text):
    """判断两句话是否实质相同，忽略空白、全半角和标点差异。"""
    if not isinstance(text, str):
        return ''
    value = unicodedata.normalize('NFKC', normalize_text(text)).casefold()
    return re.sub(r'[\s，,。.；;：:、!！?？（）()\[\]【】"“”\'‘’]', '', value)


def _date_span(text):
    if not isinstance(text, str):
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return normalize_text(match.group(0))
    return None


_CHANGE_WORDS = ('改至', '改为', '改到', '调整为', '调整至', '调整到', '变更至', '变更到',
                 '变更成', '更改为', '推迟到', '提前到', '顺延至', '从', '原为', '原先')


def _residue(text):
    value = normalize_text(text)
    for pattern in _DATE_PATTERNS:
        value = pattern.sub('', value)
    for word in _CHANGE_WORDS:
        value = value.replace(word, '')
    return _canonical(value)


def _all_dates(text):
    if not isinstance(text, str):
        return ()
    found = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            found.append(_canonical(normalize_text(match.group(0))))
    return tuple(sorted(set(found)))


def substantive_key(text):
    """同一安排的等价表示：日期一致且去掉改期措辞后内容一致。"""
    return (_all_dates(text), _residue(text))


def _equivalent(left, right):
    if _canonical(left) == _canonical(right):
        return True
    return substantive_key(left) == substantive_key(right)


def _sort_date(documented):
    """按日期排序；缺日期的条目排最前，无法解析的按原文排序。"""
    if not isinstance(documented, str) or not documented.strip():
        return (0, '', '')
    match = _ISO_DATE.search(documented)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return (1, f'{year:04d}{month:02d}{day:02d}', documented)
    match = _MONTH_DAY.search(documented)
    if match:
        month, day = (int(part) for part in match.groups())
        return (1, f'0000{month:02d}{day:02d}', documented)
    return (2, '', documented)


def _mask_forward(text):
    value = normalize_text(text)
    for phrase in sorted(_FORWARD_PHRASES, key=len, reverse=True):
        value = value.replace(phrase, '继续')
    return _FORWARD_PATTERN.sub('继续', value)


def is_forward_reference(text):
    """文字是否表示承接此前已经确认的安排，而非给出新的具体内容。"""
    if not isinstance(text, str):
        return False
    value = normalize_text(text)
    return bool(_FORWARD_PATTERN.search(value)) or any(
        phrase in value for phrase in _FORWARD_PHRASES
    )


def describe_change(earlier_text, latest_text):
    """给出旧提醒到当前依据的实质变化，供补发通知时说明。"""
    if not isinstance(earlier_text, str) or not isinstance(latest_text, str):
        return None
    if not earlier_text.strip() or not latest_text.strip():
        return None
    old_date = _date_span(earlier_text)
    new_date = _date_span(latest_text)
    if old_date and new_date and _canonical(old_date) != _canonical(new_date):
        return {
            'kind': '时间',
            'from': old_date,
            'to': new_date,
            'summary': f'时间由 {old_date} 改为 {new_date}',
            'substantive': True,
        }
    if _canonical(earlier_text) == _canonical(latest_text):
        return None
    return {
        'kind': '内容',
        'from': earlier_text,
        'to': latest_text,
        'summary': f'内容由“{earlier_text}”改为“{latest_text}”',
        'substantive': True,
    }


# ---------------------------------------------------------------------------
# 资料与权限
# ---------------------------------------------------------------------------


class SignedInstructions:
    """登记簿中的签署文书；只允许相应岗位在相应事项上确认安排。"""

    def __init__(self, artifacts):
        self.artifacts = {
            artifact['id']: artifact
            for artifact in artifacts or []
            if isinstance(artifact, dict) and artifact.get('id')
        }

    def artifact(self, source):
        return self.artifacts.get(source)

    def accepts(self, finding):
        """该来源的签署人是否有权确认这一条事项内容。"""
        source = finding.get('source') if isinstance(finding, dict) else None
        artifact = self.artifacts.get(source)
        if not artifact:
            return False
        if finding.get('statement_type') in {REPORTED, PROPOSED}:
            return False
        if finding.get('declared_type') in {REPORTED, PROPOSED}:
            return False
        signer = (
            artifact.get('signed_by')
            or artifact.get('signer')
            or artifact.get('signed_role')
            or artifact.get('role')
        )
        if not signer:
            return False
        is_physician = signer in _PHYSICIAN_SIGNERS
        is_coordinator = signer in _COORDINATOR_SIGNERS
        kind = _kind_of(finding)
        artifact_kind = artifact.get('kind')
        if kind == MEDICATION:
            return artifact_kind in DISCHARGE_KINDS and is_physician
        if kind == FOLLOW_UP:
            return (
                artifact_kind in DISCHARGE_KINDS and is_physician
            ) or (
                artifact_kind in RECEIPT_KINDS and is_coordinator
            )
        return False

    def scope_reason(self, finding):
        """说明某条记录为什么不能作为已确认依据。"""
        source = finding.get('source') if isinstance(finding, dict) else None
        artifact = self.artifacts.get(source)
        if not artifact:
            return '来源未在登记簿中，无法核对签署'
        artifact_kind = artifact.get('kind')
        signer = (
            artifact.get('signed_by')
            or artifact.get('signer')
            or artifact.get('signed_role')
            or artifact.get('role')
        )
        if not signer:
            return '未签署记录，不能作为患者指令'
        if finding.get('declared_type') == REPORTED or finding.get('statement_type') == REPORTED:
            return '转述内容，未形成已确认安排'
        if finding.get('declared_type') == PROPOSED or finding.get('statement_type') == PROPOSED:
            return '拟议内容，仍在讨论，尚未确认'
        kind = _kind_of(finding)
        is_coordinator = signer in _COORDINATOR_SIGNERS
        if kind == MEDICATION and artifact_kind in RECEIPT_KINDS:
            return '预约回执只能更新对应复诊，不能改变用药沟通'
        if kind == FOLLOW_UP and artifact_kind in RECEIPT_KINDS and not is_coordinator:
            return '回执签署岗位与复诊沟通不符'
        if kind == MEDICATION:
            return '用药交接须由医师签署的出院沟通确认'
        if kind == FOLLOW_UP:
            return '复诊交接须由医师签署的出院沟通或门诊协调员签署的预约回执确认'
        return '记录事项或签署岗位不在可核对范围内'


# ---------------------------------------------------------------------------
# 条目解析
# ---------------------------------------------------------------------------


def _kind_of(entry):
    value = entry.get('kind') or entry.get('handover_kind') or entry.get('topic_kind')
    if isinstance(value, str):
        value = value.strip()
        if value in MEDICATION_KINDS:
            return MEDICATION
        if value in FOLLOW_UP_KINDS:
            return FOLLOW_UP
        if value:
            return value
    text = entry.get('text') or ''
    if any(marker in text for marker in ('复诊', '随访', '预约', '门诊')):
        return FOLLOW_UP
    if any(marker in text for marker in ('用药', '药物', '服药', '条目')):
        return MEDICATION
    return ''


def _declared_type(entry):
    for key in _TYPE_KEYS:
        value = entry.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().casefold()
        if normalized in _TYPE_ALIASES:
            return _TYPE_ALIASES[normalized]
    if entry.get('confirmed') is True or entry.get('signed') is True:
        return CONFIRMED
    if entry.get('proposed') is True or entry.get('draft') is True:
        return PROPOSED
    if entry.get('reported') is True:
        return REPORTED
    return None


def _text_type(text):
    """从原句推断它是在转述、拟议还是已确认。"""
    if not isinstance(text, str):
        return None
    value = normalize_text(text)
    masked = _mask_forward(value)
    lowered = masked.casefold()

    if any(marker in value or marker in lowered for marker in _REPORTED_MARKERS):
        return REPORTED

    negated = any(marker in value for marker in _CONFIRMED_NEGATIONS)
    tentative = negated or any(
        marker in masked or marker in lowered for marker in _TENTATIVE_MARKERS
    )

    if any(marker in masked for marker in _CONFIRMED_STRONG_MARKERS) and not tentative:
        return CONFIRMED
    if tentative:
        return PROPOSED
    if any(marker in masked for marker in ('确认', '签署', '决定')):
        return CONFIRMED
    return None


def _unique(values):
    seen = set()
    output = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _entry_identities(raw, keys=_IDENTITY_KEYS):
    values = []
    for key in keys:
        if key in raw and raw[key] not in (None, '', [], {}):
            values.extend(_as_list(raw[key]))
    return _unique(values)


def _references(raw):
    values = []
    for key in _REFERENCE_KEYS:
        if key in raw:
            values.extend(_as_list(raw[key]))
    return _unique(values)


@dataclass
class Entry:
    raw: dict
    order: int
    source: str
    kind: str
    subject: str
    documented: str
    revision: int
    text: str
    statement_type: str
    signed_by: str | None
    artifact_kind: str | None
    accepted: bool
    declared_type: str | None = None
    matter: str = ''
    references: list = field(default_factory=list)
    identities: list = field(default_factory=list)
    selectors: list = field(default_factory=list)
    forward: bool = False
    ambiguous_refs: list = field(default_factory=list)
    resolved_text: str | None = None
    resolution: dict | None = None
    review_reason: str | None = None
    held_reason: str | None = None

    @property
    def sort_key(self):
        return (*_sort_date(self.documented), self.revision, self.order)

    def aliases(self):
        values = {self.source, self.subject, self.matter, *self.identities, *self.references}
        return {value for value in values if value}

    def selector_aliases(self):
        """可被“继续此前安排”认领的编号：事项、预约、计划与改期编号。"""
        values = {self.matter, *self.selectors, *self.references}
        return {value for value in values if value}

    def material(self, status):
        return {
            'source': self.source,
            'text': self.text,
            'documented': self.documented,
            'revision': self.revision,
            'kind': self.kind,
            'statement_type': self.statement_type,
            'signed_by': self.signed_by,
            'signer': self.signed_by,
            'artifact_kind': self.artifact_kind,
            'status': status,
        }


def _normalize_entries(case, authority):
    values = case.get('entries', []) if isinstance(case, dict) else []
    entries = []
    for order, raw in enumerate(values):
        if not isinstance(raw, dict):
            continue
        text_value = raw.get('text', '')
        text = normalize_text(text_value) if isinstance(text_value, str) else str(text_value)
        source = str(raw.get('source') or raw.get('artifact') or raw.get('artifact_id') or '')
        kind = _kind_of(raw)
        subject = str(
            raw.get('subject')
            or raw.get('matter')
            or raw.get('matter_id')
            or raw.get('appointment')
            or raw.get('appointment_id')
            or ''
        )
        documented = str(raw.get('documented') or raw.get('date') or raw.get('recorded') or '')
        revision = raw.get('revision', raw.get('version', 1))
        if not isinstance(revision, int):
            try:
                revision = int(revision)
            except (TypeError, ValueError):
                revision = 1
        artifact = authority.artifact(source) or {}
        signed_by = (
            artifact.get('signed_by')
            or artifact.get('signer')
            or artifact.get('signed_role')
            or artifact.get('role')
        )
        artifact_kind = artifact.get('kind')
        declared = _declared_type(raw)
        inferred = _text_type(text)
        forward = is_forward_reference(text)

        if declared is not None:
            statement_type = declared
        elif inferred is not None:
            statement_type = inferred
        elif signed_by:
            statement_type = CONFIRMED
        elif forward:
            statement_type = PROPOSED
        else:
            statement_type = REPORTED

        # 没有签署的既述文字不能仅凭“已确认”字样变成患者指令。
        if declared is None and inferred == CONFIRMED and not signed_by:
            statement_type = PROPOSED if forward else REPORTED
        # 没有原文就无法核对，也不能据此制作患者沟通。
        if not text:
            statement_type = REPORTED

        finding = {
            'source': source,
            'kind': kind,
            'statement_type': statement_type,
            'declared_type': declared,
            'text': text,
        }
        accepted = authority.accepts(finding)
        entry = Entry(
            raw=raw,
            order=order,
            source=source,
            kind=kind,
            subject=subject,
            documented=documented,
            revision=revision,
            text=text,
            statement_type=statement_type,
            signed_by=signed_by,
            artifact_kind=artifact_kind,
            accepted=accepted,
            declared_type=declared,
            references=_references(raw),
            identities=_entry_identities(raw),
            selectors=_entry_identities(raw, _SELECTOR_IDENTITY_KEYS),
            forward=forward,
        )
        if statement_type == REPORTED:
            entry.held_reason = '转述内容，未形成已确认安排'
        elif statement_type == PROPOSED:
            entry.held_reason = '拟议内容，尚未确认'
        elif not accepted:
            entry.held_reason = authority.scope_reason(finding)
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# 事项归并
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def add(self, item):
        self.parent.setdefault(item, item)

    def find(self, item):
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            nxt = self.parent[item]
            self.parent[item] = root
            item = nxt
        return root

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _base_matter_key(entry):
    raw = entry.raw
    explicit = raw.get('matter') or raw.get('matter_id')
    appointment = raw.get('appointment_id') or raw.get('appointment')
    if explicit:
        return str(explicit)
    if appointment and entry.subject:
        return f'{entry.subject}|{appointment}'
    if appointment:
        return str(appointment)
    if entry.subject:
        return entry.subject
    return f'事项{entry.order + 1}'


def _plan_matters(entries):
    """按事项归并条目；原计划、改期编号等别名指向同一事项。"""
    union = _UnionFind()
    alias_targets = defaultdict(set)
    root_kind = {}

    for entry in entries:
        entry.matter = _base_matter_key(entry)
        union.add(entry.matter)
        if entry.kind:
            root_kind.setdefault(entry.matter, entry.kind)
        alias_targets[entry.matter].add(entry.matter)
        for identity in entry.identities:
            alias_targets[identity].add(entry.matter)
        if entry.source:
            alias_targets[entry.source].add(entry.matter)

    def absorb(root, kind):
        if kind and not root_kind.get(root):
            root_kind[root] = kind

    # 引用关系可能串联（新预约同时引用原计划和改期编号），反复归并至稳定。
    changed = True
    while changed:
        changed = False
        for entry in entries:
            if not entry.references:
                continue
            own_root = union.find(entry.matter)
            roots = set()
            for reference in entry.references:
                for target in alias_targets.get(reference, ()):
                    roots.add(union.find(target))
            # 条目引用自己的编号属于自我指涉，不构成跨事项歧义。
            others = roots - {own_root}
            if len(roots) == 1:
                root = next(iter(roots))
                if own_root != root:
                    union.union(entry.matter, root)
                    absorb(union.find(entry.matter), entry.kind)
                    changed = True
            elif len(others) == 1:
                union.union(entry.matter, next(iter(others)))
                absorb(union.find(entry.matter), entry.kind)
                changed = True
            elif len(others) > 1:
                entry.ambiguous_refs = sorted(others)
                # 语句自身写明的事项类型可确定“相应事项”，但仍不据此选择
                # 承接的版本；后续承接比对会把该事项交给协调员核实。
                compatible = {
                    root for root in others
                    if not entry.kind or not root_kind.get(root)
                    or root_kind.get(root) == entry.kind
                }
                if len(compatible) == 1:
                    union.union(entry.matter, next(iter(compatible)))
                    absorb(union.find(entry.matter), entry.kind)
                    changed = True

    return union, alias_targets


# ---------------------------------------------------------------------------
# 承接关系与当前版本
# ---------------------------------------------------------------------------


def _resolve_carry_forward(entries, union):
    """解析“继续此前已经确认的安排”承接的版本和事项。

    只有已签署且属于可核对事项的条目才进入承接比对；转述和拟议文字不会
    被拿来当作患者指令，晚录入的入院历史也不会覆盖签署安排。
    """
    base = [
        entry for entry in entries
        if entry.statement_type == CONFIRMED and entry.accepted and not entry.forward
    ]
    for entry in base:
        entry.resolved_text = entry.text

    forwards = sorted(
        (entry for entry in entries
         if entry.statement_type == CONFIRMED and entry.accepted and entry.forward),
        key=lambda item: item.sort_key,
    )
    resolved_forwards = []

    for entry in forwards:
        if len(entry.ambiguous_refs) > 1:
            # 引用同时指向多个事项：可凭语句本身的事项类型确定“相应事项”，
            # 但仍无法确定承接哪一份已确认记录，只能交协调员核实。
            entry.review_reason = '签署语句无法确定承接哪一版已确认记录'
            continue

        pool = [item for item in [*base, *resolved_forwards] if item.sort_key < entry.sort_key]
        matched = []

        # 引用编号（原计划、改期、预约）优先于事项名称；同名复诊可能有多项。
        selectors = _unique([*entry.references, *entry.selectors])
        if selectors:
            matched = [
                item for item in pool
                if any(selector in item.selector_aliases() for selector in selectors)
            ]
            matched_by = 'reference'
            if not matched:
                entry.review_reason = '签署语句未找到可承接的已确认记录'
                continue
        elif entry.subject:
            matched = [item for item in pool if item.subject == entry.subject]
            matched_by = 'matter'
        else:
            matched = list(pool)
            matched_by = 'prior_records'

        if not matched:
            entry.review_reason = '签署语句未找到可承接的已确认记录'
            continue

        # 承接对象必须唯一落在同一事项；同一事项的多个版本按时间顺序取当前版本。
        matters = {union.find(item.matter) for item in matched}
        if len(matters) > 1:
            entry.review_reason = '签署语句无法确定承接哪一版已确认记录'
            continue
        if len(matched) > 1:
            matched_by = (
                'latest_confirmed_version'
                if len({substantive_key(item.resolved_text) for item in matched}) > 1
                else 'consistent_confirmed_records'
            )

        target = max(matched, key=lambda item: item.sort_key)
        entry.resolved_text = target.resolved_text
        entry.resolution = {
            'source': target.source,
            'subject': target.subject,
            'matter': target.matter,
            'text': target.resolved_text,
            'documented': target.documented,
            'revision': target.revision,
            'version': target.revision,
            'signed_by': target.signed_by,
            'matched_by': matched_by,
        }
        if not entry.subject:
            entry.subject = target.subject
        union.union(entry.matter, target.matter)
        resolved_forwards.append(entry)


def _choose_current(group):
    """选出当前有效的已确认版本；较新的未解析签署语句会阻止发出。"""
    accepted = [
        entry for entry in group
        if entry.statement_type == CONFIRMED and entry.accepted
    ]
    resolved = [entry for entry in accepted if entry.resolved_text is not None]
    unresolved = [entry for entry in accepted if entry.resolved_text is None and entry.review_reason]

    chosen = max(resolved, key=lambda item: item.sort_key) if resolved else None
    if unresolved:
        latest_unresolved = max(unresolved, key=lambda item: item.sort_key)
        if chosen is None or latest_unresolved.sort_key > chosen.sort_key:
            return MatterDecision(None, latest_unresolved)
    return MatterDecision(chosen, None)


def _matter_subject(group):
    for entry in sorted(group, key=lambda item: item.order):
        if entry.subject:
            return entry.subject
    return '未命名事项'


# ---------------------------------------------------------------------------
# 已发提醒匹配
# ---------------------------------------------------------------------------


def _sent_index(sent_items):
    by_matter = defaultdict(list)
    by_subject = defaultdict(list)
    for item in sent_items or []:
        if not isinstance(item, dict):
            continue
        value = dict(item)
        for key in ('matter', 'matter_id', 'appointment_id', 'appointment'):
            if value.get(key):
                by_matter[str(value[key])].append(value)
        subject = value.get('subject')
        if subject:
            by_subject[str(subject)].append(value)
    return by_matter, by_subject


def _sent_sort_key(item):
    for key in ('documented', 'date', 'sent_at', 'sent', 'time'):
        value = item.get(key)
        if value:
            return (*_sort_date(str(value)), str(item.get('id', '')))
    return (*_sort_date(''), str(item.get('id', '')))


def _match_sent(group, subject, by_matter, by_subject, subject_shared):
    aliases = set()
    for entry in group:
        aliases.add(entry.matter)
        aliases.update(entry.identities)
        aliases.update(entry.references)
        if entry.subject:
            aliases.add(entry.subject)
    items = []
    for alias in aliases:
        items.extend(by_matter.get(alias, []))
    if not items and subject and not subject_shared.get(subject, False):
        items = list(by_subject.get(subject, []))
    deduped = {}
    for index, item in enumerate(items):
        deduped.setdefault(str(item.get('id', index)), item)
    ordered = sorted(deduped.values(), key=_sent_sort_key)
    return SentMatch(items=ordered, earlier=ordered[-1] if ordered else None)


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def _build_item(root, group, decision, by_matter, by_subject, subject_shared):
    chosen, unresolved = decision
    subject = _matter_subject(group)
    matter = root
    if chosen is not None and chosen.resolution and chosen.resolution.get('subject'):
        subject = chosen.resolution['subject']

    sent = _match_sent(group, subject, by_matter, by_subject, subject_shared)
    earlier = sent.earlier

    blocking = unresolved is not None
    held = [
        entry for entry in group
        if entry is not chosen and entry is not unresolved
        and not (entry.statement_type == CONFIRMED and entry.accepted
                 and entry.resolved_text is not None)
    ]
    held.sort(key=lambda item: item.sort_key)

    if blocking:
        latest = None
        action = REVIEW
        review_reason = unresolved.review_reason or '签署语句无法确定承接哪份已确认记录'
    elif chosen is not None:
        latest = chosen.resolved_text
        earlier_text = earlier.get('text') if earlier else None
        if earlier is None:
            action = PREPARE
        elif _equivalent(earlier_text, latest):
            action = NOTIFIED
        else:
            action = SUPPLEMENT
        review_reason = None
    else:
        latest = None
        action = WAITING
        review_reason = None

    change = None
    if action == SUPPLEMENT and earlier is not None:
        change = describe_change(earlier.get('text', ''), latest)

    confirmed_entries = [
        entry for entry in group
        if entry.statement_type == CONFIRMED and entry.accepted
        and entry.resolved_text is not None
    ]
    duplicate_count = 0
    equivalent_count = 0
    if chosen is not None:
        equivalent_count = sum(
            1 for entry in confirmed_entries
            if _equivalent(entry.resolved_text, chosen.resolved_text)
        )
        duplicate_count = max(0, equivalent_count - 1)

    materials = []
    for entry in sorted(group, key=lambda item: item.sort_key):
        if entry is chosen:
            status = '当前沟通依据'
        elif entry is unresolved:
            status = '待协调员核实'
        elif entry.statement_type == CONFIRMED and entry.accepted and entry.resolved_text is not None:
            status = '历史已确认版本'
        elif entry.statement_type == CONFIRMED and entry.accepted and entry.review_reason:
            status = '待核实但不影响后续版本'
        elif entry.held_reason:
            status = entry.held_reason
        else:
            status = '未采用'
        materials.append(entry.material(status))

    signed_entry = chosen or unresolved
    return {
        'subject': subject,
        'matter': matter,
        'kind': chosen.kind if chosen else (unresolved.kind if unresolved else (group[0].kind if group else '')),
        'latest': latest,
        'earlier': earlier,
        'action': action,
        'status': action,
        'communication_status': action,
        'held_sources': _unique([entry.source for entry in held]),
        'held': [
            {
                'source': entry.source,
                'text': entry.text,
                'documented': entry.documented,
                'statement_type': entry.statement_type,
                'signed_by': entry.signed_by,
                'reason': entry.held_reason or entry.review_reason or '未采用',
            }
            for entry in held
        ],
        'needs_review': blocking,
        'review': {'required': blocking, 'reason': review_reason},
        'review_required': blocking,
        'review_reason': review_reason,
        'needs_coordinator': blocking,
        'original': signed_entry.text if signed_entry else None,
        'source': signed_entry.source if signed_entry else None,
        'signed_by': signed_entry.signed_by if signed_entry else None,
        'signature': signed_entry.signed_by if signed_entry else None,
        'documented': signed_entry.documented if signed_entry else None,
        'revision': signed_entry.revision if signed_entry else None,
        'statement_type': signed_entry.statement_type if signed_entry else None,
        'resolution': chosen.resolution if chosen and chosen.resolution else (
            unresolved.resolution if unresolved else None
        ),
        'change': change,
        'change_summary': change['summary'] if change else None,
        'substantive_change': change,
        'sent': sent.items,
        'reminders': sent.items,
        'confirmation_count': len(confirmed_entries),
        'duplicate_confirmations': duplicate_count,
        'equivalent_confirmations': equivalent_count,
        'materials': materials,
    }


def assess(case, registry):
    """核对每个事项，返回可直接检查的沟通状态。

    结果保留原有的 subject/latest/earlier/action/held_sources 字段，并补充
    原句、签署、承接版本、实质变化和核实标记。
    """
    registry = registry or {}
    authority = SignedInstructions(registry.get('artifacts', []))
    entries = _normalize_entries(case or {}, authority)
    union, _ = _plan_matters(entries)

    _resolve_carry_forward(entries, union)

    groups = {}
    for entry in entries:
        groups.setdefault(union.find(entry.matter), []).append(entry)

    decisions = {root: _choose_current(group) for root, group in groups.items()}
    by_matter, by_subject = _sent_index(registry.get('sent', []))

    subject_counts = defaultdict(int)
    for group in groups.values():
        subject_counts[_matter_subject(group)] += 1
    subject_shared = {subject: count > 1 for subject, count in subject_counts.items()}

    output = [
        _build_item(root, group, decisions[root], by_matter, by_subject, subject_shared)
        for root, group in groups.items()
    ]
    output.sort(key=lambda item: (item['subject'], item['matter']))
    return output
