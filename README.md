# TV Mgmt

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-blue.svg)](https://www.home-assistant.io/)
[![GitHub Release](https://img.shields.io/github/v/release/bisman-automations/ha-tv-mgmt)](https://github.com/bisman-automations/ha-tv-mgmt/releases)
[![Validate](https://github.com/bisman-automations/ha-tv-mgmt/actions/workflows/validate.yml/badge.svg)](https://github.com/bisman-automations/ha-tv-mgmt/actions/workflows/validate.yml)

> Parental controls for smart TVs in Home Assistant. Lock a TV to **one input** (say, the Apple TV), give it a **daily screen-time limit**, and keep it **off at bedtime**.

Smart TVs have no real way to say "only the Apple TV." Kids flip to the built-in YouTube app, or to another HDMI input, and any controls on the Apple TV don't apply. TV Mgmt watches the TV through the integration that already runs it in Home Assistant, and it:

- **Locks the input.** If the TV switches to anything but the allowed input, it's switched straight back.
- **Limits screen time.** It counts how long the TV is on each day. When the limit is reached, the TV is turned off, and it stays off if someone turns it back on.
- **Keeps quiet windows.** The TV stays off during bedtime, school hours, or whatever windows you set.
- **Leaves parents in charge.** You can grant extra time, block now, pause the rules, or use **adult mode** to lift everything for a movie night.

It's modeled on [Apple TV Mgmt](https://github.com/jarvis2k1/ha-appletv-mgmt), but it controls the TV itself, so it works whatever is plugged into it.

```
┌──────────────────┐  state changes   ┌──────────────┐  select_source / remote keys
│ TV media_player  │ ───────────────▶ │   tv_mgmt    │ ────────────────────────────▶  back to allowed input
│ (webOS, Roku,    │  source, on/off  │  (profile)   │  media_player.turn_off
│  Android TV, …)  │                  └──────────────┘ ────────────────────────────▶  TV off when blocked
└──────────────────┘
```

## Install

### 1. Install with HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-tv-mgmt&category=integration)

1. Click the button above, or in HACS open the menu, choose **Custom repositories**, and add `https://github.com/bisman-automations/ha-tv-mgmt` as an **Integration**.
2. Download **TV Mgmt** and restart Home Assistant.

Requires **Home Assistant 2026.3 or newer**.

<details>
<summary>Manual install (no HACS)</summary>

Copy `custom_components/tv_mgmt/` into your `config/custom_components/` folder and restart Home Assistant.
</details>

### 2. Add a profile for each TV

[![Open your Home Assistant instance and start setting up TV Mgmt.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tv_mgmt)

Or go to **Settings → Devices & services → Add integration → TV Mgmt**.

1. Pick the TV's `media_player` entity. The TV needs to be set up in Home Assistant already.
2. Turn the TV on and switch it to the input you want to allow. It's preselected for you.
3. Optionally set a daily limit and quiet windows.

You can change everything later with **Configure**. Each TV gets its own profile, rules and entities.

## Supported TVs

TV Mgmt works through the integration that already controls your TV.

| Integration | Sees current input | Switches input | Notes |
| --- | --- | --- | --- |
| LG webOS | ✅ | ✅ | Uses your input names (e.g. "Apple TV") |
| Roku | ✅ | ✅ | Inputs appear alongside channels |
| SmartThings (Samsung) | ✅ | ✅ | Best option for Samsung TVs |
| Sony Bravia, Vizio, and others with `source` + `select_source` | ✅ | ✅ | Generic support |
| Android TV Remote | ✅ (as app) | ✅ HDMI keycodes via its `remote` entity | See below |
| Android TV (ADB) | ✅ (as app) | ✅ HDMI keyevents via `adb_command` | See below |
| Samsung Smart TV (`samsungtv`) | ❌ | ✅ `KEY_HDMIn` via its `remote` entity | Input forced at power-on only |

Screen-time limits and quiet windows work on any TV Home Assistant can turn off.

**Android TV / Google TV.** These integrations report the foreground app, not the HDMI port. Each TV maker shows HDMI inputs as its own app package, so switch to the allowed input before setup and its package will be listed. Pick an **HDMI number** as the input to force back to. On some TVs every HDMI port shares one package. Those TVs still block apps like YouTube or Netflix, but can't tell HDMI 1 from HDMI 2.

**Samsung.** The `samsungtv` integration can't tell which input is on screen, so it can only force the input when the TV turns on. For a full lock, add the TV through SmartThings and use that entity instead.

## What you get

Each profile is a device with these entities:

| Entity | What it does |
| --- | --- |
| **Mode** (select) | `Enforced` acts on the rules. `Monitor only` tracks time and logs input switches without acting on them. `Paused` turns all rules off. |
| **Input lock** (switch) | Turns the one-input lock on or off. Screen-time rules still apply when it's off. |
| **Adult mode** (switch) | Lifts every rule for a set time (2 hours by default), then turns itself off. |
| **Enforcement state** (sensor) | `OK`, `Warning`, `Blocked`, `Paused` or `Adult mode`, with the reason and any active quiet window. |
| **Time used today** / **Time remaining today** (sensors) | Today's screen time. Remaining is unknown when there's no daily limit. |
| **Extra time today** (sensor) | Minutes granted (or taken) today. |
| **Current input** (sensor) | What the TV is showing, and whether it's allowed. |
| **Blocked switches today** (sensor) | How many times someone tried another input, and the last one tried. |

Counters, extra time and manual blocks reset at midnight. Mode, input lock and adult mode survive restarts.

## Services

All services take the profile (`profile_id`) to act on.

| Service | What it does |
| --- | --- |
| `tv_mgmt.grant_extension` | Add minutes to today's limit (negative to take time away). |
| `tv_mgmt.force_block` | Turn the TV off and keep it off until unblocked or the day ends. |
| `tv_mgmt.unblock` | Lift a manual block. |
| `tv_mgmt.reset_usage` | Clear today's screen time, extra time and manual block. |

```yaml
action: tv_mgmt.grant_extension
data:
  profile_id: 01JABCDEF...   # pick it from the dropdown in the UI
  minutes: 30
```

## Events

| Event | When |
| --- | --- |
| `tv_mgmt_input_blocked` | Someone switched to an input that isn't allowed. Includes `blocked_source`, `target_source`, and whether it was `reverted`. |
| `tv_mgmt_warning` | Time is about to run out. Includes `remaining_minutes`. |
| `tv_mgmt_enforcement_changed` | The enforcement state changed. Includes `state`, `previous_state`, `reason` and `quiet_window`. |

For example, to announce a heads-up on a speaker before time runs out:

```yaml
automation:
  - alias: Kids TV five minute warning
    triggers:
      - trigger: event
        event_type: tv_mgmt_warning
    actions:
      - action: tts.speak
        target:
          entity_id: tts.home_assistant_cloud
        data:
          media_player_entity_id: media_player.living_room_speaker
          message: "{{ trigger.event.data.remaining_minutes }} minutes of TV left."
```

Or to get a phone notification when someone tries another input:

```yaml
automation:
  - alias: Kids TV input switch attempt
    triggers:
      - trigger: event
        event_type: tv_mgmt_input_blocked
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "{{ trigger.event.data.tv }}: someone tried {{ trigger.event.data.blocked_source }}"
```

## Settings

**Input lock**

| Setting | Default | What it does |
| --- | --- | --- |
| Allowed inputs | input showing during setup | Inputs the TV may stay on. |
| Input to force back to | first allowed | Where the TV gets switched to. |
| Delay before switching back | 2 s | Grace period before switching back. |
| Enforce when the TV turns on | on | Also corrects the input at power-on. |
| Max retries per minute | 5 | Pauses the lock if the TV keeps refusing. |

**Screen time**

| Setting | Default | What it does |
| --- | --- | --- |
| Daily screen time | 0 (no limit) | Minutes per day before the TV is turned off. |
| Warn before time runs out | 5 min | When `tv_mgmt_warning` fires. |
| Quiet windows | none | Times the TV stays off, e.g. `20:30-07:00 Bedtime, 08:00-15:00 School`. |
| Adult mode lasts | 120 min | How long adult mode lifts the rules. |

## Development

Tests cover the enforcement rules, quiet-window parsing, and the integration running inside Home Assistant: setup, the config and options flows, the input lock, screen-time enforcement, services, and upgrading from 0.1.

```bash
pip install -r requirements_test.txt
pytest
```
