#!/usr/bin/env python3

import json
import os
import plistlib
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from shortcutkit import Shortcut, actions, ACTIONS, PARAM_KINDS, ref, variable, text, picker
import re

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


HUBSIGN_URL = "https://hubsign.routinehub.services/sign"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "cherri/1.0",
    "Origin": "https://routinehub.co",
    "Referer": "https://routinehub.co/",
}


# ---------------------------------------------------------
# INPUT
# ---------------------------------------------------------

def strip_code_fence(text):
    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    return text


def parse_blueprint(description):
    candidate = strip_code_fence(description)

    if not candidate.startswith("{"):
        return None

    try:
        blueprint = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"AI blueprint is not valid JSON: {exc}"
        ) from exc

    if not isinstance(blueprint, dict):
        raise ValueError(
            "Blueprint root must be a JSON object"
        )

    action_specs = blueprint.get("actions")

    if not isinstance(action_specs, list) or not action_specs:
        raise ValueError(
            "Blueprint must contain a non-empty actions array"
        )

    return blueprint


# ---------------------------------------------------------
# ACTION RESOLUTION
# ---------------------------------------------------------

def build_action_lookup():
    """
    Build:
        Apple identifier -> ShortcutKit action identifier

    ShortcutKit exposes generated constants in actions.
    """

    lookup = {}

    for attr in dir(actions):
        if attr.startswith("_"):
            continue

        try:
            value = getattr(actions, attr)
        except Exception:
            continue

        if isinstance(value, str):
            lookup[value] = value

    return lookup


ACTION_LOOKUP = build_action_lookup()


def resolve_identifier(identifier):
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("Missing action identifier")

    identifier = identifier.strip()

    if identifier in ACTION_LOOKUP:
        return ACTION_LOOKUP[identifier]

    if identifier in ACTIONS:
        return identifier
    exact = [key for key, info in ACTIONS.items() if info.get('name', '').lower() == identifier.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        native = [key for key in exact if key == 'is.workflow.actions.' + identifier.lower().replace(' ', '')]
        if len(native) == 1:
            return native[0]
    names = {}
    for key, info in ACTIONS.items():
        for label in (info.get("name"), key.rsplit(".", 1)[-1]):
            if label:
                names.setdefault(re.sub(r"[^a-z0-9]", "", label.lower()), []).append(key)
    normalized = re.sub(r"[^a-z0-9]", "", identifier.lower())
    matches = list(dict.fromkeys(names.get(normalized, [])))
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(f"Ambiguous action {identifier!r}: {matches}. Use an exact identifier.")
    raise ValueError(f"Unknown action {identifier!r}. Third-party App Intents require imported metadata; arbitrary identifiers are unsafe.")


def resolve_parameter(identifier, key):
    info = ACTIONS[identifier]
    keys = set(info.get("params", [])) | set(PARAM_KINDS.get(identifier, {}))
    if key in keys:
        return key
    definition = __import__('shortcutkit').get_action(identifier)
    aliases = {}
    schema = (definition or {}).get('definition', {})
    for p in schema.get('Parameters', []):
        label = p.get('Label')
        if label and p.get('Key'):
            aliases.setdefault(re.sub(r'[^a-z0-9]', '', label.lower()), set()).add(p['Key'])
    for label, p in schema.get('ParameterOverrides', {}).items():
        if p.get('Key'):
            aliases.setdefault(re.sub(r'[^a-z0-9]', '', label.lower()), set()).add(p['Key'])
    candidates = aliases.get(re.sub(r'[^a-z0-9]', '', key.lower()), set())
    if len(candidates) == 1:
        return candidates.pop()
    raise ValueError(f"{identifier}: unknown or ambiguous parameter {key!r}; available keys: {sorted(keys)}")


def resolve_value(value, produced, names):
    if isinstance(value, dict):
        if '$ref' in value:
            target = value['$ref']
            if target not in produced:
                raise ValueError(f"Unknown or forward action reference {target!r}")
            return ref(produced[target])
        if '$variable' in value:
            target = value['$variable']
            if target not in names:
                raise ValueError(f"Named variable {target!r} has not been set")
            return variable(target)
        if '$text' in value:
            return text(*(resolve_value(part, produced, names) for part in value['$text']))
        return {k: resolve_value(v, produced, names) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v, produced, names) for v in value]
    return value


