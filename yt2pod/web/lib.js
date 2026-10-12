// Small helpers: API calls, formatting, springs, icons.
import { html } from "/vendor/htm-preact.js";

// --- API --------------------------------------------------------------------------
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, signal, keepalive } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method, signal, keepalive, credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, "Sin conexión con el servidor");
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const err = new ApiError(res.status, data?.error || data?.detail?.[0]?.msg || `Error ${res.status}`);
    if (res.status === 401) window.dispatchEvent(new CustomEvent("yt2pod:unauthorized"));
    throw err;
  }
  return data;
}

// --- formatting -------------------------------------------------------------------
const rtf = new Intl.RelativeTimeFormat("es", { numeric: "auto" });
const compact = new Intl.NumberFormat("es", { notation: "compact", maximumFractionDigits: 1 });

export function relDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const days = Math.round((startOfDay(new Date()) - startOfDay(d)) / 86400000);
  if (days <= 0) return "hoy";
  if (days === 1) return "ayer";
  if (days < 7) return rtf.format(-days, "day");
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString("es", sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
}
function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }

/** 4320 → "1 h 12 min", 540 → "9 min" */
export function humanDuration(sec) {
  if (!sec) return "";
  const m = Math.round(sec / 60);
  if (m < 1) return `${Math.round(sec)} s`;
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}

