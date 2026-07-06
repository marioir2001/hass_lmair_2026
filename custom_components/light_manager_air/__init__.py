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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
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




def _count_actuators(coordinator: LightManagerAirCoordinator) -> int:
    """Return the number of actuators loaded from the Light Manager XML."""
    return sum(len(zone.actuators) for zone in coordinator.zones)


def _sync_summary_message(coordinator: LightManagerAirCoordinator, sync_stats: dict | None = None) -> str:
    """Build a human-readable sync summary for persistent notifications."""
    sync_stats = sync_stats or {}
    host = coordinator.entry.data.get("host") or getattr(coordinator.light_manager, "host", None)
    online = "Online" if getattr(coordinator, "last_update_success", True) else "Offline"
    lines = [
        "Die aktuelle Konfiguration wurde vom Light Manager Air geladen.",
        "",
        f"**Status:** {online}",
    ]
    if host:
        lines.append(f"**IP/Host:** `{host}`")
    lines.extend([
        f"**Zonen:** {len(coordinator.zones)}",
        f"**Aktoren/Geräte:** {_count_actuators(coordinator)}",
        f"**Szenen:** {len(coordinator.scenes)}",
        f"**Marker:** {len(coordinator.markers)}",
        f"**Wetterkanäle:** {len(coordinator.weather_channels)}",
        f"**Entfernte Entitäten:** {sync_stats.get('removed_entities', 0)}",
        f"**Entfernte Geräte/Zonen:** {sync_stats.get('removed_devices', 0)}",
    ])
    return "\n".join(lines)


def _expected_entity_registry_keys(hass: HomeAssistant, coordinator: LightManagerAirCoordinator) -> set[tuple[str, str]]:
    """Build the entity registry keys that should exist for the current XML/config.

    Home Assistant's entity registry uniqueness is scoped by entity domain. The
    same Light Manager actuator can therefore leave a stale entry behind when it
    changes platform, for example ``cover`` -> ``switch``. For cleanup we must
    compare both the Home Assistant entity domain and the integration unique ID.
    """
    from .base_entity import LightManagerAirBaseEntity
    from .button import _BASIC_NAMES
    from .const import CONF_COVER_TIMINGS, CONF_ENTITY_ID, CONF_EXTERNAL_ENTITY, CONF_IGNORED_SCENE_ZONE
    from .cover import LightManagerAirCover
    from .entity_utils import command_name, is_single_action_actuator
    from .light import LightManagerAirLight
    from .switch import LightManagerAirSwitch

    device_id = coordinator.device_id
    expected: set[tuple[str, str]] = {
        ("remote", f"{device_id}_remote"),
        ("sensor", f"{device_id}_last_radio_signal"),
        ("event", f"{device_id}_radio_event"),
        ("button", f"{device_id}_learn_radio_signal"),
        ("button", f"{device_id}_show_radio_automation_yaml"),
        ("button", f"{device_id}_synchronize"),
        ("sensor", f"{device_id}_ip_address"),
        ("sensor", f"{device_id}_connection_status"),
        ("sensor", f"{device_id}_zone_count"),
        ("sensor", f"{device_id}_actuator_count"),
        ("sensor", f"{device_id}_scene_count"),
        ("sensor", f"{device_id}_marker_count"),
    }

    for marker in coordinator.markers:
        expected.add(("switch", f"{device_id}_marker_{marker.marker_id}"))

    for channel in coordinator.weather_channels:
        if channel.weather_id:
            expected.add(("weather", f"{device_id}_weather_{channel.channel_id}"))
        else:
            if channel.temperature != "":
                expected.add(("sensor", f"{device_id}_temperature_{channel.channel_id}"))
            if channel.humidity != "" and channel.humidity > 0:
                expected.add(("sensor", f"{device_id}_humidity_{channel.channel_id}"))

    for zone in coordinator.zones:
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue
        for actuator in zone.actuators:
            if LightManagerAirCover.check_actuator(actuator, zone.name, hass):
                expected.add(("cover", f"{device_id}_{zone.name}_{actuator.name}"))
                continue
            if LightManagerAirLight.check_actuator(actuator, zone.name, hass):
                expected.add(("light", f"{device_id}_{zone.name}_{actuator.type}_{actuator.name}"))
                continue
            if LightManagerAirSwitch.check_actuator(actuator, zone.name, hass):
                expected.add(("switch", f"{device_id}_{zone.name}_{actuator.name}"))
                continue

            if is_single_action_actuator(actuator):
                expected.add(("button", f"{device_id}_action_button_{zone.name}_{actuator.name}"))
                continue

            for index, command in enumerate(actuator.commands):
                name = command_name(command)
                if name in _BASIC_NAMES or name.endswith("%"):
                    continue
                expected.add(("button", f"{device_id}_button_{zone.name}_{actuator.name}_{index}_{command.name}"))

    if not LightManagerAirBaseEntity.is_zone_ignored(CONF_IGNORED_SCENE_ZONE, hass):
        for index, scene in enumerate(coordinator.scenes):
            expected.add(("scene", f"{device_id}_scene_{scene.name}"))
            expected.add(("button", f"{device_id}_scene_button_{index}_{scene.name}"))

    for cover_cfg in hass.data.get(DOMAIN, {}).get(CONF_COVER_TIMINGS, []) or []:
        if cover_cfg.get(CONF_EXTERNAL_ENTITY, False):
            entity_id = cover_cfg[CONF_ENTITY_ID]
            expected.add(("cover", f"{DOMAIN}_cover_{entity_id.replace('.', '_')}"))

    return expected


