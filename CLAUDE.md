# TV Mgmt: working on this repo

TV Mgmt is a HACS custom integration (`custom_components/tv_mgmt`, domain `tv_mgmt`) that adds parental controls to smart TVs in Home Assistant: an input lock, screen-time limits, quiet windows, and rules for the Apple TV plugged into the TV. It's modeled on [Apple TV Mgmt](https://github.com/jarvis2k1/ha-appletv-mgmt), and [Media Mgmt](https://github.com/bisman-automations/ha-media-mgmt) follows its structure and style for speakers. Keep the two consistent.

TV Mgmt works through other integrations' entities (the TV's `media_player`, the Apple TV's `media_player` and `remote`), never by talking to devices directly.

The sidebar app is the product. Every setting should be manageable there, not only in the config/options flow.

## Layout

| Path | What it is |
| --- | --- |
| `manager.py` | `TVManager`, one per TV (config entry). Counts screen time, makes decisions, turns the TV off, follows and wakes the Apple TV, announces warnings. |
| `backends/` | Adapters over the TV's `media_player`: generic `source`/`select_source`, Android TV Remote, Android TV (ADB), Samsung. |
| `guard.py` | The input lock. |
| `streaming.py` | `StreamingBox`: the linked Apple TV. App and show rules, stopping, sleep and wake, what's playing. |
| `quiet.py`, `state.py`, `activity.py`, `names.py`, `apps.py`, `media.py` | **Pure modules: no Home Assistant imports.** Unit-tested standalone through `tests/conftest.py` (as package `tv_mgmt_pure`). Keep them pure. |
| `slides.py` | Draws full-screen messages for AirPlay (Pillow only). `scripts/make_airplay_videos.py` rebuilds the videos in `frontend/airplay/` and the previews in `docs/images/`. Fonts in `fonts/` are Inter (OFL), subset to Latin. |
| `announce.py`, `messages.py` | Warnings and dashboard messages: text to speech, notify (TV screen), and AirPlay videos made with ffmpeg. |
| `remote_keys.py` | Maps remote buttons to each TV integration's service. |
| `storage.py` | `ProfileState` (today's counters and controls) and the activity log, each in its own `Store`. |
| `websocket.py` | The sidebar app's API (`tv_mgmt/*` commands). |
| `frontend/tv-mgmt-panel.js` | The sidebar app: one plain web component, **no build step**. |
| `panel.py`, `access.py` | Sidebar registration, and who may use it (admins plus parents an admin picks). |
| `config_flow.py` | Adds a TV. The options flow has sections: input lock, screen time, Apple TV, warnings, Apple TV Mgmt sync. |
| `entity.py`, `sensor.py`, `switch.py`, `select.py` | Entities, all on one service device per TV. |
| `scripts/panel-preview/` | A page that fakes Home Assistant with sample TVs, and `shoot.py`, which screenshots every tab. |

## Rules that keep it working

- **Support Home Assistant 2026.3 and newer** (`hacs.json`).
  - Device linking has two paths, chosen by `hasattr(registry, "async_get_devices")`. From 2026.8, TV Mgmt's device carries the TV's and the Apple TV's identifiers, so they show as linked devices. Older versions use `via_device` and add the entry to the TV's device.
  - On 2026.8+ never pass `via_device`, even as `None` (deprecated, removed in 2027.8), and never read `DeviceEntry.config_entries`.
  - `DeviceInfo` is a dataclass from 2026.11: build it with keyword arguments, never as a dict.
  - Never pair the update listener with reloading methods in a config flow (`async_update_reload_and_abort`, `_abort_if_unique_id_configured(updates=...)`): that's an error from 2026.12. The update listener reloads the entry when options change; the sidebar app saves settings by updating options.
- **State listeners must be `@callback`.** A plain function or lambda passed to `async_track_state_change_event` runs in a worker thread.
- **Config flow sections are `vol.Required` with no default.** An optional section with a default is submitted as that default, dropping what the form showed.
- **Never set a `NumberSelector` unit to `None`**; leave the key out instead.
- **Stored data must stay backward compatible.** New `ProfileState` fields need defaults, since unknown keys are dropped on load.
- **Access checks:** every websocket command uses `@require_access`, admin-only ones (access settings) use `require_admin`, and entity actions and services call `async_ensure_allowed`. Automations (no user) are always allowed.
- **Register static paths in `async_setup`**, not lazily: the web server may not accept new routes later.
- **Panel:**
  - Escape everything with `esc()`. Wrap every `localStorage` use in try/catch.
  - Keep Home Assistant's theme variables so it works in light and dark mode. Check phone width (390px) as well as desktop.
  - Don't redraw while someone is typing (`_typing()`); live updates catch up on blur.
  - TV cards are collapsed by default and remember which are open on the device (`tv-mgmt-expanded`).
- `manifest.json` keys: `domain`, `name`, then alphabetical (hassfest).
- `strings.json` and `translations/en.json` must be identical.

## Testing

Run the suite on both Python versions. CI runs Python 3.13 (Home Assistant before 2026.8, the older device path) and 3.14 (newest Home Assistant):

```bash
pip install -r requirements_test.txt   # in a Python 3.13 and a Python 3.14 venv
pytest -q
```

- Treat any `Detected that custom integration 'tv_mgmt' ...` warning as a bug to fix, even if tests pass. To see them: `pytest -q -o log_cli=true --log-cli-level=WARNING | grep Detected`.
- To try the next Home Assistant before it's released, clone `home-assistant/core`, install its `pyproject.toml` requirements and then the clone with `--no-deps` into the Python 3.14 venv, and run pytest from Python with Home Assistant imported first (it aliases `httpx`): `python -c "import homeassistant, sys, pytest; sys.exit(pytest.main(['tests', '-q']))"`.
- `tests/test_*.py` cover the pure modules; `tests/ha/` runs inside Home Assistant. `tests/ha/form.py` serializes a form's schema on any Home Assistant version.
- The message tests need `ffmpeg` installed.
- For panel changes, run `python scripts/panel-preview/shoot.py <out dir>` (needs Playwright with Chromium) and look at the screenshots. It fails on console errors or sideways scrolling. Add sample data to `scripts/panel-preview/index.html` when a feature needs it.

## Writing

- User-facing text (README, CHANGELOG, panel, `strings.json`) is plain and short, written to a parent, not a developer. Say what happens, in the order it happens.
- **Never put the maintainer's own account details** (account numbers, service IDs, addresses, entity IDs or names from his home) in the README, docs, screenshots or tests. Use placeholders and sample data.
- Screenshots in `docs/images` use sample data.

## Releasing

1. Check what's released: `git ls-remote --tags origin | awk -F/ '{print $3}' | sort -V | tail -1`. Use `sort -V`: plain sorting puts `v1.10.0` before `v1.9.0`. Never add changes to a version that's already released.
2. Bump `version` in `manifest.json` (semantic versioning).
3. Add a section to `CHANGELOG.md` (Keep a Changelog), with the compare link at the bottom.
4. Update the README for anything a user would notice.
5. Push to `main`. `draft-release.yml` creates or updates a draft GitHub release for that version. The maintainer publishes it, which is what HACS users get. Don't create tags or publish releases.

`test.yml` (pytest on both Python versions) and `validate.yml` (hassfest and the HACS check) must pass.
