"""Place a client with the questionnaire and (optionally) write the stored client file.

Examples, from the second_look directory:
    python scripts/place_client.py --persona robert
    python scripts/place_client.py --persona all
    python scripts/place_client.py answers.json --write

answers.json shape (holdings in units, cash in CAD; everything fictional):
{
  "client_id": "newclient", "name": "New Client", "age": 35,
  "answers": {"horizon_years": 12, "capacity": "moderate", "willingness": "moderate",
              "objective": "balanced", "experience": "basic"},
  "cash_cad": 10000, "holdings": {"XBB.TO": 300, "SPY": 10}, "holdings_as_of": "2026-11-13",
  "preferences": {"excluded_tickers": [], "excluded_sectors": []}
}
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from second_look.questionnaire import PERSONA_ANSWERS, QUESTIONS, build_client, place_investor  # noqa: E402


def show(label, placement):
    print(f'\n{label}: {placement["profile"]}')
    print('  scores:', ', '.join(f'{k}={v}' for k, v in placement['scores'].items()),
          f'| total {placement["total"]} -> band {placement["band_profile"]}')
    print('  caps:  ', ', '.join(f'{k}={v}' for k, v in placement['caps'].items()),
          '| applied:', ', '.join(placement['caps_applied']) or 'none')
    print('  ', placement['explanation'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('answers_file', nargs='?', help='JSON file with client_id, name, age, answers, holdings...')
    parser.add_argument('--persona', choices=[*PERSONA_ANSWERS, 'all'], help='show a persona placement')
    parser.add_argument('--write', action='store_true', help='write examples/clients/<client_id>.json')
    parser.add_argument('--questions', action='store_true', help='print the questionnaire')
    args = parser.parse_args()

    if args.questions:
        for q in QUESTIONS:
            print(f'\n[{q["key"]}] {q["text"]}')
            if q['kind'] == 'years':
                print('   a number of years; scored as:', q['labels'])
            else:
                for code, label in q['labels'].items():
                    print(f'   {code:12s} {label}')
        return
    if args.persona:
        names = list(PERSONA_ANSWERS) if args.persona == 'all' else [args.persona]
        for n in names:
            show(n, place_investor(PERSONA_ANSWERS[n]))
        return
    if not args.answers_file:
        parser.error('give an answers file, --persona or --questions')

    spec = json.loads(Path(args.answers_file).read_text(encoding='utf-8'))
    client = build_client(spec['client_id'], spec['name'], spec['age'], spec['answers'],
                          spec['cash_cad'], spec['holdings'], spec['holdings_as_of'],
                          spec.get('preferences'), spec.get('fictional', True),
                          goals=spec.get('goals'), income=spec.get('income'))
    show(client['client_id'], place_investor(spec['answers']))
    if args.write:
        out = ROOT / 'examples' / 'clients' / f'{client["client_id"]}.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(client, indent=2) + '\n', encoding='utf-8')
        print('\nwrote', out.relative_to(ROOT))
    else:
        print('\n(dry run; add --write to store the client)')


if __name__ == '__main__':
    main()