def dictionary_items(mapping, produced, names):
    """Encode a simple dictionary using Shortcuts' WFDictionaryFieldValue format."""
    encoded = []
    for key, item in mapping.items():
        if not isinstance(key, str):
            raise ValueError('Dictionary keys must be strings')
        if isinstance(item, bool):
            kind, content = 4, {'WFSerializationType': 'WFNumberSubstitutableState', 'Value': item}
        elif isinstance(item, (int, float)):
            kind, content = 3, text(str(item))
        elif isinstance(item, (list, dict)) and not any(k.startswith('$') for k in item if isinstance(k, str)):
            raise ValueError('Nested dictionary/array entries require an explicitly serialized WFValue; cannot safely infer their encoding')
        else:
            kind, content = 0, text(resolve_value(item, produced, names))
        encoded.append({'WFItemType': kind, 'WFKey': text(key), 'WFValue': content})
    return {'WFSerializationType': 'WFDictionaryFieldValue',
            'Value': {'WFDictionaryFieldValueItems': encoded}}


def validate_document(document, expected):
    def reject_null(value, path):
        if value is None:
            raise ValueError(f'{path}: null is not a valid XML plist parameter')
        if isinstance(value, dict):
            for key, child in value.items():
                reject_null(child, f'{path}.{key}')
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_null(child, f'{path}[{index}]')
    reject_null(document, 'workflow')
    entries = document.get('WFWorkflowActions')
    if not isinstance(entries, list) or len(entries) != expected or not entries:
        raise ValueError(f"Serialized action count mismatch: expected {expected}, got {len(entries) if isinstance(entries, list) else 'invalid'}")
    seen, groups, stack = set(), {}, []
    def walk(obj, current):
        if isinstance(obj, dict):
            if obj.get('Type') == 'ActionOutput':
                target = obj.get('OutputUUID')
                if target not in seen:
                    raise ValueError(f"Action {current}: dangling or forward OutputUUID {target}")
            for v in obj.values(): walk(v, current)
        elif isinstance(obj, list):
            for v in obj: walk(v, current)
    for index, entry in enumerate(entries, 1):
        identifier = entry.get('WFWorkflowActionIdentifier')
        if identifier not in ACTIONS:
            raise ValueError(f"Action {index}: unknown identifier {identifier}")
        params = entry.get('WFWorkflowActionParameters', {})
        uid = params.get('UUID')
        if not uid or uid in seen:
            raise ValueError(f"Action {index}: missing or duplicate UUID")
        walk(params, index)
        seen.add(uid)
        if 'WFControlFlowMode' in params:
            gid = params.get('GroupingIdentifier')
            if not gid or type(params['WFControlFlowMode']) is not int:
                raise ValueError(f"Action {index}: invalid control flow mode or group")
            groups.setdefault(gid, []).append((identifier, params['WFControlFlowMode']))
            mode = params['WFControlFlowMode']
            if mode == 0:
                stack.append(gid)
            elif not stack or stack[-1] != gid:
                raise ValueError(f"Action {index}: crossing or orphaned control-flow group {gid}")
            elif mode == 2:
                stack.pop()
            elif mode != 1:
                raise ValueError(f"Action {index}: invalid control-flow mode {mode}")
    if stack:
        raise ValueError(f"Unclosed control-flow groups: {stack}")
    for gid, members in groups.items():
        if members[0][1] != 0 or members[-1][1] != 2 or len({i for i, _ in members}) != 1:
            raise ValueError(f"Invalid control-flow group {gid}: {members}")
    return len(entries)


# ---------------------------------------------------------
# PARAMETER NORMALIZATION
# ---------------------------------------------------------