/** 4320 → "1:12:00" (clock style, for badges and the scrubber) */
export function clock(sec) {
  if (sec == null || !isFinite(sec)) return "--:--";
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function remaining(ep) {
  if (!ep.duration) return "";
  const left = ep.duration - (ep.position || 0);
  return left < 60 ? "menos de 1 min" : `quedan ${humanDuration(left)}`;
}

export const subscribers = (n) => (n ? `${compact.format(n)} suscriptores` : "");

export function initials(name = "") {
  return name.replace(/^@/, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

/** A stable, pleasant gradient for things without artwork. */
export function tint(seed = "") {
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `linear-gradient(135deg, hsl(${h} 70% 58%), hsl(${(h + 40) % 360} 75% 45%))`;
}

export const haptic = (ms = 8) => { try { navigator.vibrate?.(ms); } catch {} };

export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// --- springs ----------------------------------------------------------------------
// Apple-style parameters: damping ratio (1 = no overshoot) and response (seconds, ~ "speed").
// Starts from the current value and velocity, so it can be interrupted and retargeted at any time.
export function spring({ from, to, velocity = 0, damping = 1, response = 0.35, onUpdate, onDone }) {
  const k = Math.pow((2 * Math.PI) / response, 2);
  const c = (4 * Math.PI * damping) / response;
  let x = from, v = velocity, last = performance.now(), raf = 0, stopped = false;
  if (reducedMotion()) { onUpdate(to, 0); onDone?.(); return { stop() {}, get value() { return to; }, get velocity() { return 0; } }; }
  const step = (now) => {
    if (stopped) return;
    let dt = Math.min(0.064, (now - last) / 1000);
    last = now;
    // Integrate in small fixed steps for stability.
    while (dt > 0) {
      const h = Math.min(dt, 1 / 240);
      const a = -k * (x - to) - c * v;
      v += a * h; x += v * h; dt -= h;
    }
    if (Math.abs(x - to) < 0.3 && Math.abs(v) < 2) { x = to; v = 0; onUpdate(x, v); onDone?.(); return; }
    onUpdate(x, v);
    raf = requestAnimationFrame(step);
  };
  raf = requestAnimationFrame(step);
  return { stop() { stopped = true; cancelAnimationFrame(raf); }, get value() { return x; }, get velocity() { return v; } };
}

/** Where a flick would come to rest (Apple's deceleration projection). */
export const project = (velocity, rate = 0.998) => (velocity / 1000) * rate / (1 - rate);

export function rubberband(over, dim, c = 0.55) {
  return (over * dim * c) / (dim + c * Math.abs(over));
}

// --- icons (SF Symbols–like, drawn as SVG) ---------------------------------------------
const svg = (body, vb = "0 0 24 24", extra = {}) => (props = {}) =>
  html`<svg viewBox=${vb} fill="currentColor" aria-hidden="true" ...${extra} ...${props}>${body}</svg>`;

export const Icon = {
  play: svg(html`<path d="M7 4.6v14.8c0 .9 1 1.5 1.8 1l12-7.4c.7-.5.7-1.5 0-2L8.8 3.6C8 3.1 7 3.7 7 4.6z"/>`),
  pause: svg(html`<rect x="5.5" y="4" width="4.5" height="16" rx="1.4"/><rect x="14" y="4" width="4.5" height="16" rx="1.4"/>`),
  plus: svg(html`<path d="M12 4.5v15M4.5 12h15" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/>`),
  check: svg(html`<path d="M5 12.5l4.5 4.5L19 7.5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>`),
  more: svg(html`<circle cx="5.5" cy="12" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="18.5" cy="12" r="1.9"/>`),
  back: svg(html`<path d="M11 2L2 11l9 9" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>`, "0 0 13 22"),
  chev: svg(html`<path d="M1.5 1.5l5 5.5-5 5.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>`, "0 0 8 14"),
  search: svg(html`<circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" stroke-width="2.2" fill="none"/><path d="M15.5 15.5L20 20" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>`),
  xcircle: svg(html`<circle cx="12" cy="12" r="10"/><path d="M8.5 8.5l7 7M15.5 8.5l-7 7" stroke="var(--bg-elevated, #fff)" stroke-width="2" stroke-linecap="round"/>`),
  x: svg(html`<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>`),
  gear: svg(html`<path d="M12 8.6a3.4 3.4 0 100 6.8 3.4 3.4 0 000-6.8zm8.6 4.6l1.8 1.4-2 3.5-2.2-.8a7.6 7.6 0 01-1.9 1.1l-.3 2.3h-4l-.3-2.3a7.6 7.6 0 01-1.9-1.1l-2.2.8-2-3.5 1.8-1.4a7.7 7.7 0 010-2.3L1.6 9.4l2-3.5 2.2.8a7.6 7.6 0 011.9-1.1L8 3.3h4l.3 2.3c.7.3 1.3.6 1.9 1.1l2.2-.8 2 3.5-1.8 1.4c.1.8.1 1.6 0 2.4z" fill-rule="evenodd"/>`),
  listen: svg(html`<path d="M12 3a8 8 0 00-8 8v5.5A2.5 2.5 0 006.5 19H8a1 1 0 001-1v-5a1 1 0 00-1-1H6v-1a6 6 0 0112 0v1h-2a1 1 0 00-1 1v5a1 1 0 001 1h1.5a2.5 2.5 0 002.5-2.5V11a8 8 0 00-8-8z"/>`),
  channels: svg(html`<rect x="3" y="3" width="8" height="8" rx="2.4"/><rect x="13" y="3" width="8" height="8" rx="2.4"/><rect x="3" y="13" width="8" height="8" rx="2.4"/><rect x="13" y="13" width="8" height="8" rx="2.4"/>`),
  back15: svg(html`<path d="M12 5V2L7.5 5.5 12 9V6.5a6.5 6.5 0 11-6.5 6.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/><text x="12" y="16.6" font-size="7" font-weight="700" text-anchor="middle" font-family="-apple-system,system-ui,sans-serif">15</text>`),
  fwd30: svg(html`<path d="M12 5V2l4.5 3.5L12 9V6.5a6.5 6.5 0 106.5 6.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/><text x="12" y="16.6" font-size="7" font-weight="700" text-anchor="middle" font-family="-apple-system,system-ui,sans-serif">30</text>`),
  verified: svg(html`<path d="M12 1.5l2.4 1.8 3 .1.9 2.8 2.4 1.8-.9 2.9.9 2.9-2.4 1.8-.9 2.8-3 .1L12 22.5l-2.4-1.8-3-.1-.9-2.8-2.4-1.8.9-2.9-.9-2.9 2.4-1.8.9-2.8 3-.1z"/><path d="M8 12.2l2.8 2.8L16.3 9.5" stroke="var(--bg, #fff)" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`),
  waveform: svg(html`<rect x="2.5" y="10" width="2.2" height="4" rx="1.1"/><rect x="6.6" y="6.5" width="2.2" height="11" rx="1.1"/><rect x="10.8" y="3" width="2.2" height="18" rx="1.1"/><rect x="15" y="7.5" width="2.2" height="9" rx="1.1"/><rect x="19.2" y="10" width="2.2" height="4" rx="1.1"/>`),
  warn: svg(html`<path d="M12 2.8c.6 0 1.1.3 1.4.8l8.6 15.2c.6 1.1-.2 2.4-1.4 2.4H3.4c-1.2 0-2-1.3-1.4-2.4l8.6-15.2c.3-.5.8-.8 1.4-.8zm0 13.6a1.3 1.3 0 100 2.6 1.3 1.3 0 000-2.6zm0-8.2c-.8 0-1.3.6-1.2 1.4l.4 5c0 .5.4.8.8.8s.8-.3.8-.8l.4-5c.1-.8-.4-1.4-1.2-1.4z"/>`),
  refresh: svg(html`<path d="M20 11a8 8 0 10-2.3 5.7M20 4.5V11h-6.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>`),
  bell: svg(html`<path d="M12 2.5a6.5 6.5 0 00-6.5 6.5v4.2L3.8 16a1 1 0 00.9 1.5h14.6a1 1 0 00.9-1.5l-1.7-2.8V9A6.5 6.5 0 0012 2.5zM9.2 19a2.9 2.9 0 005.6 0z"/>`),
};
