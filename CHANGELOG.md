# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-08

First release: parental controls for smart TVs, one profile per TV.

### Added

- **Input lock.** Keeps a TV on its allowed input (e.g. the Apple TV's HDMI) and switches it straight back when someone changes it. A retry limit pauses the lock if the TV keeps refusing.
- **Daily screen time.**
  - Counts how long the TV is on each day.
  - Warns before time runs out.
  - Turns the TV off and keeps it off when the limit is reached.
- **Quiet windows.** Times the TV stays off, such as bedtime or school hours. Windows can cross midnight.
- **Mode** select: enforced, monitor only, or paused.
- **Adult mode** switch that lifts every rule for a set time, then turns itself off.
- **Sensors:**
  - enforcement state (with reason)
  - time used today
  - time remaining today
  - extra time today
  - current input
  - blocked switches today
- **Services:** `tv_mgmt.grant_extension`, `tv_mgmt.force_block`, `tv_mgmt.unblock` and `tv_mgmt.reset_usage`.
- **Events:** `tv_mgmt_input_blocked`, `tv_mgmt_warning` and `tv_mgmt_enforcement_changed`.
- **Supported integrations:**
  - Any TV `media_player` with `source` and `select_source`, such as LG webOS, Roku, SmartThings, Sony Bravia and Vizio.
  - Android TV Remote and Android TV (ADB): HDMI switching through keycodes.
  - Samsung Smart TV (`samsungtv`): input forced at power-on through `KEY_HDMIn`.
- **Diagnostics download.**
- **Integration icon** with light and dark versions.

[Unreleased]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bisman-automations/ha-tv-mgmt/releases/tag/v0.1.0
