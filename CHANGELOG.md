# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bisman-automations/ha-tv-mgmt/releases/tag/v0.1.0
