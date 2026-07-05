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


def command_by_name(actuator: Any, names: set[str]) -> Any | None:
    """Return the first command whose normalized name is in names."""
    for command in getattr(actuator, "commands", []) or []:
        if command_name(command) in names:
            return command
    return None


def command_cmd_value(command: Any) -> str:
    """Return the raw /control cmd payload for a command."""
    raw_cmd = getattr(command, "cmd", None)
    if isinstance(raw_cmd, tuple):
        return str(raw_cmd[1] or "")
    if isinstance(raw_cmd, list):
        for key, value in raw_cmd:
            if key == "cmd":
                return str(value or "")
    return ""


def command_has_payload(command: Any | None) -> bool:
    """Return true if a command contains a real sendable payload.

    AirStudio always exports on/toggle/off command entries for many actuator
    types. For learned IR commands, an unused Taste 2 is exported as an off
    command with an empty dta payload, for example
    ``cmd=off,typ,ir,seq,1,dta,&id=...``. Such commands should not make the
    actuator a stateful switch.
    """
    if command is None:
        return False

    value = command_cmd_value(command).strip()
    if not value:
        return False

    # The synthetic toggle command is only a helper and not a learned payload.
    if value == "toggle,toggle":
        return False

    parts = value.split(",")
    if "dta" in parts:
        idx = parts.index("dta")
        # IR commands with an unprogrammed key have dta as the final token
        # or followed by an empty token. Programmed IR commands have payload
        # data immediately after dta.
        return idx + 1 < len(parts) and bool(parts[idx + 1].strip())

    return True


def get_on_command(actuator: Any) -> Any | None:
    """Return the actuator's on command."""
    return command_by_name(actuator, ON_NAMES)


def get_off_command(actuator: Any) -> Any | None:
    """Return the actuator's off command."""
    return command_by_name(actuator, OFF_NAMES)


def get_toggle_command(actuator: Any) -> Any | None:
    """Return the actuator's toggle command."""
    return command_by_name(actuator, TOGGLE_NAMES)


def has_on_payload(actuator: Any) -> bool:
    """Return true if the on command is programmed."""
    return command_has_payload(get_on_command(actuator))


def has_off_payload(actuator: Any) -> bool:
    """Return true if the off command is programmed."""
    return command_has_payload(get_off_command(actuator))


def has_on_off_payloads(actuator: Any) -> bool:
    """Return true if both on and off commands are programmed."""
    return has_on_payload(actuator) and has_off_payload(actuator)


def is_single_action_actuator(actuator: Any) -> bool:
    """Return true for command-style actuators that should be a button.

    This maps the common AirStudio pattern "one entry with only Taste 1
    programmed" to a Home Assistant ButtonEntity. If Taste 1 and Taste 2 are
    both programmed, the actuator remains a switch/light according to the
    existing classification.
    """
    return has_on_payload(actuator) and not has_off_payload(actuator)


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
    """Return true if an actuator has programmed on/off commands."""
    return has_on_off_payloads(actuator)


def has_only_basic_toggle_commands(actuator: Any) -> bool:
    """Return true for simple on/off/toggle-only actuators with real payloads."""
    names = {command_name(command) for command in getattr(actuator, "commands", []) or [] if command_name(command)}
    if not names:
        return False
    return names.issubset(ON_NAMES | OFF_NAMES | TOGGLE_NAMES) and has_on_off_payloads(actuator)
