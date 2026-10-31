// TV Mgmt sidebar panel. Plain web component, no build step.
// Talks to the integration over Home Assistant's websocket (tv_mgmt/* commands).

const EXPANDED_KEY = "tv-mgmt-expanded";

const TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "activity", label: "Activity" },
  { id: "analytics", label: "Analytics" },
  { id: "limits", label: "Limits" },
  { id: "lights", label: "Lights" },
];

const MODES = [
  { id: "enforced", label: "Enforced" },
  { id: "monitor_only", label: "Monitor only" },
  { id: "paused", label: "Paused" },
];

const RANGES = [7, 30, 90];

// Colours for apps on the Apple TV strip, chosen to stay apart in light and dark themes.
const APP_COLOURS = ["#29b6f6", "#ab47bc", "#26a69a", "#ffa726", "#ec407a", "#8d9aa5"];

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Lights tab wording.
const LS_HELD = {
  tv_off: "Waiting for the TV to turn on",
  blocked: "Paused while the TV is blocked",
  stopped: "Paused after a blocked app was stopped",
};
const LS_SOURCES = { title_mapping: "Saved title colors", poster: "Artwork", genre: "Genre", default: "Default color", none: "Nothing" };
const LS_PRESETS = {
  balanced: "Saved titles, then artwork, then genre",
  artwork_first: "Artwork first",
  genre_first: "Genre before artwork",
  titles_only: "Saved titles only",
  artwork_only: "Artwork only",
  genre_only: "Genre only",
  custom: "My own order",
};
const LS_MEDIA = { movie: "Movies", tvshow: "TV shows", episode: "Episodes", video: "Videos", channel: "TV channels", music: "Music", podcast: "Podcasts", game: "Games", app: "Apps" };
const LS_EXTRACTION = { dominant: "The most common color", vibrant: "The most vivid color", average: "The average of the artwork" };
const LS_IDLE = { restore: "Put the lights back how they were", turn_off: "Turn the lights off", default_color: "Show the default color", leave: "Leave the color as it is" };
const LS_UNSUPPORTED = { brightness_only: "Set their brightness only", skip: "Leave them alone" };
const LS_COLOR_MODES = { single: "One color for every light", palette: "A palette spread across the lights" };
const LS_STRATEGIES = { spread: "Take turns", dominant_first: "Main color on the first light", mirrored: "Mirrored from both ends" };
// [key, label, min, max, whole number]
const LS_NUMBERS = [
  ["brightness_pct", "Brightness", 1, 100, true],
  ["transition", "Fade", 0, 30, false],
  ["restore_delay", "Wait before putting the lights back", 0, 300, false],
  ["palette_size", "Colors in the palette", 3, 5, true],
  ["min_saturation", "Skip colors greyer than", 0, 100, true],
  ["saturation_boost", "Make colors richer by", 1, 3, false],
  ["min_brightness", "Lowest color brightness", 0, 100, true],
  ["dark_threshold", "Ignore pixels darker than", 0, 255, true],
  ["light_threshold", "Ignore pixels lighter than", 0, 255, true],
];
const rgbHex = (rgb) => (Array.isArray(rgb) ? `#${rgb.map((n) => Number(n).toString(16).padStart(2, "0")).join("")}` : "#ff9329");
const hexRgb = (hex) => {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  return m ? m.slice(1).map((x) => parseInt(x, 16)) : [255, 147, 41];
};

const minutes = (seconds) => Math.round((seconds || 0) / 60);

function duration(seconds) {
  const total = minutes(seconds);
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

// 1:05:09 or 5:09
function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

const timeOf = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function dayLabel(isoDate, today) {
  if (isoDate === today) return "Today";
  const d = new Date(`${isoDate}T12:00:00`);
  const t = new Date(`${today}T12:00:00`);
  if ((t - d) / 86400000 === 1) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}

function shiftDate(isoDate, days) {
  const d = new Date(`${isoDate}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

// What the state chip says, and its tone.
function stateInfo(p) {
  switch (p.state) {
    case "enforcing": {
      const why = { budget: "Daily limit reached", quiet_window: p.quiet_window || "Quiet window", manual: "Blocked by a parent", home_daily: "Home's time is up today", home_weekly: "Home's time is up this week" }[p.reason];
      return { tone: "bad", text: why || "Blocked" };
    }
    case "warning":
      return { tone: "warn", text: `${minutes(p.remaining_seconds)} min left` };
    case "paused":
      return { tone: "muted", text: "Rules paused" };
    case "adult_mode":
      return { tone: "info", text: p.adult_mode_until ? `Adult mode until ${timeOf(p.adult_mode_until)}` : "Adult mode" };
    default:
      return { tone: "good", text: p.mode === "monitor_only" ? "Monitoring" : "OK" };
  }
}

// Display name for an input, using the names set for that TV.
const nameOf = (p, raw) => (raw == null ? raw : p?.input_names?.[raw] ?? p?.custom_input_names?.[raw] ?? raw);

function describeEvent(e, p) {
  const n = (raw) => nameOf(p, raw);
  switch (e.type) {
    case "tv_on":
      return { icon: "power", text: e.source ? `TV turned on, showing ${n(e.source)}` : "TV turned on" };
    case "tv_off":
      return { icon: "power-off", text: "TV turned off" };
    case "input":
      return { icon: "input", text: `Switched to ${n(e.source)}` };
    case "input_blocked":
      return e.reverted
        ? { icon: "shield", tone: "bad", text: `Tried ${n(e.source) ?? "another input"}, switched back to ${n(e.target)}` }
        : { icon: "eye", tone: "warn", text: `Switched to ${n(e.source) ?? "another input"}, which isn't allowed (monitor only)` };
    case "enforcement": {
      const text =
        e.state === "enforcing"
          ? { budget: "Daily limit reached", quiet_window: `Quiet window started${e.quiet_window ? `: ${e.quiet_window}` : ""}`, manual: "Blocked by a parent", home_daily: "The whole home's TV time is up for today", home_weekly: "The whole home's TV time is up for this week" }[e.reason] || "Blocked"
          : { warning: "Time is almost up", ok: "Allowed to watch again", paused: "Rules paused", adult_mode: "Adult mode started" }[e.state] || e.state;
      return { icon: "clock", tone: e.state === "enforcing" ? "bad" : e.state === "warning" ? "warn" : undefined, text };
    }
    case "turned_off":
      return { icon: "power-off", tone: "bad", text: "TV Mgmt turned the TV off" };
    case "mode": {
      const label = MODES.find((m) => m.id === e.mode)?.label ?? e.mode;
      return { icon: "mode", text: `Mode set to ${label}${e.synced ? " from Apple TV Mgmt" : ""}` };
    }
    case "input_lock":
      return { icon: "lock", text: e.on ? "Input lock turned on" : "Input lock turned off" };
    case "adult_mode":
      return { icon: "adult", text: e.on ? `Adult mode on for ${e.minutes} min` : e.reason === "expired" ? "Adult mode ended" : "Adult mode turned off" };
    case "extension":
      return { icon: "plus", text: e.minutes >= 0 ? `${e.minutes} min of extra time added` : `${-e.minutes} min of time taken away` };
    case "home_extension":
      return { icon: "plus", text: `${e.minutes >= 0 ? `${e.minutes} min of extra time added` : `${-e.minutes} min of time taken away`} for the whole home${e.by ? ` by ${e.by}` : ""}` };
    case "block":
      return { icon: "shield", tone: "bad", text: "Blocked now" };
    case "unblock":
      return { icon: "shield", text: "Unblocked" };
    case "reset":
      return { icon: "clock", text: "Today's time reset" };
    case "tv_lights_off":
      return { icon: "power-off", tone: e.failed ? "warn" : undefined, text: e.failed ? `TV lights turned off (${e.failed} didn't answer)` : "TV lights turned off" };
    case "message": {
      const where = { apple_tv: "the Apple TV", screen: "the TV screen", speak: "the speakers" };
      const to = (e.sent || []).map((k) => where[k] || k).join(" and ");
      return { icon: "remote", text: `${e.by ? `${e.by} sent` : "Sent"} "${e.text}"${to ? ` to ${to}` : ""}` };
    }
    case "media": {
      const what = describeMedia(e);
      return { icon: "play", text: `Watching ${what}${e.name ? ` in ${e.name}` : ""}` };
    }
    case "app":
      return { icon: "box", text: e.app ? `Opened ${e.name ?? e.app} on the Apple TV` : "Closed the app on the Apple TV" };
    case "app_stopped": {
      const app = e.name ?? e.app;
      if (e.show) {
        const did = !e.acted ? " (monitor only)" : e.action === "sleep" ? ", put the Apple TV to sleep" : ", went back to the home screen";
        return { icon: "shield", tone: e.acted ? "bad" : "warn", text: `Tried ${e.show}${app ? ` in ${app}` : ""}, which is blocked${did}` };
      }
      const why = { blocked: `Tried ${app}, which is blocked`, not_allowed: `Tried ${app}, which isn't on the allowed list`, limit: `${app} reached its daily limit` }[e.reason] || `Stopped ${app}`;
      const did = !e.acted ? " (monitor only)" : e.action === "sleep" ? ", put the Apple TV to sleep" : ", went back to the home screen";
      return { icon: "shield", tone: e.acted ? "bad" : "warn", text: why + did };
    }
    case "follow":
      return { icon: "input", text: `Apple TV woke, switched the TV to ${n(e.target)}` };
    case "box_wake":
      return { icon: "power", text: "TV turned on, TV Mgmt woke the Apple TV" };
    case "box_sleep":
      return { icon: "power-off", tone: "bad", text: "TV Mgmt put the Apple TV to sleep" };
    default:
      return { icon: "clock", text: e.type };
  }
}

// Small inline icons (Material Design Icons paths).
const ICONS = {
  power: "M16.56,5.44L15.11,6.89C16.84,7.94 18,9.83 18,12A6,6 0 0,1 12,18A6,6 0 0,1 6,12C6,9.83 7.16,7.94 8.88,6.88L7.44,5.44C5.36,6.88 4,9.28 4,12A8,8 0 0,0 12,20A8,8 0 0,0 20,12C20,9.28 18.64,6.88 16.56,5.44M13,3H11V13H13",
  "power-off": "M16.56,5.44L15.11,6.89C16.84,7.94 18,9.83 18,12A6,6 0 0,1 12,18A6,6 0 0,1 6,12C6,9.83 7.16,7.94 8.88,6.88L7.44,5.44C5.36,6.88 4,9.28 4,12A8,8 0 0,0 12,20A8,8 0 0,0 20,12C20,9.28 18.64,6.88 16.56,5.44M13,3H11V13H13",
  input: "M8,5H16V11H18V5A2,2 0 0,0 16,3H8A2,2 0 0,0 6,5V11H8M5,13V19H19V13H5M17,15V17H7V15H17Z",
  shield: "M12,1L3,5V11C3,16.55 6.84,21.74 12,23C17.16,21.74 21,16.55 21,11V5L12,1Z",
  eye: "M12,9A3,3 0 0,0 9,12A3,3 0 0,0 12,15A3,3 0 0,0 15,12A3,3 0 0,0 12,9M12,17A5,5 0 0,1 7,12A5,5 0 0,1 12,7A5,5 0 0,1 17,12A5,5 0 0,1 12,17M12,4.5C7,4.5 2.73,7.61 1,12C2.73,16.39 7,19.5 12,19.5C17,19.5 21.27,16.39 23,12C21.27,7.61 17,4.5 12,4.5Z",
  clock: "M12,20A8,8 0 0,0 20,12A8,8 0 0,0 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22C6.47,22 2,17.5 2,12A10,10 0 0,1 12,2M12.5,7V12.25L17,14.92L16.25,16.15L11,13V7H12.5Z",
  mode: "M12,1L3,5V11C3,16.55 6.84,21.74 12,23C17.16,21.74 21,16.55 21,11V5L12,1M12,7C13.4,7 14.8,8.1 14.8,9.5V11C15.4,11 16,11.6 16,12.3V15.8C16,16.4 15.4,17 14.7,17H9.2C8.6,17 8,16.4 8,15.7V12.2C8,11.6 8.6,11 9.2,11V9.5C9.2,8.1 10.6,7 12,7M12,8.2C11.2,8.2 10.5,8.7 10.5,9.5V11H13.5V9.5C13.5,8.7 12.8,8.2 12,8.2Z",
  lock: "M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V6A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18M12,3A3,3 0 0,0 9,6V8H15V6A3,3 0 0,0 12,3Z",
  adult: "M12,4A4,4 0 0,1 16,8A4,4 0 0,1 12,12A4,4 0 0,1 8,8A4,4 0 0,1 12,4M12,14C16.42,14 20,15.79 20,18V20H4V18C4,15.79 7.58,14 12,14Z",
  plus: "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z",
  left: "M15.41,16.58L10.83,12L15.41,7.41L14,6L8,12L14,18L15.41,16.58Z",
  right: "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z",
  play: "M8,5.14V19.14L19,12.14L8,5.14Z",
  pause: "M14,19H18V5H14M6,19H10V5H6V19Z",
  prev: "M6,18V6H8V18H6M9.5,12L18,6V18L9.5,12Z",
  next: "M16,18H18V6H16M6,18L14.5,12L6,6V18Z",
  home: "M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z",
  box: "M3,8H21A1,1 0 0,1 22,9V15A1,1 0 0,1 21,16H3A1,1 0 0,1 2,15V9A1,1 0 0,1 3,8M17,11A1,1 0 0,0 16,12A1,1 0 0,0 17,13A1,1 0 0,0 18,12A1,1 0 0,0 17,11M5,17H7V18H5V17M17,17H19V18H17V17Z",
  up: "M7.41,15.41L12,10.83L16.59,15.41L18,14L12,8L6,14L7.41,15.41Z",
  down: "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z",
  back: "M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z",
  "volume-up": "M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.84 14,18.7V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.76 16.5,12M3,9V15H7L12,20V4L7,9H3Z",
  "volume-down": "M5,9V15H9L14,20V4L9,9M18.5,12C18.5,10.23 17.5,8.71 16,7.97V16C17.5,15.29 18.5,13.76 18.5,12Z",
  mute: "M12,4L9.91,6.09L12,8.18M4.27,3L3,4.27L7.73,9H3V15H7L12,20V13.27L16.25,17.53C15.58,18.04 14.83,18.46 14,18.7V20.77C15.38,20.45 16.63,19.82 17.68,18.96L19.73,21L21,19.73L12,10.73M19,12C19,12.94 18.8,13.82 18.46,14.64L19.97,16.15C20.62,14.91 21,13.5 21,12C21,7.72 18,4.14 14,3.23V5.29C16.89,6.15 19,8.83 19,12M16.5,12C16.5,10.23 15.5,8.71 14,7.97V10.18L16.45,12.63C16.5,12.43 16.5,12.21 16.5,12Z",
  remote: "M12,0C8.96,0 6.21,1.23 4.22,3.22L5.63,4.63C7.26,3 9.5,2 12,2C14.5,2 16.74,3 18.36,4.64L19.77,3.23C17.79,1.23 15.04,0 12,0M7.05,6.05L8.46,7.46C9.37,6.56 10.62,6 12,6C13.38,6 14.63,6.56 15.54,7.46L16.95,6.05C15.68,4.78 13.93,4 12,4C10.07,4 8.32,4.78 7.05,6.05M12,15A2,2 0 0,1 10,13A2,2 0 0,1 12,11A2,2 0 0,1 14,13A2,2 0 0,1 12,15M15,9H9A1,1 0 0,0 8,10V22A1,1 0 0,0 9,23H15A1,1 0 0,0 16,22V10A1,1 0 0,0 15,9Z",
  "chevron-down": "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z",
  "lightbulb-off": "M12,2C9.76,2 7.78,3.05 6.5,4.68L7.93,6.11C8.84,4.84 10.32,4 12,4A5,5 0 0,1 17,9C17,10.68 16.16,12.16 14.89,13.06L16.31,14.5C17.94,13.21 19,11.24 19,9A7,7 0 0,0 12,2M3.28,4L2,5.27L5.04,8.3C5,8.53 5,8.76 5,9C5,11.38 6.19,13.47 8,14.74V17A1,1 0 0,0 9,18H14.73L18.73,22L20,20.72L3.28,4M7.23,10.5L12.73,16H10V13.58C8.68,13 7.66,11.88 7.23,10.5M9,20V21A1,1 0 0,0 10,22H14A1,1 0 0,0 15,21V20H9Z",
  delete: "M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z",
};
// TV Mgmt's own entities, in the order the dashboard links to them.
const ENTITY_LABELS = {
  mode: "Mode",
  input_lock: "Input lock",
  adult_mode: "Adult mode",
  enforcement_state: "Enforcement state",
  current_input: "Current input",
  time_used_today: "Time used today",
  adult_mode_time_today: "Adult mode time today",
  time_remaining_today: "Time remaining today",
  extra_time_today: "Extra time today",
  blocked_switches_today: "Blocked switches today",
  current_app: "Current app",
  app_time_today: "App time today",
  tv_lights_follow: "TV lights follow the TV",
  turn_off_tv_lights: "Turn off TV lights",
};
const ENTITY_ORDER = Object.keys(ENTITY_LABELS);

// "Bluey, Season 2, Episode 14: Hammerbarn" — same wording as the integration.
function describeMedia(m) {
  if (m.series) {
    const se = [m.season != null && `Season ${m.season}`, m.episode != null && `Episode ${m.episode}`].filter(Boolean).join(", ");
    return m.series + (se ? `, ${se}` : "") + (m.title && m.title !== m.series ? `: ${m.title}` : "");
  }
  return m.artist ? `${m.title} by ${m.artist}` : m.title || "";
}

const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name] ?? ICONS.clock}"/></svg>`;

// Placeholder shaped like what's loading: "cards" (dashboard), "chart" (analytics) or "rows".
function loading(label, shape = "rows") {
  const bar = (w, h = 12) => `<span class="sk" style="width:${w};height:${h}px"></span>`;
  let body;
  if (shape === "cards") {
    const card = `<div class="card sk-card"><div class="screen-wrap"><span class="sk sk-screen"></span></div>
        <div class="card-body sk-lines"><div class="sk-row"><span class="sk sk-dot"></span><span class="sk sk-dot"></span><span class="sk sk-dot"></span></div>${bar("60%", 14)}${bar("100%", 8)}</div></div>`;
    body = `<div class="grid">${card}${card}</div>`;
  } else if (shape === "chart") {
    const stat = `<div class="card stat">${bar("50%", 22)}${bar("70%", 10)}</div>`;
    body = `<div class="stats">${stat.repeat(4)}</div><section class="card"><div class="card-body">${bar("30%", 14)}<span class="sk" style="width:100%;height:180px"></span></div></section>`;
  } else {
    const row = (w) => `<div class="sk-row">${bar("64px", 10)}${bar(w)}</div>`;
    body = `<section class="card"><div class="card-body sk-lines">${["70%", "55%", "80%", "45%", "65%"].map(row).join("")}</div></section>`;
  }
  return `<div class="loading" role="status" aria-live="polite"><span class="sr-only">${esc(label)}</span><div aria-hidden="true">${body}</div></div>`;
}

const STYLES = `
:host {
  display: block;
  min-height: 100vh;
  background: var(--primary-background-color);
  color: var(--primary-text-color);
  font-family: var(--paper-font-body1_-_font-family, var(--ha-font-family-body, Roboto, sans-serif));
  --tm-good: var(--success-color, #43a047);
  --tm-warn: var(--warning-color, #ffa600);
  --tm-bad: var(--error-color, #db4437);
  --tm-info: var(--info-color, #039be5);
  /* Adult mode, in the Activity strips and Analytics chart. */
  --tm-adult: #8e6cc9;
  --tm-radius: var(--ha-card-border-radius, 12px);
  --tm-screen: #12161c;
  --tm-screen-text: #f3f6fa;
}
* { box-sizing: border-box; }
button { font: inherit; color: inherit; }
.icon { width: 18px; height: 18px; fill: currentColor; flex: none; }

header {
  position: sticky; top: 0; z-index: 2;
  background: var(--app-header-background-color, var(--primary-color));
  color: var(--app-header-text-color, var(--text-primary-color, #fff));
}
.bar { display: flex; align-items: center; height: 56px; padding: 0 12px; gap: 4px; }
.bar h1 { font-size: 20px; font-weight: 400; margin: 0 0 0 8px; }
nav { display: flex; gap: 4px; padding: 0 12px; overflow-x: auto; scrollbar-width: none; }
nav button {
  background: none; border: 0; padding: 12px 14px 10px; cursor: pointer; opacity: .75;
  border-bottom: 2px solid transparent; white-space: nowrap; font-size: 14px;
}
nav button[aria-selected="true"] { opacity: 1; border-bottom-color: currentColor; }
/* Phones: tighter tabs share the width, so all six fit from 375px; narrower, they scroll. */
@media (max-width: 420px) { nav { gap: 0; padding: 0 4px; } nav button { flex: 1 0 auto; padding: 12px 6px 10px; } }
nav button:focus-visible, .btn:focus-visible, .seg button:focus-visible, .chip:focus-visible, input:focus-visible {
  outline: 2px solid var(--primary-color); outline-offset: 2px;
}

main { max-width: 1100px; margin: 0 auto; padding: 20px 16px 48px; }
.empty { max-width: 520px; margin: 48px auto; text-align: center; color: var(--secondary-text-color); line-height: 1.5; }
.empty h2 { color: var(--primary-text-color); font-weight: 500; }
.error {
  background: color-mix(in srgb, var(--tm-bad) 12%, transparent); color: var(--primary-text-color);
  border-left: 4px solid var(--tm-bad); padding: 12px 14px; border-radius: 8px; margin-bottom: 16px;
}

.grid { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); }
main.wide { max-width: 1760px; }

/* A TV's card: stacked on a phone, side by side when there's room. */
.tv-card { container: tv / inline-size; }
.tv-layout { display: grid; }
.tv-layout > .col + .col.card-body { padding-top: 0; }
@container tv (min-width: 720px) {
  .tv-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-areas: "tv controls"; }
  .has-atv .tv-layout { grid-template-areas: "tv atv" "controls atv"; grid-template-rows: auto 1fr; }
  .col-tv { grid-area: tv; }
  .col-atv { grid-area: atv; padding-top: 16px !important; border-left: 1px solid var(--divider-color); align-content: start; }
  .col-controls { grid-area: controls; padding-top: 16px !important; align-content: start; }
  .tv-card:not(.has-atv) .col-controls { border-left: 1px solid var(--divider-color); }
  .has-atv .col-controls { border-top: 1px solid var(--divider-color); }
  .col-atv .atv { border-top: 0; padding-top: 0; }
}
@container tv (min-width: 1080px) {
  .has-atv .tv-layout { grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr) minmax(0, 0.95fr); grid-template-areas: "tv atv controls"; grid-template-rows: auto; }
  .has-atv .col-controls { border-top: 0; border-left: 1px solid var(--divider-color); }
}
.card {
  background: var(--card-background-color, var(--ha-card-background, #fff));
  border-radius: var(--tm-radius);
  box-shadow: var(--ha-card-box-shadow, none);
  border: 1px solid var(--divider-color);
  overflow: hidden;
}
.card-body { padding: 16px; display: grid; gap: 16px; }

/* The TV: what's on screen right now. */
.screen-wrap { padding: 16px 16px 0; }
/* The lights around the TV, glowing out from behind its frame. */
.tv-frame { position: relative; isolation: isolate; }
.ambient { position: absolute; inset: -9px; z-index: -1; border-radius: 18px; filter: blur(9px) saturate(1.3); pointer-events: none;
  transition: background .6s, opacity .6s; }
.ambient[hidden] { display: none; }
.screen {
  position: relative; aspect-ratio: 16 / 7; border-radius: 10px;
  background: var(--tm-screen); color: var(--tm-screen-text);
  border: 6px solid #2a3038; display: flex; flex-direction: column; justify-content: flex-end;
  padding: 14px 16px; overflow: hidden;
}
.screen .glow { position: absolute; inset: 0; opacity: .35; pointer-events: none;
  background: radial-gradient(120% 90% at 20% 0%, var(--tm-glow, #3a6df0) 0%, transparent 60%); }
.screen.off { background: #0b0d10; }
.screen.off .glow { opacity: 0; }
.screen.off .now { color: #6b7480; }
.screen.blocked .glow { --tm-glow: var(--tm-bad); }
.screen.not-allowed .glow { --tm-glow: var(--tm-warn); opacity: .5; }
.screen .tv-name { position: absolute; top: 10px; left: 14px; font-size: 13px; opacity: .7; }
.screen .now { position: relative; font-size: clamp(26px, 7vw, 38px); font-weight: 500; letter-spacing: -.01em; line-height: 1.05; overflow-wrap: anywhere; }
.screen .sub { position: relative; font-size: 13px; opacity: .75; margin-top: 4px; }
/* Collapsed, every TV is the same short height, so a row of them lines up. */
.tv-card.collapsed .screen { aspect-ratio: auto; min-height: 170px; padding-top: 40px; }
.atv-mini { position: relative; display: flex; align-items: center; gap: 10px; margin-top: 12px; padding: 6px 10px 6px 6px;
  border: 0; border-radius: 8px; background: rgba(255,255,255,.08); color: inherit; font: inherit; text-align: left; cursor: pointer; min-width: 0; }
.atv-mini:hover { background: rgba(255,255,255,.13); }
.atv-mini:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.atv-mini.asleep { opacity: .7; }
.atv-mini-art { flex: none; width: 40px; height: 40px; border-radius: 6px; overflow: hidden; display: grid; place-items: center; background: rgba(255,255,255,.08); }
.atv-mini-art img { width: 100%; height: 100%; object-fit: cover; display: block; }
.atv-mini-text { display: grid; min-width: 0; }
.atv-mini-top { font-size: 12px; opacity: .75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.atv-mini-title { font-size: 15px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mini-flag { margin-left: 6px; color: #ff8a80; }
.tv-card.collapsed .screen-wrap { padding-bottom: 4px; }
.tv-card:not(.collapsed) { grid-column: 1 / -1; }
.grid { align-items: start; }
.expand { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; min-height: 44px;
  padding: 10px 16px; border: 0; border-top: 1px solid var(--divider-color); background: none; cursor: pointer;
  color: var(--secondary-text-color); font-size: 14px; text-align: left; }
.expand span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.expand:hover { background: color-mix(in srgb, var(--primary-color) 6%, transparent); }
.expand:focus-visible { outline: 2px solid var(--primary-color); outline-offset: -2px; }
.expand .icon { transition: transform .15s; }
.expand[aria-expanded="true"] .icon { transform: rotate(180deg); }
.stand { width: 22%; height: 8px; margin: 0 auto; background: var(--divider-color); border-radius: 0 0 6px 6px; }

.chip { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; padding: 4px 10px; border-radius: 999px;
  background: color-mix(in srgb, var(--tm-tone) 16%, transparent); color: var(--primary-text-color); }
.chip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--tm-tone); }
.tone-good { --tm-tone: var(--tm-good); } .tone-warn { --tm-tone: var(--tm-warn); }
.tone-bad { --tm-tone: var(--tm-bad); } .tone-info { --tm-tone: var(--tm-info); }
.tone-muted { --tm-tone: var(--secondary-text-color); }
.screen .chip { position: absolute; top: 8px; right: 10px; color: var(--tm-screen-text); background: rgba(255,255,255,.08); }

.time-line { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.time-line strong { font-size: 22px; font-weight: 500; }
.muted { color: var(--secondary-text-color); font-size: 14px; }
.meter { height: 8px; border-radius: 4px; background: var(--divider-color); overflow: hidden; margin-top: 8px; }
.meter > div { height: 100%; background: var(--tm-tone, var(--primary-color)); border-radius: 4px; }

.facts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; font-size: 14px; }
.facts dt { color: var(--secondary-text-color); }
.facts dd { margin: 0; }
.link { background: none; border: 0; padding: 0; font: inherit; color: inherit; cursor: pointer; text-align: left;
  text-decoration: underline; text-decoration-color: color-mix(in srgb, currentColor 35%, transparent); text-underline-offset: 3px; }
.link:hover { text-decoration-color: currentColor; }
.link:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 2px; }
.entities { display: grid; gap: 6px; }
.entities .label { font-size: 13px; color: var(--secondary-text-color); }
.entity-links { display: flex; flex-wrap: wrap; gap: 6px; }
.entity-links button { font-size: 13px; padding: 4px 10px; border-radius: 14px; border: 1px solid var(--divider-color);
  background: transparent; cursor: pointer; color: var(--primary-text-color); }
.entity-links button:hover { background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
.entity-links button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }

.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.row .label { width: 100%; font-size: 14px; color: var(--secondary-text-color); }
.btn {
  border: 1px solid var(--divider-color); background: transparent; border-radius: 8px;
  padding: 8px 12px; cursor: pointer; font-size: 14px; min-height: 36px;
}
.btn:hover { background: color-mix(in srgb, var(--primary-color) 8%, transparent); }
.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
.btn.danger { color: var(--tm-bad); border-color: color-mix(in srgb, var(--tm-bad) 45%, transparent); }
.btn[disabled] { opacity: .5; cursor: default; }

.seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
.seg button { background: none; border: 0; padding: 8px 12px; cursor: pointer; font-size: 14px; min-height: 36px; }
.seg button + button { border-left: 1px solid var(--divider-color); }
.seg.wide { display: grid; grid-template-columns: 1fr 1fr; width: 100%; max-width: 480px; }
.seg button[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); }

.toggle { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 14px; }
.toggle small { display: block; color: var(--secondary-text-color); }
.switch { position: relative; width: 40px; height: 22px; flex: none; }
.switch input { opacity: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; position: absolute; z-index: 1; }
.switch span { position: absolute; inset: 0; border-radius: 11px; background: var(--divider-color); transition: background .15s; }
.switch span::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: transform .15s; box-shadow: 0 1px 2px rgba(0,0,0,.3); }
.switch input:checked + span { background: var(--primary-color); }
.switch input:checked + span::after { transform: translateX(18px); }
.switch input:focus-visible + span { outline: 2px solid var(--primary-color); outline-offset: 2px; }

.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.picker { display: flex; flex-wrap: wrap; gap: 6px; }
.picker .btn[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
.date-nav { display: flex; align-items: center; gap: 4px; }
.date-nav .btn { padding: 6px; min-width: 36px; display: inline-flex; justify-content: center; }
.date-nav h2 { font-size: 18px; font-weight: 500; margin: 0 8px; min-width: 9em; text-align: center; }

/* Activity: 24-hour tape. */
.tape { position: relative; height: 44px; border-radius: 8px; background: var(--secondary-background-color, var(--divider-color)); overflow: hidden; }
.tape .seg-bar { position: absolute; top: 0; bottom: 0; background: var(--primary-color); min-width: 2px; }
.tape .seg-bar.not-allowed { background: var(--tm-warn); min-width: 4px; z-index: 1; }
.tape .seg-bar.live { background-image: repeating-linear-gradient(135deg, transparent 0 6px, rgba(255,255,255,.25) 6px 12px); }
/* Adult mode: shaded over both strips, which show no app for it. */
.tape .adult-band { position: absolute; top: 0; bottom: 0; z-index: 2; pointer-events: none;
  background: repeating-linear-gradient(135deg, color-mix(in srgb, var(--tm-adult) 55%, transparent) 0 5px, color-mix(in srgb, var(--tm-adult) 30%, transparent) 5px 10px);
  border-left: 2px solid var(--tm-adult); border-right: 2px solid var(--tm-adult); }
.legend i.adult { background: var(--tm-adult); }
.chart .day-bar.adult { fill: var(--tm-adult); }
.tape .now-line { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--primary-text-color); opacity: .6; }
.hours { position: relative; height: 18px; font-size: 12px; color: var(--secondary-text-color); margin-top: 4px; }
.hours span { position: absolute; transform: translateX(-50%); }
.legend { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 14px; margin-top: 12px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; background: var(--primary-color); }
.legend i.not-allowed { background: var(--tm-warn); }

.log { list-style: none; margin: 0; padding: 0; }
.log li { display: grid; grid-template-columns: 72px 22px 1fr; gap: 10px; align-items: start; padding: 10px 0; border-top: 1px solid var(--divider-color); font-size: 14px; }
.log li:first-child { border-top: 0; }
.log time { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
.log .icon { margin-top: 1px; color: var(--secondary-text-color); }
.log .tone-bad .icon { color: var(--tm-bad); } .log .tone-warn .icon { color: var(--tm-warn); }

/* Analytics */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
.stat { padding: 14px 16px; }
.stat b { display: block; font-size: 24px; font-weight: 500; }
.stat span { color: var(--secondary-text-color); font-size: 14px; }
.chart { width: 100%; height: auto; display: block; }
.chart .day-bar { fill: var(--primary-color); }
.chart .day-bar.over { fill: var(--tm-warn); }
.chart .limit { fill: none; stroke: var(--primary-text-color); stroke-width: 1.5; stroke-dasharray: 4 3; opacity: .55; }
.chart .grid-line { stroke: var(--divider-color); }
.chart text { fill: var(--secondary-text-color); font-size: 11px; }

/* Limits */
form { display: grid; gap: 20px; max-width: 560px; }
.field { display: grid; gap: 6px; }
.field label { font-size: 14px; font-weight: 500; }
.field small { color: var(--secondary-text-color); font-size: 13px; line-height: 1.4; }
input[type="number"], input[type="text"], input[type="time"], input[type="password"] {
  font: inherit; font-size: 15px; padding: 10px 12px; border-radius: 8px; min-height: 42px;
  border: 1px solid var(--divider-color); background: var(--card-background-color, transparent); color: var(--primary-text-color);
}
input[type="number"] { width: 140px; }
.windows { display: grid; gap: 8px; }
.window { display: grid; grid-template-columns: auto auto 1fr auto; gap: 8px; align-items: center; }
.window input[type="text"] { min-width: 0; }
.window .btn { padding: 6px; }
.form-actions { display: flex; gap: 12px; align-items: center; }
.tv-lights-now { display: grid; gap: 10px; padding-top: 16px; margin-top: 16px; border-top: 1px solid var(--divider-color); }
.tv-lights-now > small { font-size: 13px; }
.names { display: grid; gap: 10px; }
.name-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: center; }
.name-row .raw { font-size: 14px; color: var(--secondary-text-color); overflow-wrap: anywhere; }
.name-row input { width: 100%; }

/* Apple TV: now playing */
.np { display: grid; gap: 10px; }
.np + .atv-head { margin-top: 8px; }
.np-tile { display: flex; gap: 14px; align-items: center; padding: 12px; border-radius: 10px;
  background: var(--tm-screen); color: var(--tm-screen-text); border: 4px solid #2a3038; min-height: 100px; }
.np-tile.asleep { background: #0b0d10; }
.np-tile.asleep .np-title { color: #8a939e; }
.np-art { flex: none; width: 76px; height: 76px; border-radius: 8px; overflow: hidden; display: grid; place-items: center;
  background: rgba(255,255,255,.08); color: rgba(255,255,255,.6); }
.np-art img { width: 100%; height: 100%; object-fit: cover; display: block; }
.np-art .icon { width: 34px; height: 34px; }
.np-meta { min-width: 0; display: grid; gap: 2px; }
.np-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; opacity: .8; }
.np-top .chip { color: var(--tm-screen-text); background: rgba(255,255,255,.1); font-size: 12px; padding: 2px 8px; }
.np-state::before { content: ""; display: inline-block; width: 4px; height: 4px; border-radius: 50%; background: currentColor; margin: 0 8px 2px 0; vertical-align: middle; }
.np-title { font-size: 18px; font-weight: 500; line-height: 1.25; overflow-wrap: anywhere; }
.np-sub, .np-app { font-size: 13px; opacity: .75; overflow-wrap: anywhere; }
.np-progress { display: grid; gap: 4px; }
.np-track { height: 4px; border-radius: 2px; background: var(--divider-color); overflow: hidden; }
.np-fill { height: 100%; background: var(--primary-text-color); opacity: .7; }
.np-times { display: flex; justify-content: space-between; font-size: 12px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
.np-controls { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.np-spacer { flex: 1; }
.icon-btn { width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--divider-color); background: transparent;
  display: inline-grid; place-items: center; cursor: pointer; color: var(--primary-text-color); }
.icon-btn[data-primary] { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
.icon-btn:hover { background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
.icon-btn[data-primary]:hover { background: var(--primary-color); filter: brightness(1.08); }
.icon-btn:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.np-controls .btn { display: inline-flex; align-items: center; gap: 6px; }

/* Remote buttons */
.remote { display: grid; gap: 10px; }
.tv-remote:empty { display: none; }
.remote-bar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.remote-bar .btn { display: inline-flex; align-items: center; gap: 6px; }
.remote-bar .vol { display: inline-flex; gap: 6px; }
.icon-btn[aria-pressed="true"] { background: color-mix(in srgb, var(--primary-color) 16%, transparent); border-color: var(--primary-color); }
.icon-btn.power-on { color: var(--tm-good); }
.icon-btn.power-off { color: var(--tm-bad); }
.icon-btn:disabled { opacity: .45; cursor: default; }
.dpad-wrap { display: flex; justify-content: center; align-items: center; gap: 18px; flex-wrap: wrap; padding: 6px 0 2px; }
.dpad { display: grid; grid-template-columns: repeat(3, 48px); grid-template-rows: repeat(3, 48px); gap: 4px; }
.dpad .icon-btn { width: 48px; height: 48px; }
.dpad .ok { font-size: 14px; font-weight: 500; background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
.dpad .k-up { grid-area: 1 / 2; } .dpad .k-left { grid-area: 2 / 1; } .dpad .ok { grid-area: 2 / 2; }
.dpad .k-right { grid-area: 2 / 3; } .dpad .k-down { grid-area: 3 / 2; }
.dpad-side { display: grid; gap: 8px; }
.dpad-side .btn { display: inline-flex; align-items: center; gap: 6px; justify-content: flex-start; }

/* Apple TV */
.atv { border-top: 1px solid var(--divider-color); padding-top: 14px; display: grid; gap: 10px; }
.atv-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 14px; }
.atv-head .label { color: var(--secondary-text-color); }
.atv-head strong { font-weight: 500; font-size: 16px; }
.apps-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; font-size: 14px; }
.apps-list li { display: grid; gap: 4px; }
.apps-list .row-line { display: flex; justify-content: space-between; gap: 8px; }
.apps-list .meter { margin-top: 0; height: 6px; }
.tape.apps-tape .seg-bar { background: var(--tm-info); }
.tape-label { font-size: 13px; color: var(--secondary-text-color); margin: 12px 0 6px; display: flex; align-items: center; gap: 6px; }
.legend i.app { background: var(--tm-info); }
.top-apps { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; font-size: 14px; }
.top-apps li { display: grid; grid-template-columns: minmax(0, 9em) 1fr 6.5em; gap: 10px; align-items: center; }
.top-apps li > .muted { text-align: right; }
.top-apps .bar-track { height: 10px; border-radius: 5px; background: var(--divider-color); overflow: hidden; }
.top-apps .bar-fill { height: 100%; background: var(--tm-info); border-radius: 5px; }
.top-apps .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.top-apps .bar-fill.media { background: var(--primary-color); }
.apps-list.watched small { display: block; font-size: 12px; color: var(--secondary-text-color); }
.app-rules { display: grid; gap: 4px; }
.app-rule { display: grid; grid-template-columns: auto minmax(0, 1fr) 7.5em; gap: 10px; align-items: center; padding: 6px 0; border-top: 1px solid var(--divider-color); font-size: 14px; }
.app-rule:first-child { border-top: 0; }
.app-rule input[type="number"] { width: 100%; }
.app-rule .raw { display: block; font-size: 12px; color: var(--secondary-text-color); overflow-wrap: anywhere; }
.app-rules-head { display: grid; grid-template-columns: auto minmax(0, 1fr) 7.5em; gap: 10px; font-size: 12px; color: var(--secondary-text-color); }
.radio { display: flex; align-items: center; gap: 8px; font-size: 14px; min-height: 32px; }
input[type="checkbox"].check, input[type="radio"] { width: 18px; height: 18px; accent-color: var(--primary-color); }
@media (max-width: 600px) { .name-row { grid-template-columns: 1fr; gap: 4px; } }
.saved { color: var(--tm-good); font-size: 14px; }
.message-box { display: grid; gap: 8px; }
.message-box > .label { font-size: 14px; color: var(--secondary-text-color); }
.message-form { display: grid; gap: 10px; max-width: none; }
.message-row { display: flex; gap: 8px; }
.message-row input { flex: 1; min-width: 0; width: 0; }
.message-row input[type="color"] { flex: none; }
.presets { display: flex; flex-wrap: wrap; gap: 6px; }
.chip-btn { font-size: 13px; padding: 4px 10px; border-radius: 14px; border: 1px solid var(--divider-color);
  background: transparent; cursor: pointer; color: var(--primary-text-color); }
.chip-btn:hover { background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
.chip-btn:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.show-rules { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 14px; }
.show-rule { display: inline-flex; align-items: center; gap: 6px; }
.watched-end { display: inline-flex; align-items: center; gap: 10px; }
.chip-btn.on { border-color: var(--tm-bad); color: var(--tm-bad); }
.message-where { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 14px; }
.message-where label { display: inline-flex; align-items: center; gap: 6px; }
.custom-time { display: inline-flex; gap: 8px; align-items: center; max-width: none; }
.custom-time input[type="number"] { width: 110px; min-height: 38px; padding: 6px 10px; }

/* Access */
.denied code { font-size: 13px; overflow-wrap: anywhere; }
.people { display: grid; }
.person { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 10px 0; border-top: 1px solid var(--divider-color); font-size: 15px; }
.person:first-child { border-top: 0; }
.person .tag { font-size: 12px; color: var(--secondary-text-color); }
.person input:disabled { opacity: .6; }

@media (max-width: 600px) {
  main { padding: 12px 10px 40px; }
  .grid { grid-template-columns: 1fr; gap: 14px; }
  .window { grid-template-columns: 1fr 1fr auto; }
  .window input[type="text"] { grid-column: 1 / 3; grid-row: 2; }
  .date-nav h2 { min-width: 0; font-size: 16px; }
}
.zone { display: grid; gap: 8px; padding: 14px 0; border-top: 1px solid var(--divider-color); }
.zone.editing { gap: 16px; }
.zone-head { display: flex; align-items: flex-start; gap: 12px; }
.zone-head > .switch { margin-top: 1px; }
.zone-title { flex: 1; min-width: 0; display: flex; align-items: center; flex-wrap: wrap; gap: 6px 10px; }
.zone-title strong { min-width: 0; overflow-wrap: anywhere; }
.zone-now { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.zone-now > span:last-child { min-width: 0; overflow-wrap: anywhere; }
.swatches { display: inline-flex; gap: 6px; flex: none; }
.zone-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.zone-confirm { display: grid; gap: 8px; font-size: 14px; }
.swatch { display: inline-block; flex: none; width: 18px; height: 18px; border-radius: 50%; border: 1px solid var(--divider-color); }
.tone-bad-text { color: var(--tm-bad); }
.order { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 14px; }
.order-item { display: inline-flex; align-items: center; gap: 6px; }
.titles { display: grid; gap: 6px; }
.title-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: 10px; align-items: center; font-size: 14px; }
.title-row > span:nth-child(2) { overflow-wrap: anywhere; }
.more summary { cursor: pointer; font-size: 14px; font-weight: 500; }
.more-body { display: grid; gap: 16px; margin-top: 14px; }
select { font: inherit; font-size: 15px; padding: 9px 10px; border-radius: 8px; min-height: 42px; max-width: 100%;
  border: 1px solid var(--divider-color); background: var(--card-background-color, transparent); color: var(--primary-text-color); }
input[type="color"] { width: 52px; height: 42px; padding: 2px; border-radius: 8px; border: 1px solid var(--divider-color); background: transparent; cursor: pointer; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.card-head h2 { margin: 0; font-size: 16px; font-weight: 500; }
.home-strip { margin-bottom: 20px; }
.home-rows { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); }
.checks { display: flex; flex-wrap: wrap; gap: 0 18px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.sk { display: block; border-radius: 6px; background: color-mix(in srgb, var(--primary-text-color) 9%, transparent); position: relative; overflow: hidden; }
.sk::after { content: ""; position: absolute; inset: 0; transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--card-background-color, #fff) 55%, transparent), transparent);
  animation: sk-shimmer 1.4s ease-in-out infinite; }
@keyframes sk-shimmer { to { transform: translateX(100%); } }
.sk-lines { display: grid; gap: 10px; }
.sk-row { display: flex; gap: 12px; align-items: center; }
.sk-screen { width: 100%; aspect-ratio: 16 / 7; border-radius: 10px; }
.sk-dot { width: 40px; height: 40px; border-radius: 50%; flex: none; }
.stat .sk + .sk { margin-top: 8px; }
.loading .card-body { gap: 14px; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } .sk::after { animation: none; display: none; } }
`;

class TvMgmtPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "dashboard";
    this._profiles = null;
    this._selected = null;
    this._activityDate = null;
    this._activity = null;
    this._range = 7;
    this._analytics = null;
    this._limits = null;
    this._limitsDraft = null;
    this._error = null;
    this._busy = false;
    this._savedNote = "";
    this._remoteOpen = new Set();
    // TVs opened on the dashboard. Collapsed (just the TV) by default;
    // remembered on this device.
    this._expanded = new Set();
    try {
      this._expanded = new Set(JSON.parse(localStorage.getItem(EXPANDED_KEY) || "[]"));
    } catch (err) {
      /* storage unavailable: everything starts collapsed */
    }
    this._msgDrafts = {};
    this._msgNote = {};
  }

  _toggleExpanded(id) {
    if (this._expanded.has(id)) this._expanded.delete(id);
    else this._expanded.add(id);
    try {
      localStorage.setItem(EXPANDED_KEY, JSON.stringify([...this._expanded]));
    } catch (err) {
      /* not remembered, still works */
    }
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first) this._start();
    else this._refreshNowPlaying();
  }

  // The colors of the lights around the TV, as a glow around the TV's frame.
  // Lights that are off, or can't show a color, are left out.
  _ambientColors(p) {
    const out = [];
    for (const eid of p.ambient_lights || []) {
      const st = this._hass?.states?.[eid];
      if (!st || st.state !== "on") continue;
      const rgb = st.attributes?.rgb_color;
      if (!Array.isArray(rgb) || rgb.length < 3) continue;
      const level = st.attributes.brightness == null ? 1 : Math.max(0.6, Number(st.attributes.brightness) / 255);
      out.push({ rgb: rgb.slice(0, 3).map((v) => Math.round(Number(v) || 0)), level });
    }
    return out;
  }

  _ambient(p) {
    const colors = this._ambientColors(p);
    const id = `amb-${esc(p.entry_id)}`;
    if (!colors.length) return `<div class="ambient" id="${id}" hidden></div>`;
    const css = colors.map((c) => `rgb(${c.rgb.join(" ")})`);
    // Spread several colors around the frame; one color goes all the way round.
    const fill = css.length === 1 ? css[0] : `conic-gradient(from 210deg, ${[...css, css[0]].join(", ")})`;
    const level = Math.max(...colors.map((c) => c.level));
    return `<div class="ambient" id="${id}" style="background:${fill};opacity:${level.toFixed(2)}" aria-hidden="true"></div>`;
  }

  // Redraw just the Apple TV "Now playing" spots when their media player changes.
  _refreshNowPlaying() {
    if (this._tab !== "dashboard" || !this._profiles) return;
    this._npStates = this._npStates || {};
    this._ambStates = this._ambStates || {};
    for (const p of this._profiles) {
      const lights = (p.ambient_lights || []).map((e) => this._hass.states[e]);
      const prev = this._ambStates[p.entry_id];
      if (!prev || prev.length !== lights.length || lights.some((st, i) => st !== prev[i])) {
        this._ambStates[p.entry_id] = lights;
        const amb = this.shadowRoot.getElementById(`amb-${p.entry_id}`);
        if (amb) amb.outerHTML = this._ambient(p);
      }
      const tv = this._hass.states[p.tv_entity_id];
      if (tv !== this._npStates[p.tv_entity_id]) {
        this._npStates[p.tv_entity_id] = tv;
        const node = this.shadowRoot.getElementById(`tvr-${p.entry_id}`);
        if (node) node.innerHTML = this._tvRemote(p);
      }
      const eid = p.apple_tv?.entity_id;
      if (!eid) continue;
      const st = this._hass.states[eid];
      if (st === this._npStates[eid]) continue;
      this._npStates[eid] = st;
      const node = this.shadowRoot.getElementById(`np-${p.entry_id}`);
      if (node) node.innerHTML = this._nowPlayingInner(p);
      const mini = this.shadowRoot.getElementById(`npm-${p.entry_id}`);
      if (mini) mini.outerHTML = this._atvMini(p);
    }
  }

  // Move progress bars along between Home Assistant updates.
  _tickProgress() {
    this.shadowRoot.querySelectorAll(".np-progress[data-duration]").forEach((el) => {
      const duration = Number(el.dataset.duration);
      let pos = Number(el.dataset.position);
      if (el.dataset.playing === "1" && el.dataset.updated) {
        pos += (Date.now() - new Date(el.dataset.updated).getTime()) / 1000;
      }
      pos = Math.max(0, Math.min(duration, pos));
      const fill = el.querySelector(".np-fill");
      const elapsed = el.querySelector(".np-elapsed");
      if (fill) fill.style.width = `${(pos / duration) * 100}%`;
      if (elapsed) elapsed.textContent = clock(pos);
    });
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
  }

  disconnectedCallback() {
    if (this._unsub) {
      this._unsub.then((unsub) => unsub()).catch(() => {});
      this._unsub = null;
    }
    clearInterval(this._timer);
    this._timer = null;
    clearInterval(this._progressTimer);
    this._progressTimer = null;
  }

  // ---- data ------------------------------------------------------------------

  _ws(msg) {
    return this._hass.callWS(msg);
  }

  async _start() {
    try {
      this._me = await this._ws({ type: "tv_mgmt/access/me" });
    } catch (err) {
      this._me = null; // Older backend: carry on, the commands still check.
    }
    this._renderShell();
    if (this._me && !this._me.allowed) {
      this._renderDenied();
      return;
    }
    await this._loadProfiles();
    this._subscribe();
  }

  _renderDenied() {
    const me = this._me;
    this.shadowRoot.getElementById("main").innerHTML = `<div class="empty denied" role="alert">
      <h2>You don't have access to TV Mgmt</h2>
      <p>Ask an admin to give you access. They can do it in TV Mgmt, under Access.</p>
      ${me.user_id ? `<p class="muted">Signed in as ${esc(me.name || "")}<br>User ID <code>${esc(me.user_id)}</code></p>` : ""}
    </div>`;
  }

  _subscribe() {
    if (this._unsub || !this._hass) return;
    this._unsub = this._hass.connection
      .subscribeMessage(() => this._scheduleRefresh(), { type: "tv_mgmt/subscribe" })
      .catch((err) => {
        this._unsub = null;
        this._showError(err);
      });
    // Keep clocks and live bars moving even when nothing changes.
    clearInterval(this._timer);
    this._timer = setInterval(() => this._scheduleRefresh(), 30000);
    clearInterval(this._progressTimer);
    this._progressTimer = setInterval(() => this._tickProgress(), 1000);
  }

  _scheduleRefresh() {
    if (this._refreshPending) return;
    this._refreshPending = true;
    setTimeout(async () => {
      this._refreshPending = false;
      await this._loadProfiles({ quiet: true });
      if (this._tab === "activity" && this._activity && this._activity.date === this._activity.today) {
        await this._loadActivity({ quiet: true });
      }
      if (this._tab === "lights") await this._loadLightSync({ quiet: true });
    }, 600);
  }

  async _loadProfiles({ quiet = false } = {}) {
    try {
      const res = await this._ws({ type: "tv_mgmt/profiles" });
      // Most settings apply live, but linking another Apple TV or renaming a TV
      // briefly reloads its profile. If the TV on screen is missing from a
      // background refresh, keep what we have and look again.
      if (quiet && this._selected && !res.profiles.some((p) => p.entry_id === this._selected)) {
        clearTimeout(this._retryTimer);
        this._retryTimer = setTimeout(() => this._loadProfiles({ quiet: true }), 1500);
        return;
      }
      this._profiles = res.profiles;
      this._home = res.home || null;
      if (!this._selected || !this._profiles.some((p) => p.entry_id === this._selected)) {
        this._selected = this._profiles[0]?.entry_id ?? null;
      }
      if (!quiet) this._error = null;
    } catch (err) {
      if (!quiet) this._error = this._errorText(err);
    }
    // The limits form keeps what you're typing; don't redraw it on live updates.
    if (quiet && this._tab === "limits") return;
    // The Lights tab redraws when its own data comes back.
    if (quiet && this._tab === "lights") return;
    if (quiet && this._typing()) {
      this._renderLater = true;
      return;
    }
    this._render();
  }

  async _loadActivity({ quiet = false } = {}) {
    if (!this._selected) return;
    try {
      this._activity = await this._ws({ type: "tv_mgmt/activity", entry_id: this._selected, ...(this._activityDate ? { date: this._activityDate } : {}) });
      this._activityDate = this._activity.date;
      if (!quiet) this._error = null;
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._render();
  }

  async _loadAnalytics() {
    if (!this._selected) return;
    try {
      const res = await this._ws({ type: "tv_mgmt/analytics", entry_id: this._selected, days: this._range });
      this._analytics = res.profiles[0];
      this._error = null;
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._render();
  }

  async _loadLimits() {
    if (!this._selected) return;
    try {
      this._limits = await this._ws({ type: "tv_mgmt/limits/get", entry_id: this._selected });
      this._limitsDraft = {
        ...this._limits,
        windows: this._parseWindows(this._limits.quiet_windows),
      };
      this._error = null;
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._render();
  }

  async _action(entryId, action, extra = {}) {
    try {
      const res = await this._ws({ type: "tv_mgmt/action", entry_id: entryId, action, ...extra });
      const index = this._profiles.findIndex((p) => p.entry_id === entryId);
      if (index >= 0) this._profiles[index] = res.profile;
      this._error = null;
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._render();
  }

  async _callService(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
      this._error = null;
    } catch (err) {
      this._error = this._errorText(err);
      this._render();
    }
  }

  _errorText(err) {
    if (err?.code === "unauthorized") return "You don't have access to TV Mgmt. An admin can give you access under Access.";
    return err?.message || String(err);
  }

  _showError(err) {
    this._error = this._errorText(err);
    this._render();
  }

  _parseWindows(text) {
    return (text || "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const [times, ...label] = part.split(" ");
        const [start, end] = times.split("-");
        return { start, end, label: label.join(" ") };
      });
  }

  // ---- rendering -------------------------------------------------------------------

  _tabs() {
    if (this._me && !this._me.allowed) return [];
    return this._me?.is_admin ? [...TABS, { id: "access", label: "Access" }] : TABS;
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <header>
        <div class="bar"><span id="menu"></span><h1>TV Mgmt</h1></div>
        <nav role="tablist" aria-label="TV Mgmt sections">
          ${this._tabs().map((t) => `<button role="tab" data-tab="${t.id}" aria-selected="${t.id === this._tab}">${t.label}</button>`).join("")}
        </nav>
      </header>
      <main id="main" aria-live="polite"></main>`;

    const menu = document.createElement("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    this._menu = menu;
    this.shadowRoot.getElementById("menu").appendChild(menu);

    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => this._onChange(ev));
    this.shadowRoot.addEventListener("input", (ev) => this._onInput(ev));
    this.shadowRoot.addEventListener("submit", (ev) => this._onSubmit(ev));
    this.shadowRoot.addEventListener("focusout", () => {
      // Catch up on live updates held back while typing.
      setTimeout(() => {
        if (this._renderLater && !this._typing()) {
          this._renderLater = false;
          this._render();
        }
      }, 0);
    });
  }

  _render() {
    const main = this.shadowRoot.getElementById("main");
    if (!main) return;
    this.shadowRoot.querySelectorAll("nav button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === this._tab)));
    if (this._navShown !== this._tab) {
      // Where the tabs scroll (narrow phones), keep the open one in view.
      const current = this.shadowRoot.querySelector(`nav button[data-tab="${this._tab}"]`);
      if (current) {
        this._navShown = this._tab;
        current.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    }

    let body;
    if (this._tab === "access") {
      body = this._renderAccess();
    } else if (this._profiles === null) {
      body = this._error ? "" : loading("Loading your TVs…", "cards");
    } else if (!this._profiles.length) {
      body = `<div class="empty"><h2>No TVs yet</h2><p>Add a TV under Settings, Devices &amp; services, TV Mgmt, and it will show up here.</p></div>`;
    } else {
      body = {
        dashboard: () => this._renderDashboard(),
        activity: () => this._renderActivity(),
        analytics: () => this._renderAnalytics(),
        limits: () => this._renderLimits(),
        lights: () => this._renderLightsTab(),
      }[this._tab]();
    }
    const error = this._error ? `<div class="error" role="alert">${esc(this._error)}</div>` : "";
    main.classList.toggle("wide", this._tab === "dashboard");
    main.innerHTML = error + body;
  }

  _renderPicker() {
    if (this._profiles.length < 2) return `<div></div>`;
    return `<div class="picker" role="group" aria-label="TV">
      ${this._profiles.map((p) => `<button class="btn" data-pick="${p.entry_id}" aria-pressed="${p.entry_id === this._selected}">${esc(p.name)}</button>`).join("")}
    </div>`;
  }

  // Dashboard ------------------------------------------------------------------------

  _renderDashboard() {
    return `${this._renderHomeStrip()}<div class="grid">${this._profiles.map((p) => this._renderTv(p)).join("")}</div>`;
  }

  // The whole home's TV time, above the TVs, when a home limit is set.
  _renderHomeStrip() {
    const h = this._home;
    if (!h?.active) return "";
    const bar = (label, used, limitMin, left) => {
      if (!limitMin) return "";
      const total = limitMin * 60;
      const pct = Math.min(100, (used / total) * 100);
      const tone = left <= 0 ? "bad" : left <= (h.warn_minutes || 0) * 60 ? "warn" : "info";
      return `<div class="home-row">
        <div class="time-line"><span><strong>${duration(used)}</strong> <span class="muted">of ${duration(total)} ${label}</span></span>
          <span class="muted">${left > 0 ? `${duration(left)} left` : "No time left"}</span></div>
        <div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="${limitMin}" aria-valuenow="${minutes(used)}" aria-label="Whole home, ${label}"><div class="tone-${tone}" style="width:${pct}%"></div></div>
      </div>`;
    };
    const extra = h.daily_minutes ? h.daily_minutes + h.extension_minutes : 0;
    return `<section class="card home-strip" aria-label="Whole home">
      <div class="card-body">
        <div class="card-head"><h2>Whole home</h2><button class="link" data-tab="limits">Change</button></div>
        <div class="home-rows">
          ${bar("today", h.today_seconds, extra, h.daily_remaining)}
          ${bar("this week", h.week_seconds, h.weekly_minutes ? h.weekly_minutes + (h.extension_week_minutes || 0) : 0, h.weekly_remaining)}
        </div>
        ${h.action === "notify" ? `<small class="muted">When it runs out, the people picked are told. The TVs aren't turned off.</small>` : ""}
        <div class="row">
          <span class="label">Extra time for the whole home today${h.extension_minutes ? ` (${h.extension_minutes > 0 ? "+" : ""}${h.extension_minutes} min so far)` : ""}</span>
          <button class="btn" data-home-extra="-15">−15 min</button>
          <button class="btn" data-home-extra="15">+15 min</button>
          <button class="btn" data-home-extra="30">+30 min</button>
          <button class="btn" data-home-extra="60">+1 h</button>
        </div>
      </div>
    </section>`;
  }

  async _homeExtra(minutes) {
    try {
      const res = await this._ws({ type: "tv_mgmt/home/extension", minutes });
      this._home = res.home;
      this._error = null;
      await this._loadProfiles({ quiet: true });
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._render();
  }

  _renderTv(p) {
    const state = stateInfo(p);
    const off = p.is_on === false;
    const unknown = p.is_on === null;
    const blocked = p.state === "enforcing";
    const screenClass = ["screen", off || unknown ? "off" : "", blocked ? "blocked" : "", p.input_allowed === false ? "not-allowed" : ""].join(" ");
    const now = unknown ? "Can't reach the TV" : off ? "TV off" : esc(nameOf(p, p.current_input) ?? "Unknown input");
    let sub = "";
    if (!off && !unknown && p.input_lock) {
      sub = p.input_allowed ? "Allowed input" : `Not allowed, switching back to ${esc(nameOf(p, p.target_input) ?? "")}`;
    } else if (!off && !unknown && !p.input_lock) {
      sub = "Input lock is off";
    }
    if (p.lock_paused_reason) sub = esc(p.lock_paused_reason);

    const limit = p.budget_minutes > 0 ? p.budget_minutes + p.extension_minutes : 0;
    const usedMin = minutes(p.used_seconds);
    const pct = limit ? Math.min(100, (usedMin / limit) * 100) : 0;
    const homeLimited = (p.limit === "home_daily" || p.limit === "home_weekly") && p.remaining_seconds != null;
    const timeLine = homeLimited
      ? `<div class="time-line"><span><strong>${duration(p.used_seconds)}</strong> <span class="muted">today on this TV</span></span><span class="muted">${p.remaining_seconds > 0 ? `${duration(p.remaining_seconds)} left for the home ${p.limit === "home_weekly" ? "this week" : "today"}` : `No home time left ${p.limit === "home_weekly" ? "this week" : "today"}`}</span></div>`
      : limit
      ? `<div class="time-line"><span><strong>${duration(p.used_seconds)}</strong> <span class="muted">of ${duration(limit * 60)}</span></span><span class="muted">${p.remaining_seconds > 0 ? `${duration(p.remaining_seconds)} left` : "No time left"}</span></div>
         <div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="${limit}" aria-valuenow="${usedMin}" aria-label="Screen time used"><div class="tone-${state.tone === "bad" ? "bad" : state.tone === "warn" ? "warn" : "info"}" style="width:${pct}%"></div></div>`
      : `<div class="time-line"><span><strong>${duration(p.used_seconds)}</strong> <span class="muted">today</span></span><span class="muted">No daily limit</span></div>`;

    const lastBlocked = p.last_blocked_input && p.last_blocked_at ? `${esc(nameOf(p, p.last_blocked_input))} at ${timeOf(p.last_blocked_at)}` : "None";
    const adultOn = p.state === "adult_mode" || !!p.adult_mode_until;
    const id = p.entry_id;
    const open = this._expanded.has(id);
    // Collapsed, the Apple TV sits inside the TV's screen; expanded, it gets its own spot.
    const screen = (withAtv) => `<div class="screen-wrap">
          <div class="tv-frame">
          ${this._ambient(p)}
          <div class="${screenClass}${withAtv ? " with-atv" : ""}">
            <div class="glow"></div>
            <button class="tv-name link" data-more-info="${esc(p.tv_entity_id)}" title="Open ${esc(p.name)}">${esc(p.name)}</button>
            <span class="chip tone-${state.tone}">${esc(state.text)}</span>
            <div class="now">${now}</div>
            ${sub ? `<div class="sub">${sub}</div>` : ""}
            ${withAtv ? this._atvMini(p) : ""}
          </div>
          </div>
          <div class="stand"></div>
        </div>`;
    const summary = [
      limit ? `${duration(p.used_seconds)} of ${duration(limit * 60)}` : `${duration(p.used_seconds)} today`,
      limit ? (p.remaining_seconds > 0 ? `${duration(p.remaining_seconds)} left` : "No time left") : "",
      p.force_block ? "Blocked" : "",
    ].filter(Boolean).join(" · ");
    const toggle = `<button class="expand" data-expand="${id}" aria-expanded="${open}" aria-controls="more-${id}">
        <span>${open ? "Show less" : esc(summary)}</span>${icon("chevron-down")}</button>`;
    if (!open) {
      return `
      <section class="card tv-card collapsed${p.apple_tv ? " has-atv" : ""}" aria-label="${esc(p.name)}">
        ${screen(!!p.apple_tv)}
        ${toggle}
      </section>`;
    }

    return `
      <section class="card tv-card${p.apple_tv ? " has-atv" : ""}" aria-label="${esc(p.name)}">
       <div class="tv-layout" id="more-${id}">
        <div class="col col-tv">
        ${screen(false)}
        <div class="card-body">
          <div class="tv-remote" id="tvr-${id}">${this._tvRemote(p)}</div>
          <div>${timeLine}</div>
          ${this._messageBox(p)}
        </div>
        </div>
        ${p.apple_tv ? `<div class="col col-atv card-body">${this._renderAppleTv(p)}</div>` : ""}
        <div class="col col-controls card-body">
          <dl class="facts">
            <dt>Extra time today</dt><dd>${p.extension_minutes ? `${p.extension_minutes > 0 ? "+" : ""}${p.extension_minutes} min` : "None"}</dd>
            <dt>Blocked switches</dt><dd>${p.blocked_switches}</dd>
            ${p.adult_seconds ? `<dt>Adult mode today</dt><dd>${duration(p.adult_seconds)}${p.adult_mode_counts ? "" : `<span class="muted"> · not counted</span>`}</dd>` : ""}
            <dt>Last tried</dt><dd>${lastBlocked}</dd>
            <dt>Allowed</dt><dd>${esc((p.allowed_inputs || []).map((raw) => nameOf(p, raw)).join(", ") || "Not set")}</dd>
            ${(p.tv_lights || []).length ? `<dt>TV lights</dt><dd>${esc(p.tv_lights.map((e) => this._hass?.states?.[e]?.attributes?.friendly_name || e).join(", "))}</dd>` : ""}
          </dl>
          <div class="row">
            <span class="label">Extra time</span>
            <button class="btn" data-act="grant_extension" data-id="${id}" data-minutes="-15">−15 min</button>
            <button class="btn" data-act="grant_extension" data-id="${id}" data-minutes="15">+15 min</button>
            <button class="btn" data-act="grant_extension" data-id="${id}" data-minutes="30">+30 min</button>
            <button class="btn" data-act="grant_extension" data-id="${id}" data-minutes="60">+1 h</button>
            <form class="custom-time" data-extra="${id}" novalidate>
              <input type="number" name="minutes" min="-240" max="720" step="1" inputmode="numeric" placeholder="Minutes" aria-label="Extra minutes for ${esc(p.name)}. Use a minus sign to take time away.">
              <button class="btn" type="submit">Add</button>
            </form>
          </div>
          <div class="row">
            <span class="label">Mode${p.mode_sync_entity ? " (kept in sync with Apple TV Mgmt)" : ""}</span>
            <div class="seg" role="group" aria-label="Mode">
              ${MODES.map((m) => `<button data-act="set_mode" data-id="${id}" data-mode="${m.id}" aria-pressed="${p.mode === m.id}">${m.label}</button>`).join("")}
            </div>
          </div>
          <label class="toggle"><span>Input lock<small>Keep the TV on ${esc(nameOf(p, p.target_input) ?? "its allowed input")}</small></span>
            <span class="switch"><input type="checkbox" data-toggle="set_input_lock" data-id="${id}" ${p.input_lock ? "checked" : ""}><span></span></span></label>
          <label class="toggle"><span>Adult mode<small>${adultOn && p.adult_mode_until ? `Rules lifted until ${timeOf(p.adult_mode_until)}` : `Lift every rule for ${duration(p.adult_mode_duration * 60)}`}</small></span>
            <span class="switch"><input type="checkbox" data-toggle="set_adult_mode" data-id="${id}" ${adultOn ? "checked" : ""}><span></span></span></label>
          ${this._entityLinks(p)}
          <div class="row">
            ${p.force_block
              ? `<button class="btn primary" data-act="unblock" data-id="${id}">Unblock</button>`
              : `<button class="btn danger" data-act="force_block" data-id="${id}">Block now</button>`}
            <button class="btn" data-act="reset_usage" data-id="${id}" data-confirm="Reset today's screen time, extra time and block for ${esc(p.name)}?">Reset today</button>
          </div>
        </div>
       </div>
       ${toggle}
      </section>`;
  }

  // The Apple TV in one line, inside a collapsed TV's screen.
  _atvMini(p) {
    const a = p.apple_tv;
    if (!a) return "";
    const st = this._hass?.states?.[a.entity_id];
    const attrs = st?.attributes || {};
    const state = st?.state;
    const unavailable = !st || state === "unavailable" || state === "unknown";
    const asleep = !unavailable && (state === "off" || state === "standby");
    const appName = attrs.app_name || a.app_name;
    const title = unavailable ? "Can't reach it" : asleep ? "Asleep"
      : attrs.media_series_title || attrs.media_title || appName || "Home screen";
    const stateLabel = { playing: "Playing", paused: "Paused", idle: "Idle", on: "On" }[state];
    const detail = !unavailable && !asleep
      ? [stateLabel, (attrs.media_title || attrs.media_series_title) && appName ? `in ${appName}` : ""].filter(Boolean).join(" · ")
      : "";
    const pic = !unavailable && !asleep && attrs.entity_picture;
    const art = pic
      ? `<img src="${esc(this._hass.hassUrl ? this._hass.hassUrl(pic) : pic)}" alt="" loading="lazy" onerror="this.remove()">`
      : icon("box");
    const rule = a.stop_reason ? `<span class="mini-flag">${a.stop_reason === "show_blocked" ? "Blocked show" : a.stop_reason === "limit" ? "Limit reached" : "Blocked app"}</span>` : "";
    return `<button class="atv-mini${asleep || unavailable ? " asleep" : ""}" id="npm-${p.entry_id}" data-more-info="${esc(a.entity_id)}" title="Open the Apple TV">
        <span class="atv-mini-art">${art}</span>
        <span class="atv-mini-text"><span class="atv-mini-top">Apple TV${detail ? ` · ${esc(detail)}` : ""}${rule}</span>
        <span class="atv-mini-title">${esc(title)}</span></span>
      </button>`;
  }

  _nowPlayingInner(p) {
    const a = p.apple_tv;
    const st = this._hass?.states?.[a.entity_id];
    const attrs = st?.attributes || {};
    const state = st?.state;
    const unavailable = !st || state === "unavailable" || state === "unknown";
    const asleep = !unavailable && (state === "off" || state === "standby");
    const features = Number(attrs.supported_features || 0);
    const has = (bit) => (features & bit) === bit;

    const appName = attrs.app_name || a.app_name;
    let title;
    let sub = "";
    if (unavailable) title = "Can't reach the Apple TV";
    else if (asleep) title = "Asleep";
    else {
      title = attrs.media_title || appName || "Home screen";
      if (attrs.media_series_title) {
        const se = [attrs.media_season && `Season ${attrs.media_season}`, attrs.media_episode && `Episode ${attrs.media_episode}`].filter(Boolean).join(", ");
        sub = [attrs.media_series_title, se].filter(Boolean).join(", ");
      } else if (attrs.media_artist) {
        sub = [attrs.media_artist, attrs.media_album_name].filter(Boolean).join(", ");
      }
    }
    const stateLabel = { playing: "Playing", paused: "Paused", idle: "Idle", on: "On" }[state];
    const rule = a.stop_reason
      ? `<span class="chip tone-bad">${{ blocked: "Blocked app", not_allowed: "Not allowed", limit: "Limit reached", show_blocked: "Blocked show" }[a.stop_reason] || "Not allowed"}</span>`
      : "";
    const pic = !unavailable && !asleep && attrs.entity_picture;
    const art = pic
      ? `<img src="${esc(this._hass.hassUrl ? this._hass.hassUrl(pic) : pic)}" alt="" loading="lazy" onerror="this.remove()">`
      : icon("box");
    const appLine = !unavailable && !asleep && attrs.media_title && appName ? `<div class="np-app">in ${esc(appName)}</div>` : "";

    const duration = Number(attrs.media_duration || 0);
    const progress = !unavailable && !asleep && duration > 0
      ? `<div class="np-progress" data-duration="${duration}" data-position="${Number(attrs.media_position || 0)}" data-updated="${esc(attrs.media_position_updated_at || "")}" data-playing="${state === "playing" ? 1 : 0}">
          <div class="np-track"><div class="np-fill" style="width:${Math.min(100, (Number(attrs.media_position || 0) / duration) * 100)}%"></div></div>
          <div class="np-times"><span class="np-elapsed">${clock(Number(attrs.media_position || 0))}</span><span>${clock(duration)}</span></div>
        </div>`
      : "";

    const eid = esc(a.entity_id);
    const button = (svc, ico, label, extra = "") =>
      `<button class="icon-btn" data-media="${svc}" data-eid="${eid}" aria-label="${label}" title="${label}" ${extra}>${icon(ico)}</button>`;
    let controls = "";
    if (!asleep && !unavailable) {
      const playing = state === "playing";
      controls = [
        has(16) ? button("media_previous_track", "prev", "Previous") : "",
        (playing ? has(1) : has(16384)) ? button("media_play_pause", playing ? "pause" : "play", playing ? "Pause" : "Play", 'data-primary="1"') : "",
        has(32) ? button("media_next_track", "next", "Next") : "",
      ].join("");
    }
    const remote = unavailable ? "" : this._remote(p, "apple_tv", a.remote_keys, !asleep);

    return `<div class="np-tile${asleep || unavailable ? " asleep" : ""}">
        <div class="np-art">${art}</div>
        <div class="np-meta">
          <div class="np-top"><button class="np-label link" data-more-info="${eid}" title="Open the Apple TV">Apple TV</button>${stateLabel && !asleep ? `<span class="np-state">${stateLabel}</span>` : ""}${rule}</div>
          <div class="np-title">${esc(title)}</div>
          ${sub ? `<div class="np-sub">${esc(sub)}</div>` : ""}
          ${appLine}
        </div>
      </div>
      ${progress}
      ${controls ? `<div class="np-controls">${controls}</div>` : ""}
      ${remote}`;
  }

  _msg(id) {
    return (this._msgDrafts[id] ||= { text: "", apple_tv: true, screen: true, speak: true });
  }

  // Send a message to the TV: AirPlayed to the Apple TV, on the TV screen, or spoken.
  _messageBox(p) {
    const t = p.message_targets || {};
    const id = p.entry_id;
    const d = this._msg(id);
    const where = [
      t.apple_tv && { key: "apple_tv", label: "Apple TV (AirPlay)" },
      t.screen && { key: "screen", label: "TV screen" },
      t.speak && { key: "speak", label: "Speakers" },
    ].filter(Boolean);
    if (!where.length) {
      return `<div class="message-box"><span class="label">Message the TV</span>
        <small class="muted">Link an Apple TV, or set up Configure → Warnings on the TV, to send messages to the TV.</small></div>`;
    }
    const busy = this._msgBusy === id;
    return `<div class="message-box">
      <label class="label" for="msg-${id}">Message the TV</label>
      <form class="message-form" data-message="${id}" novalidate>
        <div class="message-row">
          <input id="msg-${id}" type="text" name="message" maxlength="120" placeholder="Type a message" value="${esc(d.text)}" autocomplete="off">
          <button class="btn primary" type="submit" ${busy ? "disabled" : ""}>${busy ? "Sending…" : "Send"}</button>
        </div>
        <div class="presets">${(p.message_presets || [])
          .map((m) => `<button type="button" class="chip-btn" data-preset="${esc(m)}" data-id="${id}">${esc(m)}</button>`)
          .join("")}</div>
        <div class="message-where" role="group" aria-label="Send to">${where
          .map((w) => `<label><input type="checkbox" class="check" data-msg-where="${w.key}" data-id="${id}" ${d[w.key] ? "checked" : ""}> ${w.label}</label>`)
          .join("")}</div>
        ${this._msgNote[id] ? `<span class="saved" role="status">${esc(this._msgNote[id])}</span>` : ""}
      </form>
    </div>`;
  }

  async _sendMessage(id) {
    const d = this._msg(id);
    const text = (d.text || "").trim();
    if (!text) {
      this._error = "Type a message, or pick one.";
      this._render();
      return;
    }
    const p = this._profiles.find((x) => x.entry_id === id);
    const t = p?.message_targets || {};
    const pick = { apple_tv: !!(t.apple_tv && d.apple_tv), screen: !!(t.screen && d.screen), speak: !!(t.speak && d.speak) };
    if (!Object.values(pick).some(Boolean)) {
      this._error = "Pick where to send the message.";
      this._render();
      return;
    }
    this._msgBusy = id;
    this._error = null;
    this._render();
    try {
      const res = await this._ws({ type: "tv_mgmt/message", entry_id: id, text, ...pick });
      const names = { apple_tv: "the Apple TV", screen: "the TV screen", speak: "the speakers" };
      this._msgNote[id] = `Sent to ${res.sent.map((k) => names[k]).join(" and ")}`;
      d.text = "";
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._msgBusy = null;
    this._render();
  }

  // Links that open Home Assistant's dialog for each of this TV's entities.
  _entityLinks(p) {
    const own = (p.entities || [])
      .filter((e) => ENTITY_LABELS[e.key])
      .sort((a, b) => ENTITY_ORDER.indexOf(a.key) - ENTITY_ORDER.indexOf(b.key));
    const links = [
      { entity_id: p.tv_entity_id, label: "TV" },
      ...(p.apple_tv ? [{ entity_id: p.apple_tv.entity_id, label: "Apple TV" }] : []),
      ...(p.apple_tv?.remote_entity ? [{ entity_id: p.apple_tv.remote_entity, label: "Apple TV remote" }] : []),
      ...own.map((e) => ({ entity_id: e.entity_id, label: ENTITY_LABELS[e.key] })),
    ];
    return `<div class="entities"><span class="label">Entities</span>
      <div class="entity-links">${links
        .map((l) => `<button data-more-info="${esc(l.entity_id)}" title="${esc(l.entity_id)}">${esc(l.label)}</button>`)
        .join("")}</div></div>`;
  }

  _tvRemote(p) {
    this._npStates = this._npStates || {};
    const st = this._hass?.states?.[p.tv_entity_id];
    this._npStates[p.tv_entity_id] = st;
    const lights = this._tvLightsButton(p);
    if (!st || st.state === "unavailable") return lights ? `<div class="remote"><div class="remote-bar">${lights}</div>${this._tvLightsStatus(p)}</div>` : "";
    return this._remote(p, "tv", p.remote_keys, !["off", "standby"].includes(st.state));
  }

  // "Turn TV lights off", next to the TV's power button: only with TV lights
  // picked, and in any TV state (off, blocked, bedtime), since it's for the
  // lights, not the TV.
  _tvLightsButton(p) {
    if (!(p.tv_lights || []).length) return "";
    const busy = this._tvLightsBusy?.has(p.entry_id);
    return `<button class="btn" data-tv-lights-off="${esc(p.entry_id)}" ${busy ? "disabled" : ""}>${icon("lightbulb-off")} ${busy ? "Turning off…" : "Turn TV lights off"}</button>`;
  }

  _tvLightsStatus(p) {
    const note = this._tvLightsNote?.[p.entry_id];
    if (!note) return "";
    return note.bad
      ? `<small class="tone-bad-text" role="alert">${esc(note.text)}</small>`
      : `<small class="saved" role="status">${esc(note.text)}</small>`;
  }

  // Power, volume and a d-pad for the TV ("tv") or its Apple TV ("apple_tv").
  // Only the buttons that device's integration can press are shown.
  _remote(p, device, keys, isOn) {
    keys = keys || [];
    // Power depends on whether the device is on right now, so check it live.
    const eid = device === "apple_tv" ? p.apple_tv?.entity_id : p.tv_entity_id;
    const features = Number(this._hass?.states?.[eid]?.attributes?.supported_features || 0);
    if (!(features & (isOn === false ? 128 : 256))) keys = keys.filter((k) => k !== "power");
    const lights = device === "tv" ? this._tvLightsButton(p) : "";
    const lightsStatus = device === "tv" ? this._tvLightsStatus(p) : "";
    if (!keys.length) return lights ? `<div class="remote"><div class="remote-bar">${lights}</div>${lightsStatus}</div>` : "";
    const has = (k) => keys.includes(k);
    const id = p.entry_id;
    const what = device === "apple_tv" ? "Apple TV" : "TV";
    const open = this._remoteOpen.has(`${id}:${device}`);
    const key = (k, ico, label, cls = "", extra = "") =>
      `<button class="icon-btn ${cls}" data-key="${k}" data-device="${device}" data-id="${id}" aria-label="${label}" title="${label}" ${extra}>${ico}</button>`;

    const awake = isOn !== false;
    const powerLabel = device === "apple_tv" ? (awake ? "Sleep" : "Wake") : awake ? "Turn TV off" : "Turn TV on";
    const powerAria = device === "apple_tv" ? `${powerLabel} the ${what}` : powerLabel;
    // Awake: a round power button beside volume. Off: the only button, so it's labelled.
    const power = !has("power")
      ? ""
      : awake
        ? key("power", icon("power-off"), powerAria, "power-off")
        : `<button class="btn" data-key="power" data-device="${device}" data-id="${id}" aria-label="${powerAria}">${icon("power")} ${powerLabel}</button>`;
    if (!awake) return `<div class="remote"><div class="remote-bar">${power}${lights}</div>${lightsStatus}</div>`;

    let muted = false;
    if (device === "tv") muted = !!this._hass?.states?.[p.tv_entity_id]?.attributes?.is_volume_muted;
    const volume = [
      has("volume_down") ? key("volume_down", icon("volume-down"), `${what} volume down`) : "",
      has("mute") ? key("mute", icon("mute"), muted ? `Unmute the ${what}` : `Mute the ${what}`, "", `aria-pressed="${muted}"`) : "",
      has("volume_up") ? key("volume_up", icon("volume-up"), `${what} volume up`) : "",
    ].join("");
    const nav = ["up", "down", "left", "right", "select"].some(has);
    const toggle = nav || has("back") || has("home")
      ? `<button class="btn" data-remote-toggle="${id}:${device}" aria-expanded="${open}">${icon("remote")} Remote</button>`
      : "";

    let pad = "";
    if (open) {
      const back = device === "apple_tv" ? "Menu" : "Back";
      pad = `<div class="dpad-wrap" role="group" aria-label="${what} remote">
        ${nav ? `<div class="dpad">
          ${has("up") ? key("up", icon("up"), "Up", "k-up") : ""}
          ${has("left") ? key("left", icon("left"), "Left", "k-left") : ""}
          ${has("select") ? key("select", "OK", "OK", "ok") : ""}
          ${has("right") ? key("right", icon("right"), "Right", "k-right") : ""}
          ${has("down") ? key("down", icon("down"), "Down", "k-down") : ""}
        </div>` : ""}
        <div class="dpad-side">
          ${has("back") ? `<button class="btn" data-key="back" data-device="${device}" data-id="${id}">${icon("back")} ${back}</button>` : ""}
          ${has("home") ? `<button class="btn" data-key="home" data-device="${device}" data-id="${id}">${icon("home")} Home</button>` : ""}
        </div>
      </div>`;
    }
    return `<div class="remote">
      <div class="remote-bar">${power}${lights}${volume ? `<span class="vol">${volume}</span>` : ""}<span class="np-spacer"></span>${toggle}</div>
      ${lightsStatus}
      ${pad}
    </div>`;
  }

  async _press(entryId, device, key) {
    try {
      await this._ws({ type: "tv_mgmt/remote", entry_id: entryId, device, key });
      if (this._error) {
        this._error = null;
        this._render();
      }
    } catch (err) {
      this._showError(err);
    }
  }

  _renderAppleTv(p) {
    const a = p.apple_tv;
    const apps = (a.apps_today || []).slice(0, 4);
    const list = apps.length
      ? `<ul class="apps-list">${apps
          .map((app) => {
            const mins = minutes(app.seconds);
            const limit = app.limit_minutes;
            const over = limit && mins >= limit;
            return `<li><div class="row-line"><span>${esc(app.name)}</span><span class="muted">${limit ? `${mins} of ${limit} min` : duration(app.seconds)}</span></div>
              ${limit ? `<div class="meter"><div class="${over ? "tone-bad" : "tone-info"}" style="width:${Math.min(100, (mins / limit) * 100)}%"></div></div>` : ""}</li>`;
          })
          .join("")}</ul>`
      : `<p class="muted" style="margin:0">No apps opened today.</p>`;
    if (this._npStates) this._npStates[a.entity_id] = this._hass?.states?.[a.entity_id];
    return `<div class="atv">
      <section class="np" id="np-${p.entry_id}" aria-label="Now playing on the Apple TV" aria-live="polite">${this._nowPlayingInner(p)}</section>
      <div class="atv-head"><span class="label">Apple TV apps today</span></div>
      ${list}
      ${this._watchedToday(a)}
    </div>`;
  }

  _watchedToday(a) {
    const shows = (a.media_today || []).slice(0, 5);
    if (!shows.length) return "";
    return `<div class="atv-head"><span class="label">Watched today</span></div>
      <ul class="apps-list watched">${shows
        .map((m) => {
          const isBlocked = (a.rules.blocked_shows || []).some((x) => x.trim().toLowerCase() === m.show.trim().toLowerCase());
          return `<li><div class="row-line"><span>${esc(m.show)}${m.app ? `<small>${esc(m.app)}</small>` : ""}</span>
            <span class="watched-end"><span class="muted">${duration(m.seconds)}</span>
            <button class="chip-btn${isBlocked ? " on" : ""}" data-block-show="${esc(m.show)}" data-id="${this._entryFor(a)}" aria-pressed="${isBlocked}" title="${isBlocked ? "Unblock" : "Block"} ${esc(m.show)}">${isBlocked ? "Blocked" : "Block"}</button></span></div></li>`;
        })
        .join("")}</ul>`;
  }

  _entryFor(a) {
    return this._profiles.find((p) => p.apple_tv === a)?.entry_id ?? "";
  }

  // Block or unblock a show straight from the dashboard.
  async _toggleShow(entryId, show) {
    const p = this._profiles.find((x) => x.entry_id === entryId);
    const current = p?.apple_tv?.rules?.blocked_shows || [];
    const fold = (x) => x.trim().toLowerCase();
    const isBlocked = current.some((x) => fold(x) === fold(show));
    if (!isBlocked && !window.confirm(`Block ${show} on the Apple TV? It's stopped in any app.`)) return;
    const next = isBlocked ? current.filter((x) => fold(x) !== fold(show)) : [...current, show];
    try {
      await this._ws({ type: "tv_mgmt/apple_tv/set", entry_id: entryId, blocked_shows: next });
      this._appsDraft = null;
      this._error = null;
      await this._loadProfiles({ quiet: true });
    } catch (err) {
      this._showError(err);
    }
  }

  // Access ---------------------------------------------------------------------------

  _renderAccess() {
    const d = this._accessDraft;
    if (!d) {
      if (!this._loadingAccess) {
        this._loadingAccess = true;
        this._ws({ type: "tv_mgmt/access/get" })
          .then((res) => {
            this._accessDraft = { people: res.people, ids: new Set(res.user_ids) };
          })
          .catch((err) => (this._error = this._errorText(err)))
          .finally(() => {
            this._loadingAccess = false;
            this._render();
          });
      }
      return loading("Loading people…");
    }
    const rows = d.people
      .map((u, i) => {
        const checked = u.is_admin || d.ids.has(u.id);
        const tag = u.is_owner ? "Owner" : u.is_admin ? "Admin" : "";
        return `<div class="person">
          <input type="checkbox" class="check" id="person-${i}" data-person="${esc(u.id)}" ${checked ? "checked" : ""} ${u.is_admin ? "disabled" : ""}>
          <label for="person-${i}">${esc(u.name)}</label>
          <span class="tag">${tag}</span>
        </div>`;
      })
      .join("");
    return `<section class="card"><div class="card-body">
      <form id="access" novalidate>
        <div class="field">
          <label>Who can use TV Mgmt</label>
          <small>Admins always can. Check the other people who can use this app and change TV Mgmt's switches, mode and actions, such as a parent who isn't an admin. Everyone else, kids included, is turned away. Automations aren't affected.</small>
        </div>
        <div class="people">${rows}</div>
        ${[...d.ids].some((uid) => d.people.some((u) => u.id === uid && !u.is_admin))
          ? `<small class="muted">Home Assistant can't show a sidebar entry to only some people, so everyone will see TV Mgmt in the sidebar. People who aren't checked get a page saying they don't have access.</small>`
          : `<small class="muted">Only admins see TV Mgmt in the sidebar.</small>`}
        <div class="form-actions">
          <button class="btn primary" type="submit" ${this._busy ? "disabled" : ""}>Save</button>
          ${this._accessNote ? `<span class="saved" role="status">${esc(this._accessNote)}</span>` : ""}
        </div>
      </form>
    </div></section>`;
  }

  async _saveAccess() {
    const d = this._accessDraft;
    this._busy = true;
    this._render();
    try {
      const res = await this._ws({ type: "tv_mgmt/access/set", user_ids: [...d.ids] });
      this._accessDraft = { people: res.people, ids: new Set(res.user_ids) };
      this._accessNote = "Access saved";
      this._error = null;
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busy = false;
    this._render();
  }

  // Activity -------------------------------------------------------------------------

  _renderActivity() {
    const a = this._activity;
    if (!a || a.entry_id !== this._selected) {
      if (!this._loadingActivity) {
        this._loadingActivity = true;
        this._loadActivity().finally(() => (this._loadingActivity = false));
      }
      return `${this._activityToolbar(null)}${loading("Loading activity…")}`;
    }
    const profile = this._profiles.find((p) => p.entry_id === this._selected);
    const allowed = new Set(profile?.allowed_inputs || []);
    const dayStart = new Date(`${a.date}T00:00:00`).getTime();
    const daySpan = 24 * 3600 * 1000;
    const pos = (iso) => Math.max(0, Math.min(100, ((new Date(iso).getTime() - dayStart) / daySpan) * 100));

    const bars = a.segments
      .map((s) => {
        const left = pos(s.start);
        const width = Math.max(0.3, pos(s.end) - left);
        const cls = ["seg-bar", allowed.size && !allowed.has(s.source) ? "not-allowed" : "", s.live ? "live" : ""].join(" ");
        return `<div class="${cls}" style="left:${left}%;width:${width}%" title="${esc(nameOf(profile, s.source) ?? "Unknown")}: ${timeOf(s.start)} to ${timeOf(s.end)}"></div>`;
      })
      .join("");
    // Adult mode: shaded over both strips. The app strip and the log leave out
    // what was open then (the server drops it), so only the stretch shows.
    const adult = a.adult_periods || [];
    const adultBands = adult
      .map((s) => {
        const left = pos(s.start);
        const width = Math.max(0.3, pos(s.end) - left);
        return `<div class="adult-band" style="left:${left}%;width:${width}%" title="Adult mode: ${timeOf(s.start)} to ${timeOf(s.end)}"></div>`;
      })
      .join("");
    const isToday = a.date === a.today;
    const nowLine = isToday ? `<div class="now-line" style="left:${pos(new Date().toISOString())}%"></div>` : "";
    const hours = [0, 6, 12, 18]
      .map((h) => `<span style="left:${(h / 24) * 100}%;${h === 0 ? "transform:none" : ""}">${new Date(dayStart + h * 3600000).toLocaleTimeString([], { hour: "numeric" })}</span>`)
      .join("");

    const bySource = {};
    for (const s of a.segments) bySource[s.source ?? "Unknown"] = (bySource[s.source ?? "Unknown"] || 0) + s.seconds;
    const legend = Object.entries(bySource)
      .sort((x, y) => y[1] - x[1])
      .map(([src, secs]) => `<span><i class="${allowed.size && !allowed.has(src) ? "not-allowed" : ""}"></i>${esc(nameOf(profile, src))}: ${duration(secs)}</span>`)
      .join("") + (adult.length ? `<span><i class="adult"></i>Adult mode: ${duration(a.adult_viewing_seconds || 0)}</span>` : "");

    const events = a.events.length
      ? `<ul class="log">${a.events
          .map((e) => {
            const d = describeEvent(e, profile);
            return `<li class="${d.tone ? `tone-${d.tone}` : ""}"><time datetime="${e.t}">${timeOf(e.t)}</time>${icon(d.icon)}<span>${esc(d.text)}</span></li>`;
          })
          .join("")}</ul>`
      : `<p class="muted">Nothing happened on this TV ${isToday ? "yet today" : "this day"}.</p>`;

    return `
      ${this._activityToolbar(a)}
      <section class="card"><div class="card-body">
        <div class="time-line"><span><strong>${duration(a.viewing_seconds)}</strong> <span class="muted">on screen${a.adult_viewing_seconds ? `, ${duration(a.adult_viewing_seconds)} in adult mode` : ""}</span></span>
          ${a.totals ? `<span class="muted">${a.totals.blocked} blocked switch${a.totals.blocked === 1 ? "" : "es"}</span>` : ""}</div>
        <div>
          <div class="tape" role="img" aria-label="When the TV was on, by input">${bars}${adultBands}${nowLine}</div>
          <div class="hours">${hours}</div>
          ${legend ? `<div class="legend">${legend}</div>` : ""}
          ${profile?.apple_tv ? this._appTape(a, pos, nowLine, adultBands) : ""}
        </div>
      </div></section>
      <section class="card" style="margin-top:20px"><div class="card-body">${events}</div></section>`;
  }

  _appTape(a, pos, nowLine, adultBands = "") {
    const segs = a.app_segments || [];
    const byApp = {};
    for (const s of segs) byApp[s.name ?? s.app] = (byApp[s.name ?? s.app] || 0) + s.seconds;
    const ranked = Object.entries(byApp).sort((x, y) => y[1] - x[1]);
    // Most-used apps get their own colour; the rest share the last one.
    const colour = {};
    ranked.forEach(([name], i) => (colour[name] = APP_COLOURS[Math.min(i, APP_COLOURS.length - 1)]));
    const bars = segs
      .map((s) => {
        const left = pos(s.start);
        const width = Math.max(0.3, pos(s.end) - left);
        const name = s.name ?? s.app;
        return `<div class="seg-bar${s.live ? " live" : ""}" style="left:${left}%;width:${width}%;background-color:${colour[name]}" title="${esc(name)}: ${timeOf(s.start)} to ${timeOf(s.end)}"></div>`;
      })
      .join("");
    const legend = ranked
      .map(([name, secs]) => `<span><i style="background:${colour[name]}"></i>${esc(name)}: ${duration(secs)}</span>`)
      .join("");
    return `<div class="tape-label">${icon("box")} Apple TV apps</div>
      <div class="tape apps-tape" role="img" aria-label="When apps were open on the Apple TV">${bars}${adultBands}${nowLine}</div>
      ${legend ? `<div class="legend">${legend}</div>` : `<p class="muted">No apps opened on the Apple TV this day.</p>`}`;
  }

  _activityToolbar(a) {
    const date = a?.date;
    const today = a?.today;
    const canBack = a && (!a.first_date || date > a.first_date);
    const canForward = a && date < today;
    return `<div class="toolbar">
      ${this._renderPicker()}
      <div class="date-nav">
        <button class="btn" data-day="-1" aria-label="Previous day" ${canBack ? "" : "disabled"}>${icon("left")}</button>
        <h2>${a ? esc(dayLabel(date, today)) : ""}</h2>
        <button class="btn" data-day="1" aria-label="Next day" ${canForward ? "" : "disabled"}>${icon("right")}</button>
        ${a && date !== today ? `<button class="btn" data-day="today">Today</button>` : ""}
      </div>
    </div>`;
  }

  // Analytics ------------------------------------------------------------------------

  _renderAnalytics() {
    const data = this._analytics;
    if (!data || data.entry_id !== this._selected || data.days.length !== this._range) {
      if (!this._loadingAnalytics) {
        this._loadingAnalytics = true;
        this._loadAnalytics().finally(() => (this._loadingAnalytics = false));
      }
      return `${this._analyticsToolbar()}${loading("Loading analytics…", "chart")}`;
    }
    const s = data.summary;
    const stats = `
      <div class="stats">
        <div class="card stat"><b>${duration(s.total_seconds)}</b><span>Total screen time</span></div>
        <div class="card stat"><b>${duration(s.average_seconds)}</b><span>Daily average</span></div>
        <div class="card stat"><b>${s.days_over_limit}</b><span>Days the limit was reached</span></div>
        <div class="card stat"><b>${s.blocked}</b><span>Blocked input switches</span></div>
        ${data.has_apple_tv ? `<div class="card stat"><b>${s.apps_stopped ?? 0}</b><span>Apple TV apps stopped</span></div>` : ""}
        <div class="card stat"><b>${duration(s.adult_seconds || 0)}</b><span>Adult mode${this._adultCountNote(s)}</span></div>
      </div>`;
    const notes = [];
    if (s.recorded_days < data.days.length) {
      notes.push(`TV Mgmt has ${s.recorded_days} day${s.recorded_days === 1 ? "" : "s"} of history so far. Days before that show as empty.`);
    }
    if (s.adult_untracked_days) {
      notes.push("Adult mode is tracked from TV Mgmt 1.16.0. Before that, its time is part of the screen time.");
    }
    const note = notes.map((n) => `<p class="muted">${esc(n)}</p>`).join("");
    const legend = s.adult_seconds
      ? `<div class="legend"><span><i></i>Screen time</span><span><i class="adult"></i>Adult mode</span></div>`
      : "";
    return `${this._analyticsToolbar()}${stats}
      <section class="card"><div class="card-body">
        <div><strong>Screen time per day</strong><div class="muted">The dashed line is the daily limit, including extra time.</div></div>
        ${this._chart(data.days)}
        ${legend}
        ${note}
      </div></section>
      ${data.has_apple_tv ? this._topApps(s.top_apps || []) + this._topMedia(s.top_media || []) : ""}`;
  }

  // How the Adult mode total relates to the limits, for its stat card.
  _adultCountNote(s) {
    if (!s.adult_seconds) return "";
    if (!s.adult_counted_seconds) return ", not counted";
    if (s.adult_counted_seconds >= s.adult_seconds) return ", counted";
    return `, ${duration(s.adult_counted_seconds)} counted`;
  }

  _topApps(apps) {
    const max = Math.max(1, ...apps.map((a) => a.seconds));
    const rows = apps.length
      ? `<ul class="top-apps">${apps
          .map((a) => `<li><span class="name">${esc(a.name ?? a.app)}</span><div class="bar-track"><div class="bar-fill" style="width:${(a.seconds / max) * 100}%"></div></div><span class="muted">${duration(a.seconds)}</span></li>`)
          .join("")}</ul>`
      : `<p class="muted">No Apple TV apps in this range yet.</p>`;
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <div><strong>Top Apple TV apps</strong></div>${rows}</div></section>`;
  }

  _topMedia(shows) {
    const max = Math.max(1, ...shows.map((m) => m.seconds));
    const rows = shows.length
      ? `<ul class="top-apps">${shows
          .map((m) => `<li><span class="name" title="${esc(m.show)}${m.app ? ` (${esc(m.app)})` : ""}">${esc(m.show)}</span><div class="bar-track"><div class="bar-fill media" style="width:${(m.seconds / max) * 100}%"></div></div><span class="muted">${duration(m.seconds)}</span></li>`)
          .join("")}</ul>`
      : `<p class="muted">Nothing watched on the Apple TV in this range yet.</p>`;
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <div><strong>Top shows and movies</strong><div class="muted">Time spent playing, not paused.</div></div>${rows}</div></section>`;
  }

  _analyticsToolbar() {
    return `<div class="toolbar">${this._renderPicker()}
      <div class="seg" role="group" aria-label="Range">
        ${RANGES.map((r) => `<button data-range="${r}" aria-pressed="${this._range === r}">${r} days</button>`).join("")}
      </div></div>`;
  }

  _chart(days) {
    // Draw at the card's real width so labels aren't stretched.
    const main = this.shadowRoot.getElementById("main");
    const W = Math.max(280, Math.min(1060, (main?.clientWidth || 720) - 64));
    const H = 220, padL = 40, padB = 26, padT = 10, padR = 8;
    // Each bar: screen time that wasn't adult mode, with adult mode stacked on
    // top in its own colour. used_seconds holds only the adult time that
    // counted, so the rest is added on. A day is over its limit by
    // used_seconds alone: adult time that didn't count can't reach it.
    const adult = days.map((d) => minutes(d.adult_seconds || 0));
    const values = days.map((d) => minutes(d.used_seconds));
    const own = days.map((d) => minutes(Math.max(0, d.used_seconds - (d.adult_counted_seconds || 0))));
    const totals = own.map((v, i) => v + adult[i]);
    const limits = days.map((d) => (d.budget_minutes ? d.budget_minutes + d.extension_minutes : 0));
    const max = Math.max(60, ...totals, ...limits);
    const step = max > 300 ? 120 : max > 120 ? 60 : 30;
    const top = Math.ceil(max / step) * step;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const bw = plotW / days.length;
    const y = (v) => padT + plotH - (v / top) * plotH;

    let grid = "";
    for (let v = 0; v <= top; v += step) {
      grid += `<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}"/><text x="${padL - 6}" y="${y(v) + 4}" text-anchor="end">${v >= 60 ? `${v / 60}h` : `${v}m`}</text>`;
    }
    const bars = days
      .map((d, i) => {
        const over = limits[i] && values[i] >= limits[i];
        const h = Math.max(own[i] ? 2 : 0, (own[i] / top) * plotH);
        const ah = adult[i] ? Math.max(2, (adult[i] / top) * plotH) : 0;
        const x = padL + i * bw + bw * 0.15;
        const title = `${d.date}: ${duration(d.used_seconds)}${limits[i] ? ` of ${duration(limits[i] * 60)}` : ""}${d.adult_seconds ? `, adult mode ${duration(d.adult_seconds)}${d.adult_counted_seconds ? "" : " (not counted)"}` : ""}`;
        return `<rect class="day-bar${over ? " over" : ""}" x="${x}" y="${padT + plotH - h}" width="${bw * 0.7}" height="${h}" rx="2"><title>${esc(title)}</title></rect>`
          + (ah ? `<rect class="day-bar adult" x="${x}" y="${padT + plotH - h - ah}" width="${bw * 0.7}" height="${ah}" rx="2"><title>${esc(title)}</title></rect>` : "");
      })
      .join("");
    let limitPath = "";
    days.forEach((d, i) => {
      if (!limits[i]) return;
      const x1 = padL + i * bw, x2 = x1 + bw, yy = y(limits[i]);
      limitPath += `M${x1} ${yy}H${x2}`;
    });
    const labelWidth = days.length <= 7 ? 36 : 56;
    const every = Math.max(1, Math.ceil(days.length / Math.max(4, Math.floor(plotW / labelWidth))));
    const labels = days
      .map((d, i) => {
        if ((days.length - 1 - i) % every) return "";
        const date = new Date(`${d.date}T12:00:00`);
        const text = days.length <= 7 ? date.toLocaleDateString([], { weekday: "short" }) : date.toLocaleDateString([], { month: "short", day: "numeric" });
        return `<text x="${padL + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${text}</text>`;
      })
      .join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Screen time per day">${grid}${bars}<path class="limit" d="${limitPath}"/>${labels}</svg>`;
  }

  // Limits ---------------------------------------------------------------------------

  // Whole home: one set of limits for every TV, above the TV's own.
  _renderHomeLimits() {
    const e = this._homeEdit;
    if (!e) {
      if (!this._loadingHome) {
        this._loadingHome = true;
        this._ws({ type: "tv_mgmt/home/get" })
          .then((res) => (this._homeEdit = res))
          .catch((err) => (this._error = this._errorText(err)))
          .finally(() => {
            this._loadingHome = false;
            this._render();
          });
      }
      return loading("Loading the whole home's limits…");
    }
    const h = e.home;
    const hours = h.weekly_minutes ? +(h.weekly_minutes / 60).toFixed(2) : 0;
    const radio = (name, value, label, checked) =>
      `<label class="radio"><input type="radio" name="${name}" value="${value}" ${checked ? "checked" : ""}> ${label}</label>`;
    const targets = e.notify_targets.length
      ? `<div class="checks">${e.notify_targets
          .map((t) => `<label class="radio"><input type="checkbox" class="check" data-home-notify="${esc(t)}" ${h.notify.includes(t) ? "checked" : ""}> ${esc(t.replace(/^notify\./, "").replace(/_/g, " "))}</label>`)
          .join("")}</div>`
      : `<small>Nothing to send to yet. Set up the Home Assistant app on a phone, or another notify integration.</small>`;
    const tvs = e.tvs
      .map((t) => `<label class="radio"><input type="checkbox" class="check" data-home-tv="${esc(t.entry_id)}" ${h.excluded.includes(t.entry_id) ? "" : "checked"}> ${esc(t.name)}</label>`)
      .join("");
    return `<section class="card home-limits" style="margin-bottom:20px"><div class="card-body">
      <form id="home" novalidate>
        <div class="field"><label>Whole home</label>
          <small>Limits for all the TVs together. Each TV's time is added up, so two TVs on for an hour count as two hours. The TVs' own limits still apply too.</small></div>
        <div class="field">
          <label for="home-daily">Daily TV time for the home</label>
          <span><input id="home-daily" type="number" min="0" max="1440" step="5" value="${h.daily_minutes}"> <span class="muted">minutes</span></span>
          <small>0 means no daily limit.</small>
        </div>
        <div class="field">
          <label for="home-weekly">Weekly TV time for the home</label>
          <span><input id="home-weekly" type="number" min="0" max="168" step="0.5" value="${hours}"> <span class="muted">hours</span></span>
          <small>0 means no weekly limit.</small>
          <div class="row">${radio("home-week", "monday", "Weeks start on Monday", h.week_start === "monday")}${radio("home-week", "sunday", "Sunday", h.week_start === "sunday")}</div>
        </div>
        <div class="field">
          <label for="home-warn">Tell me before it runs out</label>
          <span><input id="home-warn" type="number" min="0" max="120" value="${h.warn_minutes}"> <span class="muted">minutes before</span></span>
        </div>
        <div class="field"><label>When the home's time runs out</label>
          ${radio("home-action", "turn_off", "Turn the TVs off, like their own limits", h.action === "turn_off")}
          ${radio("home-action", "notify", "Only tell me", h.action === "notify")}
        </div>
        <div class="field"><label>Tell</label>${targets}</div>
        <div class="field"><label>TVs that count</label>
          ${tvs || `<small>No TVs yet.</small>`}
          <small>A TV that's left out isn't counted or turned off by the home's limit.</small></div>
        <div class="form-actions">
          <button type="submit" class="btn primary" ${this._busyHome ? "disabled" : ""}>${this._busyHome ? "Saving…" : "Save whole home"}</button>
          ${this._homeNote ? `<span class="saved" role="status">${esc(this._homeNote)}</span>` : ""}
        </div>
      </form>
    </div></section>`;
  }

  async _saveHome() {
    const root = this.shadowRoot;
    const num = (id) => Number(root.getElementById(id).value || 0);
    const daily = num("home-daily");
    const weeklyHours = num("home-weekly");
    const warn = num("home-warn");
    if (!Number.isInteger(daily) || daily < 0 || daily > 1440) return this._showError("Daily TV time for the home needs a whole number of minutes from 0 to 1440.");
    if (weeklyHours < 0 || weeklyHours > 168) return this._showError("Weekly TV time for the home needs a number of hours from 0 to 168.");
    if (!Number.isInteger(warn) || warn < 0 || warn > 120) return this._showError("Tell me before it runs out needs a whole number of minutes from 0 to 120.");
    const msg = {
      type: "tv_mgmt/home/set",
      daily_minutes: daily,
      weekly_minutes: Math.round(weeklyHours * 60),
      week_start: root.querySelector('input[name="home-week"]:checked')?.value || "monday",
      warn_minutes: warn,
      action: root.querySelector('input[name="home-action"]:checked')?.value || "turn_off",
      notify: [...root.querySelectorAll("[data-home-notify]")].filter((el) => el.checked).map((el) => el.dataset.homeNotify),
      excluded: [...root.querySelectorAll("[data-home-tv]")].filter((el) => !el.checked).map((el) => el.dataset.homeTv),
    };
    this._busyHome = true;
    this._error = null;
    this._render();
    try {
      this._homeEdit = await this._ws(msg);
      this._home = this._homeEdit.home;
      this._homeNote = "Whole home saved";
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busyHome = false;
    this._render();
  }

  _renderLimits() {
    const d = this._limitsDraft;
    if (!d || this._limitsFor !== this._selected) {
      if (!this._loadingLimits) {
        this._loadingLimits = true;
        this._limitsFor = this._selected;
        this._loadLimits().finally(() => (this._loadingLimits = false));
      }
      return `${this._renderHomeLimits()}<div class="toolbar">${this._renderPicker()}</div>${loading("Loading limits…")}`;
    }
    const windows = d.windows
      .map(
        (w, i) => `<div class="window">
          <input type="time" aria-label="Starts" data-win="${i}" data-key="start" value="${esc(w.start)}">
          <input type="time" aria-label="Ends" data-win="${i}" data-key="end" value="${esc(w.end)}">
          <input type="text" aria-label="Name" placeholder="Name, e.g. Bedtime" data-win="${i}" data-key="label" value="${esc(w.label)}">
          <button type="button" class="btn" data-remove-window="${i}" aria-label="Remove this quiet window">${icon("delete")}</button>
        </div>`,
      )
      .join("");
    return `${this._renderHomeLimits()}<div class="toolbar">${this._renderPicker()}</div>
      <section class="card"><div class="card-body">
        <form id="limits" novalidate>
          <div class="field">
            <label for="daily_budget">Daily screen time</label>
            <span><input id="daily_budget" type="number" min="0" max="1440" step="5" value="${d.daily_budget}"> <span class="muted">minutes</span></span>
            <small>0 means no daily limit. When the time runs out, the TV is turned off until tomorrow.</small>
          </div>
          <div class="field">
            <label for="warn_minutes">Warn before time runs out</label>
            <span><input id="warn_minutes" type="number" min="0" max="60" value="${d.warn_minutes}"> <span class="muted">minutes</span></span>
            <small>Fires the tv_mgmt_warning event, for a notification or spoken heads-up.</small>
          </div>
          <div class="field">
            <label>Quiet windows</label>
            <small>Times the TV stays off, like bedtime or school hours. A window can run past midnight.</small>
            <div class="windows">${windows || `<p class="muted">No quiet windows.</p>`}</div>
            <span><button type="button" class="btn" data-add-window>${icon("plus")} Add quiet window</button></span>
          </div>
          <div class="field">
            <label for="adult_mode_duration">Adult mode lasts</label>
            <span><input id="adult_mode_duration" type="number" min="5" max="720" step="5" value="${d.adult_mode_duration}"> <span class="muted">minutes</span></span>
          </div>
          <div class="field">
            <label class="toggle"><span>Count adult mode time toward limits<small>When off, time in adult mode doesn't use up this TV's or the home's time, or any app's. Activity and Analytics show it as its own total.</small></span>
              <span class="switch"><input type="checkbox" id="adult_mode_counts" ${d.adult_mode_counts ? "checked" : ""}><span></span></span></label>
          </div>
          <div class="form-actions">
            <button type="submit" class="btn primary" ${this._busy ? "disabled" : ""}>${this._busy ? "Saving…" : "Save limits"}</button>
            ${this._savedNote ? `<span class="saved" role="status">${esc(this._savedNote)}</span>` : ""}
          </div>
        </form>
      </div></section>
      ${this._renderAppRules()}
      ${this._renderNames()}`;
  }

  _renderAppRules() {
    const p = this._profiles.find((x) => x.entry_id === this._selected);
    const a = p?.apple_tv;
    if (!a) return "";
    if (!this._appsDraft || this._appsFor !== p.entry_id) {
      this._appsFor = p.entry_id;
      this._appsDraft = {
        mode: a.rules.mode,
        apps: [...a.rules.apps],
        // The other mode's checklist starts empty: switching never turns a
        // blocked app into an allowed one.
        byMode: { [a.rules.mode]: [...a.rules.apps] },
        limits: { ...a.rules.limits },
        action: a.rules.action,
        sleep: a.rules.sleep_on_block,
        wake: a.rules.wake_with_tv !== false,
        shows: [...(a.rules.blocked_shows || [])],
        newShow: "",
      };
    }
    const d = this._appsDraft;
    const fold = (x) => String(x).toLowerCase();
    // One row per app: what the Apple TV has opened or lists, plus anything in the rules.
    const rows = new Map();
    for (const [key, name] of Object.entries(a.known_apps || {})) rows.set(key, name);
    for (const key of [...d.apps, ...Object.keys(d.limits)]) {
      const exists = [...rows.entries()].some(([k, n]) => fold(k) === fold(key) || fold(n) === fold(key));
      if (!exists) rows.set(key, key);
    }
    const listed = (key, name) => d.apps.some((x) => fold(x) === fold(key) || fold(x) === fold(name));
    const limitOf = (key, name) => {
      const hit = Object.entries(d.limits).find(([k]) => fold(k) === fold(key) || fold(k) === fold(name));
      return hit ? hit[1] : "";
    };
    const sorted = [...rows.entries()].sort((x, y) => x[1].localeCompare(y[1]));
    const appRows = sorted
      .map(([key, name], i) => `<div class="app-rule">
          <input type="checkbox" class="check" id="app-${i}" data-app-check="${esc(key)}" ${listed(key, name) ? "checked" : ""}>
          <label for="app-${i}">${esc(name)}${name !== key ? `<span class="raw">${esc(key)}</span>` : ""}</label>
          <input type="number" min="0" max="1440" step="5" aria-label="Daily minutes for ${esc(name)}" placeholder="No limit" data-app-limit="${esc(key)}" value="${esc(limitOf(key, name))}">
        </div>`)
      .join("");
    const checkLabel = d.mode === "allow" ? "Allowed" : "Blocked";
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <form id="apps" novalidate>
        <div class="field">
          <label>Apple TV apps</label>
          <small>Rules for apps on the Apple TV plugged into this TV. They pause in adult mode and when the mode is Paused.</small>
          <div class="seg wide" role="group" aria-label="How the app list is used">
            <button type="button" data-appmode="block" aria-pressed="${d.mode === "block"}">Block checked apps</button>
            <button type="button" data-appmode="allow" aria-pressed="${d.mode === "allow"}">Allow only checked apps</button>
          </div>
        </div>
        <div class="field">
          ${appRows
            ? `<div class="app-rules-head"><span>${checkLabel}</span><span>App</span><span>Daily limit (min)</span></div><div class="app-rules">${appRows}</div>`
            : `<p class="muted">Open a few apps on the Apple TV and they'll appear here.</p>`}
        </div>
        ${this._renderShowRules(a, d)}
        <div class="field">
          <label>When an app isn't allowed</label>
          <label class="radio"><input type="radio" name="app_action" value="home" data-app-action ${d.action === "home" ? "checked" : ""}> Go back to the Apple TV home screen</label>
          <label class="radio"><input type="radio" name="app_action" value="sleep" data-app-action ${d.action === "sleep" ? "checked" : ""}> Put the Apple TV to sleep</label>
          ${a.has_remote ? "" : `<small>No Apple TV remote entity was found, so going home falls back to sleep.</small>`}
        </div>
        <label class="toggle"><span>Wake the Apple TV when the TV turns on<small>Not while the TV is blocked, or in monitor-only, paused or adult mode.</small></span>
          <span class="switch"><input type="checkbox" data-app-wake ${d.wake ? "checked" : ""}><span></span></span></label>
        <label class="toggle"><span>Sleep the Apple TV when the TV is blocked<small>When the daily limit runs out, a quiet window starts, or you block the TV.</small></span>
          <span class="switch"><input type="checkbox" data-app-sleep ${d.sleep ? "checked" : ""}><span></span></span></label>
        <div class="form-actions">
          <button type="submit" class="btn primary" ${this._busyApps ? "disabled" : ""}>${this._busyApps ? "Saving…" : "Save app rules"}</button>
          ${this._appsNote ? `<span class="saved" role="status">${esc(this._appsNote)}</span>` : ""}
        </div>
      </form>
    </div></section>`;
  }

  _renderShowRules(a, d) {
    const fold = (x) => String(x).trim().toLowerCase();
    const blocked = new Set(d.shows.map(fold));
    const names = [...new Map([...d.shows, ...(a.known_shows || [])].map((n) => [fold(n), n])).values()];
    const rows = names
      .map((name, i) => `<label class="show-rule"><input type="checkbox" class="check" data-show-check="${esc(name)}" ${blocked.has(fold(name)) ? "checked" : ""}> ${esc(name)}</label>`)
      .join("");
    return `<div class="field">
        <label for="new-show">Blocked shows and movies</label>
        <small>Stopped in any app, like a blocked app. Check one that's been watched, or type a name exactly as the Apple TV shows it. "Cars" doesn't block "Cars 2".</small>
        ${rows ? `<div class="show-rules">${rows}</div>` : `<p class="muted">Shows and movies watched on the Apple TV appear here.</p>`}
        <div class="message-row">
          <input id="new-show" type="text" data-new-show placeholder="Add a show or movie" value="${esc(d.newShow)}" autocomplete="off">
          <button type="button" class="btn" data-add-show>Add</button>
        </div>
      </div>`;
  }

  async _saveApps() {
    const d = this._appsDraft;
    const limits = {};
    for (const [key, value] of Object.entries(d.limits)) {
      const n = Number(value);
      if (value === "" || value === null) continue;
      if (!Number.isInteger(n) || n < 0 || n > 1440) {
        this._error = `Daily limits per app need a whole number of minutes from 0 to 1440.`;
        this._render();
        return;
      }
      if (n > 0) limits[key] = n;
    }
    this._busyApps = true;
    this._error = null;
    this._render();
    try {
      await this._ws({
        type: "tv_mgmt/apple_tv/set", entry_id: this._selected,
        mode: d.mode, apps: d.apps, limits, action: d.action, sleep_on_block: d.sleep, wake_with_tv: d.wake, blocked_shows: d.shows,
      });
      this._appsNote = "App rules saved";
      this._appsDraft = null;
      await this._loadProfiles({ quiet: true });
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busyApps = false;
    this._render();
  }

  // Lights that go on and off with the TV.
  _renderLights() {
    const p = this._profiles.find((x) => x.entry_id === this._selected);
    if (!p) return "";
    if (!this._lightsDraft || this._lightsFor !== p.entry_id) {
      this._lightsFor = p.entry_id;
      this._lightsDraft = { picked: new Set(p.tv_lights || []), filter: "" };
    }
    const d = this._lightsDraft;
    const states = this._hass?.states || {};
    const nameOfLight = (eid) => states[eid]?.attributes?.friendly_name || eid;
    const all = [...new Set([...Object.keys(states).filter((e) => e.startsWith("light.")), ...d.picked])]
      .sort((a, b) => Number(d.picked.has(b)) - Number(d.picked.has(a)) || nameOfLight(a).localeCompare(nameOfLight(b)));
    const q = d.filter.trim().toLowerCase();
    const shown = q ? all.filter((e) => d.picked.has(e) || nameOfLight(e).toLowerCase().includes(q) || e.includes(q)) : all;
    const rows = shown
      .map((eid, i) => `<label class="show-rule"><input type="checkbox" class="check" data-light="${esc(eid)}" ${d.picked.has(eid) ? "checked" : ""}> ${esc(nameOfLight(eid))}</label>`)
      .join("");
    // Below the picker: turn them off now, and whether they follow the TV.
    // Both apply at once (no Save), and only once lights are saved for this TV.
    const saved = p.tv_lights || [];
    const busy = this._busyLightsNow;
    const now = saved.length ? `<div class="tv-lights-now">
        <label class="toggle"><span>Turn on and off with the TV<small>Off: TV Mgmt leaves these lights alone.</small></span>
          <span class="switch"><input type="checkbox" id="tv_lights_follow" data-lights-follow ${p.tv_lights_follow !== false ? "checked" : ""} ${busy ? "disabled" : ""}><span></span></span></label>
        <div class="form-actions">
          <button type="button" class="btn" data-lights-off ${busy ? "disabled" : ""}>${busy === "off" ? "Turning off…" : "Turn off now"}</button>
          ${this._lightsNowNote ? `<span class="saved" role="status">${esc(this._lightsNowNote)}</span>` : ""}
        </div>
        <small class="muted">${p.tv_lights_held_off ? "Turned off. They stay off until the TV next turns on or off." : "Turned off here, they stay off until the TV next turns on or off, even during light sync."}</small>
      </div>` : "";
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <form id="lights" novalidate>
        <div class="field">
          <label>TV lights</label>
          <small>Lights that turn on when the TV turns on, and off when it turns off, such as a light behind the TV. They don't turn on while the TV is blocked.</small>
          ${all.length > 10 ? `<input type="text" data-light-filter placeholder="Find a light" value="${esc(d.filter)}" autocomplete="off">` : ""}
          ${rows ? `<div class="show-rules">${rows}</div>` : `<p class="muted">${all.length ? "No lights match." : "No lights in Home Assistant yet."}</p>`}
        </div>
        <div class="form-actions">
          <button type="submit" class="btn primary" ${this._busyLights ? "disabled" : ""}>${this._busyLights ? "Saving…" : "Save TV lights"}</button>
          ${this._lightsNote ? `<span class="saved" role="status">${esc(this._lightsNote)}</span>` : ""}
        </div>
      </form>
      ${now}
    </div></section>`;
  }

  // Turn a TV's lights off (Lights tab and dashboard): { ok, text } to show.
  async _tvLightsOff(entryId) {
    try {
      const res = await this._ws({ type: "tv_mgmt/lights/off", entry_id: entryId });
      const failed = Object.keys(res.failed || {});
      const states = this._hass?.states || {};
      if (failed.length) return { ok: false, text: `Didn't turn off: ${failed.map((e) => states[e]?.attributes?.friendly_name || e).join(", ")}` };
      return { ok: true, text: "TV lights off" };
    } catch (err) {
      return { ok: false, text: this._errorText(err) };
    }
  }

  async _turnOffLights() {
    this._busyLightsNow = "off";
    this._lightsNowNote = "";
    this._error = null;
    this._render();
    const out = await this._tvLightsOff(this._selected);
    if (out.ok) this._lightsNowNote = out.text;
    else this._error = out.text;
    await this._loadProfiles({ quiet: true });
    this._busyLightsNow = false;
    this._render();
  }

  async _dashboardLightsOff(entryId) {
    this._tvLightsBusy = this._tvLightsBusy || new Set();
    this._tvLightsNote = this._tvLightsNote || {};
    this._tvLightsBusy.add(entryId);
    delete this._tvLightsNote[entryId];
    this._render();
    const out = await this._tvLightsOff(entryId);
    this._tvLightsBusy.delete(entryId);
    this._tvLightsNote[entryId] = { text: out.text, bad: !out.ok };
    clearTimeout(this._tvLightsNoteTimer?.[entryId]);
    if (out.ok) {
      // The "TV lights off" note goes after a few seconds; a failure stays.
      this._tvLightsNoteTimer = this._tvLightsNoteTimer || {};
      this._tvLightsNoteTimer[entryId] = setTimeout(() => {
        delete this._tvLightsNote[entryId];
        if (!this._typing()) this._render();
      }, 6000);
    }
    await this._loadProfiles({ quiet: true });
    this._render();
  }

  async _setLightsFollow(follow) {
    this._busyLightsNow = "follow";
    this._lightsNowNote = "";
    this._error = null;
    this._render();
    try {
      await this._ws({ type: "tv_mgmt/lights/set", entry_id: this._selected, follow });
      this._lightsNowNote = follow ? "They follow the TV" : "TV Mgmt leaves them alone";
      await this._loadProfiles({ quiet: true });
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busyLightsNow = false;
    this._render();
  }

  async _saveLights() {
    const d = this._lightsDraft;
    this._busyLights = true;
    this._error = null;
    this._render();
    try {
      await this._ws({ type: "tv_mgmt/lights/set", entry_id: this._selected, lights: [...d.picked] });
      this._lightsNote = "TV lights saved";
      this._lightsDraft = null;
      await this._loadProfiles({ quiet: true });
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busyLights = false;
    this._render();
  }

  _renderNames() {
    const p = this._profiles.find((x) => x.entry_id === this._selected);
    if (!p) return "";
    if (!this._namesDraft || this._namesFor !== p.entry_id) {
      this._namesFor = p.entry_id;
      this._namesDraft = { ...(p.custom_input_names || {}) };
    }
    const raws = [...new Set([...Object.keys(p.input_names || {}), ...Object.keys(this._namesDraft)])];
    const rows = raws
      .map((raw, i) => {
        const fallback = p.input_names?.[raw] && !(raw in (p.custom_input_names || {})) ? p.input_names[raw] : raw;
        return `<div class="name-row">
          <label for="name-${i}"><span class="raw">${esc(raw)}</span></label>
          <input id="name-${i}" type="text" data-name-for="${esc(raw)}" value="${esc(this._namesDraft[raw] ?? "")}" placeholder="${esc(fallback)}">
        </div>`;
      })
      .join("");
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <form id="names" novalidate>
        <div class="field">
          <label>Input names</label>
          <small>Give an input a name you'll recognise, like Apple TV for com.tcl.tv. TV Mgmt shows the name everywhere, and still matches on what the TV reports. Leave a box empty to use the value shown in grey.</small>
        </div>
        ${rows ? `<div class="names">${rows}</div>` : `<p class="muted">Turn the TV on and switch between inputs, and they'll appear here to name.</p>`}
        <div class="form-actions">
          <button type="submit" class="btn primary" ${this._busyNames ? "disabled" : ""}>${this._busyNames ? "Saving…" : "Save names"}</button>
          ${this._namesNote ? `<span class="saved" role="status">${esc(this._namesNote)}</span>` : ""}
        </div>
      </form>
    </div></section>`;
  }

  // ---- Lights tab -----------------------------------------------------------------------

  async _loadLightSync({ quiet = false } = {}) {
    try {
      this._ls = await this._ws({ type: "tv_mgmt/light_sync/get" });
      if (!quiet) this._error = null;
    } catch (err) {
      if (!quiet) this._error = this._errorText(err);
      if (!this._ls) this._ls = { failed: true };
    }
    if (this._tab !== "lights") return;
    // Don't redraw a zone you're changing, or a box you're typing in.
    if (quiet && this._zoneEdit) return;
    if (quiet && this._typing()) {
      this._renderLater = true;
      return;
    }
    this._render();
  }

  _lsTv() {
    return this._ls?.tvs?.find((t) => t.entry_id === this._selected) || null;
  }

  _renderLightsTab() {
    if (!this._ls) {
      if (!this._loadingLs) {
        this._loadingLs = true;
        this._loadLightSync().finally(() => (this._loadingLs = false));
      }
      return `<div class="toolbar">${this._renderPicker()}</div>${loading("Loading lights…")}`;
    }
    if (this._ls.failed) return `<div class="toolbar">${this._renderPicker()}</div>`;
    return `<div class="toolbar">${this._renderPicker()}</div>
      ${this._renderLights()}
      ${this._renderZones()}
      ${this._renderLightSettings()}
      ${this._renderTitleColors()}`;
  }

  // A checklist of entities from one domain, with a search box when the list is long.
  _entityChecklist({ domain, picked, filter, attr, filterAttr, exclude = [], empty }) {
    const states = this._hass?.states || {};
    const nameOf = (eid) => states[eid]?.attributes?.friendly_name || eid;
    const all = [...new Set([...Object.keys(states).filter((e) => e.startsWith(`${domain}.`) && !exclude.includes(e)), ...picked])]
      .sort((a, b) => Number(picked.includes(b)) - Number(picked.includes(a)) || nameOf(a).localeCompare(nameOf(b)));
    const q = (filter || "").trim().toLowerCase();
    const shown = q ? all.filter((e) => picked.includes(e) || nameOf(e).toLowerCase().includes(q) || e.includes(q)) : all;
    const rows = shown
      .map((eid) => `<label class="show-rule"><input type="checkbox" class="check" ${attr}="${esc(eid)}" ${picked.includes(eid) ? "checked" : ""}> ${esc(nameOf(eid))}</label>`)
      .join("");
    return `${all.length > 10 ? `<input type="text" ${filterAttr} placeholder="Search" value="${esc(filter || "")}" autocomplete="off">` : ""}
      ${rows ? `<div class="show-rules">${rows}</div>` : `<p class="muted">${all.length ? "Nothing matches." : esc(empty)}</p>`}`;
  }

  _renderZones() {
    const tv = this._lsTv();
    if (!tv) return "";
    const zones = tv.zones || [];
    const editingNew = this._zoneEdit && this._zoneEdit.entry_id === tv.entry_id && !this._zoneEdit.cfg.id;
    const list = zones
      .map((z) => (this._zoneEdit?.entry_id === tv.entry_id && this._zoneEdit.cfg.id === z.config.id ? this._renderZoneForm(tv) : this._renderZone(tv, z)))
      .join("");
    const canAdd = zones.length < (this._ls.max_zones || 8);
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <div class="field">
        <label>Light sync</label>
        <small>Color lights to match what's playing on this TV, from the artwork, a color you saved for the title, or the genre. When playback stops, the lights go back to how they were. Nothing is colored while the TV is off or blocked, and if a blocked app is stopped the lights go back straight away.</small>
      </div>
      ${this._zoneError ? `<div class="error" role="alert">${esc(this._zoneError)}</div>` : ""}
      ${list || (editingNew ? "" : `<p class="muted">No light zones yet. A zone is a group of lights that change together, like a strip behind the TV.</p>`)}
      ${editingNew ? this._renderZoneForm(tv) : ""}
      ${!this._zoneEdit && canAdd ? `<span><button type="button" class="btn" data-zone-add>${icon("plus")} Add a light zone</button></span>` : ""}
      ${!canAdd ? `<small>A TV can have up to ${esc(this._ls.max_zones)} light zones.</small>` : ""}
      ${this._zoneNote ? `<span class="saved" role="status">${esc(this._zoneNote)}</span>` : ""}
    </div></section>`;
  }

  _renderZone(tv, z) {
    const c = z.config;
    const s = z.status;
    const states = this._hass?.states || {};
    const nameOf = (eid) => states[eid]?.attributes?.friendly_name || eid;
    let chip;
    if (!s) chip = `<span class="chip tone-muted">Starting</span>`;
    else if (!c.enabled) chip = `<span class="chip tone-muted">Off</span>`;
    else if (s.syncing) chip = `<span class="chip tone-good">Coloring</span>`;
    else if (s.held) chip = `<span class="chip tone-warn">${esc(LS_HELD[s.held] || s.held)}</span>`;
    else chip = `<span class="chip tone-muted">Waiting for something to play</span>`;
    const colors = s?.syncing ? (s.palette?.length > 1 ? s.palette : s.color ? [s.color] : []) : [];
    const swatches = colors.map((hex) => `<span class="swatch" style="background:${esc(hex)}" title="${esc(hex)}"></span>`).join("");
    const now = s?.syncing && s.title
      ? `<div class="zone-now"><span class="swatches">${swatches}</span><span>${esc(s.title)} <span class="muted">· ${esc(LS_SOURCES[s.source] || s.source)}</span></span></div>`
      : "";
    const failed = s && Object.keys(s.failed_lights || {}).length
      ? `<small class="tone-bad-text">Couldn't reach: ${esc(Object.keys(s.failed_lights).map(nameOf).join(", "))}</small>`
      : "";
    const err = s?.last_error ? `<small class="tone-bad-text">${esc(s.last_error)}</small>` : "";
    const players = [
      c.use_tv_player && tv.tv_player ? "the TV" : null,
      c.use_apple_tv && tv.apple_tv ? "the Apple TV" : null,
      ...(c.extra_players || []).map(nameOf),
    ].filter(Boolean);
    const id = esc(c.id);
    return `<div class="zone">
      <div class="zone-head">
        <div class="zone-title"><strong>${esc(c.name)}</strong>${chip}</div>
        <label class="switch" title="Light sync on or off"><input type="checkbox" data-zone-enable="${id}" aria-label="Light sync for ${esc(c.name)}" ${c.enabled ? "checked" : ""}><span></span></label>
      </div>
      ${now}
      <small class="muted">${esc((c.lights || []).map(nameOf).join(", "))}${players.length ? ` · follows ${esc(players.join(", "))}` : " · not following any player yet"}</small>
      ${failed}${err}
      <div class="zone-actions">
        <button type="button" class="btn" data-zone-act="resync" data-zone="${id}">Look again</button>
        ${s?.save_title ? `<button type="button" class="btn" data-zone-act="save_title_color" data-zone="${id}">Keep this color for ${esc(s.save_title)}</button>` : ""}
        <button type="button" class="btn" data-zone-edit="${id}">Change</button>
        <button type="button" class="btn danger" data-zone-remove="${id}" aria-label="Remove ${esc(c.name)}">${icon("delete")}</button>
      </div>
      ${this._zoneConfirm === c.id ? `<div class="zone-confirm" role="group" aria-label="Remove ${esc(c.name)}">
        <span>Remove ${esc(c.name)}? Its lights go back to how they were.</span>
        <span class="zone-actions"><button type="button" class="btn danger" data-zone-remove-yes="${id}">Remove</button>
        <button type="button" class="btn" data-zone-remove-no>Keep it</button></span>
      </div>` : ""}
    </div>`;
  }

  _startZoneEdit(tv, zoneId) {
    const source = zoneId ? tv.zones.find((z) => z.config.id === zoneId)?.config : { ...this._ls.defaults, name: "" };
    if (!source) return;
    this._zoneEdit = { entry_id: tv.entry_id, cfg: JSON.parse(JSON.stringify(source)), lightFilter: "", playerFilter: "" };
    if (!zoneId) delete this._zoneEdit.cfg.id;
    this._zoneNote = "";
    this._zoneError = "";
    this._zoneConfirm = null;
    this._error = null;
    this._render();
  }

  _renderZoneForm(tv) {
    const e = this._zoneEdit;
    const c = e.cfg;
    const ch = this._ls.choices;
    const opts = (values, labels, current) =>
      values.map((v) => `<option value="${esc(v)}" ${String(current) === String(v) ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
    const toggle = (key, label, hint = "") =>
      `<label class="toggle"><span>${label}${hint ? `<small>${hint}</small>` : ""}</span>
        <span class="switch"><input type="checkbox" data-zf="${key}" data-ztype="bool" ${c[key] ? "checked" : ""}><span></span></span></label>`;
    const number = (key, label, min, max, step, unit, hint = "") =>
      `<div class="field"><label for="zf-${key}">${label}</label>
        <span><input id="zf-${key}" type="number" data-zf="${key}" min="${min}" max="${max}" step="${step}" value="${esc(c[key])}"> ${unit ? `<span class="muted">${unit}</span>` : ""}</span>
        ${hint ? `<small>${hint}</small>` : ""}</div>`;
    const select = (key, label, values, labels, hint = "") =>
      `<div class="field"><label for="zf-${key}">${label}</label>
        <select id="zf-${key}" data-zf="${key}">${opts(values, labels, c[key])}</select>
        ${hint ? `<small>${hint}</small>` : ""}</div>`;

    const lights = c.lights || [];
    const order = c.color_mode === "palette" && lights.length > 1
      ? `<div class="order">${lights
          .map((eid, i) => `<span class="order-item"><span>${i + 1}. ${esc(this._hass?.states?.[eid]?.attributes?.friendly_name || eid)}</span>${i ? `<button type="button" class="chip-btn" data-zlight-up="${esc(eid)}" aria-label="Move up">↑</button>` : ""}</span>`)
          .join("")}</div><small>Palette colors go to the lights in this order.</small>`
      : "";
    const sources = [...c.color_sources, ...ch.color_sources.filter((x) => !c.color_sources.includes(x))];
    const preset = Object.entries(ch.presets).find(([, list]) => list.join() === c.color_sources.join())?.[0] || "custom";
    const sourceRows = sources
      .map((src) => {
        const on = c.color_sources.includes(src);
        const idx = c.color_sources.indexOf(src);
        return `<span class="order-item"><label class="show-rule"><input type="checkbox" class="check" data-zsrc="${esc(src)}" ${on ? "checked" : ""}> ${esc(LS_SOURCES[src] || src)}</label>${on && idx > 0 ? `<button type="button" class="chip-btn" data-zsrc-up="${esc(src)}" aria-label="Try ${esc(LS_SOURCES[src] || src)} sooner">↑</button>` : ""}</span>`;
      })
      .join("");
    const media = ch.media_types
      .map((m) => `<label class="show-rule"><input type="checkbox" class="check" data-zmedia="${esc(m)}" ${(c.media_types || []).includes(m) ? "checked" : ""}> ${esc(LS_MEDIA[m] || m)}</label>`)
      .join("");
    const defaultHex = rgbHex(c.default_color);
    const isNew = !c.id;
    return `<form id="zone-form" class="zone editing" novalidate>
      <div class="field"><label for="zf-name">Name</label>
        <input id="zf-name" type="text" data-zf="name" maxlength="60" placeholder="${esc(this._ls.defaults.name)}" value="${esc(c.name)}"></div>
      <div class="field"><label>Lights</label>
        <small>Lights that can't show color just follow the brightness.</small>
        ${this._entityChecklist({ domain: "light", picked: lights, filter: e.lightFilter, attr: "data-zlight", filterAttr: "data-zlight-filter", empty: "No lights in Home Assistant yet." })}
        ${order}
      </div>
      <div class="field"><label>What to follow</label>
        <small>The lights color for whichever of these is playing.</small></div>
      ${tv.tv_player ? toggle("use_tv_player", "This TV", "What the TV itself is playing.") : ""}
      ${tv.apple_tv ? toggle("use_apple_tv", "The Apple TV", "The Apple TV plugged into this TV.") : ""}
      <div class="field"><label>Other players</label>
        <small>Add a player that shows what's on, like a Music Assistant or Plex player.</small>
        ${this._entityChecklist({ domain: "media_player", picked: c.extra_players || [], filter: e.playerFilter, attr: "data-zplayer", filterAttr: "data-zplayer-filter", exclude: [tv.tv_player, tv.apple_tv].filter(Boolean), empty: "No other media players." })}
      </div>
      ${toggle("require_tv_on", "Only while the TV is on", "Turn off to color the lights for another player even when the TV is off.")}
      ${number("brightness_pct", "Brightness", 1, 100, 1, "%")}
      ${number("transition", "Fade", 0, 30, 0.5, "seconds")}
      <div class="field"><label for="zf-preset">Where colors come from</label>
        <select id="zf-preset" data-zpreset>${opts([...Object.keys(ch.presets), ...(preset === "custom" ? ["custom"] : [])], LS_PRESETS, preset)}</select>
        <div class="order">${sourceRows}</div>
        <small>Tried in this order. The default color is used if nothing else works.</small></div>
      <div class="field"><label for="zf-default">Default color</label>
        <span><input id="zf-default" type="color" data-zf="default_color" data-ztype="rgb" value="${esc(defaultHex)}"></span></div>
      ${select("color_mode", "Colors", ch.color_modes, LS_COLOR_MODES)}
      ${c.color_mode === "palette" ? `${select("palette_size", "Colors in the palette", ch.palette_sizes, {})}${select("palette_strategy", "How colors are spread", ch.palette_strategies, LS_STRATEGIES)}` : ""}
      ${select("pause_behavior", "When playback pauses", ch.idle_behaviors, LS_IDLE)}
      ${select("stop_behavior", "When playback stops", ch.idle_behaviors, LS_IDLE)}
      ${number("restore_delay", "Wait before putting the lights back", 0, 300, 1, "seconds", "Gives you a moment to start the next episode.")}
      <div class="field"><label>Only for</label>
        <small>Leave all unchecked to color for everything.</small>
        <div class="show-rules">${media}</div></div>
      <details class="more">
        <summary>More settings</summary>
        <div class="more-body">
          ${select("extraction_method", "Artwork color", ch.extraction_methods, LS_EXTRACTION)}
          ${number("min_saturation", "Skip colors greyer than", 0, 100, 1, "% saturation")}
          ${number("saturation_boost", "Make colors richer by", 1, 3, 0.1, "×")}
          ${number("min_brightness", "Lowest color brightness", 0, 100, 1, "%")}
          ${number("dark_threshold", "Ignore pixels darker than", 0, 255, 1, "of 255")}
          ${number("light_threshold", "Ignore pixels lighter than", 0, 255, 1, "of 255")}
          ${toggle("skip_achromatic", "Skip black, white and grey", "Uses the next color source when the artwork has no real color.")}
          ${toggle("metadata_artwork", "Look up artwork online", "Uses TMDB or Trakt from Shared settings when the player doesn't show artwork or a genre.")}
          ${toggle("fuzzy_title_match", "Match titles loosely", `So "The Matrix (1999)" uses the color saved for "The Matrix".`)}
          ${select("unsupported_lights", "Lights that can't show color", ch.unsupported_lights, LS_UNSUPPORTED)}
          ${toggle("expand_groups", "Color each light in a group", "Treats a light group as the lights inside it.")}
          ${toggle("persist_snapshot", "Remember how the lights were across restarts")}
        </div>
      </details>
      <div class="form-actions">
        <button type="submit" class="btn primary" ${this._busyZone ? "disabled" : ""}>${this._busyZone ? "Saving…" : isNew ? "Add light zone" : "Save light zone"}</button>
        <button type="button" class="btn" data-zone-cancel>Cancel</button>
      </div>
    </form>`;
  }

  _zoneField(el) {
    const c = this._zoneEdit?.cfg;
    if (!c) return false;
    const key = el.dataset.zf;
    const type = el.dataset.ztype;
    if (type === "bool") c[key] = el.checked;
    else if (type === "rgb") c[key] = hexRgb(el.value);
    else c[key] = el.value;
    return key === "color_mode";
  }

  _zoneFail(text) {
    this._zoneError = text;
    this._render();
  }

  async _saveZone() {
    const e = this._zoneEdit;
    const c = e.cfg;
    if (!c.lights?.length) return this._zoneFail("Pick at least one light for this zone.");
    const zone = {};
    for (const key of ["name", "enabled", "use_tv_player", "use_apple_tv", "extra_players", "require_tv_on", "unsupported_lights",
      "expand_groups", "default_color", "color_sources", "fuzzy_title_match", "media_types", "extraction_method", "skip_achromatic",
      "pause_behavior", "stop_behavior", "persist_snapshot", "color_mode", "palette_strategy", "metadata_artwork", "lights"]) {
      if (c[key] !== undefined) zone[key] = c[key];
    }
    zone.name = (zone.name || "").trim();
    for (const [key, label, min, max, whole] of LS_NUMBERS) {
      if (c[key] === undefined) continue;
      const n = Number(c[key]);
      if (c[key] === "" || Number.isNaN(n) || n < min || n > max || (whole && !Number.isInteger(n))) {
        return this._zoneFail(`${label} needs ${whole ? "a whole number" : "a number"} from ${min} to ${max}.`);
      }
      zone[key] = n;
    }
    if (c.id) zone.id = c.id;
    this._busyZone = true;
    this._error = null;
    this._zoneError = "";
    this._render();
    try {
      await this._ws({ type: "tv_mgmt/light_sync/zone/save", entry_id: e.entry_id, zone });
      this._zoneEdit = null;
      this._zoneNote = c.id ? "Light zone saved" : "Light zone added";
      await this._loadLightSync({ quiet: true });
    } catch (err) {
      this._zoneError = this._errorText(err);
    }
    this._busyZone = false;
    this._render();
  }

  // A zone command (remove, on/off, look again). What went wrong shows in the
  // Light sync card itself, next to the zones, not only at the top of the page.
  async _zoneRequest(msg, note) {
    this._error = null;
    this._zoneError = "";
    this._zoneNote = "";
    try {
      const res = await this._ws({ entry_id: this._selected, ...msg });
      this._zoneNote = typeof note === "function" ? note(res) : note;
    } catch (err) {
      this._zoneError = this._errorText(err);
    }
    // Reload either way: a toggle that didn't save flips back.
    await this._loadLightSync({ quiet: true });
    this._render();
  }

  _renderLightSettings() {
    const s = this._ls.settings;
    if (!this._lsDraft) this._lsDraft = { providers: [...s.metadata_providers], tmdb: "", trakt: "" };
    const d = this._lsDraft;
    const ch = this._ls.choices;
    const names = { tmdb: "TMDB", trakt: "Trakt" };
    const providers = ch.metadata_providers
      .map((p) => `<label class="show-rule"><input type="checkbox" class="check" data-ls-provider="${esc(p)}" ${d.providers.includes(p) ? "checked" : ""}> ${esc(names[p] || p)}</label>`)
      .join("");
    const key = (id, label, set, hint) => `<div class="field"><label for="ls-${id}">${label}</label>
        <div class="message-row">
          <input id="ls-${id}" type="password" data-ls-key="${id}" autocomplete="off" placeholder="${set ? "Saved. Type a new one to replace it." : "Not set"}" value="${esc(d[id])}">
          ${set ? `<button type="button" class="btn" data-ls-forget="${id}">Remove</button>` : ""}
        </div>
        <small>${hint}</small></div>`;
    const cache = this._ls.cache || {};
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <form id="ls-settings" novalidate>
        <div class="field"><label>Shared settings</label>
          <small>Used by every TV. When a player doesn't show artwork or a genre, light sync can look the title up online.</small></div>
        <div class="field"><label>Look titles up on</label>
          <div class="show-rules">${providers}</div>
          ${d.providers.length > 1 ? `<span><button type="button" class="chip-btn" data-ls-swap>Ask ${esc(names[d.providers[1]])} first</button></span>` : ""}
          <small>${d.providers.length ? `Asked in this order: ${esc(d.providers.map((p) => names[p] || p).join(", then "))}.` : "Not looking anything up."}</small></div>
        ${key("tmdb", "TMDB API key", s.tmdb_key_set, "From themoviedb.org, under Settings, API. Either the short key or the long read access token works.")}
        ${key("trakt", "Trakt client ID", s.trakt_key_set, "From trakt.tv, under Settings, Your API apps.")}
        <div class="form-actions">
          <button type="submit" class="btn primary" ${this._busyLs ? "disabled" : ""}>${this._busyLs ? "Checking…" : "Save shared settings"}</button>
          ${this._lsNote ? `<span class="saved" role="status">${esc(this._lsNote)}</span>` : ""}
        </div>
        <div class="field"><label>Artwork colors</label>
          <small>${esc(cache.size || 0)} artwork color${cache.size === 1 ? "" : "s"} remembered, so the same poster isn't worked out twice.</small>
          <span><button type="button" class="btn" data-ls-clear-cache>Forget artwork colors</button></span></div>
      </form>
    </div></section>`;
  }

  async _saveLightSettings(extra = {}) {
    const d = this._lsDraft;
    const msg = { type: "tv_mgmt/light_sync/settings/set", metadata_providers: d.providers, ...extra };
    if (!("tmdb_api_key" in extra) && d.tmdb.trim()) msg.tmdb_api_key = d.tmdb.trim();
    if (!("trakt_client_id" in extra) && d.trakt.trim()) msg.trakt_client_id = d.trakt.trim();
    this._busyLs = true;
    this._error = null;
    this._lsNote = "";
    this._render();
    try {
      await this._ws(msg);
      this._lsDraft = null;
      this._lsNote = "Shared settings saved";
      await this._loadLightSync({ quiet: true });
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busyLs = false;
    this._render();
  }

  _renderTitleColors() {
    if (!this._titleDraft) this._titleDraft = { title: "", color: "#ff9329" };
    const d = this._titleDraft;
    const rows = (this._ls.titles || [])
      .map((t) => `<div class="title-row"><span class="swatch" style="background:${esc(t.hex)}"></span><span>${esc(t.title)}</span><span class="muted">${esc(t.hex)}</span>
          <button type="button" class="btn" data-title-remove="${esc(t.title)}" aria-label="Forget the color for ${esc(t.title)}">${icon("delete")}</button></div>`)
      .join("");
    return `<section class="card" style="margin-top:20px"><div class="card-body">
      <form id="ls-title" novalidate>
        <div class="field"><label>Title colors</label>
          <small>A color that's always used for a movie or show, on every TV. "Keep this color" on a zone adds the one showing now.</small></div>
        ${rows ? `<div class="titles">${rows}</div>` : `<p class="muted">No title colors yet.</p>`}
        <div class="message-row">
          <input type="text" data-title-new placeholder="Movie or show, e.g. The Matrix" value="${esc(d.title)}" autocomplete="off" aria-label="Title">
          <input type="color" data-title-color value="${esc(d.color)}" aria-label="Color">
          <button type="submit" class="btn">Add</button>
        </div>
        ${this._titleNote ? `<span class="saved" role="status">${esc(this._titleNote)}</span>` : ""}
      </form>
    </div></section>`;
  }

  async _saveTitleColor() {
    const d = this._titleDraft;
    const title = d.title.trim();
    if (!title) return this._showError("Type the movie or show's name.");
    this._error = null;
    try {
      await this._ws({ type: "tv_mgmt/light_sync/titles/set", title, rgb_color: hexRgb(d.color) });
      this._titleNote = `Saved a color for ${title}`;
      this._titleDraft = null;
      await this._loadLightSync({ quiet: true });
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._render();
  }

  // Button presses on the Lights tab. Returns true when handled.
  _onLightsClick(el) {
    const d = el.dataset;
    const tv = this._lsTv();
    if (d.lightsOff !== undefined) {
      this._turnOffLights();
      return true;
    }
    if (d.zoneAdd !== undefined && tv) {
      this._startZoneEdit(tv, null);
      return true;
    }
    if (d.zoneEdit && tv) {
      this._startZoneEdit(tv, d.zoneEdit);
      return true;
    }
    if (d.zoneCancel !== undefined) {
      this._zoneEdit = null;
      this._error = null;
      this._zoneError = "";
      this._render();
      return true;
    }
    // Removing asks first, in the zone itself: window.confirm() doesn't show
    // in every Home Assistant app.
    if (d.zoneRemove) {
      this._zoneConfirm = this._zoneConfirm === d.zoneRemove ? null : d.zoneRemove;
      this._zoneError = "";
      this._zoneNote = "";
      this._render();
      return true;
    }
    if (d.zoneRemoveNo !== undefined) {
      this._zoneConfirm = null;
      this._render();
      return true;
    }
    if (d.zoneRemoveYes) {
      this._zoneConfirm = null;
      this._zoneRequest({ type: "tv_mgmt/light_sync/zone/remove", zone_id: d.zoneRemoveYes }, "Light zone removed");
      return true;
    }
    if (d.zoneAct) {
      this._zoneRequest({ type: "tv_mgmt/light_sync/zone/action", zone_id: d.zone, action: d.zoneAct }, (res) =>
        d.zoneAct === "resync" ? (res.error ? res.error : "Looked again") : "Color kept for this title");
      return true;
    }
    if (d.zlightUp && this._zoneEdit) {
      const list = this._zoneEdit.cfg.lights;
      const i = list.indexOf(d.zlightUp);
      if (i > 0) [list[i - 1], list[i]] = [list[i], list[i - 1]];
      this._render();
      return true;
    }
    if (d.zsrcUp && this._zoneEdit) {
      const list = this._zoneEdit.cfg.color_sources;
      const i = list.indexOf(d.zsrcUp);
      if (i > 0) [list[i - 1], list[i]] = [list[i], list[i - 1]];
      this._render();
      return true;
    }
    if (d.lsSwap !== undefined && this._lsDraft) {
      this._lsDraft.providers.reverse();
      this._lsNote = "";
      this._render();
      return true;
    }
    if (d.lsForget && this._lsDraft) {
      const field = d.lsForget === "tmdb" ? "tmdb_api_key" : "trakt_client_id";
      this._saveLightSettings({ [field]: "" });
      return true;
    }
    if (d.lsClearCache !== undefined) {
      this._callService("tv_mgmt", "clear_artwork_cache", { resync: true }).then(() => {
        this._lsNote = this._error ? "" : "Artwork colors forgotten";
        this._loadLightSync({ quiet: true });
        this._render();
      });
      return true;
    }
    if (d.titleRemove) {
      this._error = null;
      this._ws({ type: "tv_mgmt/light_sync/titles/remove", title: d.titleRemove })
        .then(() => {
          this._titleNote = `Forgot the color for ${d.titleRemove}`;
          return this._loadLightSync({ quiet: true });
        })
        .catch((err) => (this._error = this._errorText(err)))
        .finally(() => this._render());
      return true;
    }
    return false;
  }

  // Checkboxes and selects on the Lights tab. Returns true when handled.
  _onLightsChange(input) {
    const d = input.dataset;
    const e = this._zoneEdit;
    if (d.lightsFollow !== undefined) {
      this._setLightsFollow(input.checked);
      return true;
    }
    if (d.zoneEnable) {
      const z = this._lsTv()?.zones.find((x) => x.config.id === d.zoneEnable);
      if (z) {
        const enabled = input.checked;
        this._zoneRequest(
          { type: "tv_mgmt/light_sync/zone/save", zone: { id: d.zoneEnable, lights: z.config.lights, enabled } },
          enabled ? `${z.config.name} on` : `${z.config.name} off`,
        );
      }
      return true;
    }
    if (e && d.zf !== undefined) {
      if (this._zoneField(input)) this._render();
      return true;
    }
    if (e && (d.zlight !== undefined || d.zplayer !== undefined)) {
      const key = d.zlight !== undefined ? "lights" : "extra_players";
      const eid = d.zlight ?? d.zplayer;
      const list = (e.cfg[key] = (e.cfg[key] || []).filter((x) => x !== eid));
      if (input.checked) list.push(eid);
      if (key === "lights" && e.cfg.color_mode === "palette") this._render();
      return true;
    }
    if (e && d.zmedia !== undefined) {
      const list = (e.cfg.media_types = (e.cfg.media_types || []).filter((x) => x !== d.zmedia));
      if (input.checked) list.push(d.zmedia);
      return true;
    }
    if (e && d.zsrc !== undefined) {
      const list = (e.cfg.color_sources = e.cfg.color_sources.filter((x) => x !== d.zsrc));
      if (input.checked) list.push(d.zsrc);
      this._render();
      return true;
    }
    if (e && d.zpreset !== undefined) {
      const preset = this._ls.choices.presets[input.value];
      if (preset) e.cfg.color_sources = [...preset];
      this._render();
      return true;
    }
    if (this._lsDraft && d.lsProvider !== undefined) {
      const list = (this._lsDraft.providers = this._lsDraft.providers.filter((x) => x !== d.lsProvider));
      if (input.checked) list.push(d.lsProvider);
      this._lsNote = "";
      this._render();
      return true;
    }
    return false;
  }

  // Typing on the Lights tab. Returns true when handled.
  _onLightsInput(t) {
    const d = t.dataset;
    const e = this._zoneEdit;
    if (e && (d.zlightFilter !== undefined || d.zplayerFilter !== undefined)) {
      const attr = d.zlightFilter !== undefined ? "data-zlight-filter" : "data-zplayer-filter";
      e[d.zlightFilter !== undefined ? "lightFilter" : "playerFilter"] = t.value;
      clearTimeout(this._zoneFilterTimer);
      this._zoneFilterTimer = setTimeout(() => {
        this._render();
        const box = this.shadowRoot.querySelector(`[${attr}]`);
        if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
      }, 250);
      return true;
    }
    if (e && d.zf !== undefined && t.tagName === "INPUT" && t.type !== "checkbox") {
      this._zoneField(t);
      return true;
    }
    if (this._lsDraft && d.lsKey) {
      this._lsDraft[d.lsKey] = t.value;
      this._lsNote = "";
      return true;
    }
    if (this._titleDraft && d.titleNew !== undefined) {
      this._titleDraft.title = t.value;
      this._titleNote = "";
      return true;
    }
    if (this._titleDraft && d.titleColor !== undefined) {
      this._titleDraft.color = t.value;
      return true;
    }
    return false;
  }

  // ---- events ------------------------------------------------------------------------

  _onClick(ev) {
    const el = ev.target.closest("button");
    if (!el) return;

    if (this._tab === "lights" && this._onLightsClick(el)) return;
    if (el.dataset.tvLightsOff) {
      this._dashboardLightsOff(el.dataset.tvLightsOff);
      return;
    }
    if (el.dataset.homeExtra) {
      this._homeExtra(Number(el.dataset.homeExtra));
      return;
    }
    if (el.dataset.tab) {
      this._tab = el.dataset.tab;
      if (this._tab === "limits") {
        this._homeEdit = null;
        this._homeNote = "";
      }
      if (this._tab === "access") {
        this._accessDraft = null;
        this._accessNote = "";
      }
      if (this._tab === "lights") {
        this._zoneEdit = null;
        this._zoneNote = "";
        this._zoneError = "";
        this._zoneConfirm = null;
        this._lsNote = "";
        this._titleNote = "";
        this._lsDraft = null;
        this._lightsDraft = null;
        this._lightsNote = "";
        this._lightsNowNote = "";
        if (this._ls) this._loadLightSync({ quiet: true });
      }
      this._error = null;
      this._savedNote = "";
      this._render();
      return;
    }
    if (el.dataset.pick) {
      this._selected = el.dataset.pick;
      this._activity = null;
      this._activityDate = null;
      this._limitsDraft = null;
      this._namesDraft = null;
      this._lightsDraft = null;
      this._lightsNote = "";
      this._lightsNowNote = "";
      this._zoneEdit = null;
      this._zoneNote = "";
      this._zoneError = "";
      this._zoneConfirm = null;
      this._appsDraft = null;
      this._appsNote = "";
      this._savedNote = "";
      this._namesNote = "";
      this._render();
      return;
    }
    if (el.dataset.act) {
      if (el.dataset.confirm && !window.confirm(el.dataset.confirm)) return;
      const extra = {};
      if (el.dataset.minutes) extra.minutes = Number(el.dataset.minutes);
      if (el.dataset.mode) extra.mode = el.dataset.mode;
      this._action(el.dataset.id, el.dataset.act, extra);
      return;
    }
    if (el.dataset.day) {
      const a = this._activity;
      this._activityDate = el.dataset.day === "today" ? a.today : shiftDate(a.date, Number(el.dataset.day));
      this._loadActivity();
      return;
    }
    if (el.dataset.range) {
      this._range = Number(el.dataset.range);
      this._render();
      return;
    }
    if (el.dataset.media) {
      this._callService("media_player", el.dataset.media, { entity_id: el.dataset.eid });
      return;
    }
    if (el.dataset.addShow !== undefined && this._appsDraft) {
      const d = this._appsDraft;
      const name = (d.newShow || "").trim();
      if (name && !d.shows.some((x) => x.trim().toLowerCase() === name.toLowerCase())) d.shows.push(name);
      d.newShow = "";
      this._appsNote = "";
      this._render();
      return;
    }
    if (el.dataset.blockShow) {
      this._toggleShow(el.dataset.id, el.dataset.blockShow);
      return;
    }
    if (el.dataset.expand) {
      this._toggleExpanded(el.dataset.expand);
      return;
    }
    if (el.dataset.preset) {
      const d = this._msg(el.dataset.id);
      d.text = el.dataset.preset;
      this._msgNote[el.dataset.id] = "";
      this._render();
      this.shadowRoot.getElementById(`msg-${el.dataset.id}`)?.focus();
      return;
    }
    if (el.dataset.moreInfo) {
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: el.dataset.moreInfo }, bubbles: true, composed: true }));
      return;
    }
    if (el.dataset.key) {
      this._press(el.dataset.id, el.dataset.device, el.dataset.key);
      return;
    }
    if (el.dataset.remoteToggle) {
      const k = el.dataset.remoteToggle;
      if (this._remoteOpen.has(k)) this._remoteOpen.delete(k);
      else this._remoteOpen.add(k);
      this._render();
      return;
    }
    if (el.dataset.appmode && this._appsDraft) {
      if (this._limitsDraft) this._syncLimitInputs();
      const d = this._appsDraft;
      d.byMode[d.mode] = [...d.apps];
      d.mode = el.dataset.appmode;
      d.apps = [...(d.byMode[d.mode] || [])];
      this._appsNote = "";
      this._render();
      return;
    }
    if (el.hasAttribute("data-add-window")) {
      this._syncLimitInputs();
      this._limitsDraft.windows.push({ start: "20:30", end: "07:00", label: "Bedtime" });
      this._render();
      return;
    }
    if (el.dataset.removeWindow !== undefined) {
      this._syncLimitInputs();
      this._limitsDraft.windows.splice(Number(el.dataset.removeWindow), 1);
      this._render();
    }
  }

  _onChange(ev) {
    const input = ev.target;
    if (this._tab === "lights" && this._onLightsChange(input)) return;
    if (input.dataset.light !== undefined && this._lightsDraft) {
      if (input.checked) this._lightsDraft.picked.add(input.dataset.light);
      else this._lightsDraft.picked.delete(input.dataset.light);
      this._lightsNote = "";
      return;
    }
    if (input.dataset.msgWhere) {
      this._msg(input.dataset.id)[input.dataset.msgWhere] = input.checked;
      return;
    }
    if (input.dataset.person !== undefined && this._accessDraft) {
      if (input.checked) this._accessDraft.ids.add(input.dataset.person);
      else this._accessDraft.ids.delete(input.dataset.person);
      this._accessNote = "";
      this._render();
      return;
    }
    const d = this._appsDraft;
    if (d && input.dataset.showCheck !== undefined) {
      const name = input.dataset.showCheck;
      d.shows = d.shows.filter((x) => x.trim().toLowerCase() !== name.trim().toLowerCase());
      if (input.checked) d.shows.push(name);
      this._appsNote = "";
      return;
    }
    if (d && input.dataset.appCheck !== undefined) {
      const key = input.dataset.appCheck;
      d.apps = d.apps.filter((x) => x.toLowerCase() !== key.toLowerCase());
      if (input.checked) d.apps.push(key);
      this._appsNote = "";
      return;
    }
    if (d && input.dataset.appAction !== undefined) {
      d.action = input.value;
      this._appsNote = "";
      return;
    }
    if (d && input.dataset.appWake !== undefined) {
      d.wake = input.checked;
      this._appsNote = "";
      return;
    }
    if (d && input.dataset.appSleep !== undefined) {
      d.sleep = input.checked;
      this._appsNote = "";
      return;
    }
    if (input.dataset.toggle) {
      const extra = { enabled: input.checked };
      this._action(input.dataset.id, input.dataset.toggle, extra);
    }
  }

  // Someone is typing in a text or number field on the dashboard.
  _typing() {
    const el = this.shadowRoot.activeElement;
    return !!el && el.tagName === "INPUT" && ["text", "number", "password"].includes(el.type);
  }

  _onInput(ev) {
    if (this._tab === "lights" && this._onLightsInput(ev.target)) return;
    if (ev.target.dataset.lightFilter !== undefined && this._lightsDraft) {
      this._lightsDraft.filter = ev.target.value;
      clearTimeout(this._lightFilterTimer);
      // Redraw the list after typing pauses, keeping the cursor in the box.
      this._lightFilterTimer = setTimeout(() => {
        this._render();
        const box = this.shadowRoot.querySelector("[data-light-filter]");
        if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
      }, 250);
    }
    if (ev.target.dataset.newShow !== undefined && this._appsDraft) this._appsDraft.newShow = ev.target.value;
    if (ev.target.name === "message" && ev.target.closest("form[data-message]")) {
      const id = ev.target.closest("form[data-message]").dataset.message;
      this._msg(id).text = ev.target.value;
      this._msgNote[id] = "";
    }
    if (ev.target.closest("#limits")) this._savedNote = "";
    if (this._appsDraft && ev.target.dataset.appLimit !== undefined) {
      const key = ev.target.dataset.appLimit;
      for (const k of Object.keys(this._appsDraft.limits)) {
        if (k.toLowerCase() === key.toLowerCase()) delete this._appsDraft.limits[k];
      }
      this._appsDraft.limits[key] = ev.target.value;
      this._appsNote = "";
    }
    if (ev.target.dataset.nameFor !== undefined) {
      this._namesDraft[ev.target.dataset.nameFor] = ev.target.value;
      this._namesNote = "";
    }
  }

  async _saveNames() {
    const names = {};
    for (const [raw, name] of Object.entries(this._namesDraft || {})) {
      if (name.trim() && name.trim() !== raw) names[raw] = name.trim();
    }
    this._busyNames = true;
    this._error = null;
    this._render();
    try {
      await this._ws({ type: "tv_mgmt/input_names/set", entry_id: this._selected, names });
      this._namesDraft = names;
      this._namesNote = "Names saved";
      await this._loadProfiles({ quiet: true });
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busyNames = false;
    this._render();
  }

  _syncLimitInputs() {
    const root = this.shadowRoot;
    const d = this._limitsDraft;
    for (const key of ["daily_budget", "warn_minutes", "adult_mode_duration"]) {
      const el = root.getElementById(key);
      if (el) d[key] = el.value;
    }
    const counts = root.getElementById("adult_mode_counts");
    if (counts) d.adult_mode_counts = counts.checked;
    root.querySelectorAll("[data-win]").forEach((el) => {
      d.windows[Number(el.dataset.win)][el.dataset.key] = el.value;
    });
  }

  async _onSubmit(ev) {
    if (ev.target.id === "home") {
      ev.preventDefault();
      await this._saveHome();
      return;
    }
    if (ev.target.id === "apps" && this.shadowRoot.activeElement?.dataset?.newShow !== undefined) {
      ev.preventDefault();
      this.shadowRoot.querySelector("[data-add-show]")?.click();
      return;
    }
    if (ev.target.dataset.message) {
      ev.preventDefault();
      await this._sendMessage(ev.target.dataset.message);
      return;
    }
    if (ev.target.dataset.extra) {
      ev.preventDefault();
      const field = ev.target.elements.minutes;
      const minutes = Number(field.value);
      if (!field.value || !Number.isInteger(minutes) || minutes === 0 || minutes < -240 || minutes > 720) {
        this._error = "Enter a whole number of minutes from -240 to 720. Use a minus sign to take time away.";
        this._render();
        return;
      }
      await this._action(ev.target.dataset.extra, "grant_extension", { minutes });
      return;
    }
    if (ev.target.id === "access") {
      ev.preventDefault();
      await this._saveAccess();
      return;
    }
    if (ev.target.id === "apps") {
      ev.preventDefault();
      await this._saveApps();
      return;
    }
    if (ev.target.id === "lights") {
      ev.preventDefault();
      await this._saveLights();
      return;
    }
    if (ev.target.id === "zone-form") {
      ev.preventDefault();
      await this._saveZone();
      return;
    }
    if (ev.target.id === "ls-settings") {
      ev.preventDefault();
      await this._saveLightSettings();
      return;
    }
    if (ev.target.id === "ls-title") {
      ev.preventDefault();
      await this._saveTitleColor();
      return;
    }
    if (ev.target.id === "names") {
      ev.preventDefault();
      await this._saveNames();
      return;
    }
    if (ev.target.id !== "limits") return;
    ev.preventDefault();
    this._syncLimitInputs();
    const d = this._limitsDraft;
    const numbers = {};
    for (const [key, label, min, max] of [
      ["daily_budget", "Daily screen time", 0, 1440],
      ["warn_minutes", "Warn before time runs out", 0, 60],
      ["adult_mode_duration", "Adult mode lasts", 5, 720],
    ]) {
      const value = Number(d[key]);
      if (!Number.isInteger(value) || value < min || value > max) {
        this._error = `${label} needs a whole number of minutes from ${min} to ${max}.`;
        this._render();
        return;
      }
      numbers[key] = value;
    }
    const incomplete = d.windows.find((w) => !w.start || !w.end);
    if (incomplete) {
      this._error = "Give every quiet window a start and an end time, or remove it.";
      this._render();
      return;
    }
    const quiet = d.windows
      .map((w) => `${w.start}-${w.end}${w.label.trim() ? ` ${w.label.trim().replace(/[,]/g, "")}` : ""}`)
      .join(", ");
    this._busy = true;
    this._error = null;
    this._render();
    try {
      const adult_mode_counts = !!d.adult_mode_counts;
      await this._ws({ type: "tv_mgmt/limits/set", entry_id: this._selected, ...numbers, quiet_windows: quiet, adult_mode_counts });
      this._savedNote = "Limits saved";
      this._limits = { ...numbers, quiet_windows: quiet, adult_mode_counts };
    } catch (err) {
      this._error = this._errorText(err);
    }
    this._busy = false;
    this._render();
  }
}

if (!customElements.get("tv-mgmt-panel")) {
  customElements.define("tv-mgmt-panel", TvMgmtPanel);
}
