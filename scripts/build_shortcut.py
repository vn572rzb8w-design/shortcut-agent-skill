#!/usr/bin/env python3

import json
import os
import plistlib
import sys
from difflib import get_close_matches
from urllib.request import Request, urlopen


HUBSIGN_URL = "https://hubsign.routinehub.services/sign"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "cherri/1.0",
    "Origin": "https://routinehub.co",
    "Referer": "https://routinehub.co/",
}


# ---------------------------------------------------------
# PATHS
# ---------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

ACTION_DATABASE_PATH = os.path.join(
    REPO_ROOT,
    "data",
    "actions.json",
)


# ---------------------------------------------------------
# ACTION DATABASE
# ---------------------------------------------------------

def load_action_database():
    """
    Load the Apple Shortcuts action reference database.
    """

    if not os.path.exists(ACTION_DATABASE_PATH):
        print(
            f"WARNING: Action database not found: "
            f"{ACTION_DATABASE_PATH}"
        )
        return None

    try:
        with open(
            ACTION_DATABASE_PATH,
            "r",
            encoding="utf-8",
        ) as file:
            database = json.load(file)

    except Exception as exc:
        print(
            f"WARNING: Could not load action database: {exc}"
        )
        return None

    print(
        f"Loaded action database: "
        f"{ACTION_DATABASE_PATH}"
    )

    return database


def collect_action_identifiers(value, found=None):
    """
    Recursively scan actions.json for Apple
    WFWorkflowActionIdentifier strings.

    This intentionally supports several possible JSON
    structures so the database format can evolve.
    """

    if found is None:
        found = set()

    if isinstance(value, dict):

        for key, item in value.items():

            if (
                key in (
                    "identifier",
                    "WFWorkflowActionIdentifier",
                    "actionIdentifier",
                    "id",
                )
                and isinstance(item, str)
                and (
                    item.startswith("is.workflow.actions.")
                    or "." in item
                )
            ):
                found.add(item.strip())

            collect_action_identifiers(
                item,
                found,
            )

    elif isinstance(value, list):

        for item in value:
            collect_action_identifiers(
                item,
                found,
            )

    elif isinstance(value, str):

        if value.startswith(
            "is.workflow.actions."
        ):
            found.add(value.strip())

    return found


ACTION_DATABASE = load_action_database()

KNOWN_ACTION_IDENTIFIERS = (
    collect_action_identifiers(ACTION_DATABASE)
    if ACTION_DATABASE is not None
    else set()
)

print(
    f"Known action identifiers: "
    f"{len(KNOWN_ACTION_IDENTIFIERS)}"
)


# ---------------------------------------------------------
# BASIC SHORTCUT ACTIONS
# ---------------------------------------------------------

def action_comment(text):
    return {
        "WFWorkflowActionIdentifier":
            "is.workflow.actions.comment",

        "WFWorkflowActionParameters": {
            "WFCommentActionText": text
        },
    }


def action_notification(title, body):
    return {
        "WFWorkflowActionIdentifier":
            "is.workflow.actions.notification",

        "WFWorkflowActionParameters": {
            "WFNotificationActionTitle": title,
            "WFNotificationActionBody": body,
        },
    }


def action_text(text):
    return {
        "WFWorkflowActionIdentifier":
            "is.workflow.actions.gettext",

        "WFWorkflowActionParameters": {
            "WFTextActionText": text
        },
    }


# ---------------------------------------------------------
# AI JSON BLUEPRINT PARSER
# ---------------------------------------------------------

def strip_code_fence(text):

    text = text.strip()

    if text.startswith("```"):

        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if (
            lines
            and lines[-1].strip() == "```"
        ):
            lines = lines[:-1]

        text = "\n".join(
            lines
        ).strip()

    return text


def parse_blueprint(description):

    candidate = strip_code_fence(
        description
    )

    if not candidate.startswith("{"):
        return None

    try:
        blueprint = json.loads(
            candidate
        )

    except json.JSONDecodeError as exc:

        raise ValueError(
            f"AI blueprint is not valid JSON: {exc}"
        ) from exc

    if not isinstance(
        blueprint,
        dict,
    ):
        raise ValueError(
            "AI blueprint root must be a JSON object"
        )

    actions = blueprint.get(
        "actions"
    )

    if (
        not isinstance(actions, list)
        or not actions
    ):
        raise ValueError(
            "AI blueprint must contain a non-empty "
            "'actions' array"
        )

    return blueprint


# ---------------------------------------------------------
# IDENTIFIER VALIDATION
# ---------------------------------------------------------

def validate_identifier(identifier, index):
    """
    Validate an AI supplied action identifier against
    data/actions.json.

    We DO NOT silently turn unknown actions into Nothing,
    Text, Comments, etc.

    If there is one extremely close database match,
    use it. Otherwise fail loudly.
    """

    identifier = identifier.strip()

    # No database available:
    # preserve old behavior.
    if not KNOWN_ACTION_IDENTIFIERS:

        print(
            f"Action {index}: database unavailable; "
            f"using identifier directly: {identifier}"
        )

        return identifier

    # Exact match.
    if identifier in KNOWN_ACTION_IDENTIFIERS:

        print(
            f"Action {index}: validated: "
            f"{identifier}"
        )

        return identifier

    # Try an extremely close spelling match.
    matches = get_close_matches(
        identifier,
        list(KNOWN_ACTION_IDENTIFIERS),
        n=3,
        cutoff=0.94,
    )

    if len(matches) == 1:

        corrected = matches[0]

        print(
            f"Action {index}: corrected identifier:"
        )

        print(
            f"  AI:       {identifier}"
        )

        print(
            f"  Database: {corrected}"
        )

        return corrected

    suggestions = get_close_matches(
        identifier,
        list(KNOWN_ACTION_IDENTIFIERS),
        n=5,
        cutoff=0.60,
    )

    message = (
        f"Action {index} uses an unknown Apple "
        f"Shortcuts identifier:\n"
        f"{identifier}"
    )

    if suggestions:

        message += (
            "\n\nClosest identifiers in "
            "data/actions.json:\n"
            + "\n".join(
                f"  - {item}"
                for item in suggestions
            )
        )

    raise ValueError(
        message
    )


