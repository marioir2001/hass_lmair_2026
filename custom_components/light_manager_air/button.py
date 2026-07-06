"""Button platform for Light Manager Air commands."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import LightManagerAirBaseEntity
from .const import (
    DOMAIN,
    CONF_IGNORED_SCENE_ZONE,
    SERVICE_RELOAD_FIXTURES,
    SERVICE_START_RADIO_LEARNING,
    SERVICE_SHOW_RADIO_AUTOMATION_YAML,
    ATTR_ENTRY_ID,
)
from .coordinator import LightManagerAirCoordinator
from .entity_utils import command_name, get_on_command, is_single_action_actuator

_BASIC_NAMES = {"on", "off", "toggle", "ein", "an", "aus", "einschalten", "ausschalten", "umschalten"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up command buttons."""
    coordinator: LightManagerAirCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        LightManagerAirSynchronizeButton(coordinator, entry.entry_id),
        LightManagerAirLearnRadioSignalButton(coordinator, entry.entry_id),
        LightManagerAirRadioAutomationYamlButton(coordinator),
    ]

    for zone in coordinator.zones:
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue
        for actuator in zone.actuators:
            # AirStudio command-style entries that only have Taste 1 programmed
            # are stateless actions and should be exposed as one button using
            # the actuator name. Taste 2/off being programmed keeps the legacy
            # switch/light representation.
            if is_single_action_actuator(actuator):
                command = get_on_command(actuator)
                if command is not None:
                    entities.append(LightManagerAirActuatorActionButton(coordinator, zone.name, actuator, command))
                continue

            for index, command in enumerate(actuator.commands):
                name = command_name(command)
                # Toggle and percentage dim commands already have better native entity controls.
                if name in _BASIC_NAMES or name.endswith("%"):
                    continue
                entities.append(LightManagerAirCommandButton(coordinator, zone.name, actuator, command, index))

    if not LightManagerAirBaseEntity.is_zone_ignored(CONF_IGNORED_SCENE_ZONE, hass):
        for index, scene in enumerate(coordinator.scenes):
            entities.append(LightManagerAirSceneButton(coordinator, scene, index))

    async_add_entities(entities)


class LightManagerAirCommandButton(ButtonEntity):
    """Button for a single actuator command."""

    def __init__(self, coordinator, zone_name, actuator, command, index):
        """Initialize the command button."""
        self._coordinator = coordinator
        self._command = command
        self._attr_name = f"{actuator.name} {command.name}"
        self._attr_unique_id = f"{coordinator.device_id}_button_{zone_name}_{actuator.name}_{index}_{command.name}"
        self._attr_device_info = {
            "identifiers": {
                (DOMAIN, f"{coordinator.light_manager.mac_address}_{zone_name}"),
                (DOMAIN, f"{coordinator.device_id}_{zone_name}"),
            },
            "name": zone_name,
            "via_device": (DOMAIN, coordinator.light_manager.mac_address),
            "model": "Zone",
            "sw_version": coordinator.light_manager.fw_version,
            "suggested_area": zone_name,
        }

    async def async_press(self) -> None:
        """Press the button."""
        try:
            await self.hass.async_add_executor_job(self._command.call)
            await self._coordinator.async_refresh()
        except ConnectionError as exc:
            raise HomeAssistantError(exc) from exc


class LightManagerAirActuatorActionButton(ButtonEntity):
    """Button for an AirStudio actuator with only Taste 1 programmed."""

    def __init__(self, coordinator, zone_name, actuator, command):
        """Initialize the actuator action button."""
        self._coordinator = coordinator
        self._command = command
        self._attr_name = actuator.name
        self._attr_icon = "mdi:gesture-tap-button"
        self._attr_unique_id = f"{coordinator.device_id}_action_button_{zone_name}_{actuator.name}"
        self._attr_device_info = {
            "identifiers": {
                (DOMAIN, f"{coordinator.light_manager.mac_address}_{zone_name}"),
                (DOMAIN, f"{coordinator.device_id}_{zone_name}"),
            },
            "name": zone_name,
            "via_device": (DOMAIN, coordinator.light_manager.mac_address),
            "model": "Zone",
            "sw_version": coordinator.light_manager.fw_version,
            "suggested_area": zone_name,
        }

    async def async_press(self) -> None:
        """Press the button."""
        try:
            await self.hass.async_add_executor_job(self._command.call)
            await self._coordinator.async_refresh()
        except ConnectionError as exc:
            raise HomeAssistantError(exc) from exc


class LightManagerAirSceneButton(ButtonEntity):
    """Button for a Light Manager Air scene."""

    def __init__(self, coordinator, scene, index):
        """Initialize the scene button."""
        self._coordinator = coordinator
        self._scene = scene
        self._attr_name = f"Scene {scene.name}"
        self._attr_unique_id = f"{coordinator.device_id}_scene_button_{index}_{scene.name}"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        """Press the scene button."""
        try:
            await self.hass.async_add_executor_job(self._scene.call)
            await self._coordinator.async_refresh()
        except ConnectionError as exc:
            raise HomeAssistantError(exc) from exc


class LightManagerAirSynchronizeButton(ButtonEntity):
    """Button that reloads/synchronizes the Light Manager Air XML configuration."""

    _attr_has_entity_name = True
    _attr_name = "Synchronize"
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator, entry_id):
        """Initialize the synchronize button."""
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device_id}_synchronize"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        """Synchronize the current XML/configuration from the Light Manager Air."""
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_RELOAD_FIXTURES,
            {ATTR_ENTRY_ID: self._entry_id},
            blocking=False,
        )


class LightManagerAirLearnRadioSignalButton(ButtonEntity):
    """Button that starts the radio learning mode."""

    _attr_has_entity_name = True
    _attr_name = "Learn Radio Signal"
    _attr_icon = "mdi:radio-handheld"

    def __init__(self, coordinator, entry_id):
        """Initialize the learn button."""
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device_id}_learn_radio_signal"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        """Start learning the next radio signal."""
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_START_RADIO_LEARNING,
            {ATTR_ENTRY_ID: self._entry_id},
            blocking=False,
        )


class LightManagerAirRadioAutomationYamlButton(ButtonEntity):
    """Button that shows automation YAML for the last learned radio signal."""

    _attr_has_entity_name = True
    _attr_name = "Show Radio Automation YAML"
    _attr_icon = "mdi:file-code-outline"

    def __init__(self, coordinator):
        """Initialize the automation YAML button."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.device_id}_show_radio_automation_yaml"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        """Show the automation YAML for the last learned radio signal."""
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_SHOW_RADIO_AUTOMATION_YAML,
            {},
            blocking=False,
        )
