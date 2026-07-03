"""Light platform for Light Manager Air."""
import logging
from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_BRIGHTNESS,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import LightManagerAirBaseEntity, ToggleCommandMixin
from .const import DOMAIN, CONF_ENTITY_CONVERSIONS, CONF_TARGET_TYPE, CONF_ZONE_NAME, CONF_ACTUATOR_NAME
from .coordinator import LightManagerAirCoordinator
from .cover import LightManagerAirCover
from .entity_utils import is_dimmable_actuator, is_hue_actuator, has_only_basic_toggle_commands

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Light Manager Air lights."""
    coordinator: LightManagerAirCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for zone in coordinator.zones:
        # Skip ignored zones
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue
            
        for actuator in zone.actuators:
            if LightManagerAirLight.check_actuator(actuator, zone.name, hass):
                entities.append(LightManagerAirLight(coordinator, zone, actuator))

    async_add_entities(entities)

class LightManagerAirLight(LightManagerAirBaseEntity, ToggleCommandMixin, LightEntity):
    """Representation of a Light Manager Air light."""

    @staticmethod
    def check_actuator(actuator, zone_name, hass):
        """Check if actuator should be handled as a light."""
        # First check if there's a conversion configured
        if CONF_ENTITY_CONVERSIONS in hass.data[DOMAIN]:
            for conversion in hass.data[DOMAIN][CONF_ENTITY_CONVERSIONS]:
                if (conversion[CONF_ZONE_NAME] == zone_name and 
                    conversion[CONF_ACTUATOR_NAME] == actuator.name):
                    return conversion[CONF_TARGET_TYPE] == "light"

        # Default logic
        if LightManagerAirCover.check_actuator(actuator, zone_name, hass):
            return False

        # Philips Hue HTTP commands are lights even though their actuator type is "http".
        if is_hue_actuator(actuator):
            return True

        # Simple on/off/toggle actuators are better represented as SwitchEntity.
        if has_only_basic_toggle_commands(actuator):
            return False

        # Dimmable actuators stay in the light domain.
        if is_dimmable_actuator(actuator):
            return True

        return actuator.type not in ("ipcam", "http")

    @staticmethod
    def _check_dimmable(actuator):
        """Check if light is dimmable."""
        return is_dimmable_actuator(actuator)

    @staticmethod
    def _get_closest_brightness_command(actuator, brightness_pct):
        """Get the command closest to the desired brightness percentage."""
        if not LightManagerAirLight._check_dimmable(actuator):
            return None

        # Filter and convert percentage commands
        pct_commands = []
        for cmd in actuator.commands:
            if "%" in cmd.name:
                try:
                    pct = int(cmd.name.replace("%", ""))
                    pct_commands.append((pct, cmd))
                except ValueError:
                    continue

        if not pct_commands:
            return None

        # Find closest percentage
        pct_commands.sort(key=lambda x: abs(x[0] - brightness_pct))
        return pct_commands[0][1]

    def __init__(self, coordinator, zone, actuator):
        """Initialize the light."""
        unique_id = f"{zone.name}_{actuator.type}_{actuator.name}"
        super().__init__(
            coordinator=coordinator,
            command_container=actuator,
            unique_id_suffix=unique_id,
            zone_name=zone.name
        )
        self._actuator = actuator
        if self._check_dimmable(actuator):
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    async def async_turn_on(self, **kwargs):
        """Turn the light on."""
        if ATTR_BRIGHTNESS in kwargs and self._check_dimmable(self._actuator):
            brightness_pct = round((kwargs[ATTR_BRIGHTNESS] / 255) * 100)
            cmd = self._get_closest_brightness_command(self._actuator, brightness_pct)
            if cmd:
                try:
                    await self.hass.async_add_executor_job(cmd.call)
                    return
                except ConnectionError as e:
                    raise HomeAssistantError(e)

        await super().async_turn_on(**kwargs)