async def _async_cleanup_removed_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: LightManagerAirCoordinator,
) -> int:
    """Remove entity registry entries that no longer exist in the Light Manager XML.

    Home Assistant keeps old registry entries after an integration reload. Without
    this cleanup, deleted or renamed AirStudio zones/actuators remain visible as
    stale entities. The cleanup is intentionally limited to entities belonging to
    this config entry and this integration domain.
    """
    expected = _expected_entity_registry_keys(hass, coordinator)
    registry = er.async_get(hass)
    removed: list[str] = []

    for entity_entry in list(registry.entities.values()):
        if entity_entry.config_entry_id != entry.entry_id:
            continue
        if entity_entry.platform != DOMAIN:
            continue
        if not entity_entry.unique_id:
            continue
        # Compare both HA entity domain (light/switch/cover/button/...) and unique ID.
        # This removes stale entities when an AirStudio actuator changes type but
        # keeps the same zone/actuator based unique ID.
        if (entity_entry.domain, entity_entry.unique_id) in expected:
            continue
        registry.async_remove(entity_entry.entity_id)
        removed.append(entity_entry.entity_id)

    if removed:
        _LOGGER.info(
            "Removed %s stale Light Manager Air entities after XML sync: %s",
            len(removed),
            ", ".join(removed),
        )
    return len(removed)


def _expected_device_identifiers(hass: HomeAssistant, coordinator: LightManagerAirCoordinator) -> set[tuple[str, str]]:
    """Build the set of device identifiers that should exist for the current XML/config."""
    from .base_entity import LightManagerAirBaseEntity

    expected: set[tuple[str, str]] = {
        (DOMAIN, coordinator.light_manager.mac_address),
        (DOMAIN, f"{coordinator.light_manager.mac_address}_markers"),
    }

    for zone in coordinator.zones:
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue
        # Keep both identifier formats because older versions registered zone devices
        # with the MAC based identifier while newer versions also added the device_id
        # based identifier for migration safety.
        expected.add((DOMAIN, f"{coordinator.light_manager.mac_address}_{zone.name}"))
        expected.add((DOMAIN, f"{coordinator.device_id}_{zone.name}"))

    return expected


async def _async_cleanup_removed_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: LightManagerAirCoordinator,
) -> int:
    """Remove empty device registry entries that no longer exist in the XML.

    Entity cleanup alone is not enough: Home Assistant may keep an empty device
    around after all of its entities were removed. This removes only devices that
    belong to this config entry, have Light Manager Air identifiers, have no
    remaining entities and are not part of the current expected device list.
    """
    expected_identifiers = _expected_device_identifiers(hass, coordinator)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    removed: list[str] = []

    for device in list(device_registry.devices.values()):
        if entry.entry_id not in device.config_entries:
            continue

        lmair_identifiers = {
            identifier for identifier in device.identifiers if identifier[0] == DOMAIN
        }
        if not lmair_identifiers:
            continue

        if lmair_identifiers & expected_identifiers:
            continue

        has_entities = any(
            entity_entry.device_id == device.id
            for entity_entry in entity_registry.entities.values()
        )
        if has_entities:
            continue

        device_registry.async_remove_device(device.id)
        removed.append(device.name or next(iter(lmair_identifiers))[1])

    if removed:
        _LOGGER.info(
            "Removed %s stale Light Manager Air devices after XML sync: %s",
            len(removed),
            ", ".join(removed),
        )
    return len(removed)

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

    removed_entities = await _async_cleanup_removed_entities(hass, entry, lm_coordinator)
    removed_devices = await _async_cleanup_removed_devices(hass, entry, lm_coordinator)
    hass.data[DOMAIN].setdefault("last_sync_stats", {})[entry.entry_id] = {
        "removed_entities": removed_entities,
        "removed_devices": removed_devices,
    }

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
        coordinator = hass.data[DOMAIN].get(target_entry_id)
        if coordinator is not None:
            sync_stats = hass.data[DOMAIN].get("last_sync_stats", {}).get(target_entry_id, {})
            persistent_notification.async_create(
                hass,
                _sync_summary_message(coordinator, sync_stats),
                title="Light Manager Air synchronisiert",
                notification_id="light_manager_air_sync_complete",
            )

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
