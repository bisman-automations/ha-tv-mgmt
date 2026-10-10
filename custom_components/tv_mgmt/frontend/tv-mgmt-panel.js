// TV Mgmt sidebar panel. Plain web component, no build step.
// Talks to the integration over Home Assistant's websocket (tv_mgmt/* commands).

const TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "activity", label: "Activity" },
  { id: "analytics", label: "Analytics" },
  { id: "limits", label: "Limits" },
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
      const why = { budget: "Daily limit reached", quiet_window: p.quiet_window || "Quiet window", manual: "Blocked by a parent" }[p.reason];
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
          ? { budget: "Daily limit reached", quiet_window: `Quiet window started${e.quiet_window ? `: ${e.quiet_window}` : ""}`, manual: "Blocked by a parent" }[e.reason] || "Blocked"
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
      return { icon: "adult", text: e.on ? `Adult mode on for ${e.minutes} min` : "Adult mode turned off" };
    case "extension":
      return { icon: "plus", text: e.minutes >= 0 ? `${e.minutes} min of extra time added` : `${-e.minutes} min of time taken away` };
    case "block":
      return { icon: "shield", tone: "bad", text: "Blocked now" };
    case "unblock":
      return { icon: "shield", text: "Unblocked" };
    case "reset":
      return { icon: "clock", text: "Today's time reset" };
    case "media": {
      const what = describeMedia(e);
      return { icon: "play", text: `Watching ${what}${e.name ? ` in ${e.name}` : ""}` };
    }
    case "app":
      return { icon: "box", text: e.app ? `Opened ${e.name ?? e.app} on the Apple TV` : "Closed the app on the Apple TV" };
    case "app_stopped": {
      const app = e.name ?? e.app;
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
  time_remaining_today: "Time remaining today",
  extra_time_today: "Extra time today",
  blocked_switches_today: "Blocked switches today",
  current_app: "Current app",
  app_time_today: "App time today",
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
@media (max-width: 420px) { nav { gap: 0; padding: 0 4px; } nav button { padding: 12px 10px 10px; } }
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
input[type="number"], input[type="text"], input[type="time"] {
  font: inherit; font-size: 15px; padding: 10px 12px; border-radius: 8px; min-height: 42px;
  border: 1px solid var(--divider-color); background: var(--card-background-color, transparent); color: var(--primary-text-color);
}
input[type="number"] { width: 140px; }
.windows { display: grid; gap: 8px; }
.window { display: grid; grid-template-columns: auto auto 1fr auto; gap: 8px; align-items: center; }
.window input[type="text"] { min-width: 0; }
.window .btn { padding: 6px; }
.form-actions { display: flex; gap: 12px; align-items: center; }
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
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
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
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first) this._start();
    else this._refreshNowPlaying();
  }

  // Redraw just the Apple TV "Now playing" spots when their media player changes.
  _refreshNowPlaying() {
    if (this._tab !== "dashboard" || !this._profiles) return;
    this._npStates = this._npStates || {};
    for (const p of this._profiles) {
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
    }, 600);
  }

  async _loadProfiles({ quiet = false } = {}) {
    try {
      const res = await this._ws({ type: "tv_mgmt/profiles" });
      // Saving settings briefly reloads a TV's profile. If the TV on screen is
      // missing from a background refresh, keep what we have and look again.
      if (quiet && this._selected && !res.profiles.some((p) => p.entry_id === this._selected)) {
        clearTimeout(this._retryTimer);
        this._retryTimer = setTimeout(() => this._loadProfiles({ quiet: true }), 1500);
        return;
      }
      this._profiles = res.profiles;
      if (!this._selected || !this._profiles.some((p) => p.entry_id === this._selected)) {
        this._selected = this._profiles[0]?.entry_id ?? null;
      }
      if (!quiet) this._error = null;
    } catch (err) {
      if (!quiet) this._error = this._errorText(err);
    }
    // The limits form keeps what you're typing; don't redraw it on live updates.
    if (!(quiet && this._tab === "limits")) this._render();
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
  }

  _render() {
    const main = this.shadowRoot.getElementById("main");
    if (!main) return;
    this.shadowRoot.querySelectorAll("nav button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === this._tab)));

    let body;
    if (this._tab === "access") {
      body = this._renderAccess();
    } else if (this._profiles === null) {
      body = this._error ? "" : `<p class="empty">Loading your TVs…</p>`;
    } else if (!this._profiles.length) {
      body = `<div class="empty"><h2>No TVs yet</h2><p>Add a TV under Settings, Devices &amp; services, TV Mgmt, and it will show up here.</p></div>`;
    } else {
      body = {
        dashboard: () => this._renderDashboard(),
        activity: () => this._renderActivity(),
        analytics: () => this._renderAnalytics(),
        limits: () => this._renderLimits(),
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
    return `<div class="grid">${this._profiles.map((p) => this._renderTv(p)).join("")}</div>`;
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
    const timeLine = limit
      ? `<div class="time-line"><span><strong>${duration(p.used_seconds)}</strong> <span class="muted">of ${duration(limit * 60)}</span></span><span class="muted">${p.remaining_seconds > 0 ? `${duration(p.remaining_seconds)} left` : "No time left"}</span></div>
         <div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="${limit}" aria-valuenow="${usedMin}" aria-label="Screen time used"><div class="tone-${state.tone === "bad" ? "bad" : state.tone === "warn" ? "warn" : "info"}" style="width:${pct}%"></div></div>`
      : `<div class="time-line"><span><strong>${duration(p.used_seconds)}</strong> <span class="muted">today</span></span><span class="muted">No daily limit</span></div>`;

    const lastBlocked = p.last_blocked_input && p.last_blocked_at ? `${esc(nameOf(p, p.last_blocked_input))} at ${timeOf(p.last_blocked_at)}` : "None";
    const adultOn = p.state === "adult_mode" || !!p.adult_mode_until;
    const id = p.entry_id;

    return `
      <section class="card tv-card${p.apple_tv ? " has-atv" : ""}" aria-label="${esc(p.name)}">
       <div class="tv-layout">
        <div class="col col-tv">
        <div class="screen-wrap">
          <div class="${screenClass}">
            <div class="glow"></div>
            <button class="tv-name link" data-more-info="${esc(p.tv_entity_id)}" title="Open ${esc(p.name)}">${esc(p.name)}</button>
            <span class="chip tone-${state.tone}">${esc(state.text)}</span>
            <div class="now">${now}</div>
            ${sub ? `<div class="sub">${sub}</div>` : ""}
          </div>
          <div class="stand"></div>
        </div>
        <div class="card-body">
          <div class="tv-remote" id="tvr-${id}">${this._tvRemote(p)}</div>
          <div>${timeLine}</div>
        </div>
        </div>
        ${p.apple_tv ? `<div class="col col-atv card-body">${this._renderAppleTv(p)}</div>` : ""}
        <div class="col col-controls card-body">
          <dl class="facts">
            <dt>Extra time today</dt><dd>${p.extension_minutes ? `${p.extension_minutes > 0 ? "+" : ""}${p.extension_minutes} min` : "None"}</dd>
            <dt>Blocked switches</dt><dd>${p.blocked_switches}</dd>
            <dt>Last tried</dt><dd>${lastBlocked}</dd>
            <dt>Allowed</dt><dd>${esc((p.allowed_inputs || []).map((raw) => nameOf(p, raw)).join(", ") || "Not set")}</dd>
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
      </section>`;
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
      ? `<span class="chip tone-bad">${{ blocked: "Blocked app", not_allowed: "Not allowed", limit: "Limit reached" }[a.stop_reason] || "Not allowed"}</span>`
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
    if (!st || st.state === "unavailable") return "";
    return this._remote(p, "tv", p.remote_keys, !["off", "standby"].includes(st.state));
  }

  // Power, volume and a d-pad for the TV ("tv") or its Apple TV ("apple_tv").
  // Only the buttons that device's integration can press are shown.
  _remote(p, device, keys, isOn) {
    keys = keys || [];
    // Power depends on whether the device is on right now, so check it live.
    const eid = device === "apple_tv" ? p.apple_tv?.entity_id : p.tv_entity_id;
    const features = Number(this._hass?.states?.[eid]?.attributes?.supported_features || 0);
    if (!(features & (isOn === false ? 128 : 256))) keys = keys.filter((k) => k !== "power");
    if (!keys.length) return "";
    const has = (k) => keys.includes(k);
    const id = p.entry_id;
    const what = device === "apple_tv" ? "Apple TV" : "TV";
    const open = this._remoteOpen.has(`${id}:${device}`);
    const key = (k, ico, label, cls = "", extra = "") =>
      `<button class="icon-btn ${cls}" data-key="${k}" data-device="${device}" data-id="${id}" aria-label="${label}" title="${label}" ${extra}>${ico}</button>`;

    const awake = isOn !== false;
    const powerLabel = device === "apple_tv" ? (awake ? "Sleep" : "Wake") : awake ? "Turn off" : "Turn on";
    // Awake: a round power button beside volume. Off: the only button, so it's labelled.
    const power = !has("power")
      ? ""
      : awake
        ? key("power", icon("power-off"), `${powerLabel} the ${what}`, "power-off")
        : `<button class="btn" data-key="power" data-device="${device}" data-id="${id}" aria-label="${powerLabel} the ${what}">${icon("power")} ${powerLabel}</button>`;
    if (!awake) return `<div class="remote"><div class="remote-bar">${power}</div></div>`;

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
      <div class="remote-bar">${power}${volume ? `<span class="vol">${volume}</span>` : ""}<span class="np-spacer"></span>${toggle}</div>
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
        .map((m) => `<li><div class="row-line"><span>${esc(m.show)}${m.app ? `<small>${esc(m.app)}</small>` : ""}</span><span class="muted">${duration(m.seconds)}</span></div></li>`)
        .join("")}</ul>`;
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
      return `<p class="empty">Loading people…</p>`;
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
      return `${this._activityToolbar(null)}<p class="empty">Loading activity…</p>`;
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
      .join("");

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
        <div class="time-line"><span><strong>${duration(a.viewing_seconds)}</strong> <span class="muted">on screen</span></span>
          ${a.totals ? `<span class="muted">${a.totals.blocked} blocked switch${a.totals.blocked === 1 ? "" : "es"}</span>` : ""}</div>
        <div>
          <div class="tape" role="img" aria-label="When the TV was on, by input">${bars}${nowLine}</div>
          <div class="hours">${hours}</div>
          ${legend ? `<div class="legend">${legend}</div>` : ""}
          ${profile?.apple_tv ? this._appTape(a, pos, nowLine) : ""}
        </div>
      </div></section>
      <section class="card" style="margin-top:20px"><div class="card-body">${events}</div></section>`;
  }

  _appTape(a, pos, nowLine) {
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
      <div class="tape apps-tape" role="img" aria-label="When apps were open on the Apple TV">${bars}${nowLine}</div>
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
      return `${this._analyticsToolbar()}<p class="empty">Loading analytics…</p>`;
    }
    const s = data.summary;
    const stats = `
      <div class="stats">
        <div class="card stat"><b>${duration(s.total_seconds)}</b><span>Total screen time</span></div>
        <div class="card stat"><b>${duration(s.average_seconds)}</b><span>Daily average</span></div>
        <div class="card stat"><b>${s.days_over_limit}</b><span>Days the limit was reached</span></div>
        <div class="card stat"><b>${s.blocked}</b><span>Blocked input switches</span></div>
        ${data.has_apple_tv ? `<div class="card stat"><b>${s.apps_stopped ?? 0}</b><span>Apple TV apps stopped</span></div>` : ""}
      </div>`;
    const note = s.recorded_days < data.days.length
      ? `<p class="muted">TV Mgmt has ${s.recorded_days} day${s.recorded_days === 1 ? "" : "s"} of history so far. Days before that show as empty.</p>`
      : "";
    return `${this._analyticsToolbar()}${stats}
      <section class="card"><div class="card-body">
        <div><strong>Screen time per day</strong><div class="muted">The dashed line is the daily limit, including extra time.</div></div>
        ${this._chart(data.days)}
        ${note}
      </div></section>
      ${data.has_apple_tv ? this._topApps(s.top_apps || []) + this._topMedia(s.top_media || []) : ""}`;
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
    const values = days.map((d) => minutes(d.used_seconds));
    const limits = days.map((d) => (d.budget_minutes ? d.budget_minutes + d.extension_minutes : 0));
    const max = Math.max(60, ...values, ...limits);
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
        const v = values[i];
        const over = limits[i] && v >= limits[i];
        const h = Math.max(v ? 2 : 0, (v / top) * plotH);
        const x = padL + i * bw + bw * 0.15;
        return `<rect class="day-bar${over ? " over" : ""}" x="${x}" y="${padT + plotH - h}" width="${bw * 0.7}" height="${h}" rx="2"><title>${d.date}: ${duration(d.used_seconds)}${limits[i] ? ` of ${duration(limits[i] * 60)}` : ""}</title></rect>`;
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

  _renderLimits() {
    const d = this._limitsDraft;
    if (!d || this._limitsFor !== this._selected) {
      if (!this._loadingLimits) {
        this._loadingLimits = true;
        this._limitsFor = this._selected;
        this._loadLimits().finally(() => (this._loadingLimits = false));
      }
      return `<div class="toolbar">${this._renderPicker()}</div><p class="empty">Loading limits…</p>`;
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
    return `<div class="toolbar">${this._renderPicker()}</div>
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
        mode: d.mode, apps: d.apps, limits, action: d.action, sleep_on_block: d.sleep, wake_with_tv: d.wake,
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

  // ---- events ------------------------------------------------------------------------

  _onClick(ev) {
    const el = ev.target.closest("button");
    if (!el) return;

    if (el.dataset.tab) {
      this._tab = el.dataset.tab;
      if (this._tab === "access") {
        this._accessDraft = null;
        this._accessNote = "";
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
    if (input.dataset.person !== undefined && this._accessDraft) {
      if (input.checked) this._accessDraft.ids.add(input.dataset.person);
      else this._accessDraft.ids.delete(input.dataset.person);
      this._accessNote = "";
      this._render();
      return;
    }
    const d = this._appsDraft;
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

  _onInput(ev) {
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
    root.querySelectorAll("[data-win]").forEach((el) => {
      d.windows[Number(el.dataset.win)][el.dataset.key] = el.value;
    });
  }

  async _onSubmit(ev) {
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
      await this._ws({ type: "tv_mgmt/limits/set", entry_id: this._selected, ...numbers, quiet_windows: quiet });
      this._savedNote = "Limits saved";
      this._limits = { ...numbers, quiet_windows: quiet };
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
