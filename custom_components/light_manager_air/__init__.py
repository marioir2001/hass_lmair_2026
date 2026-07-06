"""The Light Manager Air integration."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

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
    SERVICE_EXPORT_XML,
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

EXPORT_XML_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})


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


def _format_sync_list(items: list[str] | tuple[str, ...] | None, limit: int = 10) -> list[str]:
    """Return a compact markdown list for sync notification sections."""
    values = [str(item) for item in (items or []) if item]
    if not values:
        return ["_Keine_"]
    shown = values[:limit]
    lines = [f"- `{item}`" for item in shown]
    remaining = len(values) - len(shown)
    if remaining > 0:
        lines.append(f"- … und {remaining} weitere")
    return lines


def _sync_summary_message(coordinator: LightManagerAirCoordinator, sync_stats: dict | None = None) -> str:
    """Build a human-readable sync summary for persistent notifications."""
    sync_stats = sync_stats or {}
    host = coordinator.entry.data.get("host") or getattr(coordinator.light_manager, "host", None)
    online = "Online" if getattr(coordinator, "last_update_success", True) else "Offline"

    added_devices = sync_stats.get("added_devices_list", [])
    added_entities = sync_stats.get("added_entities_list", [])
    removed_devices = sync_stats.get("removed_devices_list", [])
    removed_entities = sync_stats.get("removed_entities_list", [])
    changed = any((added_devices, added_entities, removed_devices, removed_entities))

    lines = [
        "Die aktuelle Konfiguration wurde vom Light Manager Air geladen.",
        "",
        f"**Status:** {online}",
    ]
    if host:
        lines.append(f"**IP/Host:** `{host}`")

    lines.extend([
        "",
        "## Änderungen",
    ])

    if changed:
        lines.extend(["", "### Neue Geräte/Zonen"])
        lines.extend(_format_sync_list(added_devices))
        lines.extend(["", "### Neue Entitäten"])
        lines.extend(_format_sync_list(added_entities))
        lines.extend(["", "### Entfernte Geräte/Zonen"])
        lines.extend(_format_sync_list(removed_devices))
        lines.extend(["", "### Entfernte Entitäten"])
        lines.extend(_format_sync_list(removed_entities))
    else:
        lines.extend(["", "Keine Änderungen gefunden. Alle Geräte entsprechen der aktuellen AirStudio-Konfiguration."])

    lines.extend([
        "",
        "## Gesamt",
        f"**Zonen:** {len(coordinator.zones)}",
        f"**Aktoren/Geräte:** {_count_actuators(coordinator)}",
        f"**Szenen:** {len(coordinator.scenes)}",
        f"**Marker:** {len(coordinator.markers)}",
        f"**Wetterkanäle:** {len(coordinator.weather_channels)}",
        "",
        "Synchronisation erfolgreich abgeschlossen.",
    ])
    return "\n".join(lines)

def _write_xml_debug_files(base_path: str, xml_text: str) -> tuple[str, str, int]:
    """Write the Light Manager XML debug files and return paths and size."""
    debug_dir = Path(base_path) / "light_manager_air" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    latest_path = Path(base_path) / "light_manager_air" / "debug_config.xml"
    latest_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = debug_dir / f"config_{timestamp}.xml"

    latest_path.write_text(xml_text, encoding="utf-8")
    snapshot_path.write_text(xml_text, encoding="utf-8")
    return str(latest_path), str(snapshot_path), len(xml_text.encode("utf-8"))

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
        ("button", f"{device_id}_export_xml"),
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


def _entity_registry_keys_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> set[tuple[str, str]]:
    """Return current entity registry keys for this Light Manager Air entry."""
    registry = er.async_get(hass)
    return {
        (entity_entry.domain, entity_entry.unique_id)
        for entity_entry in registry.entities.values()
        if entity_entry.config_entry_id == entry.entry_id
        and entity_entry.platform == DOMAIN
        and entity_entry.unique_id
    }


def _entity_label_from_key(key: tuple[str, str]) -> str:
    """Return a fallback label for an entity registry key."""
    domain, unique_id = key
    return f"{domain}: {unique_id}"


def _entity_entry_label(entity_entry) -> str:
    """Return the best user-facing label for an entity registry entry."""
    return (
        getattr(entity_entry, "name", None)
        or getattr(entity_entry, "original_name", None)
        or getattr(entity_entry, "entity_id", None)
        or getattr(entity_entry, "unique_id", None)
        or "Unknown entity"
    )


def _expected_entity_labels(hass: HomeAssistant, coordinator: LightManagerAirCoordinator) -> dict[tuple[str, str], str]:
    """Build user-facing labels for expected entities from the current XML/config."""
    from .base_entity import LightManagerAirBaseEntity
    from .button import _BASIC_NAMES
    from .const import CONF_COVER_TIMINGS, CONF_ENTITY_ID, CONF_EXTERNAL_ENTITY, CONF_IGNORED_SCENE_ZONE
    from .cover import LightManagerAirCover
    from .entity_utils import command_name, is_single_action_actuator
    from .light import LightManagerAirLight
    from .switch import LightManagerAirSwitch

    device_id = coordinator.device_id
    labels: dict[tuple[str, str], str] = {
        ("remote", f"{device_id}_remote"): "Remote",
        ("sensor", f"{device_id}_last_radio_signal"): "Last Radio Signal",
        ("event", f"{device_id}_radio_event"): "Radio Signal",
        ("button", f"{device_id}_learn_radio_signal"): "Learn Radio Signal",
        ("button", f"{device_id}_show_radio_automation_yaml"): "Show Radio Automation YAML",
        ("button", f"{device_id}_synchronize"): "Synchronisieren",
        ("button", f"{device_id}_export_xml"): "Export XML",
        ("sensor", f"{device_id}_ip_address"): "IP Address",
        ("sensor", f"{device_id}_connection_status"): "Connection Status",
        ("sensor", f"{device_id}_zone_count"): "Zone Count",
        ("sensor", f"{device_id}_actuator_count"): "Actuator Count",
        ("sensor", f"{device_id}_scene_count"): "Scene Count",
        ("sensor", f"{device_id}_marker_count"): "Marker Count",
    }

    for marker in coordinator.markers:
        labels[("switch", f"{device_id}_marker_{marker.marker_id}")] = f"Marker {marker.marker_id}"

    for channel in coordinator.weather_channels:
        channel_label = getattr(channel, "channel_id", "")
        if channel.weather_id:
            labels[("weather", f"{device_id}_weather_{channel.channel_id}")] = f"Weather {channel_label}"
        else:
            if channel.temperature != "":
                labels[("sensor", f"{device_id}_temperature_{channel.channel_id}")] = f"Temperature {channel_label}"
            if channel.humidity != "" and channel.humidity > 0:
                labels[("sensor", f"{device_id}_humidity_{channel.channel_id}")] = f"Humidity {channel_label}"

    for zone in coordinator.zones:
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue
        for actuator in zone.actuators:
            label = f"{zone.name} → {actuator.name}"
            if LightManagerAirCover.check_actuator(actuator, zone.name, hass):
                labels[("cover", f"{device_id}_{zone.name}_{actuator.name}")] = label
                continue
            if LightManagerAirLight.check_actuator(actuator, zone.name, hass):
                labels[("light", f"{device_id}_{zone.name}_{actuator.type}_{actuator.name}")] = label
                continue
            if LightManagerAirSwitch.check_actuator(actuator, zone.name, hass):
                labels[("switch", f"{device_id}_{zone.name}_{actuator.name}")] = label
                continue

            if is_single_action_actuator(actuator):
                labels[("button", f"{device_id}_action_button_{zone.name}_{actuator.name}")] = label
                continue

            for index, command in enumerate(actuator.commands):
                name = command_name(command)
                if name in _BASIC_NAMES or name.endswith("%"):
                    continue
                labels[("button", f"{device_id}_button_{zone.name}_{actuator.name}_{index}_{command.name}")] = f"{label} → {name}"

    if not LightManagerAirBaseEntity.is_zone_ignored(CONF_IGNORED_SCENE_ZONE, hass):
        for index, scene in enumerate(coordinator.scenes):
            labels[("scene", f"{device_id}_scene_{scene.name}")] = scene.name
            labels[("button", f"{device_id}_scene_button_{index}_{scene.name}")] = scene.name

    for cover_cfg in hass.data.get(DOMAIN, {}).get(CONF_COVER_TIMINGS, []) or []:
        if cover_cfg.get(CONF_EXTERNAL_ENTITY, False):
            entity_id = cover_cfg[CONF_ENTITY_ID]
            labels[("cover", f"{DOMAIN}_cover_{entity_id.replace('.', '_')}")] = entity_id

    return labels


async def _async_cleanup_removed_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: LightManagerAirCoordinator,
) -> list[str]:
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
        label = _entity_entry_label(entity_entry)
        registry.async_remove(entity_entry.entity_id)
        removed.append(label)

    if removed:
        _LOGGER.info(
            "Removed %s stale Light Manager Air entities after XML sync: %s",
            len(removed),
            ", ".join(removed),
        )
    return removed


def _expected_zone_device_labels(hass: HomeAssistant, coordinator: LightManagerAirCoordinator) -> dict[str, set[tuple[str, str]]]:
    """Return expected zone device labels and their supported identifiers."""
    from .base_entity import LightManagerAirBaseEntity

    labels: dict[str, set[tuple[str, str]]] = {}
    for zone in coordinator.zones:
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue
        labels[zone.name] = {
            (DOMAIN, f"{coordinator.light_manager.mac_address}_{zone.name}"),
            (DOMAIN, f"{coordinator.device_id}_{zone.name}"),
        }
    return labels


def _added_device_labels_for_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: LightManagerAirCoordinator,
) -> list[str]:
    """Return expected zone devices that are not currently registered."""
    device_registry = dr.async_get(hass)
    existing_identifiers: set[tuple[str, str]] = set()
    for device in device_registry.devices.values():
        if entry.entry_id not in device.config_entries:
            continue
        existing_identifiers.update(
            identifier for identifier in device.identifiers if identifier[0] == DOMAIN
        )

    added: list[str] = []
    for label, identifiers in _expected_zone_device_labels(hass, coordinator).items():
        if not (identifiers & existing_identifiers):
            added.append(label)
    return sorted(added, key=str.casefold)


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
) -> list[str]:
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
    return removed

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

    existing_entity_keys = _entity_registry_keys_for_entry(hass, entry)
    expected_entity_keys = _expected_entity_registry_keys(hass, lm_coordinator)
    expected_entity_labels = _expected_entity_labels(hass, lm_coordinator)
    added_entities = sorted(
        (
            expected_entity_labels.get(key, _entity_label_from_key(key))
            for key in expected_entity_keys - existing_entity_keys
        ),
        key=str.casefold,
    )
    added_devices = _added_device_labels_for_entry(hass, entry, lm_coordinator)

    removed_entities = await _async_cleanup_removed_entities(hass, entry, lm_coordinator)
    removed_devices = await _async_cleanup_removed_devices(hass, entry, lm_coordinator)
    sync_stats = {
        "added_entities": len(added_entities),
        "added_entities_list": added_entities,
        "added_devices": len(added_devices),
        "added_devices_list": added_devices,
        "removed_entities": len(removed_entities),
        "removed_entities_list": removed_entities,
        "removed_devices": len(removed_devices),
        "removed_devices_list": removed_devices,
    }
    hass.data[DOMAIN].setdefault("last_sync_stats", {})[entry.entry_id] = sync_stats

    if any(sync_stats[key] for key in ("added_entities", "added_devices", "removed_entities", "removed_devices")):
        _LOGGER.info(
            "Light Manager Air sync changes: added_devices=%s; added_entities=%s; removed_devices=%s; removed_entities=%s",
            ", ".join(added_devices) or "none",
            ", ".join(added_entities) or "none",
            ", ".join(removed_devices) or "none",
            ", ".join(removed_entities) or "none",
        )

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

    async def _async_handle_export_xml_service(call):
        coordinator = _get_service_coordinator(call.data.get(ATTR_ENTRY_ID))
        xml_text = await hass.async_add_executor_job(
            coordinator.light_manager.load_config_xml_text,
            True,
        )
        # Refresh parsed fixtures from the same XML source so the next diagnostics
        # and sync cycle use exactly what was exported.
        coordinator.zones, coordinator.scenes = await hass.async_add_executor_job(
            coordinator.light_manager.load_fixtures,
            False,
        )
        latest_path, snapshot_path, size_bytes = await hass.async_add_executor_job(
            _write_xml_debug_files,
            hass.config.path(),
            xml_text,
        )
        hass.data[DOMAIN].setdefault("xml_debug", {})[coordinator.entry.entry_id] = {
            "latest_path": latest_path,
            "snapshot_path": snapshot_path,
            "size_bytes": size_bytes,
            "zones": len(coordinator.zones),
            "actuators": _count_actuators(coordinator),
            "scenes": len(coordinator.scenes),
            "exported_at": datetime.now().isoformat(),
        }
        persistent_notification.async_create(
            hass,
            "\n".join([
                "Die aktuelle config.xml wurde vom Light Manager Air geladen und gespeichert.",
                "",
                f"**Letzte XML:** `{latest_path}`",
                f"**Snapshot:** `{snapshot_path}`",
                f"**Größe:** {size_bytes} Bytes",
                f"**Zonen:** {len(coordinator.zones)}",
                f"**Aktoren/Geräte:** {_count_actuators(coordinator)}",
                f"**Szenen:** {len(coordinator.scenes)}",
            ]),
            title="Light Manager Air XML exportiert",
            notification_id="light_manager_air_xml_export",
        )

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

    if not hass.services.has_service(DOMAIN, SERVICE_EXPORT_XML):
        hass.services.async_register(
            DOMAIN,
            SERVICE_EXPORT_XML,
            _async_handle_export_xml_service,
            schema=EXPORT_XML_SERVICE_SCHEMA,
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
            for service in (SERVICE_RELOAD_FIXTURES, SERVICE_SEND_COMMAND, SERVICE_SEND_RAW_COMMAND, SERVICE_START_RADIO_LEARNING, SERVICE_SHOW_RADIO_AUTOMATION_YAML, SERVICE_EXPORT_XML):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok
