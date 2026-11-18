from pathlib import Path

from nlp_clinical_event_reconciliation.desk_cli import load_case


def test_unsigned_history_cannot_override_signed_handover():
    fixture = Path(__file__).parents[1] / 'fixtures'
    output = load_case(fixture / 'work.json', fixture / 'business.json')
    assert output[0]['earlier']['text'] == '6 月 8 日门诊复诊'
    assert output[0]['action'] == '需要补充通知'
    assert output[1]['latest'] == '医师已签署用药条目A'
    assert output[1]['held_sources'] == ['late-intake']
