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


def build_shortcut(name, description):
    """
    Build a minimal valid Apple Shortcut.

    This is our first test builder. Once the GitHub pipeline works,
    we'll expand it to use the full shortcut-agent action library.
    """

    actions = [
        action_comment(
            "Generated automatically by ChatGPT + shortcut-agent-skill"
        ),
        action_text(description),
        action_notification(
            name,
            description
        ),
    ]

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

    print("Sending shortcut to HubSign...")

    with urlopen(request, timeout=60) as response:
        signed = response.read()

    if signed[:4] != b"AEA1":
        preview = signed[:500].decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            "HubSign did not return an AEA1 signed shortcut.\n"
            f"Response begins with:\n{preview}"
        )

    print("HubSign returned a signed AEA1 shortcut.")

    return signed


def safe_filename(name):
    allowed = []

    for character in name:
        if character.isalnum() or character in (" ", "-", "_"):
            allowed.append(character)
        else:
            allowed.append("_")

    filename = "".join(allowed).strip()

    if not filename:
        filename = "shortcut"

    return filename.replace(" ", "_")


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: build_shortcut.py "
            "\"Shortcut Name\" "
            "\"Shortcut description\""
        )
        sys.exit(1)

    name = sys.argv[1]
    description = sys.argv[2]

    print(f"Building shortcut: {name}")
    print(f"Description: {description}")

    shortcut = build_shortcut(
        name,
        description
    )

    os.makedirs(
        "output",
        exist_ok=True
    )

    unsigned_path = os.path.join(
        "output",
        safe_filename(name) + ".plist"
    )

    with open(unsigned_path, "wb") as file:
        plistlib.dump(
            shortcut,
            file,
            fmt=plistlib.FMT_XML
        )

    print(
        f"Unsigned plist created: {unsigned_path}"
    )

    signed = sign_shortcut(
        shortcut,
        name
    )

    signed_path = os.path.join(
        "output",
        safe_filename(name) + ".shortcut"
    )

    with open(signed_path, "wb") as file:
        file.write(signed)

    print(
        f"SUCCESS: {signed_path}"
    )

    print(
        f"Signed file size: {len(signed)} bytes"
    )


if __name__ == "__main__":
    main()