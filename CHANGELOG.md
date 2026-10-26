# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.15.1] - 2026-10-26

### Changed

- **Saving settings no longer restarts the TV.** Limits, quiet windows, allowed inputs, input names, app rules, announcements, the Apple TV Mgmt link and TV lights now take effect straight away, whether saved in the sidebar app or under **Configure**. A turn-off or input switch that's already on its way still happens, and light sync zones carry on. Linking a different Apple TV or renaming the TV still reloads it.
- On a phone, all six tabs of the sidebar app fit across the screen, so **Access** is no longer cut off. On very narrow screens the tabs scroll, and the open tab stays in view.
- New README screenshots of every tab.
- Tests now run against the oldest and newest Home Assistant that TV Mgmt supports (2026.3 and the latest).

## [1.15.0] - 2026-10-26

### Added

- **Light sync.** Lights behind or around the TV take their color from what's playing, and go back to how they were when it stops. Set it up in the sidebar app's new **Lights** tab.
  - Add up to 8 light zones per TV, each a group of lights that change together.
  - The color comes from a color you saved for the title, the artwork, the genre, or your default color, in the order you choose. One color for every light, or a palette spread across them.
  - A zone follows the TV and its Apple TV, and any other player you add, like a Music Assistant player.
  - It follows TV Mgmt's rules: nothing is colored while the TV is off or blocked, and when a blocked app is stopped the lights go back straight away.
  - When the TV turns off, its TV lights turn off and stay off. When it turns on, zones wait for the TV lights before remembering how the lights are.
  - **Shared settings** for every TV: TMDB and Trakt keys to look up artwork and genres when a player has none, and your title colors.
  - Each zone is a device with switch, sensor, number, select and button entities, and there are services to work colors out again, manage title colors and clear the artwork colors.

### Changed

- **TV lights** moved from **Limits** to the new **Lights** tab. They still work the same, and can still be picked under **Configure → TV lights**. Changing them no longer reloads the TV.

## [1.14.1] - 2026-10-24

### Fixed

- Diagnostics no longer include picture links for the TV and Apple TV (they carry a temporary access key), or the speakers and TV screen picked for warnings (their names often name people).
- Sending a message to the Apple TV no longer searches for ffmpeg in a way that could briefly hold up Home Assistant. When Home Assistant's own ffmpeg isn't set up, TV Mgmt looks it up once, in the background.

## [1.14.0] - 2026-10-18

### Added

- **TV lights.** Pick lights that go with the TV, such as a light behind it. They turn on when the TV turns on, and off when it turns off, including when TV Mgmt turns it off.
  - They don't turn on while the TV is blocked, so they don't flash on and off.
  - Pick them in the sidebar app under **Limits → TV lights**, or under **Configure → TV lights**. The dashboard lists them with the TV.

## [1.13.0] - 2026-10-17

### Added

