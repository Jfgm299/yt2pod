// App state, data loading and the audio engine. One tiny store, subscribed to with useStore().
import { useEffect, useState, useRef } from "/vendor/htm-preact.js";
import { api, haptic } from "/lib.js";
import { t } from "/i18n.js";

const LS = {
  get(k, d) { try { const v = localStorage.getItem("yt2pod:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("yt2pod:" + k, JSON.stringify(v)); } catch {} },
  del(k) { try { localStorage.removeItem("yt2pod:" + k); } catch {} },
};

let state = {
  booting: true,
  session: null,
  episodes: LS.get("episodes", []),
  episodesLoaded: false,
  subs: LS.get("subs", []),
  subsLoaded: false,
  player: { id: null, playing: false, position: 0, duration: 0, rate: LS.get("rate", 1), loading: false, sleepAt: null },
  sheet: null,      // "player" when the full player is open
  actions: null,    // the open action sheet, if any
  toast: null,
};
const listeners = new Set();

export const getState = () => state;
export function setState(patch) {
  state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  listeners.forEach((l) => l());
}

export function useStore(selector) {
  const [, force] = useState(0);
  const sel = useRef(selector);
  sel.current = selector;
  const last = useRef(selector(state));
  last.current = selector(state);
  useEffect(() => {
    const l = () => {
      const next = sel.current(state);
      if (!Object.is(next, last.current)) { last.current = next; force((n) => n + 1); }
    };
    listeners.add(l);
    return () => listeners.delete(l);
  }, []);
  return last.current;
}

// --- toasts -------------------------------------------------------------------------
let toastTimer;
export function toast(text, action) {
  clearTimeout(toastTimer);
  setState({ toast: { text, action, key: Date.now() } });
  toastTimer = setTimeout(() => setState({ toast: null }), action ? 5000 : 2600);
}
export const dismissToast = () => { clearTimeout(toastTimer); setState({ toast: null }); };

// --- session ----------------------------------------------------------------------------
export async function boot() {
  try {
    const s = await api("/api/session");
    setState({ session: s, booting: false });
    if (s.user) afterLogin();
  } catch (e) {
    // Offline but we were logged in before: open with cached data.
    const cached = LS.get("user", null);
    setState({ session: { user: cached, needs_setup: false, offline: true }, booting: false });
    if (cached) restorePlayer();
  }
}

export function afterLogin() {
  LS.set("user", state.session.user);
  loadEpisodes();
  loadSubs();
  restorePlayer();
  flushPendingProgress();
  pollActivity();
}

export async function logout() {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  player.unload();
  ["episodes", "subs", "user", "last"].forEach(LS.del);
  setState({ session: { user: null, needs_setup: false }, episodes: [], subs: [] });
}

window.addEventListener("yt2pod:unauthorized", () => {
  if (state.session?.user) setState({ session: { user: null, needs_setup: false } });
});

// --- data --------------------------------------------------------------------------------
export async function loadEpisodes() {
  const eps = await api("/api/episodes");
  // Keep the live position of what's playing right now; the server copy lags a few seconds.
  const p = state.player;
  const merged = eps.map((e) => (e.id === p.id && p.playing ? { ...e, position: p.position } : e));
  setState({ episodes: merged, episodesLoaded: true });
  LS.set("episodes", merged.slice(0, 120));
  syncPlayerFromServer(merged);
  return merged;
}

export async function loadSubs() {
  const subs = await api("/api/subscriptions");
  setState({ subs, subsLoaded: true });
  LS.set("subs", subs);
  return subs;
}

export function patchEpisode(id, patch) {
  setState((s) => ({ episodes: s.episodes.map((e) => (e.id === id ? { ...e, ...patch } : e)) }));
}

export async function addEpisodes(ids) {
  const { added } = await api("/api/episodes", { method: "POST", body: { video_ids: ids } });
  haptic();
  await loadEpisodes();
  pollActivity();
  return added;
}

export async function subscribe(channel, { auto_download = true, rules = {} } = {}) {
  await api(`/api/subscriptions/${channel.id}`, { method: "PUT", body: { auto_download, rules } });
  haptic();
  loadSubs();
}

export async function unsubscribe(channelId) {
  await api(`/api/subscriptions/${channelId}`, { method: "DELETE" });
  loadSubs();
}

/** Remove with an undo window: the file is only deleted once the toast is gone. */
export function removeEpisode(ep) {
  const before = state.episodes;
  if (state.player.id === ep.id) player.unload();
  setState({ episodes: before.filter((e) => e.id !== ep.id) });
  let undone = false;
  const timer = setTimeout(() => {
    if (!undone) api(`/api/episodes/${ep.id}`, { method: "DELETE" }).then(loadSubs).catch(() => loadEpisodes());
  }, 5200);
  toast(t("Episodio eliminado"), { label: t("Deshacer"), run: () => { undone = true; clearTimeout(timer); setState({ episodes: before }); } });
}

/** Remove every episode already listened to, after the user confirmed it. */
export async function removePlayed() {
  const { removed } = await api("/api/episodes/remove-played", { method: "POST" });
  if (removed.includes(state.player.id)) player.unload();
  setState({ episodes: state.episodes.filter((e) => !removed.includes(e.id)) });
  loadSubs();
  return removed.length;
}

export async function setPlayed(ep, played) {
  patchEpisode(ep.id, { played: played ? 1 : 0, position: 0 });
  if (state.player.id === ep.id && played) player.pause();
  await api(`/api/episodes/${ep.id}/played`, { method: "POST", body: { played } });
  loadSubs();
}

export async function retry(ep) {
  patchEpisode(ep.id, { download_status: "queued", download_error: null });
  await api(`/api/episodes/${ep.id}/retry`, { method: "POST" });
  pollActivity();
}

// Poll fast while something downloads, slowly otherwise (new uploads found by the server).
let pollTimer;
export async function pollActivity() {
  clearTimeout(pollTimer);
  if (!state.session?.user) return;
  let busy = false;
  try {
    const { downloads } = await api("/api/activity");
    busy = downloads.some((d) => d.status === "queued" || d.status === "downloading");
    const byId = new Map(downloads.map((d) => [d.video_id, d]));
    let finished = false;
    const known = new Set(state.episodes.map((e) => e.id));
    setState((s) => ({
      episodes: s.episodes.map((e) => {
        const d = byId.get(e.id);
        if (!d) return e;
        if (d.status === "done" && e.download_status !== "done") finished = true;
        return { ...e, download_status: d.status, download_progress: d.progress, download_error: d.error };
      }),
    }));
    if (finished || downloads.some((d) => !known.has(d.video_id))) await loadEpisodes();
  } catch {}
  pollTimer = setTimeout(pollActivity, busy ? 1500 : 60000);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.session?.user) {
    loadEpisodes().catch(() => {});
    pollActivity();
    flushPendingProgress();
  }
});

// --- player --------------------------------------------------------------------------------
const audio = new Audio();
audio.preload = "metadata";
let currentEp = null;
let lastSaved = 0;
let pendingSeek = null;

function setPlayer(patch) { setState((s) => ({ player: { ...s.player, ...patch } })); }

function saveProgress({ played, force } = {}) {
  if (!currentEp) return;
  const position = audio.currentTime || state.player.position || 0;
  if (!force && Math.abs(position - lastSaved) < 3 && played === undefined) return;
  lastSaved = position;
  const body = { position, at: new Date().toISOString(), ...(played !== undefined ? { played } : {}) };
  const id = currentEp.id;
  patchEpisode(id, { position, ...(played ? { played: 1 } : {}), progress_at: body.at });
  LS.set("last", { id, position });
  api(`/api/episodes/${id}/progress`, { method: "POST", body, keepalive: true }).catch(() => {
    const pending = LS.get("pending", {});
    pending[id] = body;
    LS.set("pending", pending);
  });
}

async function flushPendingProgress() {
  const pending = LS.get("pending", {});
  for (const [id, body] of Object.entries(pending)) {
    try { await api(`/api/episodes/${id}/progress`, { method: "POST", body }); delete pending[id]; } catch {}
  }
  LS.set("pending", pending);
}

// If another device moved on while this one sat paused, pick up its position.
function syncPlayerFromServer(eps) {
  if (!currentEp || state.player.playing) return;
  const ep = eps.find((e) => e.id === currentEp.id);
  if (!ep) return;
  currentEp = ep;
  if (Math.abs((ep.position || 0) - (audio.currentTime || state.player.position)) > 3) {
    if (audio.readyState > 0) audio.currentTime = ep.position || 0;
    else pendingSeek = ep.position || 0;
    lastSaved = ep.position || 0;
    setPlayer({ position: ep.position || 0 });
  }
}

function restorePlayer() {
  const last = LS.get("last", null);
  if (!last || currentEp) return;
  const ep = state.episodes.find((e) => e.id === last.id);
  if (ep && ep.download_status === "done" && !ep.played) player.load(ep, { autoplay: false });
}

function mediaSession(ep) {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: ep.title, artist: ep.channel_title || "", album: "yt2pod",
    artwork: ep.thumbnail ? [{ src: ep.thumbnail, sizes: "480x360", type: "image/jpeg" }] : [],
  });
  const set = (a, fn) => { try { navigator.mediaSession.setActionHandler(a, fn); } catch {} };
  // No play/pause handlers on purpose: with none, iOS drives the <audio> element itself from the
  // lock screen. A JS handler can't run while iOS has suspended the page, so play did nothing there.
  set("play", null);
  set("pause", null);
  set("seekbackward", (d) => player.skip(-(d.seekOffset || 15)));
  set("seekforward", (d) => player.skip(d.seekOffset || 30));
  set("seekto", (d) => player.seek(d.seekTime));
  set("previoustrack", () => player.skip(-15));
  set("nexttrack", () => player.skip(30));
}

