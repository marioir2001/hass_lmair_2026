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