- **Whole-home limits** ([#3](https://github.com/bisman-automations/ha-tv-mgmt/issues/3)). Set a daily or weekly limit for all the TVs together, under **Limits → Whole home** in the sidebar app.
  - Each TV's time is added up, so two TVs on for an hour count as two hours. The TVs' own limits still apply; whichever has less time left counts.
  - When the home's time runs out, every TV that counts is turned off like with its own limit, or, if you prefer, you're only told.
  - Get a message on your phone (or any notify service) when time is running low and when it's up, once per day or week. The `tv_mgmt_home_limit` event fires too.
  - Leave a TV out, such as a parents' TV, so it isn't counted or turned off.
  - The dashboard shows the home's time today and this week, with buttons to add or take away time for the whole home.
- **Loading placeholders in the sidebar app** ([#2](https://github.com/bisman-automations/ha-tv-mgmt/issues/2)). While a page loads, it shows a softly shimmering outline of what's coming instead of "Loading…". It stays still if your device is set to reduce motion, and screen readers announce what's loading.

## [1.12.0] - 2026-10-17

### Added

- **Collapsible TVs on the dashboard**, like Media Mgmt. Each TV starts collapsed to just its screen, which makes several TVs easy to see at once.
  - Collapsed, the Apple TV shows inside the TV's screen: what's playing, in which app, or that it's asleep. Tap it to open the Apple TV.
  - A line under the screen shows today's time and time left. Tap it to open the TV, with everything as before; **Show less** collapses it again.
  - Your phone or browser remembers which TVs are open.

### Fixed

- **Adding a TV after the app closed partway through** said "This is already being configured" until Home Assistant restarted. Starting over now replaces the half-done setup.

## [1.11.1] - 2026-10-17

### Fixed

- **Ready for Home Assistant 2027.8.** TV Mgmt no longer passes `via_device` for its device on Home Assistant 2026.8 and newer, where it links to the TV's and the Apple TV's devices instead. From 2026.10 this logged a deprecation warning, and it stops working in 2027.8.

## [1.11.0] - 2026-10-16

### Added

- **Block shows and movies** on the Apple TV, in any app. A blocked show or movie is stopped the same way as a blocked app: back to the home screen, or to sleep.
  - On the dashboard, tap **Block** next to a show under **Watched today**.
  - Under **Limits → Apple TV apps**, check shows and movies from everything that's been watched, or type a name. Also under **Configure → Apple TV**.
  - Names match whole, ignoring case, so blocking "Cars" doesn't block "Cars 2". A blocked series covers every episode.
  - Like app rules, they follow the mode: monitor only reports without stopping, and paused or adult mode lifts them.
  - The activity log, the `tv_mgmt_app_blocked` event (`show`) and the **Current app** sensor (`blocked_show`) say which show was stopped.

## [1.10.2] - 2026-10-16

### Fixed

- **Bedtime announcements repeated all night** when the TV couldn't be reached or wouldn't turn off ([#1](https://github.com/bisman-automations/ha-tv-mgmt/issues/1)). TV Mgmt said "The TV is turning off" again every minute it still saw the TV on.
  - The "turning off" announcement now plays once per block. It plays again only after the TV has been seen off and turned back on, or when a new block starts.
  - TV Mgmt still keeps trying to turn the TV off, but waits longer between tries, up to every 10 minutes, and logs it once.

## [1.10.1] - 2026-10-10

### Fixed

- **Linked devices on Home Assistant 2026.8 and newer.** TV Mgmt shows under **Linked devices** on the TV's and the Apple TV's device pages, and they show under TV Mgmt's.
  - Home Assistant 2026.8 gave each device a single integration, so the earlier link, which added TV Mgmt to the TV's device, no longer showed.
  - TV Mgmt's device no longer says "connected via" the TV on these versions; the linked devices replace that.
  - Copies of the TV's and the Apple TV's devices that the 2026.8 upgrade made for TV Mgmt are removed. The TV's and the Apple TV's own devices aren't touched.
  - Older Home Assistant versions keep the earlier link.
- Sending a message to the Apple TV could fail with "Unknown error" when Home Assistant's web server was already running, because TV Mgmt only set up the address for message videos when the first one was sent. It's set up at startup now.
- A message that fails to send for another reason now says why on the dashboard, instead of "Unknown error".

## [1.10.0] - 2026-10-09

### Added

- **Message the TV from the dashboard.** Type a message, or tap one like "Dinner is ready" or "Time to get ready for bed", and send it to:
  - **the Apple TV:** full screen for 10 seconds with AirPlay. It interrupts what's playing.
  - **the TV screen:** with the notify service under **Warnings on the TV**, such as Notifications for Android TV / Fire TV.
  - **speakers:** with the voice and speakers under **Warnings on the TV**.

  Only places that are set up are offered. Each message is listed in the activity log, with who sent it.
- **`tv_mgmt.send_message` action,** to send the same messages from automations.

### Changed

- Live updates no longer redraw the dashboard while you're typing in it, such as a message or extra minutes. It catches up when you leave the field.

## [1.9.0] - 2026-10-09

### Added

- **Warnings on the TV.** Under **Configure → Warnings on the TV**, TV Mgmt can tell the room when TV time is running low, and again just before it turns the TV off:
  - **Say it on speakers:** a text-to-speech voice on the media players you pick, such as "5 minutes of TV time left".
  - **Show it on the Apple TV with AirPlay:** a 10-second full-screen message, such as "5 minutes of TV time left" or "TV time is up". It interrupts what's playing.
  - **Show it on the TV screen** with a notify service, such as the Notifications for Android TV / Fire TV integration or LG webOS. Messages show over any input.
  - With a warning set up, TV Mgmt waits 10 seconds after the "turning off" message before turning off the TV and the Apple TV.
  - Warnings don't play in monitor-only, paused or adult mode, or when the TV is off.

### Changed

- **The dashboard fits the screen.** On a wide screen, each TV's card spreads out into columns: the TV and its remote, the Apple TV and what's playing, and the controls. Cards sit side by side when there's room, and stack on a phone.

## [1.8.1] - 2026-10-09

### Changed

- **The Apple TV's device page lists TV Mgmt,** as the TV's already does. If you unlink the Apple TV or pick a different one, TV Mgmt leaves the old Apple TV's device page. That device itself isn't removed.

## [1.8.0] - 2026-10-09

### Added

- **Wake the Apple TV when the TV turns on.** The Apple TV then brings the TV to its input. If HDMI-CEC doesn't do that, TV Mgmt switches the TV itself.
  - It doesn't happen while the TV is blocked, or in monitor-only, paused or adult mode.
  - It's on by default. Turn it off under **Configure → Apple TV** or in the sidebar app under **Limits → Apple TV apps**.
- **Tracks what's watched on the Apple TV:** each show, episode, movie or song, and the app it's in.
  - Time counts only while something is playing, not while it's paused.
  - The dashboard shows **Watched today**, with time per show.
  - The activity log has a line for each new episode or title, such as "Watching Bluey, Season 2, Episode 14: Hammerbarn in Disney+".
  - Analytics shows **Top shows and movies** over 7, 30 or 90 days.
  - The **Current app** sensor has `now_watching` and `show` attributes. The **App time today** sensor has `shows`, in minutes per show.
  - Apps like Prime Video that put the season and episode in the artist field are read correctly.
  - It depends on the app telling the Apple TV what's playing, which most streaming apps do.

## [1.7.0] - 2026-10-09

### Added

- **Choose who can use TV Mgmt.** A new **Access** tab in the sidebar app, for admins, lists the people in Home Assistant. Check the ones who can use TV Mgmt, such as a parent who isn't an admin. Admins always can.
  - Anyone else who opens the sidebar app sees a page saying they don't have access, with their user ID.
  - The sidebar entry stays admin-only until you give access to someone who isn't an admin. Home Assistant can't show it to only some people, so from then on everyone sees it.
- **Any amount of extra time.** Next to the extra time buttons on the dashboard, type a number of minutes and press **Add**. Use a minus sign to take time away.

### Changed

- **Only admins and people with access can change TV Mgmt.** This covers the **Input lock** and **Adult mode** switches, the **Mode** select, and the `tv_mgmt.*` actions, wherever they're used. That way a child with a Home Assistant login can't turn on adult mode from a regular dashboard. Automations and scripts that Home Assistant runs on its own aren't affected. If a parent who isn't an admin used these before, give them access under **Access**.
- The sidebar app's data, not just its controls, now needs access too.

## [1.6.0] - 2026-10-09

### Added

- **Remote buttons on the dashboard,** for the TV and the Apple TV:
  - power, volume and mute
  - a **Remote** button that opens arrow keys, OK, Back (Menu on the Apple TV) and Home. It stays open while things update.

  Only the buttons each integration can press are shown. Arrow keys work with Android TV Remote, Android TV (ADB), LG webOS, Roku, Samsung, Sony Bravia and the Apple TV. Other TVs get power and volume when their media player supports them.
- **Entity links on the dashboard.** Each TV's card links to the TV, the Apple TV and its remote, and every TV Mgmt entity, such as Mode, Input lock and Time used today. The TV's name on its screen and the Apple TV label link too. Each opens Home Assistant's usual entity dialog.
- A websocket command, `tv_mgmt/remote`, behind the remote buttons. It needs an admin user.

### Changed

- The Apple TV's **Home** button moved into its remote. **Sleep** is now the round power button beside its volume.

## [1.5.0] - 2026-10-09

### Added

- **Now playing on the dashboard.** When an Apple TV is linked, each TV's card shows the Apple TV as a media player in its own spot under the TV:
  - artwork, title, show and episode or artist, and the app it's playing in
  - whether it's playing, paused or asleep
  - a progress bar that moves live
  - play/pause, previous and next, **Home**, and **Sleep**, or **Wake** when it's asleep

  It updates as soon as Home Assistant does.
- **Lock the TV to the Apple TV.** Under **Configure → Apple TV**, pick which input the Apple TV is on, such as `HDMI 2`, or on Android TV an app like `com.tcl.tv`. TV Mgmt then:
  - always allows that input and switches the TV back to it when someone opens the TV's own apps or another input. On Android TV, switching back still uses the HDMI input you chose under Input lock.
  - switches the TV to the Apple TV when someone wakes the Apple TV, if HDMI-CEC hasn't already.
  - skips that switch in monitor-only, paused or adult mode, or when the TV is off.

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


[Unreleased]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.15.1...HEAD
[1.15.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.15.0...v1.15.1
[1.15.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.14.1...v1.15.0
[1.14.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.14.0...v1.14.1
[1.14.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.13.0...v1.14.0
[1.13.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.12.0...v1.13.0
[1.12.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.11.1...v1.12.0
[1.11.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.11.0...v1.11.1
[1.11.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.10.2...v1.11.0
[1.10.2]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.9.0...v1.10.0
[1.9.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.8.1...v1.9.0
[1.8.1]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/bisman-automations/ha-tv-mgmt/compare/v1.4.0...v1.5.0
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