# ---------------------------------------------------------
# GENERIC ACTION COMPILER
# ---------------------------------------------------------

def compile_action(spec, index):

    if not isinstance(
        spec,
        dict,
    ):
        raise ValueError(
            f"Action {index} must be a JSON object"
        )

    identifier = spec.get(
        "identifier"
    )

    parameters = spec.get(
        "parameters",
        {},
    )

    if (
        not isinstance(identifier, str)
        or not identifier.strip()
    ):
        raise ValueError(
            f"Action {index} is missing a valid "
            "'identifier'"
        )

    if not isinstance(
        parameters,
        dict,
    ):
        raise ValueError(
            f"Action {index} 'parameters' "
            "must be an object"
        )

    identifier = validate_identifier(
        identifier,
        index,
    )

    return {
        "WFWorkflowActionIdentifier":
            identifier,

        "WFWorkflowActionParameters":
            parameters,
    }


def compile_blueprint(blueprint):

    actions = []

    for index, spec in enumerate(
        blueprint["actions"],
        start=1,
    ):

        action = compile_action(
            spec,
            index,
        )

        actions.append(
            action
        )

    return actions


# ---------------------------------------------------------
# APPLE SHORTCUT DOCUMENT
# ---------------------------------------------------------

def workflow_document(actions):

    return {
        "WFWorkflowClientVersion":
            "3107.0.8.2",

        "WFWorkflowClientRelease":
            "22.1",

        "WFWorkflowMinimumClientVersion":
            900,

        "WFWorkflowTypes": [
            "NCWidget",
            "WatchKit",
        ],

        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor":
                4282601983,

            "WFWorkflowIconGlyphNumber":
                61440,
        },

        "WFWorkflowImportQuestions":
            [],

        "WFWorkflowInputContentItemClasses": [
            "WFAppStoreAppContentItem"
        ],

        "WFWorkflowActions":
            actions,
    }


# ---------------------------------------------------------
# BUILD SHORTCUT
# ---------------------------------------------------------

def build_shortcut(name, description):

    blueprint = parse_blueprint(
        description
    )

    # AI MODE
    if blueprint is not None:

        actions = compile_blueprint(
            blueprint
        )

        print(
            f"AI blueprint detected: "
            f"{len(actions)} action(s)"
        )

        for index, action in enumerate(
            actions,
            start=1,
        ):

            print(
                f"Action {index}: "
                f"{action['WFWorkflowActionIdentifier']}"
            )

        return workflow_document(
            actions
        )

    # LEGACY TEST MODE
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
            description,
        ),
    ]

    return workflow_document(
        actions
    )


# ---------------------------------------------------------
# HUBSIGN
# ---------------------------------------------------------

def sign_shortcut(shortcut, name):

    xml = plistlib.dumps(
        shortcut,
        fmt=plistlib.FMT_XML,
    ).decode(
        "utf-8"
    )

    payload = json.dumps({
        "shortcutName": name,
        "shortcut": xml,
    }).encode(
        "utf-8"
    )

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
        timeout=60,
    ) as response:

        signed = response.read()

    if signed[:4] != b"AEA1":

        preview = signed[:500].decode(
            "utf-8",
            errors="replace",
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


# ---------------------------------------------------------
# FILE NAME
# ---------------------------------------------------------

def safe_filename(name):

    allowed = []

    for character in name:

        if (
            character.isalnum()
            or character in (
                " ",
                "-",
                "_",
            )
        ):
            allowed.append(
                character
            )

        else:
            allowed.append(
                "_"
            )

    filename = "".join(
        allowed
    ).strip()

    if not filename:
        filename = "shortcut"

    return filename.replace(
        " ",
        "_",
    )


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
            description,
        )

    except ValueError as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        sys.exit(2)

    os.makedirs(
        "output",
        exist_ok=True,
    )

    unsigned_path = os.path.join(
        "output",
        safe_filename(name) + ".plist",
    )

    with open(
        unsigned_path,
        "wb",
    ) as file:

        plistlib.dump(
            shortcut,
            file,
            fmt=plistlib.FMT_XML,
        )

    print(
        f"Unsigned plist created: "
        f"{unsigned_path}"
    )

    signed = sign_shortcut(
        shortcut,
        name,
    )

    signed_path = os.path.join(
        "output",
        safe_filename(name) + ".shortcut",
    )

    with open(
        signed_path,
        "wb",
    ) as file:

        file.write(
            signed
        )

    print(
        f"SUCCESS: {signed_path}"
    )

    print(
        f"Signed file size: "
        f"{len(signed)} bytes"
    )


if __name__ == "__main__":
    main()