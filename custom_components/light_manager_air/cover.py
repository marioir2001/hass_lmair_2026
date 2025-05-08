"""Cover platform for Light Manager Air."""
import logging
from typing import Optional, Any, Dict, cast
from datetime import timedelta
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from homeassistant.components.cover import CoverEntity, CoverEntityFeature, ATTR_POSITION
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import LightManagerAirBaseEntity, ToggleCommandMixin
from .const import DOMAIN, CONF_ENTITY_CONVERSIONS, CONF_TARGET_TYPE, CONF_ZONE_NAME, CONF_ACTUATOR_NAME, \
    CONF_COVER_TIMINGS, CONF_ENTITY_ID, CONF_TRAVEL_UP_TIME, CONF_TRAVEL_DOWN_TIME, CONF_CUSTOM_STOP_LOGIC, \
    STORAGE_VERSION, STORAGE_KEY_COVER_POSITIONS, CONF_EXTERNAL_ENTITY, CONF_INVERT_DIRECTIONS
from .coordinator import LightManagerAirCoordinator
from .helpers.travelcalculator import TravelCalculator, TravelStatus

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Light Manager Air covers."""
    coordinator: LightManagerAirCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for zone in coordinator.zones:
        # Skip ignored zones
        if LightManagerAirBaseEntity.is_zone_ignored(zone.name, hass):
            continue
            
        for actuator in zone.actuators:
            if LightManagerAirCover.check_actuator(actuator, zone.name, hass):
                entities.append(LightManagerAirCover(coordinator, zone, actuator))

    # Add timing support for external cover entities
    if hass.data[DOMAIN].get(CONF_COVER_TIMINGS):
        for entry in hass.data[DOMAIN][CONF_COVER_TIMINGS]:
            if entry.get(CONF_EXTERNAL_ENTITY, False):
                entities.append(LightManagerAirCover(
                    coordinator=coordinator, 
                    zone=None, 
                    actuator=None,
                    external_entity_id=entry[CONF_ENTITY_ID]
                ))

    async_add_entities(entities)

class LightManagerAirCover(LightManagerAirBaseEntity, ToggleCommandMixin, CoverEntity):
    """Representation of a Light Manager Air cover."""

    def __init__(self, coordinator, zone, actuator, external_entity_id=None):
        """Initialize the cover.
        
        Args:
            coordinator: The LightManager coordinator
            zone: The zone this cover belongs to (None for external entities)
            actuator: The actuator this cover controls (None for external entities)
            external_entity_id: Entity ID of an external cover to track (optional)
        """
        self._is_external = external_entity_id is not None
        self._external_entity_id = external_entity_id
        self._unsubscribe_state_listener = None
        self._invert_directions = False
        
        if self._is_external:
            # External entity tracking setup
            unique_id = f"{DOMAIN}_cover_{external_entity_id.replace('.', '_')}"
            # Get the original entity name to use as a basis for our name
            state = coordinator.hass.states.get(external_entity_id)
            original_name = state.attributes.get("friendly_name", external_entity_id) if state else external_entity_id
            self._attr_name = f"{original_name} (Managed)"
            self._zone_name = None
            self._actuator = None
            self._source_entity_id = external_entity_id  # Store original entity ID for service calls
        else:
            # Normal LightManager cover setup
            unique_id = f"{zone.name}_{actuator.name}"
            super().__init__(
                coordinator=coordinator,
                command_container=actuator,
                unique_id_suffix=unique_id,
                zone_name=zone.name
            )
            self._actuator = actuator
            self._zone_name = zone.name
        
        self.coordinator = coordinator
        self._unique_id = unique_id
        self._tc = None
        self._unsubscribe_auto_updater = None
        self._custom_stop_logic = False
        self._store = Store(
            coordinator.hass, 
            STORAGE_VERSION,
            STORAGE_KEY_COVER_POSITIONS
        )

        self._is_manual_position = False
        self._attr_available = True

        # Set supported features
        if self._is_external:
            # For external entities, we need to copy the supported features
            # from the original entity, if available
            state = coordinator.hass.states.get(external_entity_id)
            if state and "supported_features" in state.attributes:
                # Copy supported features from the original entity
                features = state.attributes["supported_features"]
                self._attr_supported_features = features
                # Always support position if we have timings
                self._attr_supported_features |= CoverEntityFeature.SET_POSITION
            else:
                # Default fallback features
                self._attr_supported_features = (
                    CoverEntityFeature.OPEN | 
                    CoverEntityFeature.CLOSE | 
                    CoverEntityFeature.STOP |
                    CoverEntityFeature.SET_POSITION
                )
        else:
            # Regular LightManager cover features
            features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
            
            # Check if this is a converted entity
            self._is_converted = False
            if CONF_ENTITY_CONVERSIONS in coordinator.hass.data[DOMAIN]:
                for conversion in coordinator.hass.data[DOMAIN][CONF_ENTITY_CONVERSIONS]:
                    if (conversion[CONF_ZONE_NAME] == zone.name and 
                        conversion[CONF_ACTUATOR_NAME] == actuator.name):
                        self._is_converted = True
                        break
            
            if not self._is_converted:
                features |= CoverEntityFeature.STOP
            
            self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        """Set up the entity when added to hass."""
        # Skip parent setup for external entities
        if not self._is_external:
            await super().async_added_to_hass()

        # Initialize travel calculator after entity_id is available
        up_time = None
        down_time = None
        target_entity_id = self._external_entity_id if self._is_external else self.entity_id
        
        if self.coordinator.hass.data[DOMAIN].get(CONF_COVER_TIMINGS):
            for entry in self.coordinator.hass.data[DOMAIN][CONF_COVER_TIMINGS]:
                if entry[CONF_ENTITY_ID] == target_entity_id:
                    up_time = entry[CONF_TRAVEL_UP_TIME]
                    down_time = entry.get(CONF_TRAVEL_DOWN_TIME) or up_time
                    self._custom_stop_logic = entry.get(CONF_CUSTOM_STOP_LOGIC)
                    self._invert_directions = entry.get(CONF_INVERT_DIRECTIONS, False)
                    if not self._is_external:
                        self._attr_supported_features |= CoverEntityFeature.SET_POSITION
                    break

        if up_time:
            # Initialize TravelCalculator if both times are defined
            self._tc = TravelCalculator(
                travel_time_down=int(down_time),
                travel_time_up=int(up_time),
            )
            await self._load_stored_position()
            
            # For external entities, we need to set up state tracking
            if self._is_external:
                # Listen for state changes of the tracked entity
                self._unsubscribe_state_listener = async_track_state_change_event(
                    self.hass, [self._external_entity_id], self._handle_external_state_change
                )
                
                # Note: We can't automatically hide the original entity
                # Users should manually hide the entity in the UI if desired

    @staticmethod
    def check_actuator(actuator, zone_name, hass):
        """Check if actuator should be handled as a cover."""
        # First check if there's a conversion configured
        if CONF_ENTITY_CONVERSIONS in hass.data[DOMAIN]:
            for conversion in hass.data[DOMAIN][CONF_ENTITY_CONVERSIONS]:
                if (conversion[CONF_ZONE_NAME] == zone_name and 
                    conversion[CONF_ACTUATOR_NAME] == actuator.name):
                    return conversion[CONF_TARGET_TYPE] == "cover"

        # Default logic for native covers
        command_names = {cmd.name.lower() for cmd in actuator.commands}
        # Check for basic up/down and either stop or my command for rts-somfy
        return {"up", "down"}.issubset(command_names) and (
            "stop" in command_names or "my" in command_names)

    @property
    def unique_id(self):
        """Return unique ID for entity."""
        if self._is_external:
            return self._unique_id
        return super().unique_id
    
    @property
    def should_poll(self):
        """No need to poll as state change tracking is used."""
        if self._is_external:
            return False
        return super().should_poll
    
    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        if self._tc:
            return self._tc.is_traveling() and self._tc.travel_direction == TravelStatus.DIRECTION_UP
        
        # For external entities without travel calculator
        if self._is_external:
            state = self.hass.states.get(self._external_entity_id)
            return state is not None and state.state == "opening"
        
        return False

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        if self._tc:
            return self._tc.is_traveling() and self._tc.travel_direction == TravelStatus.DIRECTION_DOWN
            
        # For external entities without travel calculator
        if self._is_external:
            state = self.hass.states.get(self._external_entity_id)
            return state is not None and state.state == "closing"
            
        return False

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        if self._tc:
            return self._tc.is_closed()
            
        # For external entities without travel calculator
        if self._is_external:
            state = self.hass.states.get(self._external_entity_id)
            return state is not None and state.state == "closed"
        
        # Get state from parent class (marker mapping)
        is_on = super().is_on
        
        # If is_on is None (no marker mapping), return None (unknown state)
        # Otherwise, convert the boolean state (True = open, False = closed)
        return None if is_on is None else not is_on

    @property
    def current_cover_position(self) -> Optional[int]:
        """Return current position of cover in percent."""
        if self._tc:
            return self._tc.current_position()
            
        # For external entities without travel calculator
        if self._is_external:
            state = self.hass.states.get(self._external_entity_id)
            if state and "current_position" in state.attributes:
                return state.attributes["current_position"]
                
        return None

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        # Handle direction inversion if configured
        if self._invert_directions:
            await self._handle_close_command()
        else:
            await self._handle_open_command()

        # Update travel calculator
        if self._tc:
            self._is_manual_position = False
            self._tc.start_travel_up()
            self._start_auto_updater()

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        # Handle direction inversion if configured
        if self._invert_directions:
            await self._handle_open_command()
        else:
            await self._handle_close_command()

        # Update travel calculator
        if self._tc:
            self._is_manual_position = False
            self._tc.start_travel_down()
            self._start_auto_updater()
            
    async def _handle_open_command(self):
        """Handle the open command based on entity type."""
        # For external entities, we forward service calls
        if self._is_external:
            await self.hass.services.async_call(
                "cover", 
                "open_cover", 
                {"entity_id": self._external_entity_id}, 
                blocking=True
            )
        else:
            await self._send_open()
            
    async def _handle_close_command(self):
        """Handle the close command based on entity type."""
        # For external entities, we forward service calls
        if self._is_external:
            await self.hass.services.async_call(
                "cover", 
                "close_cover", 
                {"entity_id": self._external_entity_id}, 
                blocking=True
            )
        else:
            await self._send_close()

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        # For external entities, we forward service calls
        if self._is_external:
            await self.hass.services.async_call(
                "cover", 
                "stop_cover", 
                {"entity_id": self._external_entity_id}, 
                blocking=True
            )
        else:
            await self._send_stop()

        if self._tc and self._tc.is_traveling():
            self._is_manual_position = False
            self._tc.stop()
            self._stop_auto_updater()
            self.hass.async_create_task(self._save_position())

    async def async_set_cover_position(self, **kwargs: Any):
        """Move cover to a designated position."""
        position = kwargs[ATTR_POSITION]
        if not self._tc:
            return

        # For external entities, handle position setting with direction inversion if needed
        if self._is_external:
            # Apply inversion if configured
            if self._invert_directions:
                # Invert the position (0 becomes 100, 100 becomes 0, 25 becomes 75, etc.)
                inverted_position = 100 - position
                await self.hass.services.async_call(
                    "cover", 
                    "set_cover_position", 
                    {"entity_id": self._external_entity_id, "position": inverted_position}, 
                    blocking=True
                )
            else:
                # Normal position setting
                await self.hass.services.async_call(
                    "cover", 
                    "set_cover_position", 
                    {"entity_id": self._external_entity_id, "position": position}, 
                    blocking=True
                )
        else:
            # For internal covers
            current_position = self._tc.current_position()
            if position < current_position and self._tc.travel_direction != TravelStatus.DIRECTION_DOWN:
                if self._invert_directions:
                    await self._send_open()
                else:
                    await self._send_close()
            elif position > current_position and self._tc.travel_direction != TravelStatus.DIRECTION_UP:
                if self._invert_directions:
                    await self._send_close()
                else:
                    await self._send_open()

        self._is_manual_position = position != 100 and position != 0
        self._tc.start_travel(position)
        self._start_auto_updater()

    async def _send_open(self):
        if self._is_converted:
            await self.async_turn_on()
            return

        await self._send_command("up")

    async def _send_close(self):
        if self._is_converted:
            await self.async_turn_off()
            return

        await self._send_command("down")

    async def _send_stop(self):
        if self._tc and self._custom_stop_logic:
            if self._tc.travel_direction == TravelStatus.DIRECTION_UP:
                await self._send_open()
            elif self._tc.travel_direction == TravelStatus.DIRECTION_DOWN:
                await self._send_close()
        else:
            if self._is_converted:
                return  # No stop function available

            # Try "stop" and "my" commands (for Somfy RTS)
            await self._send_command(["stop", "my"])

    async def _send_command(self, command):
        """Send command to the actuator.
        
        Args:
            command: String command name or list of command names to try
        """
        commands = [command] if isinstance(command, str) else command
        
        for cmd_name in commands:
            for cmd in self._actuator.commands:
                if cmd.name.lower() == cmd_name.lower():
                    try:
                        await self.hass.async_add_executor_job(cmd.call)
                        return  # Command found and executed, exit function
                    except ConnectionError as e:
                        raise HomeAssistantError(e)
                        
        # If we get here, none of the commands were found
        raise HomeAssistantError(f"None of the commands {commands} found for actuator {self._actuator.name}")

    def _start_auto_updater(self):
        """Start interval that periodically updates the position."""
        if not self._unsubscribe_auto_updater:
            interval = timedelta(seconds=0.5)  # Update every 0.5s
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass, self._auto_updater_hook, interval
            )

    def _stop_auto_updater(self):
        """Stop the auto updater."""
        if self._unsubscribe_auto_updater:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    @callback
    async def _auto_updater_hook(self, now):
        """Called periodically while cover is moving."""
        if not self._tc:
            return

        # If target position is reached, stop movement and save position
        if self._tc.position_reached():
            if self._is_manual_position:
                self._is_manual_position = False
                await self._send_stop()
            self._tc.stop()
            self._stop_auto_updater()
            # Save position asynchronously
            self.hass.async_create_task(self._save_position())

        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        # Call parent method for non-external entities
        if not self._is_external:
            await super().async_will_remove_from_hass()
            
        # Clean up state listener for external entities
        if self._unsubscribe_state_listener is not None:
            self._unsubscribe_state_listener()
            self._unsubscribe_state_listener = None
        
        self._stop_auto_updater()
    
    @callback
    async def _handle_external_state_change(self, event):
        """Handle state changes of external cover entities."""
        if not self._tc or not self._is_external:
            return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        
        if not new_state or not old_state:
            return
            
        # Check for state changes that indicate movement
        if new_state.state == "opening" and old_state.state != "opening":
            self._is_manual_position = False
            self._tc.start_travel_up()
            self._start_auto_updater()
        elif new_state.state == "closing" and old_state.state != "closing":
            self._is_manual_position = False
            self._tc.start_travel_down()
            self._start_auto_updater()
        elif (new_state.state in ["open", "closed"] and 
              old_state.state not in ["open", "closed"]):
            # Cover has stopped movement
            if self._tc.is_traveling():
                self._tc.stop()
                self._stop_auto_updater()
                # Save position asynchronously
                self.hass.async_create_task(self._save_position())
                
        # Update position if available in attributes
        if "current_position" in new_state.attributes:
            position = new_state.attributes["current_position"]
            if position != self._tc.current_position():
                self._tc.set_position(position)
                # Save position asynchronously
                self.hass.async_create_task(self._save_position())

        # Update available state from original entity
        self._attr_available = new_state.state != "unavailable"
        self.async_write_ha_state()
        
    async def _load_stored_position(self) -> None:
        """Load the stored position for this cover."""
        if not self._tc:
            return

        position = None

        try:
            stored_data = await self._store.async_load()
            if stored_data and isinstance(stored_data, dict):
                positions = stored_data.get("positions", {})
                if self._unique_id in positions:
                    position = positions[self._unique_id]
        except Exception as err:
            entity_id = self._external_entity_id if self._is_external else self.entity_id
            _LOGGER.error("Error loading stored position for %s: %s", entity_id, err)

        # Set initial position based on entity type
        if position is None:
            if self._is_external:
                # For external entities, start with closed position
                position = self._tc.position_closed
            else:
                # For internal entities, use super state
                position = self._tc.position_closed if not super().is_on else self._tc.position_open

        self._tc.set_position(position)

    async def _save_position(self) -> None:
        """Save the current position for this cover."""
        if not self._tc:
            return

        try:
            stored_data = await self._store.async_load() or {}
            positions = stored_data.get("positions", {})
            positions[self._unique_id] = self._tc.current_position()
            await self._store.async_save({"positions": positions})
        except Exception as err:
            entity_id = self._external_entity_id if self._is_external else self.entity_id
            _LOGGER.error("Error saving position for %s: %s", entity_id, err)


