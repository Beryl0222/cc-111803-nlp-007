class SignedInstructions:
    def __init__(self, artifacts):
        self.artifacts = {artifact['id']: artifact for artifact in artifacts}

    def accepts(self, finding):
        artifact = self.artifacts[finding['source']]
        if finding['kind'] == '用药交接':
            return artifact['kind'] == '出院沟通' and artifact['signed_by'] == '医师'
        return finding['kind'] == '复诊交接' and (artifact['kind'], artifact['signed_by']) in {
            ('出院沟通', '医师'), ('预约回执', '门诊协调员'),
        }


class CurrentHandover:
    def __init__(self, authority):
        self.authority = authority

    def choose(self, entries):
        current = {}
        held = {}
        for entry in sorted(entries, key=lambda item: item['documented']):
            subject = entry['subject']
            if self.authority.accepts(entry):
                current[subject] = entry
            else:
                held.setdefault(subject, []).append(entry['source'])
        return current, held


class PatientCommunications:
    def __init__(self, sent):
        self.sent = {item['subject']: item for item in sent}

    def plan(self, current, held):
        output = []
        for subject in sorted(current.keys() | held.keys()):
            decision = current.get(subject)
            latest = decision['text'] if decision else None
            earlier = self.sent.get(subject)
            if latest is None:
                action = '等待签署依据'
            elif earlier is None:
                action = '制作交接'
            elif latest != earlier['text']:
                action = '需要补充通知'
            else:
                action = '已通知'
            output.append({'subject': subject, 'latest': latest, 'earlier': earlier, 'action': action,
                           'held_sources': held.get(subject, [])})
        return output


def assess(case, registry):
    handover = CurrentHandover(SignedInstructions(registry['artifacts']))
    return PatientCommunications(registry['sent']).plan(*handover.choose(case['entries']))
