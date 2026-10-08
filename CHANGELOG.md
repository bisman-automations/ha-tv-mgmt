# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.3] - 2026-10-08

### Changed

- Profiles are named like Apple TV Mgmt's, so the two sit side by side. The device is now **TV Mgmt — Family Room TV** instead of **Family Room TV**, so its entities show as **TV Mgmt — Family Room TV Mode**, and so on.
  - Existing entity IDs don't change. Only the displayed names do.
- The **Mode** select uses the same shield icon as Apple TV Mgmt's.

## [1.1.2] - 2026-10-08

### Changed

- The **Current input** sensor shows `TV off` when the TV is off, instead of unknown. It's still unknown when Home Assistant can't reach the TV.

## [1.1.1] - 2026-10-08

### Fixed

- The **Keep mode in sync with** setting didn't save unless you changed it. The settings form showed the preselected Apple TV Mgmt profile, but submitted the setting empty.
- `manifest.json` keys are now in the order hassfest requires, so validation passes.

## [1.1.0] - 2026-10-08

### Added

- **Mode sync with Apple TV Mgmt.**
  - Link a TV to an Apple TV Mgmt profile, and changing the mode in either integration changes it in the other.
  - The matching profile is preselected.
  - At startup, Apple TV Mgmt's mode wins if the two differ.

## [1.0.1] - 2026-10-08

### Fixed

- Adding a TV failed with "Unknown error occurred" after picking it. The settings form sent an invalid empty unit for the max retries field.

### Added

- Tests that run the integration inside Home Assistant, covering:
  - the config and options flows
  - the input lock
  - monitor-only mode
  - screen-time enforcement
  - services
  - upgrading from 0.1

## [1.0.0] - 2026-10-08

TV Mgmt is now a full parental-control integration for smart TVs, modeled on [Apple TV Mgmt](https://github.com/jarvis2k1/ha-appletv-mgmt). The 0.1 input lock is one part of it, alongside screen-time limits and quiet windows.

### Breaking changes

- **Requires Home Assistant 2026.3 or newer.**
- Renamed from "TV Management" to **TV Mgmt**. It's now a service integration instead of a helper.
- The `tv_mgmt_blocked` event is now **`tv_mgmt_input_blocked`**. Update automations that trigger on it. The event also gained `profile_id`, `entity_id` and `reverted`, and `config_entry_id` was renamed to `profile_id`.
- The **Input lock** switch turns back on after upgrading, even if it was off.
- The blocked-switches sensor now counts **today** and resets at midnight.

Existing profiles upgrade automatically. Their input-lock settings, entity IDs and history carry over.

### Added

- **Daily screen time.**
  - Counts how long the TV is on each day.
  - Warns before time runs out.
  - Turns the TV off and keeps it off when the limit is reached.
- **Quiet windows.** Times the TV stays off, such as bedtime or school hours. Windows can cross midnight.
- **Mode** select: enforced, monitor only, or paused. In monitor-only mode, input switches and screen time are tracked and reported but not acted on.
- **Adult mode** switch that lifts every rule for a set time, then turns itself off.
- **Sensors:**
  - enforcement state (with reason)
  - time used today
  - time remaining today
  - extra time today
  - current input
- **Services:** `tv_mgmt.grant_extension`, `tv_mgmt.force_block`, `tv_mgmt.unblock` and `tv_mgmt.reset_usage`.
- **Events:** `tv_mgmt_warning` and `tv_mgmt_enforcement_changed`.
- Settings are grouped into **Input lock** and **Screen time** sections.
- Profiles appear under the TV's own device.
- **Diagnostics download.**
- **Integration icon** with light and dark versions.
- One-click HACS and setup buttons in the README.

### Changed

- The input lock reports each blocked switch once instead of on every retry.

## [0.1.0] - 2026-10-08

### Added

- Input lock: keeps a TV on its allowed inputs and switches it back when someone changes to anything else.
- Setup flow:
  - Pick the TV's existing `media_player` entity.
  - Choose its allowed inputs and the input to force back to.
- Options for:
  - the delay before switching back
  - enforcing the input when the TV powers on
  - max retries per minute, which pauses enforcement if the TV keeps refusing
- Generic support for any `media_player` with `source` and `select_source`, such as LG webOS, Roku, SmartThings, Sony Bravia and Vizio.
- Android TV Remote support:
  - Tracks the foreground app.
  - Switches HDMI with `KEYCODE_TV_INPUT_HDMI_n` through the integration's `remote` entity.
- Android TV (ADB) support:
  - Tracks the foreground app.
  - Switches HDMI with an `adb_command` keyevent.
- Samsung Smart TV (`samsungtv`) support:
  - Forces the input on power-on with `KEY_HDMIn` through the integration's `remote` entity.
- **Input lock** switch to turn enforcement on and off. Its state is restored after restart.
- **Blocked switches** sensor with the last blocked input and time.
- `tv_mgmt_blocked` event fired on every block, for automations.


[Unreleased]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.3...HEAD
[1.1.3]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/bisman-automations/ha-tv-mgmt/releases/tag/v0.1.0
