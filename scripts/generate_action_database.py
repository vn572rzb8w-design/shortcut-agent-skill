#!/usr/bin/env python3
"""Export ShortcutKit's engine-derived catalogue for grounded AI planning."""
import json
import sys
from pathlib import Path
from shortcutkit import ACTIONS, PARAM_KINDS, get_action

OUTPUT = Path('data/actions.json')


def generate():
    rows = []
    for identifier, info in sorted(ACTIONS.items()):
        definition = get_action(identifier).get('definition') or {}
        labels = {p['Key']: p.get('Label') for p in definition.get('Parameters', [])
                  if isinstance(p, dict) and p.get('Key')}
        for label, meta in definition.get('ParameterOverrides', {}).items():
            if isinstance(meta, dict) and meta.get('Key'):
                labels.setdefault(meta['Key'], meta.get('Label') or label)
        params = [{'key': key, 'label': labels.get(key), 'kind': PARAM_KINDS.get(identifier, {}).get(key, 'any')}
                  for key in info.get('params', [])]
        rows.append({'identifier': identifier, 'name': info.get('name'),
                     'keywords': definition.get('ActionKeywords', ''),
                     'description': (definition.get('Description') or {}).get('DescriptionSummary', ''),
                     'parameters': params, 'output': info.get('output'),
                     'output_types': info.get('outputTypes', []),
                     'app_intent': bool(info.get('descriptor'))})
    return {'generated_from': 'ShortcutKit WorkflowKit/ActionKit and App Intents index',
            'action_count': len(rows), 'actions': rows}


def main():
    payload = generate()
    if len(sys.argv) > 1 and sys.argv[1] == '--search':
        query = ' '.join(sys.argv[2:]).lower().split()
        def score(row):
            name = (row['name'] or '').lower()
            body = ' '.join((name, row['identifier'].lower(), row['keywords'].lower(), row['description'].lower()))
            return sum(10 if word in name else 1 for word in query if word in body)
        matches = sorted((r for r in payload['actions'] if score(r)), key=score, reverse=True)
        print(json.dumps(matches[:20], ensure_ascii=False, indent=2))
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Wrote {payload['action_count']} actions with parameter schemas to {OUTPUT}")


if __name__ == '__main__':
    main()
