import json
from pathlib import Path

def load_facts(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def summarize(facts):
    signed = {}
    for article in facts['artifacts']:
        if article['signed_by']:
            signed.setdefault(article['kind'], []).append(article['id'])
    return signed
