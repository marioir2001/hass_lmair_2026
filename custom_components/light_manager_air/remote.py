"""Remote platform for Light Manager Air."""
from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LightManagerAirCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Light Manager Air remote entity."""
    coordinator: LightManagerAirCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LightManagerAirRemote(coordinator)])


class LightManagerAirRemote(RemoteEntity):
    """Remote entity for sending configured or raw Light Manager Air commands."""

    _attr_has_entity_name = True
    _attr_translation_key = "remote"
    _attr_is_on = True
    # Keep the remote entity for existing automations, but keep it out of the
    # normal overview for new installations because its ON/OFF state does not
    # control the physical Light Manager Air hub.
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LightManagerAirCoordinator) -> None:
        """Initialize the remote."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.device_id}_remote"
        self._attr_device_info = coordinator.device_info

    async def async_turn_on(self, **kwargs) -> None:
        """The hub is always available when the entity is available."""
        self._attr_is_on = True

    async def async_turn_off(self, **kwargs) -> None:
        """Do not turn off the physical hub."""
        self._attr_is_on = True

    async def async_send_command(self, command: Iterable[str], **kwargs) -> None:
        """Send commands. Use 'Zone/Actuator/Command', 'scene:Name' or 'raw:<payload>'."""
        for item in command:
            await self._send_one(str(item))

    async def _send_one(self, command: str) -> None:
        """Send one command string."""
        if command.startswith("raw:"):
            payload = command.split(":", 1)[1]
            await self.hass.async_add_executor_job(self._coordinator.light_manager.send_raw_command, payload)
            await self._coordinator.async_refresh()
            return

        if command.startswith("scene:"):
            scene_name = command.split(":", 1)[1].strip().lower()
            for scene in self._coordinator.scenes:
                if scene.name.lower() == scene_name:
                    await self.hass.async_add_executor_job(scene.call)
                    await self._coordinator.async_refresh()
                    return
            raise HomeAssistantError(f"Scene '{scene_name}' not found")

        parts = [part.strip() for part in command.split("/")]
        if len(parts) != 3:
            raise HomeAssistantError(
                "Remote commands must use 'Zone/Actuator/Command', 'scene:Name' or 'raw:<payload>'"
            )

        zone_name, actuator_name, command_name = [part.lower() for part in parts]
        for zone in self._coordinator.zones:
            if zone.name.lower() != zone_name:
                continue
            for actuator in zone.actuators:
                if actuator.name.lower() != actuator_name:
                    continue
                for actuator_command in actuator.commands:
                    if actuator_command.name.lower() == command_name:
                        await self.hass.async_add_executor_job(actuator_command.call)
                        await self._coordinator.async_refresh()
                        return
                raise HomeAssistantError(f"Command '{parts[2]}' not found for {zone.name}/{actuator.name}")
        raise HomeAssistantError(f"Actuator '{parts[0]}/{parts[1]}' not found")