function positionState() {
  try {
    if (audio.duration && isFinite(audio.duration))
      navigator.mediaSession?.setPositionState({ duration: audio.duration, playbackRate: audio.playbackRate, position: Math.min(audio.currentTime, audio.duration) });
  } catch {}
}

// Sleep timer. Checked from a timer and from timeupdate, because a locked phone may pause the
// page's timers while audio keeps playing. The last seconds fade out where volume can be set.
const FADE = 8000;
let sleepTimer = null;
function checkSleep() {
  const at = state.player.sleepAt;
  if (!at) return;
  const left = at - Date.now();
  if (left <= 0) {
    player.setSleep(null);
    audio.pause();
    toast(t("Temporizador: reproducción en pausa"));
  } else if (left < FADE && !audio.paused) {
    audio.volume = Math.max(0.05, left / FADE);
  }
}

let lastTick = 0;
audio.addEventListener("timeupdate", () => {
  const now = performance.now();
  if (now - lastTick < 250) return;
  lastTick = now;
  checkSleep();
  setPlayer({ position: audio.currentTime });
  if (Math.abs(audio.currentTime - lastSaved) >= 15) saveProgress();
});
audio.addEventListener("loadedmetadata", () => {
  if (pendingSeek != null) { audio.currentTime = pendingSeek; pendingSeek = null; }
  setPlayer({ duration: audio.duration, loading: false });
  positionState();
});
audio.addEventListener("play", () => { setPlayer({ playing: true }); navigator.mediaSession && (navigator.mediaSession.playbackState = "playing"); });
audio.addEventListener("pause", () => { audio.volume = 1; setPlayer({ playing: false }); saveProgress({ force: true }); navigator.mediaSession && (navigator.mediaSession.playbackState = "paused"); });
audio.addEventListener("waiting", () => setPlayer({ loading: true }));
audio.addEventListener("playing", () => setPlayer({ loading: false }));
audio.addEventListener("seeked", () => { saveProgress({ force: true }); positionState(); });
audio.addEventListener("ratechange", positionState);
audio.addEventListener("ended", () => { saveProgress({ played: true, force: true }); setPlayer({ playing: false }); });
audio.addEventListener("error", () => {
  if (!currentEp) return;
  setPlayer({ playing: false, loading: false });
  toast(t("No se pudo cargar el audio"));
});
window.addEventListener("pagehide", () => saveProgress({ force: true }));
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") saveProgress({ force: true }); });

