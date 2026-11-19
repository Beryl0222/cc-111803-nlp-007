"""覆盖交接核对中的业务边界。"""

from nlp_clinical_event_reconciliation.desk import assess


def registry(**overrides):
    value = {
        'artifacts': [
            {'id': 'discharge', 'kind': '出院沟通', 'signed_by': '医师'},
            {'id': 'late-intake', 'kind': '入院记录', 'signed_by': None},
            {'id': 'appt-change', 'kind': '预约回执', 'signed_by': '门诊协调员'},
            {'id': 'ward-note', 'kind': '病房讨论', 'signed_by': None},
            {'id': 'coordinator-note', 'kind': '护理记录', 'signed_by': '门诊协调员'},
        ],
        'sent': [],
    }
    value.update(overrides)
    return value


def entry(source, kind, subject, documented, text, **extra):
    return {'source': source, 'kind': kind, 'subject': subject,
            'documented': documented, 'text': text, **extra}


def test_three_phrasings_are_not_concatenated_into_patient_instruction():
    case = {
        'entries': [
            entry('late-intake', '用药交接', '用药条目A', '2026-06-01',
                  '患者以为仍在用用药条目A'),
            entry('ward-note', '用药交接', '用药条目A', '2026-06-02',
                  '讨论：考虑暂停用药条目A，仍等出院医生确认'),
            entry('discharge', '用药交接', '用药条目A', '2026-06-03',
                  '继续此前已经确认的安排'),
        ]
    }
    output = assess(case, registry())
    assert len(output) == 1
    item = output[0]
    # 若三条按日期拼接就会把讨论中的“暂停”发出去；这里必须不动。
    assert item['action'] == '等待签署依据' or item['needs_review']
    assert item['held_sources'] == ['late-intake', 'ward-note']
    assert all(row['statement_type'] != '已确认' for row in item['held'])
    assert {row['reason'] for row in item['held']} >= {'转述内容，未形成已确认安排', '拟议内容，尚未确认'}


def test_forward_reference_without_prior_record_is_not_issued():
    case = {'entries': [entry('discharge', '用药交接', '用药条目A', '2026-06-03',
                              '继续此前已经确认的安排')]}
    item = assess(case, registry())[0]
    assert item['action'] == '需要核实'
    assert item['latest'] is None
    assert item['needs_review'] is True
    assert item['materials'][0]['status'] == '待协调员核实'


def test_forward_reference_resolves_to_earliest_confirmed_version_and_matter():
    case = {
        'entries': [
            entry('discharge', '用药交接', '用药条目A', '2026-06-01',
                  '医师确认继续用药条目A每日一次'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-02',
                  '6 月 10 日门诊复诊'),
            entry('discharge2', '用药交接', '用药条目A', '2026-06-03',
                  '继续此前已经确认的安排'),
        ]
    }
    custom = registry()
    custom['artifacts'].append({'id': 'discharge2', 'kind': '出院沟通', 'signed_by': '医师'})
    output = assess(case, custom)
    medication = next(item for item in output if item['subject'] == '用药条目A')
    assert medication['latest'] == '医师确认继续用药条目A每日一次'
    assert medication['resolution']['source'] == 'discharge'
    assert medication['resolution']['text'] == '医师确认继续用药条目A每日一次'
    # 复诊事项不应被这条用药语句承接。
    follow_up = next(item for item in output if item['subject'] == '复诊')
    assert follow_up['latest'] == '6 月 10 日门诊复诊'


def test_ambiguous_forward_reference_sends_only_that_matter_for_review():
    case = {
        'entries': [
            entry('discharge', '用药交接', '用药条目A', '2026-06-01',
                  '医师确认继续用药条目A'),
            entry('discharge', '用药交接', '用药条目B', '2026-06-01',
                  '医师确认继续用药条目B'),
            entry('discharge2', '用药交接', '用药条目C', '2026-06-02',
                  '继续此前已经确认的安排'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-03', '6 月 10 日门诊复诊'),
        ]
    }
    custom = registry()
    custom['artifacts'].append({'id': 'discharge2', 'kind': '出院沟通', 'signed_by': '医师'})
    output = assess(case, custom)
    review = [item for item in output if item['needs_review']]
    assert len(review) == 1
    assert review[0]['action'] == '需要核实'
    assert review[0]['latest'] is None
    # 复诊事项照常可用。
    follow_up = next(item for item in output if item['subject'] == '复诊')
    assert follow_up['action'] == '制作交接'


def test_receipt_updates_its_own_follow_up_only():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-02', '6 月 10 日门诊复诊'),
            entry('discharge', '复诊交接', '口腔复诊', '2026-06-02', '7 月 1 日口腔复诊'),
            entry('appt-change', '用药交接', '用药条目A', '2026-06-03', '改为每日两次'),
        ]
    }
    custom = registry(sent=[{'id': 'sms-21', 'subject': '复诊', 'text': '6 月 8 日门诊复诊',
                             'from': 'discharge', 'documented': '2026-06-01'}])
    output = assess(case, custom)
    follow_up = next(item for item in output if item['subject'] == '复诊')
    assert follow_up['latest'] == '6 月 10 日门诊复诊'
    assert follow_up['action'] == '需要补充通知'
    assert follow_up['change']['from'] == '6 月 8 日'
    assert follow_up['change']['to'] == '6 月 10 日'
    mouth = next(item for item in output if item['subject'] == '口腔复诊')
    assert mouth['action'] == '制作交接'
    medication = next(item for item in output if item['subject'] == '用药条目A')
    assert medication['latest'] is None
    assert medication['action'] == '等待签署依据'
    assert '预约回执只能更新对应复诊' in medication['held'][0]['reason']


