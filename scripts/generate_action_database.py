#!/usr/bin/env python3

import json
import inspect
from pathlib import Path

from shortcutkit import actions


OUTPUT = Path("data/actions.json")


def clean(value):
    """Convert ShortcutKit metadata into JSON-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]

    if isinstance(value, dict):
        return {
            str(k): clean(v)
            for k, v in value.items()
        }

    return str(value)


def extract_parameters(obj):
    """
    Try several ways of extracting the parameter schema exposed
    by ShortcutKit.
    """

    result = []

    # Classes/functions may expose a Python signature.
    try:
        signature = inspect.signature(obj)

        for name, parameter in signature.parameters.items():
            if name in ("self", "cls"):
                continue

            item = {"name": name}

            if parameter.default is not inspect.Parameter.empty:
                item["default"] = clean(parameter.default)

            if parameter.annotation is not inspect.Parameter.empty:
                item["type"] = clean(parameter.annotation)

            result.append(item)

        if result:
            return result

    except (TypeError, ValueError):
        pass

    # Search metadata attributes commonly used by generated APIs.
    for attribute in (
        "parameters",
        "params",
        "parameter_schema",
        "schema",
        "__parameters__",
    ):
        try:
            value = getattr(obj, attribute)
        except Exception:
            continue

        if value:
            if isinstance(value, dict):
                return [
                    {
                        "name": str(name),
                        "metadata": clean(metadata)
                    }
                    for name, metadata in value.items()
                ]

            if isinstance(value, (list, tuple, set)):
                return [
                    {"name": str(item)}
                    for item in value
                ]

    return []


def discover_actions():
    database = []
    seen = set()

    for python_name in dir(actions):
        if python_name.startswith("_"):
            continue

        try:
            obj = getattr(actions, python_name)
        except Exception:
            continue

        identifier = None

        # Most generated ShortcutKit action constants are strings.
        if isinstance(obj, str):
            identifier = obj

        # Support richer generated objects as well.
        if identifier is None:
            for attr in (
                "identifier",
                "action_identifier",
                "id",
            ):
                try:
                    candidate = getattr(obj, attr)
                except Exception:
                    continue

                if isinstance(candidate, str):
                    identifier = candidate
                    break

        if not identifier:
            continue

        # Only keep actual Shortcuts/App Intent identifiers.
        if (
            not identifier.startswith("is.workflow.actions.")
            and ".intent." not in identifier.lower()
            and "appintent" not in identifier.lower()
        ):
            continue

        if identifier in seen:
            continue

        seen.add(identifier)

        entry = {
            "name": python_name,
            "identifier": identifier,
            "parameters": extract_parameters(obj),
        }

        database.append(entry)

    database.sort(
        key=lambda x: (
            x["identifier"].lower(),
            x["name"].lower()
        )
    )

    return database


def main():
    database = discover_actions()

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    payload = {
        "generated_from": "ShortcutKit",
        "action_count": len(database),
        "actions": database,
    }

    OUTPUT.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Wrote {len(database)} actions to {OUTPUT}"
    )

    # Print several important actions so GitHub logs immediately
    # tell us whether parameter extraction worked.
    wanted = (
        "orientationlock",
        "setbrightness",
        "setvolume",
        "wifi",
        "bluetooth",
        "wait",
        "lowpower",
    )

    print("\n=== SCHEMA TEST ===")

    for entry in database:
        identifier_lower = entry["identifier"].lower()

        if any(word in identifier_lower for word in wanted):
            print(
                json.dumps(
                    entry,
                    indent=2,
                    ensure_ascii=False
                )
            )


if __name__ == "__main__":
    main()