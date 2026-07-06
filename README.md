[![GitHub release (latest by date)](https://img.shields.io/github/v/release/kmifka/hass_lmair)](https://github.com/kmifka/hass_lmair/releases/latest)

# Light Manager Air for Home Assistant

A modern custom integration for the **JBMedia Light Manager Air**, providing advanced device management, smart synchronization, diagnostics and radio integration.

## Credits

This project is based on the excellent work of **kmifka**.

Since 2026 it has been actively maintained and significantly extended by **MarioIR2001**.

New features include:

- Smart Synchronization
- Automatic Cleanup
- XML Export
- Diagnostics
- Radio Learning Mode
- Last Radio Signal Sensor
- Localization

## ✨ Features

### Device Support

- 💡 Lights (including dimming)
- 🔌 Switches
- 🪟 Covers with position support
- 🎬 Scenes
- 📍 Markers
- 🌦 Weather stations

### 🔄 Smart Synchronization

The integration provides a built-in synchronization mechanism to keep Home Assistant in sync with the current Light Manager Air configuration.

Features:

- One-click synchronization
- Automatic discovery of new devices
- Automatic discovery of new zones
- Automatic removal of deleted devices
- Automatic removal of deleted zones
- Automatic entity type migration (e.g. Cover → Switch)
- Stable entity IDs whenever possible

### Radio Support

- 📡 433 MHz and 868 MHz receiver
- Last Radio Signal sensor
- Learn Radio Signal button
- YAML automation generator
- Radio Event entity

### Diagnostics

- XML export
- Connection status
- Device statistics
- Zone statistics
- Marker statistics

---

## Installation

### Option 1: Manual Installation

1. Copy the `light_manager_air` folder to the `custom_components` directory in your Home Assistant configuration folder.
2. Restart Home Assistant.
3. Add the integration via the UI:
   Go to **Settings** → **Devices & Services** → **Add Integration** and search for "Light Manager Air".

### Option 2: Installation via HACS (Home Assistant Community Store)

1. **Ensure HACS is Installed**
   If you don’t have HACS installed, follow the [HACS installation guide](https://hacs.xyz/docs/use/).

2. **Add the Custom Repository**
   - Open Home Assistant and navigate to **HACS** → **Integrations**.
   - Click the **three dots menu** in the top-right corner and select **Custom repositories**.
   - Add the following repository URL:
     ```
     https://github.com/marioir2001/hass_lmair_2026
     ```
   - Select **Integration** as the category.
   - Click **Add**.

3. **Install the Integration**
   - Search for "Light Manager Air" in the HACS integrations list.
   - Click **Install** to download and install the integration.

4. **Restart Home Assistant**
   to apply changes.

5. **Add the integration via the UI**:
   Go to **Settings** → **Devices & Services** → **Add Integration** and search for "Light Manager Air".

---

## Important Notes on Unique Zone and Actuator Names

It is crucial that each combination of zone and actuator name in the Light Manager is unique, as these are used to generate entities in Home Assistant. If the names are not unique, only the first occurrence will be added to Home Assistant, and all subsequent entities will be skipped. Changes to zones or actuators in the Light Manager will result in duplicate or new entries in Home Assistant.

---

## Configuration

### Polling Settings

The Light Manager Air relies on polling for updates because it does not support event-based communication. This integration allows you to adjust the polling intervals for:

1. **Marker Updates**: Updates the status of markers (Default: `5000 ms`).
2. **Radio Signals**: Checks for 433 MHz and 868 MHz signals (Default: `2000 ms`).
3. **Weather Updates**: Retrieves weather data from connected weather stations (Default: `300000 ms`).

You can customize these intervals to suit your needs or disable polling entirely if not required:

1. Navigate to **Settings** → **Devices & Services**.
2. Locate the Light Manager Air integration and click **Options**.
3. Set your desired intervals or disable polling by uncheck the checkbox.

⚠️ **Warning**: Short intervals improve response times but may impact performance. Use default settings as a starting point and adjust based on your system's capabilities.

---

## 🔄 Synchronizing the Light Manager Air Configuration

Whenever you make changes in AirStudio (for example adding, renaming or removing zones, actuators or scenes), Home Assistant can synchronize its configuration with the current Light Manager Air configuration.

### Available options

You can start the synchronization in two different ways:

- Press the **Synchronize** button on the Light Manager Air device.
- Call the service:

```text
light_manager_air.reload_fixtures
```

If you have multiple Light Manager Air devices, you can optionally provide the corresponding `entry_id`.

### What happens during synchronization?

The integration compares the current Light Manager Air configuration with the existing Home Assistant entities and automatically performs the following tasks:

- ✅ Detects newly created zones
- ✅ Detects newly created actuators
- ✅ Detects newly created scenes
- ✅ Removes deleted zones
- ✅ Removes deleted actuators
- ✅ Detects entity type changes (for example Cover → Switch)
- ✅ Keeps existing entity IDs whenever possible

After the synchronization has finished, a notification summarizes the result.

> **Note:** The Light Manager Air does not automatically notify Home Assistant when its configuration changes. Therefore, synchronization must be started manually after making changes in AirStudio.

---

## 📡 Radio Learning & Automation

The Light Manager Air integration includes a built-in radio learning mode that makes it easy to discover new 433 MHz and 868 MHz radio signals and create Home Assistant automations.

### Starting Learning Mode

Learning mode can be started in two different ways:

- Press the **Learn Radio Signal** button on the Light Manager Air device.
- Call the service:

```text
light_manager_air.start_radio_learning
```

The integration will wait for the next received radio signal.

---

### Learned Signal Event

When a signal is received while learning mode is active, the integration fires the event:

```text
light_manager_air_radio_signal_learned
```

Example event data:

```yaml
code: rffs_E3C20100
protocol: RFFS
raw_code: E3C20100
```

At the same time a persistent notification is created in Home Assistant showing the received signal.

---

### Last Radio Signal Sensor

The entity

```text
sensor.last_radio_signal
```

is updated whenever a new radio signal is received.

Besides the current signal it also stores useful diagnostic information:

- Protocol
- Raw Code
- Repeat Counter
- Signal Counter
- Reception Timestamp
- History of the last 20 received signals

This makes it easy to analyse unknown radio devices.

Example:

    code: rffs_E3C20100
    protocol: RFFS
    raw_code: E3C20100
    received_at: "2026-07-02T12:28:08"
    last_received: "2026-07-02 12:28:08"
    signal_count: 42
    repeat_count: 3
    history:
      - "2026-07-02 12:28:08 | rffs_E3C20100"
      - "2026-07-02 12:27:59 | rffs_E3C20100"
      - "2026-07-02 12:27:41 | rffs_270B0412"
      
---

### Radio Event

Every received radio signal is also exposed as a Home Assistant event:

```text
event.radio_signal
```

This allows automations to react instantly to received RF commands.

Example:

```yaml
triggers:
  - trigger: event
    event_type: radio_signal
    event_data:
      code: rffs_E3C20100
```

---

### Automation Generator

After learning a radio signal you can press the **Show Automation YAML** button.

The integration automatically generates a ready-to-use Home Assistant automation template based on the last learned signal.

Simply copy the generated YAML into one of your automations and adjust the actions to your needs.

This greatly simplifies the creation of automations for new radio devices.

---

### Cover Timings

You can now configure covers to display their current position and set positions based on opening and closing times. To do this, add the following configuration to your `configuration.yaml` file:

```yaml
light_manager_air:
  cover_timings:
    - entity_id: "cover.jalousie"
      travel_up_time: 35.0  # Seconds for full opening
      travel_down_time: 32.0 # Seconds for full closing (optional)
      custom_stop_logic: true
```


The `custom_stop_logic` option means that the last sent command will be repeatedly sent to stop the actuator. This is particularly useful for actuators that do not have a native stop command or for those where the stop command does not work reliably.

### Marker Mapping

Markers can be used to map the states of actuators, which are by default stateless in the Light Manager Air. 

To ensure markers are updated when an actuator is operated, you need to configure the Light Manager in AirStudio. In the actuator management section of AirStudio, select the mapped marker in the "Marker | Sensor" column.

To configure marker mappings, add the following to your `configuration.yaml` file:

```yaml
light_manager_air:
  marker_mappings:
    - marker_id: 12
      entity_id: "light.garden_lights"
    - marker_id: 15
      entity_id: "cover.bedroom_blinds"
    - marker_id: 22
      entity_id: "light.dining_room"
    - marker_id: 30
      entity_id: "light.fountain_pump"
      invert: true
```

### Ignored Zones

You can configure zones to be ignored by adding them to your `configuration.yaml` file:

```yaml
light_manager_air:
  ignored_zones:
  - "Living Room"
  - "Garage"
  - "Scenes"
```

By adding "Scenes" to the `ignored_zones` list, all scenes will be ignored and not added to Home Assistant.

### Entity Type Conversion

Convert entities to different types using the `entity_conversions` configuration. This is useful for changing how entities are represented in Home Assistant:

```yaml
light_manager_air:
  entity_conversions:
    - zone_name: "Living Room"
      actuator_name: "Ceiling Light"
      target_type: "switch"
```
## 📋 Changelog

The complete release history is available in the
[CHANGELOG](CHANGELOG.md).

## 1.3.0-beta.11

#### Added
- Native Home Assistant localization support
- Translation keys for buttons
- Translation keys for diagnostic sensors
- German translations
- English translations

#### Improved
- Home Assistant native multilingual support
- Entity naming according to Home Assistant standards

#### Fixed
- Various bug fixes
