import argparse
import json
from pathlib import Path

from .desk import assess


def load_case(case_path, registry_path):
    case = json.loads(Path(case_path).read_text(encoding='utf-8'))
    registry = json.loads(Path(registry_path).read_text(encoding='utf-8'))
    if not isinstance(case, dict) or not isinstance(registry, dict):
        raise ValueError('案例和登记簿必须是 JSON 对象')
    return assess(case, registry)


def main():
    parser = argparse.ArgumentParser(description='核对合成出院交接，只报告已记录的决定')
    parser.add_argument('--case', type=Path, default=Path('fixtures/work.json'))
    parser.add_argument('--registry', type=Path, default=Path('fixtures/business.json'))
    args = parser.parse_args()
    try:
        result = load_case(args.case, args.registry)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