def normalize_parameters(parameters):
    if parameters is None:
        return {}

    if not isinstance(parameters, dict):
        raise ValueError(
            "Action parameters must be a JSON object"
        )

    return parameters


# ---------------------------------------------------------
# SHORTCUTKIT BUILDER
# ---------------------------------------------------------

def build_with_shortcutkit(name, blueprint):
    shortcut = Shortcut(name)
    produced, names = {}, set()
    def emit(specs):
        for spec in specs:
            if not isinstance(spec, dict):
                raise ValueError('Every action must be an object')
            if 'if' in spec:
                clause = spec['if']
                gid = shortcut.if_(resolve_value(clause['input'], produced, names), clause['condition'], clause.get('value'))
                emit(spec.get('then', []))
                if 'else' in spec:
                    shortcut.otherwise(gid)
                    emit(spec['else'])
                shortcut.end_if(gid)
            elif 'repeat' in spec:
                clause = spec['repeat']
                gid = (shortcut.repeat_each(resolve_value(clause['items'], produced, names)) if 'items' in clause
                       else shortcut.repeat_count(clause['count']))
                emit(spec.get('actions', []))
                end = shortcut.end_repeat_each(gid) if 'items' in clause else shortcut.end_repeat_count(gid)
                if 'id' in spec: produced[spec['id']] = end
            elif 'menu' in spec:
                clause = spec['menu']
                options = clause['options']
                gid = shortcut.choose_from_menu(clause.get('prompt', ''), list(options))
                for title, body in options.items():
                    shortcut.menu_item(gid, title)
                    emit(body)
                shortcut.end_menu(gid)
            else:
                identifier = resolve_identifier(spec.get('identifier') or spec.get('action'))
                raw = normalize_parameters(spec.get('parameters', {}))
                params = {resolve_parameter(identifier, k): resolve_value(v, produced, names) for k, v in raw.items()}
                if identifier == 'is.workflow.actions.dictionary' and isinstance(raw.get('Items'), dict) and 'WFSerializationType' not in raw['Items']:
                    params['WFItems'] = dictionary_items(raw['Items'], produced, names)
                try:
                    entry = shortcut.action(identifier, **params)
                except Exception as exc:
                    raise ValueError(f"Action {identifier} rejected: {exc}") from exc
                if 'id' in spec:
                    if spec['id'] in produced: raise ValueError(f"Duplicate action id {spec['id']}")
                    produced[spec['id']] = entry
                if identifier == 'is.workflow.actions.setvariable':
                    names.add(params.get('WFVariableName'))
    emit(blueprint['actions'])
    return shortcut


# ---------------------------------------------------------
# LEGACY PLAIN-TEXT TEST
# ---------------------------------------------------------

def integration_blueprint():
    return {'actions': [
            {'action': 'Ask for Input', 'id': 'answer', 'parameters': {'Input Type': 'Text', 'question': 'Enter text'}},
            {'action': 'Set Variable', 'parameters': {'Variable': 'Entered Text', 'Input': {'$ref': 'answer'}}},
            {'if': {'input': {'$variable': 'Entered Text'}, 'condition': 'contains', 'value': 'work'},
             'then': [{'action': 'Show Notification', 'parameters': {'Body': {'$text': ['Entered: ', {'$variable': 'Entered Text'}]}}}],
             'else': [{'action': 'Copy to Clipboard', 'parameters': {'Content': {'$variable': 'Entered Text'}}}]}
        ]}


def build_plain_text_shortcut(name, description):
    from scripts.plan_with_ai import plan
    def preflight(blueprint):
        trial = build_with_shortcutkit(name, blueprint)
        document = trial.to_plist()
        validate_document(document, len(trial.actions))
        plistlib.dumps(document, fmt=plistlib.FMT_XML)
    return build_with_shortcutkit(
        name, plan(description, validate=preflight)
    )


# ---------------------------------------------------------
# WRITE UNSIGNED FILE
# ---------------------------------------------------------

