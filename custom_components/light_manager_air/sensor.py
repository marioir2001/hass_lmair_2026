"""Sensor platform for Light Manager Air."""
import logging
from collections import deque

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfTemperature,
    PERCENTAGE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import LightManagerAirBaseEntity
from .const import DOMAIN, WEATHER_CHANNEL_NAME_TEMPLATE
from .coordinator import LightManagerAirCoordinator, RADIO_SIGNAL_EVENT
from .weather import WeatherChannelMixin

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Light Manager Air sensor entities."""
    coordinator: LightManagerAirCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        LightManagerAirLastRadioSignalSensor(coordinator),
    ]

    for channel in coordinator.weather_channels:
        # Skip channels that provide full weather data
        if channel.weather_id:
            continue
            
        # Add temperature sensor
        if channel.temperature != "":
            entities.append(LightManagerAirTemperatureSensor(coordinator, channel))
            
        # Add humidity sensor if value > 0
        if channel.humidity != "" and channel.humidity > 0:
            entities.append(LightManagerAirHumiditySensor(coordinator, channel))

    async_add_entities(entities)

class LightManagerAirTemperatureSensor(LightManagerAirBaseEntity, WeatherChannelMixin, SensorEntity):
    """Temperature sensor for Light Manager Air."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: LightManagerAirCoordinator, channel) -> None:
        """Initialize the sensor."""
        self.weather_channel_id = channel.channel_id
        
        name_suffix = WEATHER_CHANNEL_NAME_TEMPLATE.format(channel.channel_id)
        
        super().__init__(
            coordinator=coordinator,
            command_container=channel,
            unique_id_suffix=f"temperature_{channel.channel_id}"
        )
        
        self._attr_name = name_suffix

    @property
    def native_value(self) -> float | None:
        """Return the temperature."""
        channel = self._get_weather_channel()
        return channel.temperature if channel else None

class LightManagerAirHumiditySensor(LightManagerAirBaseEntity, WeatherChannelMixin, SensorEntity):
    """Humidity sensor for Light Manager Air."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: LightManagerAirCoordinator, channel) -> None:
        """Initialize the sensor."""
        self.weather_channel_id = channel.channel_id
        
        name_suffix = WEATHER_CHANNEL_NAME_TEMPLATE.format(channel.channel_id)
        
        super().__init__(
            coordinator=coordinator,
            command_container=channel,
            unique_id_suffix=f"humidity_{channel.channel_id}"
        )
        
        self._attr_name = name_suffix

    @property
    def native_value(self) -> int | None:
        """Return the humidity."""
        channel = self._get_weather_channel()
        return channel.humidity if channel else None 


class LightManagerAirLastRadioSignalSensor(SensorEntity):
    """Sensor exposing received Light Manager Air radio signal information."""

    _attr_has_entity_name = True
    _attr_name = "Last Radio Signal"
    _attr_icon = "mdi:radio-tower"

    def __init__(self, coordinator: LightManagerAirCoordinator) -> None:
        """Initialize the last radio signal sensor."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.device_id}_last_radio_signal"
        self._attr_device_info = coordinator.device_info
        self._last_signal_data: dict | None = None
        self._last_received = None
        self._signal_count = 0
        self._repeat_count = 0
        self._history = deque(maxlen=20)

    @property
    def native_value(self) -> str | None:
        """Return the last received radio code."""
        if self._last_signal_data:
            return self._last_signal_data.get("code")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional information about the last received signal."""
        code = self.native_value
        protocol, raw_code = _parse_radio_code(code)

        attrs = {
            "code": code,
            "protocol": protocol,
            "raw_code": raw_code,
            "signal_count": self._signal_count,
            "repeat_count": self._repeat_count,
            "history": list(self._history),
        }

        if self._last_received is not None:
            received_at = self._last_received.isoformat()
            attrs["last_received"] = received_at
            # Keep the old attribute name for compatibility with existing automations.
            attrs["received_at"] = received_at

        # Keep any future payload fields from the event without overwriting the normalized fields above.
        for key, value in (self._last_signal_data or {}).items():
            attrs.setdefault(key, value)

        return attrs

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self.async_on_remove(
            self.hass.bus.async_listen(RADIO_SIGNAL_EVENT, self._handle_radio_signal)
        )

    @callback
    def _handle_radio_signal(self, event) -> None:
        """Handle a received radio signal event."""
        signal_data = dict(event.data or {})
        code = signal_data.get("code")
        now = dt_util.utcnow()
        protocol, raw_code = _parse_radio_code(code)

        if code and self.native_value == code:
            self._repeat_count += 1
        else:
            self._repeat_count = 1 if code else 0

        self._last_signal_data = signal_data
        self._last_received = now
        self._signal_count += 1

        self._history.appendleft(
            {
                "code": code,
                "protocol": protocol,
                "raw_code": raw_code,
                "received_at": now.isoformat(),
                "repeat_count": self._repeat_count,
            }
        )
        self.async_write_ha_state()


def _parse_radio_code(code: str | None) -> tuple[str | None, str | None]:
    """Split a Light Manager radio code into protocol and raw code parts."""
    if not isinstance(code, str) or not code:
        return None, None

    if "_" not in code:
        return None, code

    protocol, raw_code = code.split("_", 1)
    return protocol.upper(), raw_code
