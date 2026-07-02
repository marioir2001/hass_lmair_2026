"""Event platform for Light Manager Air."""
from __future__ import annotations

import logging
from typing import Any

import homeassistant
from homeassistant.components.event import (
    EventEntity,
    EventDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Context
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import LightManagerAirCoordinator, RADIO_SIGNAL_EVENT

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Light Manager Air event entities."""
    coordinator: LightManagerAirCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([LightManagerAirRadioEvent(coordinator)])


class LightManagerAirRadioEvent(EventEntity):
    """Representation of a Light Manager Air radio event."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = ["radio_signal"]
    _attr_has_entity_name = True
    _attr_name = "Radio Signal"

    def __init__(self, coordinator: LightManagerAirCoordinator) -> None:
        """Initialize the event."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.device_id}_radio_event"
        # Attach the radio event entity to the main Light Manager Air device.
        # _attr_device_id is not enough for registry grouping; device_info is.
        self._attr_device_info = coordinator.device_info
        self._signal_data = None

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self.hass.bus.async_listen(
            RADIO_SIGNAL_EVENT,
            self._handle_event
        )

    @property
    def state(self) -> str | None:
        """Return the last radio code as state for easier debugging in developer tools."""
        if self._signal_data:
            return self._signal_data.get("code")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the last radio signal data as attributes."""
        return self._signal_data or {}

    @callback
    def _handle_event(self, event) -> None:
        """Handle the radio signal event."""
        self._signal_data = event.data

        self._trigger_event(
            RADIO_SIGNAL_EVENT,
            self._signal_data
        )

        self.async_write_ha_state()
