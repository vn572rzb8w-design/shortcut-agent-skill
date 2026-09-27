#!/usr/bin/env python3

import json
import os
import plistlib
import sys
from urllib.request import Request, urlopen


HUBSIGN_URL = "https://hubsign.routinehub.services/sign"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "cherri/1.0",
    "Origin": "https://routinehub.co",
    "Referer": "https://routinehub.co/",
}


def action_comment(text):
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.comment",
        "WFWorkflowActionParameters": {
            "WFCommentActionText": text
        },
    }


def action_notification(title, body):
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.notification",
        "WFWorkflowActionParameters": {
            "WFNotificationActionTitle": title,
            "WFNotificationActionBody": body,
        },
    }


def action_text(text):
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
        "WFWorkflowActionParameters": {
            "WFTextActionText": text
        },
    }


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
    """
    Parse structured JSON produced by the AI.

    Plain English is allowed temporarily and falls back
    to the original test builder.
    """

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
            "AI blueprint root must be a JSON object"
        )

    actions = blueprint.get("actions")

    if not isinstance(actions, list) or not actions:
        raise ValueError(
            "AI blueprint must contain a non-empty 'actions' array"
        )

    return blueprint


def compile_action(spec, index):
    """
    Convert one AI action specification directly into
    Apple's WFWorkflowAction format.
    """

    if not isinstance(spec, dict):
        raise ValueError(
            f"Action {index} must be a JSON object"
        )

    identifier = spec.get("identifier")
    parameters = spec.get("parameters", {})

    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(
            f"Action {index} is missing a valid 'identifier'"
        )

    if not isinstance(parameters, dict):
        raise ValueError(
            f"Action {index} 'parameters' must be an object"
        )

    return {
        "WFWorkflowActionIdentifier": identifier.strip(),
        "WFWorkflowActionParameters": parameters,
    }


def compile_blueprint(blueprint):
    actions = []

    for index, spec in enumerate(
        blueprint["actions"],
        start=1
    ):
        actions.append(
            compile_action(spec, index)
        )

    return actions


def workflow_document(actions):
    return {
        "WFWorkflowClientVersion": "3107.0.8.2",
        "WFWorkflowClientRelease": "22.1",
        "WFWorkflowMinimumClientVersion": 900,

        "WFWorkflowTypes": [
            "NCWidget",
            "WatchKit",
        ],

        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4282601983,
            "WFWorkflowIconGlyphNumber": 61440,
        },

        "WFWorkflowImportQuestions": [],

        "WFWorkflowInputContentItemClasses": [
            "WFAppStoreAppContentItem"
        ],

        "WFWorkflowActions": actions,
    }


def build_shortcut(name, description):

    blueprint = parse_blueprint(description)

    # NEW AI MODE
    if blueprint is not None:

        actions = compile_blueprint(
            blueprint
        )

        print(
            f"AI blueprint detected: "
            f"{len(actions)} action(s)"
        )

        return workflow_document(
            actions
        )

    # OLD FALLBACK MODE
    # Keeps our existing working pipeline alive
    # until the iPhone AI step is connected.

    print(
        "Plain-text description detected; "
        "using legacy test builder"
    )

    actions = [
        action_comment(
            "Generated automatically by "
            "ChatGPT + shortcut-agent-skill"
        ),

        action_text(
            description
        ),

        action_notification(
            name,
            description
        ),
    ]

    return workflow_document(
        actions
    )


def sign_shortcut(shortcut, name):

    xml = plistlib.dumps(
        shortcut,
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
        method="POST",
    )

    print(
        "Sending shortcut to HubSign..."
    )

    with urlopen(
        request,
        timeout=60
    ) as response:

        signed = response.read()

    if signed[:4] != b"AEA1":

        preview = signed[:500].decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            "HubSign did not return an "
            "AEA1 signed shortcut.\n"
            f"Response begins with:\n{preview}"
        )

    print(
        "HubSign returned a signed "
        "AEA1 shortcut."
    )

    return signed


def safe_filename(name):

    allowed = []

    for character in name:

        if (
            character.isalnum()
            or character in (" ", "-", "_")
        ):
            allowed.append(character)

        else:
            allowed.append("_")

    filename = "".join(
        allowed
    ).strip()

    if not filename:
        filename = "shortcut"

    return filename.replace(
        " ",
        "_"
    )


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

    print(
        f"Building shortcut: {name}"
    )

    print(
        f"Input length: "
        f"{len(description)} characters"
    )

    try:

        shortcut = build_shortcut(
            name,
            description
        )

    except ValueError as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr
        )

        sys.exit(2)

    os.makedirs(
        "output",
        exist_ok=True
    )

    unsigned_path = os.path.join(
        "output",
        safe_filename(name) + ".plist"
    )

    with open(
        unsigned_path,
        "wb"
    ) as file:

        plistlib.dump(
            shortcut,
            file,
            fmt=plistlib.FMT_XML
        )

    print(
        f"Unsigned plist created: "
        f"{unsigned_path}"
    )

    signed = sign_shortcut(
        shortcut,
        name
    )

    signed_path = os.path.join(
       