def write_unsigned(shortcut, output_path):
    """
    ShortcutKit write() works on Linux.

    We intentionally do NOT call Shortcut.sign(),
    because Apple's shortcuts sign command requires
    macOS + iCloud.
    """

    shortcut.write(output_path)

    if not os.path.exists(output_path):
        raise RuntimeError(
            "ShortcutKit did not create the unsigned file"
        )

    print(
        f"ShortcutKit unsigned file: {output_path}"
    )


# ---------------------------------------------------------
# READ SHORTCUTKIT OUTPUT
# ---------------------------------------------------------

def read_shortcut_plist(path):
    with open(path, "rb") as file:
        raw = file.read()

    try:
        return plistlib.loads(raw)
    except Exception as exc:
        raise RuntimeError(
            "Could not parse ShortcutKit output as a plist"
        ) from exc


# ---------------------------------------------------------
# HUBSIGN
# ---------------------------------------------------------

def sign_shortcut(shortcut_document, name):
    xml = plistlib.dumps(
        shortcut_document,
        fmt=plistlib.FMT_XML
    ).decode("utf-8")

    payload = json.dumps({
        "shortcutName": name,
        "shortcut": xml
    }).encode("utf-8")

    request = Request(
        HUBSIGN_URL,
        data=payload,
        headers=HEADERS,
        method="POST"
    )

    print("Sending ShortcutKit output to HubSign...")

    with urlopen(request, timeout=60) as response:
        signed = response.read()

    if signed[:4] != b"AEA1":
        preview = signed[:500].decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            "HubSign did not return an AEA1 signed shortcut.\n"
            + preview
        )

    print("HubSign returned signed AEA1 shortcut.")

    return signed


# ---------------------------------------------------------
# FILE NAME
# ---------------------------------------------------------

def safe_filename(name):
    chars = []

    for character in name:
        if character.isalnum() or character in " -_":
            chars.append(character)
        else:
            chars.append("_")

    result = "".join(chars).strip()

    if not result:
        result = "shortcut"

    return result.replace(" ", "_")


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print(
            'Usage: build_shortcut.py '
            '"Shortcut Name" '
            '"Shortcut description or AI JSON"'
        )
        sys.exit(1)

    name = sys.argv[1]
    description = sys.argv[2]

    print("=" * 60)
    print("ShortcutKit AI Builder")
    print("=" * 60)

    print(f"Shortcut name: {name}")
    print(f"Input length: {len(description)}")

    blueprint = integration_blueprint() if os.getenv('INTEGRATION_FIXTURE') == 'true' else parse_blueprint(description)

    try:
        if blueprint is not None:
            print("AI JSON blueprint detected.")
            shortcut = build_with_shortcutkit(
                name,
                blueprint
            )
        else:
            print("Plain text input detected.")
            shortcut = build_plain_text_shortcut(
                name,
                description
            )

        os.makedirs(
            "output",
            exist_ok=True
        )

        filename = safe_filename(name)

        unsigned_path = os.path.join(
            "output",
            filename + "-unsigned.plist"
        )

        signed_path = os.path.join(
            "output",
            filename + ".shortcut"
        )

        write_unsigned(
            shortcut,
            unsigned_path
        )

        document = read_shortcut_plist(
            unsigned_path
        )

        action_count = validate_document(document, len(shortcut.actions))

        print(
            f"ShortcutKit generated {action_count} "
            "serialized action(s)."
        )

        signed = sign_shortcut(
            document,
            name
        )

        with open(signed_path, "wb") as file:
            file.write(signed)

        print()
        print("=" * 60)
        print("SUCCESS")
        print("=" * 60)

        print(f"Signed shortcut: {signed_path}")
        print(f"Signed size: {len(signed)} bytes")

    except Exception as exc:
        print()
        print("=" * 60)
        print("BUILD FAILED")
        print("=" * 60)
        print(str(exc))
        sys.exit(2)


if __name__ == "__main__":
    main()
