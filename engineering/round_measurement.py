"""Project-selected current measurement, displayed without physical permission."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from engineering import ina226_protocol as P
from engineering.facts import FactManifest, FactStatus
from engineering.instructions import markdown

ROOT = Path(__file__).resolve().parents[1]
DRAFT = 'engineering/measurements/round_current_draft.json'
PROTOCOL = 'engineering/INA226_MEASUREMENT_PROTOCOL.md'


def selected_strategy(root=ROOT):
    fact = FactManifest.from_path(Path(root) / 'cad/prototype_parameters.json').resolve(
        'bench_current_measurement_strategy', expected_unit='enum',
        allowed_provenance={'REQUIREMENT'}, require_datum=False, expected_scope=None)
    if fact.status != FactStatus.READY or fact.value != 'ina226_on_bus':
        raise ValueError('CURRENT_MEASUREMENT_STRATEGY: INA226 selection requires a current REQUIREMENT fact')
    return fact.value


def load_record(root=ROOT):
    return json.loads((Path(root) / DRAFT).read_text(encoding='utf-8'))


def status(root=ROOT):
    return {'strategy': selected_strategy(root), 'measurement': P.inspect(root, load_record(root))}


def guide_section(root=ROOT):
    view = status(root)
    result = view['measurement']
    issues = ''.join('<li>' + html.escape(item) + '</li>' for item in result['errors'] + result['missing'])
    content = (f'<p>Подготовительная запись измерений: <strong>{result["state"]}</strong>; '
               'physical_approval: false. Установка и силовые измерения этой записью не подтверждены.</p>'
               '<details><summary>Что ещё требуется получить</summary><ul>' + issues + '</ul></details>'
               + markdown.render((Path(root) / PROTOCOL).read_text(encoding='utf-8')))
    return {'heading': 'Физическая итерация и измерение тока INA226', 'html': content}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, help='Inspect another measurement record under the repository root')
    args = parser.parse_args()
    strategy = selected_strategy()
    record = json.loads(args.record.read_text(encoding='utf-8')) if args.record else load_record()
    result = P.inspect(ROOT, record)
    print(json.dumps({'strategy': strategy, 'measurement': result}, ensure_ascii=False, indent=2))
    return {'REVIEW_REQUIRED': 0, 'INCOMPLETE': 2, 'INVALID': 3}[result['state']]


if __name__ == '__main__':
    raise SystemExit(main())
