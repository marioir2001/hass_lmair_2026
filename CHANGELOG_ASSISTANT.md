# Assistant Changelog

## 1.3.0-beta.3

Radio signal polish based on the working hotfix 8 branch.

- Extended `sensor.last_radio_signal` with a 20-entry history.
- Added `repeat_count` to make repeated RF telegrams easier to spot.
- Normalized `protocol` and `raw_code` extraction from codes such as `rffs_E3C20100`.
- Kept compatibility attributes such as `received_at`.
- Kept the existing `event.radio_signal` and learning service behavior unchanged.

## 1.3.0-beta.2-hotfix.8

- Fixed service registration after adding radio learning.

## 1.3.0b4

- Added automatic single-action button detection from the Light Manager Air XML.
- Actuators with programmed `on`/Taste 1 and empty `off`/Taste 2 are now exposed as buttons.
- Actuators with both `on` and `off` payloads stay as switches/lights.
- Empty learned IR `off` payloads such as `dta,` no longer force switch classification.

### 1.3.0b5

- Added Radio Automation Assistant.
- Learning a radio signal now creates a second persistent notification with ready-to-copy automation YAML.
- Added service `light_manager_air.show_radio_automation_yaml`.
- Added button `Show Radio Automation YAML` on the Light Manager Air device.
- Fixed EventEntity timestamp handling for `event.radio_signal` by not overriding the event state.
