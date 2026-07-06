## 1.3.0-beta.3

#### Added
- Last Radio Signal sensor
- Signal history
- Repeat counter
- Signal counter
- Learn Radio Signal service
- Learn Radio Signal button
- Reload Fixtures service

#### Changed
- Marker entities moved into a dedicated device

## 1.3.0-beta.4

#### Added
- Radio Learning Mode
- light_manager_air_radio_signal_learned event
- Radio learning notification

#### Improved
- Radio event handling
- Event entity reliability

## 1.3.0-beta.5

#### Added
- YAML Automation Generator
- Button to generate automation YAML from the last learned radio signal

#### Improved
- Radio workflow for creating Home Assistant automations

## 1.3.0-beta.6

#### Added
- Smart Synchronization
- Automatic detection of newly created devices
- Automatic detection of newly created zones
- Automatic detection of new scenes

#### Improved
- Reload service now performs a complete synchronization

## 1.3.0-beta.7

#### Added
- Automatic cleanup of removed devices
- Automatic cleanup of removed scenes
- Automatic cleanup of removed zones

#### Improved
- Entity registry cleanup
- Stable entity IDs during synchronization

## 1.3.0-beta.8

#### Added
- Automatic entity type migration
  - Cover → Switch
  - Cover → Light
  - Switch → Cover
  - and other supported conversions

#### Improved
- Synchronization engine
- Registry cleanup performance

#### Fixed
- Entity type changes no longer require deleting and re-adding the integration

## 1.3.0-beta.9

#### Added
- Synchronize button
- Synchronization result notification
- Diagnostic sensors
  - IP Address
  - Connection Status
  - Zone Counter
  - Device Counter
  - Scene Counter
  - Marker Counter

#### Improved
- User experience during synchronization

## 1.3.0-beta.10

#### Added
- XML Export button
- light_manager_air.export_xml service
- Export current XML to Home Assistant
- Timestamped XML snapshots
- XML debugging support

#### Improved
- Diagnostics for synchronization troubleshooting

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
