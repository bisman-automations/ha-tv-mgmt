# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.0] - 2026-10-09

### Added

- **Apple TV support.** Link the Apple TV plugged into a TV, under **Configure → Apple TV**, so the room has one set of rules for both. It uses Home Assistant's Apple TV integration.
  - **Time per app:** which app is open and for how long. The home screen isn't counted.
  - **Block apps, or allow only a few:** block the apps you pick, or allow only those and block everything else.
  - **Daily limits per app,** such as `YouTube = 30`.
  - A stopped app sends the Apple TV back to its home screen, or puts it to sleep if you choose.
  - When the TV is blocked (limit reached, quiet window, or Block now), the Apple TV goes to sleep too. This can be turned off.
  - App rules follow the TV's mode: monitor only reports without closing apps, and paused or adult mode lifts them.
- **Sensors:** **Current app** and **App time today**, with per-app minutes. They're added only when an Apple TV is linked.
- **Event:** `tv_mgmt_app_blocked`, for automations.
- **Sidebar app:**
  - The dashboard shows the Apple TV's current app and today's top apps, with progress toward per-app limits.
  - Activity has a strip of app use with a colour per app, plus app opens, stops and sleeps in the log.
  - Analytics shows the top apps and how many apps were stopped.
  - **Limits → Apple TV apps** edits the app rules, with a separate checklist for block and allow-only modes.
- Diagnostics include the Apple TV and its rules.

## [1.3.0] - 2026-10-09

### Added

- **Input names.** Give any input a name you'll recognise. For example, show `com.tcl.tv` as **Apple TV**. TV Mgmt still matches on what the TV reports, so the input lock and allowed inputs keep working.
  - Set names in the sidebar app under **Limits → Input names**, which lists every input the TV has reported, or under **Configure → Input lock → Input names** as `com.tcl.tv = Apple TV` lines.
  - Names show on the **Current input** sensor, the input lock switch's attributes, the sidebar app's dashboard, activity strip and log, and the settings dropdowns.
  - Common Android TV and Google TV packages have readable names built in, such as Google TV home, YouTube and Netflix. Your names take priority.
- The **Current input** sensor has a `source` attribute with the value the TV reports.
- `tv_mgmt_input_blocked` events include `blocked_source_name` and `target_source_name`.
- **Linked to the TV's device.** The TV's own device page now lists TV Mgmt, with a link to its profile. The **TV Mgmt — Family Room TV** device still shows as connected via the TV.

### Changed

- Home Assistant's history for the **Current input** sensor shows names from now on. Earlier entries keep the value the TV reported.

## [1.2.0] - 2026-10-08

### Added

- **TV Mgmt sidebar app**, for admin users, with four tabs:
  - **Dashboard:** each TV's current input, time used and left, and parent controls: extra time, block now, mode, input lock and adult mode.
  - **Activity:** a 24-hour strip of when the TV was on and on which input, with not-allowed inputs highlighted, and a log of what happened, by day.
  - **Analytics:** screen time per day against the limit over 7, 30 or 90 days, with totals and blocked switches.
  - **Limits:** edit the daily screen time, warning time, quiet windows and adult mode length.
- **Activity log.**
  - TV Mgmt records TV on and off, input changes, blocked switches, limits reached, and parent actions.
  - It also records daily screen-time totals.
  - Activity is kept for 90 days and daily totals for about a year.
- A websocket API (`tv_mgmt/*`) behind the sidebar app. Changes through it need an admin user.

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


[Unreleased]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/bisman-automations/ha-tv-mgmt/releases/tag/v0.1.0
