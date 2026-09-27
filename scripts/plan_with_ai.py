#!/usr/bin/env python3
"""Plan natural-language shortcuts with live ShortcutKit metadata as a search tool.

Set OPENAI_API_KEY. The resulting semantic blueprint is validated by
build_shortcut.py before serialization and signing. Model output is untrusted.
"""
import json
import os
import sys
from functools import lru_cache
from scripts.generate_action_database import generate

INSTRUCTIONS = '''Translate the user's Apple Shortcut request into a semantic JSON blueprint.
First search the action catalogue as often as needed. Use ONLY identifiers and parameter keys
returned by search_actions. Do not infer native or third-party identifiers. If metadata cannot
establish a needed parameter encoding, say so via an error rather than inventing values.
Output JSON: {"actions":[{"action":"exact identifier or unique title", "id":"optional output id",
"parameters":{"exact key or unambiguous label":"value"}}, ...]}.
References: {"$ref":"earlier action id"}, named variable {"$variable":"name"}, interpolated
text {"$text":["literal",{"$ref":"id"}]}. Control blocks are nested:
{"if":{"input":{"$ref":"id"},"condition":"contains","value":"word"},"then":[...],"else":[...]};
{"repeat":{"items":{"$ref":"id"}},"actions":[...]}; {"repeat":{"count":3},"actions":[...]};
{"menu":{"prompt":"Choose","options":{"First":[...],"Second":[...]}}}.
Use Set Variable only when named storage is requested. Any app intent must be catalogued.
No raw plist and no prefilled fake output. Return only a JSON object.''' 


@lru_cache(maxsize=1)
def catalogue():
    return generate()['actions']


def search_actions(query, limit=12):
    words = query.lower().split()
    rows = catalogue()
    def score(row):
        title = (row['name'] or '').lower()
        body = ' '.join((title, row['identifier'].lower(), row['keywords'].lower(), row['description'].lower()))
        return sum(12 if word in title else 2 if word in body else 0 for word in words)
    results = sorted((r for r in rows if score(r)), key=lambda r: (-score(r), r['identifier']))
    return results[:min(max(int(limit), 1), 20)]


def plan(description, client=None):
    if client is None:
        if not os.environ.get('OPENAI_API_KEY'):
            raise ValueError('Natural-language planning requires OPENAI_API_KEY; JSON blueprints and the built-in integration fixture do not.')
        from openai import OpenAI
        client = OpenAI()
    tools = [{'type':'function','name':'search_actions',
              'description':'Search the engine-derived Apple Shortcuts and App Intents catalogue by a short query. Return exact parameter keys, types, and action identifiers.',
              'parameters':{'type':'object','properties':{'query':{'type':'string'},'limit':{'type':'integer'}},'required':['query'],'additionalProperties':False}}]
    history = [{'role':'user','content':description}]
    for _ in range(12):
        response = client.responses.create(model=os.getenv('SHORTCUT_PLANNER_MODEL', 'gpt-4.1'),
                                           instructions=INSTRUCTIONS, input=history, tools=tools, store=False)
        calls = [item for item in response.output if item.type == 'function_call']
        if not calls:
            result = json.loads(response.output_text)
            if not isinstance(result, dict) or not isinstance(result.get('actions'), list) or not result['actions']:
                raise ValueError('Planner did not return a nonempty actions blueprint')
            return result
        output = []
        for call in calls:
            if call.name != 'search_actions':
                raise ValueError(f'Unexpected planner tool: {call.name}')
            args = json.loads(call.arguments)
            output.append({'type':'function_call_output','call_id':call.call_id,
                           'output':json.dumps(search_actions(args['query'], args.get('limit',12)))})
        history.extend(item.model_dump(exclude_none=True) for item in response.output)
        history.extend(output)
    raise ValueError('Planner exceeded 12 metadata searches without a blueprint')


if __name__ == '__main__':
    print(json.dumps(plan(' '.join(sys.argv[1:])), ensure_ascii=False))