def test_new_appointment_referencing_original_plan_and_reschedule_merges():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊',
                  plan_id='plan-1', appointment_id='appt-1'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-02', '改至 6 月 10 日门诊复诊',
                  reschedule_id='change-1', references=['plan-1']),
            entry('appt-change', '复诊交接', '复诊', '2026-06-03', '6 月 10 日门诊复诊',
                  references=['plan-1', 'change-1']),
        ]
    }
    custom = registry(sent=[{'id': 'sms-21', 'subject': '复诊', 'text': '6 月 8 日门诊复诊',
                             'from': 'discharge', 'documented': '2026-06-01'}])
    output = assess(case, custom)
    assert len(output) == 1
    item = output[0]
    assert item['latest'] == '6 月 10 日门诊复诊'
    assert item['action'] == '需要补充通知'
    assert item['duplicate_confirmations'] == 1


def test_duplicate_confirmation_does_not_resend_notification():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 10 日门诊复诊'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-02', '6 月 10 日门诊复诊'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-03', '6 月 10 日门诊复诊'),
        ]
    }
    custom = registry(sent=[{'id': 'sms-21', 'subject': '复诊', 'text': '6 月 10 日门诊复诊',
                             'from': 'appt-change', 'documented': '2026-06-01'}])
    item = assess(case, custom)[0]
    assert item['action'] == '已通知'
    assert item['duplicate_confirmations'] == 2
    assert item['change'] is None


def test_sent_reminder_text_survives_and_shows_substantive_change():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-05', '6 月 10 日门诊复诊'),
        ]
    }
    custom = registry(sent=[{'id': 'sms-21', 'subject': '复诊', 'text': '6 月 8 日门诊复诊',
                             'from': 'discharge', 'documented': '2026-06-01'}])
    item = assess(case, custom)[0]
    assert item['action'] == '需要补充通知'
    assert item['earlier']['text'] == '6 月 8 日门诊复诊'
    assert item['change_summary'] == '时间由 6 月 8 日 改为 6 月 10 日'
    assert item['substantive_change']['substantive'] is True


def test_late_intake_history_cannot_override_signed_arrangement():
    case = {
        'entries': [
            entry('discharge', '用药交接', '用药条目A', '2026-06-01', '医师确认继续用药条目A'),
            entry('late-intake', '用药交接', '用药条目A', '2026-06-09',
                  '入院前清单称患者以为仍在用条目A'),
        ]
    }
    item = assess(case, registry())[0]
    assert item['latest'] == '医师确认继续用药条目A'
    assert item['action'] == '制作交接'
    assert item['held_sources'] == ['late-intake']
    # 仍能查见晚录入历史的原因，只是不覆盖签署安排。
    assert item['held'][0]['text'] == '入院前清单称患者以为仍在用条目A'
    assert item['held'][0]['statement_type'] == '转述'


def test_materials_show_original_sentence_signature_and_status():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-02', '6 月 10 日门诊复诊'),
            entry('late-intake', '复诊交接', '复诊', '2026-06-03', '入院前提到 6 月 9 日复诊'),
        ]
    }
    custom = registry(sent=[{'id': 'sms-21', 'subject': '复诊', 'text': '6 月 8 日门诊复诊',
                             'from': 'discharge'}])
    item = assess(case, custom)[0]
    statuses = {row['source']: row['status'] for row in item['materials']}
    assert statuses['discharge'] == '历史已确认版本'
    assert statuses['appt-change'] == '当前沟通依据'
    assert statuses['late-intake'] == '转述内容，未形成已确认安排'
    for row in item['materials']:
        assert {'source', 'text', 'signed_by', 'status'} <= set(row)


