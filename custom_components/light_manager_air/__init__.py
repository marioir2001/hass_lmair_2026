"""The Light Manager Air integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later
from homeassistant.components import persistent_notification

from .const import (
    ATTR_ENTRY_ID,
    DOMAIN,
    MAPPING_SCHEMA,
    CONF_MAPPINGS,
    CONF_ENTITY_CONVERSIONS,
    CONVERSION_SCHEMA,
    CONF_IGNORED_ZONES,
    CONF_COVER_TIMINGS,
    COVER_TIMING_SCHEMA,
    SERVICE_RELOAD_FIXTURES,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_RAW_COMMAND,
    SERVICE_START_RADIO_LEARNING,
    SERVICE_SHOW_RADIO_AUTOMATION_YAML,
    ATTR_ZONE,
    ATTR_ACTUATOR,
    ATTR_COMMAND,
    ATTR_COMMAND_INDEX,
    ATTR_PAYLOAD,
    ATTR_TIMEOUT,
    ATTR_CODE,
)
from .coordinator import LightManagerAirCoordinator, RADIO_SIGNAL_EVENT

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

PLATFORMS = [
    Platform.LIGHT,
    Platform.SCENE,
    Platform.COVER,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.REMOTE,
    Platform.WEATHER,
    Platform.SENSOR,
    Platform.EVENT,
]

RELOAD_FIXTURES_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})

SEND_COMMAND_SERVICE_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTRY_ID): cv.string,
    vol.Required(ATTR_ZONE): cv.string,
    vol.Required(ATTR_ACTUATOR): cv.string,
    vol.Optional(ATTR_COMMAND): cv.string,
    vol.Optional(ATTR_COMMAND_INDEX): vol.Coerce(int),
})

SEND_RAW_COMMAND_SERVICE_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTRY_ID): cv.string,
    vol.Required(ATTR_PAYLOAD): cv.string,
})

START_RADIO_LEARNING_SERVICE_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTRY_ID): cv.string,
    vol.Optional(ATTR_TIMEOUT, default=30): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
})

SHOW_RADIO_AUTOMATION_YAML_SERVICE_SCHEMA = vol.Schema({
    vol.Optional(ATTR_CODE): cv.string,
})


def _parse_radio_code(code: str | None) -> tuple[str | None, str | None]:
    """Split a Light Manager radio code into protocol and raw code parts."""
    if not isinstance(code, str) or not code:
        return None, None
    if "_" not in code:
        return None, code
    protocol, raw_code = code.split("_", 1)
    return protocol.upper(), raw_code


def _automation_yaml_for_radio_code(code: str) -> str:
    """Return a ready-to-copy automation YAML snippet for a radio code."""
    safe_alias = str(code).replace('"', '\"')
    return (
        f'alias: Radio Signal {safe_alias}\n'
        'description: "Triggered by a learned Light Manager Air radio signal"\n'
        'triggers:\n'
        '  - trigger: event\n'
        '    event_type: radio_signal\n'
        '    event_data:\n'
        f'      code: {safe_alias}\n'
        'conditions: []\n'
        'actions: []\n'
        'mode: single'
    )


def _create_radio_automation_notification(hass: HomeAssistant, code: str, protocol: str | None = None, raw_code: str | None = None) -> None:
    """Create a persistent notification with ready-to-copy automation YAML."""
    yaml = _automation_yaml_for_radio_code(code)
    details = [
        f"**Code:** `{code}`",
    ]
    if protocol:
        details.append(f"**Protokoll:** `{protocol}`")
    if raw_code:
        details.append(f"**Rohcode:** `{raw_code}`")

    details.append(
        "**Automation YAML:**\n\n"
        "```yaml\n"
        f"{yaml}\n"
        "```"
    )
    details.append(
        "Öffne **Einstellungen → Automationen & Szenen → Automation erstellen** "
        "und füge das YAML als neue Automation ein."
    )

    persistent_notification.async_create(
        hass,
        "\n\n".join(details),
        title="Light Manager Air Automation YAML",
        notification_id="light_manager_air_radio_automation_yaml",
    )

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema({
            vol.Optional(CONF_MAPPINGS): vol.All(
                cv.ensure_list, [MAPPING_SCHEMA]
            ),
            vol.Optional(CONF_ENTITY_CONVERSIONS): vol.All(
                cv.ensure_list, [CONVERSION_SCHEMA]
            ),
            vol.Optional(CONF_IGNORED_ZONES, default=[]): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(CONF_COVER_TIMINGS): vol.All(
                cv.ensure_list, [COVER_TIMING_SCHEMA]
            ),
        })
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Light Manager Air component."""
    if DOMAIN not in config:
        return True

    hass.data.setdefault(DOMAIN, {})

    if CONF_MAPPINGS in config[DOMAIN]:
        hass.data[DOMAIN][CONF_MAPPINGS] = config[DOMAIN][CONF_MAPPINGS]
    if CONF_ENTITY_CONVERSIONS in config[DOMAIN]:
        hass.data[DOMAIN][CONF_ENTITY_CONVERSIONS] = config[DOMAIN][CONF_ENTITY_CONVERSIONS]
    if CONF_IGNORED_ZONES in config[DOMAIN]:
        hass.data[DOMAIN][CONF_IGNORED_ZONES] = config[DOMAIN][CONF_IGNORED_ZONES]
    if CONF_COVER_TIMINGS in config[DOMAIN]:
        hass.data[DOMAIN][CONF_COVER_TIMINGS] = config[DOMAIN][CONF_COVER_TIMINGS]

    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Light Manager Air from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("entries", set()).add(entry.entry_id)

    lm_coordinator = LightManagerAirCoordinator(hass, entry)
    await lm_coordinator.async_setup()
    await lm_coordinator.async_config_entry_first_refresh()
    
    hass.data[DOMAIN][entry.entry_id] = lm_coordinator


    def _get_service_coordinator(target_entry_id: str | None):
        target_entry_id = target_entry_id or entry.entry_id
        coordinator = hass.data[DOMAIN].get(target_entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"Light Manager Air entry '{target_entry_id}' not found")
        return coordinator

    def _clear_radio_learning() -> None:
        learn_state = hass.data[DOMAIN].get("radio_learning")
        if not learn_state:
            return
        unsub = learn_state.get("unsub_timeout")
        if unsub:
            unsub()
        hass.data[DOMAIN]["radio_learning"] = None

    @callback
    def _async_radio_learning_timeout(_now) -> None:
        learn_state = hass.data[DOMAIN].get("radio_learning")
        if not learn_state:
            return
        persistent_notification.async_create(
            hass,
            "Es wurde innerhalb der Wartezeit kein Funksignal empfangen.",
            title="Light Manager Air Lernmodus",
            notification_id="light_manager_air_radio_learning",
        )
        _clear_radio_learning()

    @callback
    def _async_radio_learning_event(event) -> None:
        learn_state = hass.data[DOMAIN].get("radio_learning")
        if not learn_state:
            return
        code = event.data.get("code", "unknown")
        protocol, raw_code = _parse_radio_code(code)
        learned_data = {
            "code": code,
            "protocol": protocol,
            "raw_code": raw_code,
        }
        hass.data[DOMAIN]["last_learned_radio_signal"] = learned_data

        details = [f"**Code:** `{code}`"]
        if protocol:
            details.append(f"**Protokoll:** `{protocol}`")
        if raw_code:
            details.append(f"**Rohcode:** `{raw_code}`")
        details.append(
            "Das fertige Automation-YAML wurde in einer zweiten Benachrichtigung erstellt. "
            "Du kannst außerdem den Service `light_manager_air.show_radio_automation_yaml` nutzen."
        )

        persistent_notification.async_create(
            hass,
            "\n\n".join(details),
            title="Light Manager Air Funksignal gelernt",
            notification_id="light_manager_air_radio_learning",
        )
        _create_radio_automation_notification(hass, code, protocol, raw_code)

        hass.bus.async_fire(f"{DOMAIN}_radio_signal_learned", learned_data)
        _clear_radio_learning()

    async def _async_handle_start_radio_learning_service(call):
        _get_service_coordinator(call.data.get(ATTR_ENTRY_ID))
        _clear_radio_learning()
        timeout = call.data.get(ATTR_TIMEOUT, 30)
        hass.data[DOMAIN]["radio_learning"] = {
            "entry_id": call.data.get(ATTR_ENTRY_ID, entry.entry_id),
            "unsub_timeout": async_call_later(hass, timeout, _async_radio_learning_timeout),
        }
        persistent_notification.async_create(
            hass,
            f"Lernmodus aktiv. Drücke jetzt innerhalb von {timeout} Sekunden eine Taste auf der Funkfernbedienung.",
            title="Light Manager Air Lernmodus",
            notification_id="light_manager_air_radio_learning",
        )

    if not hass.data[DOMAIN].get("radio_learning_listener_registered"):
        hass.bus.async_listen(RADIO_SIGNAL_EVENT, _async_radio_learning_event)
        hass.data[DOMAIN]["radio_learning_listener_registered"] = True

    async def _async_handle_show_radio_automation_yaml_service(call):
        code = call.data.get(ATTR_CODE)
        protocol = None
        raw_code = None
        if not code:
            learned_data = hass.data[DOMAIN].get("last_learned_radio_signal") or {}
            code = learned_data.get("code")
            protocol = learned_data.get("protocol")
            raw_code = learned_data.get("raw_code")
        else:
            protocol, raw_code = _parse_radio_code(code)

        if not code:
            raise HomeAssistantError("No learned radio signal available yet")

        _create_radio_automation_notification(hass, code, protocol, raw_code)

    async def _async_handle_send_command_service(call):
        coordinator = _get_service_coordinator(call.data.get(ATTR_ENTRY_ID))
        zone_name = call.data[ATTR_ZONE]
        actuator_name = call.data[ATTR_ACTUATOR]
        command_name = call.data.get(ATTR_COMMAND)
        command_index = call.data.get(ATTR_COMMAND_INDEX)

        for zone in coordinator.zones:
            if zone.name != zone_name:
                continue
            for actuator in zone.actuators:
                if actuator.name != actuator_name:
                    continue
                commands = actuator.commands
                selected = None
                if command_name is not None:
                    selected = next((cmd for cmd in commands if cmd.name and cmd.name.lower() == command_name.lower()), None)
                    if selected is None:
                        selected = next((cmd for cmd in commands if cmd.name and command_name.lower() in cmd.name.lower()), None)
                if selected is None and command_index is not None:
                    try:
                        selected = commands[command_index]
                    except IndexError as exc:
                        raise HomeAssistantError(f"Command index {command_index} not found for {zone_name}/{actuator_name}") from exc
                if selected is None and len(commands) == 1:
                    selected = commands[0]
                if selected is None:
                    raise HomeAssistantError(f"Command not found for {zone_name}/{actuator_name}")
                await hass.async_add_executor_job(selected.call)
                await coordinator.async_refresh()
                return
        raise HomeAssistantError(f"Actuator '{zone_name}/{actuator_name}' not found")

    async def _async_handle_send_raw_command_service(call):
        coordinator = _get_service_coordinator(call.data.get(ATTR_ENTRY_ID))
        payload = call.data[ATTR_PAYLOAD]
        await hass.async_add_executor_job(coordinator.light_manager.send_raw_command, payload)
        await coordinator.async_refresh()

    async def _async_handle_reload_service(call):
        target_entry_id = call.data.get(ATTR_ENTRY_ID, entry.entry_id)
        await hass.config_entries.async_reload(target_entry_id)

    if not hass.services.has_service(DOMAIN, SERVICE_RELOAD_FIXTURES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RELOAD_FIXTURES,
            _async_handle_reload_service,
            schema=RELOAD_FIXTURES_SERVICE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_COMMAND,
            _async_handle_send_command_service,
            schema=SEND_COMMAND_SERVICE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_RAW_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_RAW_COMMAND,
            _async_handle_send_raw_command_service,
            schema=SEND_RAW_COMMAND_SERVICE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_START_RADIO_LEARNING):
        hass.services.async_register(
            DOMAIN,
            SERVICE_START_RADIO_LEARNING,
            _async_handle_start_radio_learning_service,
            schema=START_RADIO_LEARNING_SERVICE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SHOW_RADIO_AUTOMATION_YAML):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SHOW_RADIO_AUTOMATION_YAML,
            _async_handle_show_radio_automation_yaml_service,
            schema=SHOW_RADIO_AUTOMATION_YAML_SERVICE_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        entries = hass.data[DOMAIN].get("entries", set())
        entries.discard(entry.entry_id)

        if not entries:
            for service in (SERVICE_RELOAD_FIXTURES, SERVICE_SEND_COMMAND, SERVICE_SEND_RAW_COMMAND, SERVICE_START_RADIO_LEARNING, SERVICE_SHOW_RADIO_AUTOMATION_YAML):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok
