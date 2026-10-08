# TV Management for Home Assistant

Lock a TV to the inputs you allow. If someone switches to anything else, TV Management switches it straight back. For example, a kids' TV can be locked to only the Apple TV input.

It doesn't talk to TVs directly. It works on top of the integration that already runs your TV in Home Assistant.

## How it works

1. Pick the TV's `media_player` entity.
2. Choose the **allowed inputs** and the **input to force back to**.
3. Whenever the TV lands on something that isn't allowed, it waits a moment (configurable), then switches back.
4. Optionally, it forces the input every time the TV turns on.

A retry limit stops it from spamming a TV that refuses to switch. It pauses and picks back up once the TV is on an allowed input.

## Supported integrations

| Integration | Sees current input | Switches input | Notes |
| --- | --- | --- | --- |
| LG webOS | ✅ | ✅ | Uses your input names (e.g. "Apple TV") |
| Roku | ✅ | ✅ | Inputs appear alongside channels |
| SmartThings (Samsung) | ✅ | ✅ | Best option for Samsung TVs |
| Sony Bravia, Vizio, and others with `source` + `select_source` | ✅ | ✅ | Generic support |
| Android TV Remote | ✅ (as app) | ✅ HDMI keycodes via its `remote` entity | See below |
| Android TV (ADB) | ✅ (as app) | ✅ HDMI keyevents via `adb_command` | See below |
| Samsung Smart TV (`samsungtv`) | ❌ | ✅ `KEY_HDMIn` via its `remote` entity | Power-on enforcement only |

### Android TV / Google TV

These integrations report the **foreground app**, not the HDMI port. Each TV maker shows HDMI inputs as its own app package.

Switch the TV to the input you want to allow before setup. Its package then appears in the list. Pick an **HDMI number** as the input to force back to.

On some TVs every HDMI port shares one package. Those TVs can still block apps like YouTube or Netflix, but can't tell HDMI 1 from HDMI 2.

### Samsung

The `samsungtv` integration can't tell which input is on screen. With it, TV Management can only force the input when the TV turns on.

For a full lock, add the TV through SmartThings and pick that entity instead.

## Entities

- **Input lock** (switch): turns enforcement on or off. Its state survives restarts.
  - Attributes: current input, allowed inputs, target, and why it paused (if it did).
- **Blocked switches** (sensor): counts how many times it switched the TV back.

Each block also fires a `tv_mgmt_blocked` event:

```yaml
event_type: tv_mgmt_blocked
data:
  config_entry_id: ...
  tv: Kids TV
  blocked_source: YouTube
  target_source: Apple TV
```

Example: get a phone notification when someone tries to switch.

```yaml
automation:
  - alias: Kids TV switch attempt
    triggers:
      - trigger: event
        event_type: tv_mgmt_blocked
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "{{ trigger.event.data.tv }}: blocked {{ trigger.event.data.blocked_source }}"
```

Unlock on a schedule by turning the **Input lock** switch off and on from an automation.

## Install

### HACS

1. In HACS, open the menu, choose **Custom repositories**, and add `https://github.com/bisman-automations/ha-tv-mgmt` as an **Integration**.
2. Install **TV Management** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → TV Management**.

### Manual

Copy `custom_components/tv_mgmt` into your `config/custom_components` folder and restart.

## Options

| Option | Default | What it does |
| --- | --- | --- |
| Allowed inputs | — | Inputs the TV may stay on |
| Input to force back to | first allowed | Where the TV gets switched to |
| Delay before switching back | 2 s | Grace period before reverting |
| Enforce when the TV turns on | on | Also corrects the input at power-on |
| Max retries per minute | 5 | Pauses if the TV keeps refusing |

You can change all of these later from the integration's **Configure** button.
