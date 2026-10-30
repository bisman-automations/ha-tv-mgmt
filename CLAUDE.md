# TV Mgmt: notes for Claude and other coding agents

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
| `quiet.py`, `state.py`, `activity.py`, `names.py`, `apps.py`, `media.py`, `home.py` | **Pure modules: no Home Assistant imports.** Unit-tested standalone through `tests/conftest.py` (as package `tv_mgmt_pure`). Keep them pure. |
| `slides.py` | Draws full-screen messages for AirPlay (Pillow only). `scripts/make_airplay_videos.py` rebuilds the videos in `frontend/airplay/` and the previews in `docs/images/`. Fonts in `fonts/` are Inter (OFL), subset to Latin. |
| `announce.py`, `messages.py` | Warnings and dashboard messages: text to speech, notify (TV screen), and AirPlay videos made with ffmpeg. |
| `remote_keys.py` | Maps remote buttons to each TV integration's service. |
| `home_limits.py` | `HomeLimits`: the whole home's limits, one for all TVs (stored in `tv_mgmt.home`, not in any config entry). Adds up each counted TV's time, gives each TV the home's time left (`decide(home_remaining=...)`), sends the messages, and makes every TV re-decide when the home's time runs out. |
| `light_sync/` | Light sync, ported from Movie Light Sync. `LightSync` (one per TV, in `__init__.py`) owns the TV's `LightZone`s (`controller.py`, one per zone). Zone settings and the shared settings (TMDB/Trakt) live in the `tv_mgmt.light_sync` Store, never in options, so saving a zone never reloads the TV. `color.py`, `metadata.py`, `storage.py` (title colors, artwork cache), `entities.py` (zone entities on a zone device linked to the TV's), `services.py`, `websocket.py` (`tv_mgmt/light_sync/*`). TV Mgmt's rules (TV off, blocked, a stopped app) hold a zone through `held_reason`. |
| `storage.py` | `ProfileState` (today's counters and controls) and the activity log, each in its own `Store`. |
| `websocket.py` | The sidebar app's API (`tv_mgmt/*` commands). |
| `frontend/tv-mgmt-panel.js` | The sidebar app: one plain web component, **no build step**. |
| `panel.py`, `access.py` | Sidebar registration, and who may use it (admins plus parents an admin picks). |
| `config_flow.py` | Adds a TV. The options flow has sections: input lock, screen time, Apple TV, warnings, TV lights, Apple TV Mgmt sync. **Reconfigure** changes the TV media player (entry data) with the same selector and one-profile-per-TV check as setup. |
| `repairs.py` | Raises a repair when a TV, Apple TV, warning speaker, TV light, or light-zone light or player is removed or renamed. The fix flow picks a replacement or removes it from the profile. |
| `entity.py`, `sensor.py`, `switch.py`, `select.py`, `binary_sensor.py`, `number.py`, `button.py` | Entities. The TV's are on one service device per TV; each light zone has its own device. |
| `scripts/panel-preview/` | A page that fakes Home Assistant with sample TVs, `shoot.py`, which screenshots every tab, and `check.py`, which clicks through the dashboard and Lights tab buttons. |

## Rules that keep it working

- **Support Home Assistant 2026.8 and newer** (`hacs.json`). Do not put `homeassistant` in `manifest.json`: hassfest rejects that key. The minimum lives in `hacs.json`.
  - Device linking is one path. TV Mgmt's device carries the TV's and the Apple TV's identifiers, so they show as linked devices. Look devices up with `async_get_device_by_identifier` or `async_get_devices` (identifiers are unique per config entry). A device has `config_entry_id`, not `config_entries`. Remove a leftover device with `async_remove_device`. Don't move the TV's device onto this profile with `new_config_entry_id`; it stays on the TV's integration.
  - Never pass `via_device`, even as `None` (deprecated, removed in 2027.8). A light zone still sets `via_device_id` so it shows under the profile. Clear a stale `via_device_id` on the profile device only when one is set.
  - `DeviceInfo` is a dataclass from 2026.11: build it with keyword arguments, never as a dict.
  - Never pair the update listener with reloading methods in a config flow (`async_update_reload_and_abort`, `_abort_if_unique_id_configured(updates=...)`): that's an error from 2026.12. The sidebar app saves settings by updating options, and the update listener applies them live with `TVManager.apply_options`, so timers, today's state and light sync zones carry on. It reloads only for options outside `LIVE_OPTIONS` (which Apple TV is linked) or a changed entry title or data. A new option must be added to `LIVE_OPTIONS` and applied there, or it reloads. Light zones aren't options at all. Reconfigure uses `async_update_reload_and_abort` and calls `detach_update_listener` first, only when the TV actually changes, so the flow's reload is the only one. Setup registers the listener again.
- **State listeners must be `@callback`.** A plain function or lambda passed to `async_track_state_change_event` runs in a worker thread.
- **Config flow sections are `vol.Required` with no default.** An optional section with a default is submitted as that default, dropping what the form showed.
- **Never set a `NumberSelector` unit to `None`**; leave the key out instead.
- **Stored data must stay backward compatible.** New `ProfileState` fields need defaults, since unknown keys are dropped on load.
- **Access checks:** every websocket command uses `@require_access`, admin-only ones (access settings) use `require_admin`, and entity actions and services call `async_ensure_allowed`. Automations (no user) are always allowed.
- **Adult mode shows totals only.** Its time goes in `adult_seconds` (and `used_seconds` only when the TV counts it). Never surface apps or titles from adult mode time: activity and analytics hide them with `hide_adult_detail`, and when it doesn't count they aren't recorded. Credit usage (`_count_usage`) before adult mode turns on, off or runs out.
- **TV lights belong to TV Mgmt only while they follow the TV.** With `tv_lights_follow` off, light sync treats them like any other light. Lights turned off by hand are held off (runtime only) until the TV next turns on or off: zones leave them out and drop them from their snapshot.
- **Register static paths in `async_setup`**, not lazily: the web server may not accept new routes later.
- **Panel:**
  - Escape everything with `esc()`. Wrap every `localStorage` use in try/catch.
  - Keep Home Assistant's theme variables so it works in light and dark mode. Check phone width (390px) as well as desktop.
  - Don't redraw while someone is typing (`_typing()`); live updates catch up on blur.
  - TV cards are collapsed by default and remember which are open on the device (`tv-mgmt-expanded`).
- `manifest.json` keys: `domain`, `name`, then alphabetical (hassfest).
- `strings.json` and `translations/en.json` must be identical.

## Testing

Run the shared checks at the end of this file against the oldest and the newest Home Assistant TV Mgmt supports, both on Python 3.14. CI does the same: `requirements_test_min.txt` pins Home Assistant 2026.8.3 (the newest harness release of 2026.8), and `requirements_test.txt` takes the newest. There's no Python 3.13 job: Home Assistant 2026.3 and newer need 3.14, and a 3.13 venv resolves 2026.2, which TV Mgmt doesn't support.

- Treat any `Detected that custom integration 'tv_mgmt' ...` warning as a bug to fix, even if tests pass. To see them: `pytest -q -o log_cli=true --log-cli-level=WARNING | grep Detected`.
- To try the next Home Assistant before it's released, clone `home-assistant/core`, install its `pyproject.toml` requirements and then the clone with `--no-deps` into the Python 3.14 venv, and run pytest from Python with Home Assistant imported first (it aliases `httpx`): `python -c "import homeassistant, sys, pytest; sys.exit(pytest.main(['tests', '-q']))"`.
- `tests/test_*.py` cover the pure modules; `tests/ha/` runs inside Home Assistant. `tests/ha/form.py` serializes a form's schema on any Home Assistant version.
- The message tests need `ffmpeg` on the PATH. CI uses the static build from the `imageio-ffmpeg` package (the shared workflow's `ffmpeg: true` input) rather than apt, which took up to 15 minutes.
- For panel changes, run `python scripts/panel-preview/shoot.py <out dir>` (needs Playwright with Chromium) and look at the screenshots. It fails on console errors or sideways scrolling. `shoot.py --docs` retakes the README screenshots, `docs/images/panel-<tab>.png`, the same way for every tab; rerun it when a tab's look changes. Add sample data to `scripts/panel-preview/index.html` when a feature needs it. The sample backend must answer like the real one (same keys, same refusals): 1.16.0 shipped a Lights tab that only worked against a sample that called a zone's id `zone_id` instead of `id`. Run `python scripts/panel-preview/check.py` too.

## Writing

- User-facing text (README, CHANGELOG, panel, `strings.json`) is plain and short, written to a parent, not a developer. Say what happens, in the order it happens.
- **Never put the maintainer's own account details** (account numbers, service IDs, addresses, entity IDs or names from his home) in the README, docs, screenshots or tests. Use placeholders and sample data.
- Screenshots in `docs/images` use sample data.

<!-- BEGIN bisman-automations shared rules -->
<!-- This block is the same in every bisman-automations repo. Edit it in
     bisman-automations/.github (templates/CLAUDE.shared.md) and copy it to
     every repo; the Validate workflow's "standards" job checks it matches. -->

## Shared rules (every bisman-automations repo)

### Releases

- The version lives in `custom_components/<domain>/manifest.json`. Anything
  else that repeats it is called out in the repo-specific notes below and is
  bumped in the same commit.
- Versions follow [Semantic Versioning](https://semver.org): patch for fixes,
  translations, docs and CI; minor for new features, options or entities;
  major when something users rely on is removed or renamed (entities,
  services, events, options) or setup has to be redone.
- Check what is released before choosing a version:
  `git ls-remote --tags origin | awk -F/ '{print $3}' | sort -V | tail -1`
  (`sort -V`: plain sorting puts `v1.10.0` before `v1.9.0`). Never add
  changes to a released version. If the manifest version has no tag yet,
  add to its CHANGELOG section instead of bumping again.
- Every push to `main` ships as a release. In that same commit, named
  `Release X.Y.Z`, bump the manifest version and write the notes directly
  under a new `## [X.Y.Z] - YYYY-MM-DD` heading in `CHANGELOG.md` (Keep a
  Changelog: Added, Changed, Deprecated, Removed, Fixed, Security), with
  that version's compare link at the bottom. There is no `## [Unreleased]`
  section and no `[Unreleased]:` compare link. New work is not collected
  under a holding heading and moved later; the release commit is where the
  heading and the entries are written. The standards check fails if a
  `## [Unreleased]` heading is present.
- Ask Donavan before every push. Push fast-forward only: never force-push,
  rebase or amend anything already pushed, or rewrite history in any other
  way. Rewriting history orphans the release tags (it broke drafting in
  ha-button-actions).
- Pushing to `main` runs **Draft release**, which creates or refreshes a
  draft `vX.Y.Z` with that version's CHANGELOG section as the notes, the
  commits since the last release and `<domain>.zip`. **Donavan publishes
  the draft himself**, and publishing creates the tag. Never create or push
  tags, publish releases, or edit published releases.
- Publishing runs **Release check**: the tag must match the manifest version
  and the CHANGELOG, and be on `main`. If it fails, fix it with a new patch
  release, not by moving the tag.

### Commits

- Author commits as `Donavan Becker <donavan.becker@icloud.com>` (set
  `git config user.name` / `user.email` in the clone). HACS's
  default-repository check needs the submitter to have a fair share of the
  commits. Credit an AI assistant with a `Co-Authored-By:` trailer if you
  like, never as the author.
- Subject lines are plain, imperative and say what changed for the user
  ("Show Pico remotes as unavailable when Lutron isn't running"). No
  `fix:`/`feat:` prefixes. Release commits are "Release X.Y.Z".
- Commit and push only when asked.

### CI (shared workflows)

- `.github/workflows/` holds four thin callers of the reusable workflows in
  [bisman-automations/.github](https://github.com/bisman-automations/.github):
  `test.yml` (lint, frontend, pytest on the oldest supported and the newest
  Home Assistant, plus the upcoming one as information only), `validate.yml`
  (hassfest, HACS, standards), `draft-release.yml` and `release-check.yml`.
  Don't add jobs to the callers. Use the shared workflow's inputs, or change
  the shared workflow so every repo gets the improvement.
- `requirements_test.txt` leaves `pytest-homeassistant-custom-component`
  unpinned (newest Home Assistant) and pins ruff (and mypy where used).
  `requirements_test_min.txt` pins the harness release for the minimum in
  `hacs.json`: the newest harness release of that month. CI fails if they
  drift apart.
- Raising the minimum Home Assistant means changing, together: `hacs.json`,
  `requirements_test_min.txt`, `python-min` in `test.yml` (3.13 up to
  2026.2, 3.14 from 2026.3), the README's Home Assistant badge, and a
  CHANGELOG entry.
- Home Assistant 2026.3 and newer need Python 3.14 (3.14.2+).
- The published hassfest rejects `categories` in `manifest.json` and Pillow
  in `requirements`. Leave both out.
- Dependabot (`.github/dependabot.yml`) proposes action and pinned-tool
  updates weekly. It ignores the test harness on purpose.
- Test and Validate also run every Thursday, the day after Home Assistant's
  betas come out. The **next** test job (newest pre-release harness) is
  information only: when it fails, fix the cause before that release ships.

### Before saying work is done

Run from the repo root, in a Python 3.14 venv with `requirements_test.txt`
installed:

```
ruff check .
ruff format --check .
pytest
```

Also run `node --test tests/*.mjs` when the repo has Node tests, mypy when
the repo-specific notes list it, and the tests in a second venv with
`requirements_test_min.txt` when a change could behave differently on the
oldest supported Home Assistant. The standards check (also in CI) is
`python <path to bisman-automations/.github>/scripts/check_repo.py .`.

### Code and docs

- Support every Home Assistant from the `hacs.json` minimum to the newest
  release. Check for deprecations in the newest release and the beta.
- Keep entity IDs, unique IDs, service names and event names stable. When
  one has to change, migrate it and say so under Changed in the CHANGELOG.
- `strings.json` and `translations/en.json` hold the same keys. Home
  Assistant reads only `translations/`, so change both in the same commit.
- Never put personal details in the repo (README, CHANGELOG, code, tests,
  screenshots): no names, addresses, account or device identifiers, or real
  entity IDs. Use placeholders such as `light.living_room`.
- `README.md` is user documentation with the standard header (badges,
  Marketplace, HACS and "add integration" My buttons) and sections
  Installation, Setup, ..., Troubleshooting, Development, License. Use plain
  language and show names, not entity IDs, wherever the UI does.
<!-- END bisman-automations shared rules -->
