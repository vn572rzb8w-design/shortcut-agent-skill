#!/usr/bin/env python3

import json
import os
import plistlib
import sys
import tempfile
from urllib.request import Request, urlopen

from shortcutkit import Shortcut, actions


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

    # ShortcutKit intentionally permits identifiers outside its
    # built-in set, which is needed for third-party App Intents.
    # For Apple/native actions, however, print a warning so we
    # can see when the AI has invented something.
    if identifier.startswith("is.workflow.actions."):
        raise ValueError(
            "Unknown built-in Apple Shortcuts action identifier: "
            + identifier
        )

    print(
        "WARNING: identifier is outside ShortcutKit's built-in "
        f"catalogue; passing through: {identifier}"
    )

    return identifier


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

    specs = blueprint["actions"]

    print(
        f"Blueprint contains {len(specs)} action(s)"
    )

    for index, spec in enumerate(specs, start=1):
        if not isinstance(spec, dict):
            raise ValueError(
                f"Action {index} must be a JSON object"
            )

        identifier = resolve_identifier(
            spec.get("identifier")
        )

        parameters = normalize_parameters(
            spec.get("parameters", {})
        )

        print()
        print(f"Action {index}")
        print(f"Identifier: {identifier}")

        if parameters:
            print(
                "Parameters:",
                json.dumps(
                    parameters,
                    ensure_ascii=False,
                    indent=2
                )
            )
        else:
            print("Parameters: {}")

        try:
            shortcut.action(
                identifier,
                **parameters
            )
        except Exception as exc:
            raise ValueError(
                f"ShortcutKit rejected action {index}\n"
                f"Identifier: {identifier}\n"
                f"Parameters: {json.dumps(parameters, ensure_ascii=False)}\n"
                f"Reason: {exc}"
            ) from exc

    return shortcut


# ---------------------------------------------------------
# LEGACY PLAIN-TEXT TEST
# ---------------------------------------------------------

def build_plain_text_shortcut(name, description):
    shortcut = Shortcut(name)

    # These are known ShortcutKit catalogue constants.
    shortcut.action(
        actions.GETTEXT,
        WFTextActionText=description
    )

    shortcut.action(
        actions.NOTIFICATION,
        WFNotificationActionTitle=name,
        WFNotificationActionBody=description
    )

    return shortcut


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

    blueprint = parse_blueprint(description)

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
            filename + "-unsigned.shortcut"
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

        action_count = len(
            document.get(
                "WFWorkflowActions",
                []
            )
        )

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