def test_carry_forward_uses_signed_confirmed_version_not_discussion():
    case = {
        'entries': [
            entry('late-intake', '用药交接', '用药条目A', '2026-06-01',
                  '入院前清单称患者以为仍在用用药条目A'),
            entry('ward-note', '用药交接', '用药条目A', '2026-06-02',
                  '讨论：考虑暂停用药条目A，仍等出院医生确认'),
            entry('discharge', '用药交接', '用药条目A', '2026-06-03',
                  '医师确认暂停用药条目A'),
            entry('discharge2', '用药交接', '用药条目A', '2026-06-04',
                  '继续此前已经确认的安排'),
        ]
    }
    custom = registry()
    custom['artifacts'].append({'id': 'discharge2', 'kind': '出院沟通', 'signed_by': '医师'})
    item = assess(case, custom)[0]
    assert item['latest'] == '医师确认暂停用药条目A'
    assert item['resolution']['source'] == 'discharge'
    assert item['action'] == '制作交接'
    assert item['held_sources'] == ['late-intake', 'ward-note']


def test_receipt_does_not_decide_a_different_follow_up_with_same_subject():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊',
                  appointment_id='appt-1', plan_id='plan-1'),
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '8 月 3 日骨科复诊',
                  appointment_id='appt-2', plan_id='plan-2'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-02', '6 月 10 日门诊复诊',
                  appointment_id='appt-1', references=['plan-1']),
        ]
    }
    output = assess(case, registry())
    assert len(output) == 2
    changed = next(item for item in output if item['latest'] == '6 月 10 日门诊复诊')
    untouched = next(item for item in output if item['latest'] == '8 月 3 日骨科复诊')
    assert changed['action'] == '制作交接'
    assert untouched['action'] == '制作交接'
    assert changed['matter'] != untouched['matter']


def test_forward_reference_does_not_leak_across_same_subject_appointments():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊',
                  appointment_id='appt-1', plan_id='plan-1'),
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '8 月 3 日骨科复诊',
                  appointment_id='appt-2', plan_id='plan-2'),
            entry('discharge2', '复诊交接', '复诊', '2026-06-02', '继续此前已经确认的安排',
                  appointment_id='appt-1', references=['plan-1']),
        ]
    }
    custom = registry()
    custom['artifacts'].append({'id': 'discharge2', 'kind': '出院沟通', 'signed_by': '医师'})
    output = assess(case, custom)
    assert len(output) == 2
    resolved = next(item for item in output if item['latest'] == '6 月 8 日门诊复诊'
                    and item['original'] == '继续此前已经确认的安排')
    assert resolved['resolution']['text'] == '6 月 8 日门诊复诊'
    assert resolved['resolution']['matched_by'] == 'reference'
    other = next(item for item in output if item['latest'] == '8 月 3 日骨科复诊')
    assert other['original'] == '8 月 3 日骨科复诊'


def test_record_ids_are_not_used_to_claim_a_prior_arrangement():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊',
                  record_id='r1'),
            entry('discharge2', '复诊交接', '复诊', '2026-06-02', '继续此前已经确认的安排',
                  record_id='r2'),
        ]
    }
    custom = registry()
    custom['artifacts'].append({'id': 'discharge2', 'kind': '出院沟通', 'signed_by': '医师'})
    item = assess(case, custom)[0]
    # 没有可核对的事项编号时只能按同名事项承接，不能凭记录编号认领。
    assert item['resolution']['matched_by'] == 'matter'


def test_unresolvable_latest_forward_reference_blocks_older_version_but_keeps_history():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2026-06-01', '6 月 8 日门诊复诊',
                  plan_id='plan-1'),
            entry('discharge', '用药交接', '用药条目A', '2026-06-02', '医师确认继续用药条目A',
                  plan_id='plan-2'),
            entry('discharge2', '复诊交接', '', '2026-06-05', '继续此前已经确认的安排',
                  references=['plan-1', 'plan-2']),
        ]
    }
    custom = registry(sent=[{'id': 'sms-21', 'subject': '复诊', 'text': '6 月 8 日门诊复诊',
                             'from': 'discharge'}])
    custom['artifacts'].append({'id': 'discharge2', 'kind': '出院沟通', 'signed_by': '医师'})
    item = next(row for row in assess(case, custom) if row['subject'] == '复诊')
    assert item['action'] == '需要核实'
    assert item['latest'] is None
    # 已发提醒原文仍可查。
    assert item['earlier']['text'] == '6 月 8 日门诊复诊'
    assert item['review_reason'] == '签署语句无法确定承接哪一版已确认记录'


def test_unknown_unit_does_not_treat_month_day_as_same_across_years():
    case = {
        'entries': [
            entry('discharge', '复诊交接', '复诊', '2025-06-10', '6 月 10 日门诊复诊'),
            entry('appt-change', '复诊交接', '复诊', '2026-06-10', '6 月 10 日门诊复诊'),
        ]
    }
    item = assess(case, registry())[0]
    assert item['latest'] == '6 月 10 日门诊复诊'