export const player = {
  audio,
  load(ep, { autoplay = true } = {}) {
    if (ep.download_status !== "done") { toast(ep.download_status === "failed" || ep.download_status === "gave_up" ? t("La descarga falló; prueba a reintentarla") : t("Aún se está descargando")); return; }
    if (currentEp?.id === ep.id) { if (autoplay) this.play(); return; }
    if (currentEp) saveProgress({ force: true });
    currentEp = ep;
    const start = ep.played || (ep.duration && ep.position > ep.duration - 15) ? 0 : ep.position || 0;
    lastSaved = start;
    pendingSeek = start;
    audio.src = `/api/audio/${ep.id}`;
    audio.playbackRate = state.player.rate;
    audio.defaultPlaybackRate = state.player.rate;
    setPlayer({ id: ep.id, position: start, duration: ep.duration || 0, playing: false, loading: autoplay });
    LS.set("last", { id: ep.id, position: start });
    mediaSession(ep);
    if (autoplay) this.play();
  },
  play() {
    if (!currentEp) return;
    audio.play().catch(() => setPlayer({ playing: false, loading: false }));
  },
  pause() { audio.pause(); },
  toggle() { audio.paused ? this.play() : this.pause(); },
  seek(t) {
    const d = audio.duration || state.player.duration || 0;
    t = Math.max(0, Math.min(d || t, t));
    if (audio.readyState > 0) audio.currentTime = t; else pendingSeek = t;
    setPlayer({ position: t });
  },
  skip(delta) { this.seek((audio.currentTime || state.player.position) + delta); },
  setRate(rate) {
    audio.playbackRate = rate; audio.defaultPlaybackRate = rate;
    LS.set("rate", rate);
    setPlayer({ rate });
  },
  /** Pause after `minutes` (null turns the timer off). */
  setSleep(minutes) {
    clearInterval(sleepTimer);
    audio.volume = 1;
    if (!minutes) { setPlayer({ sleepAt: null }); return; }
    setPlayer({ sleepAt: Date.now() + minutes * 60000 });
    sleepTimer = setInterval(checkSleep, 1000);
    if (audio.paused) this.play();
  },
  unload() {
    this.setSleep(null);
    if (currentEp) saveProgress({ force: true });
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    currentEp = null;
    LS.del("last");
    setPlayer({ id: null, playing: false, position: 0, duration: 0 });
    if (state.sheet === "player") setState({ sheet: null });
  },
};

// Desktop keyboard shortcuts.
document.addEventListener("keydown", (e) => {
  if (!currentEp || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest?.("input, textarea, [contenteditable]")) return;
  if (e.code === "Space") { e.preventDefault(); player.toggle(); }
  else if (e.key === "ArrowLeft") player.skip(-15);
  else if (e.key === "ArrowRight") player.skip(30);
});
