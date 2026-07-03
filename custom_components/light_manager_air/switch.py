"""Switch platform for Light Manager Air."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import LightManagerAirBaseEntity, ToggleCommandMixin
from .const import DOMAIN, CONF_ENTITY_CONVERSIONS, CONF_TARGET_TYPE, CONF_ZONE_NAME, CONF_ACTUATOR_NAME
from .coordinator import LightManagerAirCoordinator
from .cover import LightManagerAirCover
from .entity_utils import (
    has_on_off_commands,
    has_only_basic_toggle_commands,
    is_dimmable_actuator,
    is_hue_actuator,
)
from .lmair import LMMarker


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Light Manager Air switches."""
    coordinator: LightManagerAirCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    for marker in coordinator.markers:
        entities.append(LightManagerAirMarkerSwitch(coordinator, marker))

    for zone in coordinator.zones:
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue

        for actuator in zone.actuators:
            if LightManagerAirSwitch.check_actuator(actuator, zone.name, hass):
                entities.append(LightManagerAirSwitch(coordinator, zone, actuator))

    async_add_entities(entities)


class LightManagerAirMarkerSwitch(LightManagerAirBaseEntity, ToggleCommandMixin, SwitchEntity):
    """Representation of a Light Manager Air Marker Switch."""

    def __init__(self, coordinator: LightManagerAirCoordinator, marker: LMMarker):
        """Initialize the marker switch."""
        super().__init__(
            coordinator=coordinator,
            command_container=marker,
            unique_id_suffix=f"marker_{marker.marker_id}",
        )
        self._marker_id = marker.marker_id
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        # Marker are mostly helper/state objects. Keep them available for
        # users who need marker-based automations, but do not enable the
        # potentially large marker set by default in the entity registry.
        self._attr_entity_registry_enabled_default = False
        self._attr_icon = "mdi:bookmark-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{coordinator.light_manager.mac_address}_markers")},
            "name": "Light Manager Air Markers",
            "manufacturer": "JBMedia",
            "model": "Marker",
            "via_device": (DOMAIN, coordinator.light_manager.mac_address),
            "sw_version": coordinator.light_manager.fw_version,
        }

    @property
    def is_on(self) -> bool:
        """Return true if the marker is on."""
        for marker in self._coordinator.markers:
            if marker.marker_id == self._marker_id:
                return marker.state
        return False


class LightManagerAirSwitch(LightManagerAirBaseEntity, ToggleCommandMixin, SwitchEntity):
    """Representation of a Light Manager Air Switch."""

    def __init__(self, coordinator, zone, actuator):
        """Initialize the switch."""
        unique_id = f"{zone.name}_{actuator.name}"
        super().__init__(
            coordinator=coordinator,
            command_container=actuator,
            unique_id_suffix=unique_id,
            zone_name=zone.name,
        )
        self._actuator = actuator

    @staticmethod
    def check_actuator(actuator, zone_name, hass):
        """Check if actuator should be handled as a switch."""
        if CONF_ENTITY_CONVERSIONS in hass.data[DOMAIN]:
            for conversion in hass.data[DOMAIN][CONF_ENTITY_CONVERSIONS]:
                if (
                    conversion[CONF_ZONE_NAME] == zone_name
                    and conversion[CONF_ACTUATOR_NAME] == actuator.name
                ):
                    return conversion[CONF_TARGET_TYPE] == "switch"

        if LightManagerAirCover.check_actuator(actuator, zone_name, hass):
            return False

        if is_hue_actuator(actuator) or is_dimmable_actuator(actuator):
            return False

        return has_only_basic_toggle_commands(actuator) or (
            actuator.type == "http" and has_on_off_commands(actuator)
        )
