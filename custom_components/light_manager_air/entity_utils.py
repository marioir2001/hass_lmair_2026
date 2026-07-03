"""Entity classification helpers for Light Manager Air."""
from __future__ import annotations

import re
from typing import Any

HUE_API_PATTERN = re.compile(r"/api/[^/]+/(?:lights|groups)/\d+/(?:state|action)")

ON_NAMES = {"on", "ein", "an", "einschalten"}
OFF_NAMES = {"off", "aus", "ausschalten"}
TOGGLE_NAMES = {"toggle", "umschalten"}


def command_name(command: Any) -> str:
    """Return a normalized command name."""
    return (getattr(command, "name", "") or "").strip().lower()


def is_hue_actuator(actuator: Any) -> bool:
    """Return true for HTTP commands targeting a Hue light/group state endpoint."""
    if not getattr(actuator, "commands", None):
        return False
    try:
        first_cmd = actuator.commands[0].cmd
        # SDK commands are either ("cmd", payload) or parse_qsl lists.
        if isinstance(first_cmd, tuple):
            candidate = str(first_cmd[1])
        else:
            candidate = " ".join(str(value) for _key, value in first_cmd)
    except (IndexError, TypeError, ValueError):
        return False
    return bool(HUE_API_PATTERN.search(candidate))


def is_dimmable_actuator(actuator: Any) -> bool:
    """Return true if an actuator exposes percentage based dimming commands."""
    if getattr(actuator, "type", None) == "http" and not is_hue_actuator(actuator):
        return False
    for command in getattr(actuator, "commands", []) or []:
        name = command_name(command)
        if re.fullmatch(r"\d{1,3}\s*%", name):
            return True
    return False


def has_on_off_commands(actuator: Any) -> bool:
    """Return true if an actuator has recognizable on/off commands."""
    names = {command_name(command) for command in getattr(actuator, "commands", []) or []}
    return bool(names & ON_NAMES) and bool(names & OFF_NAMES)


def has_only_basic_toggle_commands(actuator: Any) -> bool:
    """Return true for simple on/off/toggle-only actuators."""
    names = {command_name(command) for command in getattr(actuator, "commands", []) or [] if command_name(command)}
    if not names:
        return False
    return names.issubset(ON_NAMES | OFF_NAMES | TOGGLE_NAMES) and bool(names & ON_NAMES) and bool(names & OFF_NAMES)
