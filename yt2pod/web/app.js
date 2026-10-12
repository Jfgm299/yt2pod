// yt2pod PWA. Preact + htm, no build step.
import { html, render, useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect } from "/vendor/htm-preact.js";
import { api, relDate, humanDuration, clock, remaining, subscribers, fileSize, initials, tint, Icon, spring, project, rubberband, haptic } from "/lib.js";
import {
  useStore, getState, setState, boot, afterLogin, logout, loadEpisodes, loadSubs, addEpisodes, subscribe,
  unsubscribe, removeEpisode, removePlayed, setPlayed, retry, pollActivity, player, toast, dismissToast,
} from "/store.js";

// --- routing ---------------------------------------------------------------------------
const scrollPos = new Map();
let routeListeners = new Set();
export function navigate(path, { replace = false } = {}) {
  if (path === location.pathname + location.search) return;
  scrollPos.set(location.pathname + location.search, scrollY);
  history[replace ? "replaceState" : "pushState"]({}, "", path);
  routeListeners.forEach((l) => l(false));
}
addEventListener("popstate", () => routeListeners.forEach((l) => l(true)));
function useRoute() {
  const [r, set] = useState({ path: location.pathname, search: location.search, pop: false });
  useEffect(() => {
    const l = (pop) => set({ path: location.pathname, search: location.search, pop });
    routeListeners.add(l);
    return () => routeListeners.delete(l);
  }, []);
  useLayoutEffect(() => { scrollTo(0, r.pop ? scrollPos.get(r.path + r.search) || 0 : 0); }, [r]);
  return r;
}
const link = (path) => (e) => { e.preventDefault(); navigate(path); };

// --- building blocks --------------------------------------------------------------------
function Img({ src, alt = "", seed = "", children }) {
  const [state, set] = useState(src ? "loading" : "error");
  useEffect(() => set(src ? "loading" : "error"), [src]);
  if (state === "error") return html`<div class="placeholder-art" style=${{ background: tint(seed) }}>${children || html`<${Icon.waveform}/>`}</div>`;
  return html`<img src=${src} alt=${alt} loading="lazy" decoding="async" referrerpolicy="no-referrer"
    class=${"fade" + (state === "loaded" ? " loaded" : "")} onLoad=${() => set("loaded")} onError=${() => set("error")}/>`;
}

function Thumb({ ep, showDuration = true, progress = true }) {
  const pct = ep.duration && ep.position && !ep.played ? Math.min(100, (100 * ep.position) / ep.duration) : 0;
  return html`<div class="thumb16">
    <${Img} src=${ep.thumbnail} seed=${ep.channel_id || ep.id}/>
    ${showDuration && ep.duration ? html`<span class="dur">${clock(ep.duration)}</span>` : null}
    ${progress && pct > 0 ? html`<div class="bar"><i style=${{ width: pct + "%" }}></i></div>` : null}
  </div>`;
}

function Avatar({ src, name, size = 52 }) {
  const [failed, setFailed] = useState(!src);
  useEffect(() => setFailed(!src), [src]);
  return html`<div class="avatar" style=${{ width: size, height: size, background: failed ? tint(name) : undefined, fontSize: size * 0.36 }}>
    ${failed ? initials(name) : html`<img src=${src} alt="" loading="lazy" referrerpolicy="no-referrer" onError=${() => setFailed(true)}/>`}
  </div>`;
}

/** A screen with an iOS-style navigation bar. Its title fades in once the large title (or, with
 * revealAt, that many px of content) has scrolled under the bar. */
function Page({ title, large = true, revealAt = 0, left, right, children }) {
  const sentinel = useRef();
  const [scrolled, setScrolled] = useState(false);
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    const onScroll = () => { setScrolled(scrollY > 2); setRevealed(large ? false : scrollY > revealAt); };
    onScroll();
    addEventListener("scroll", onScroll, { passive: true });
    if (!large) return () => removeEventListener("scroll", onScroll);
    const io = new IntersectionObserver(([e]) => setRevealed(!e.isIntersecting), { rootMargin: "-52px 0px 0px 0px" });
    io.observe(sentinel.current);
    return () => { io.disconnect(); removeEventListener("scroll", onScroll); };
  }, [large, revealAt]);
  return html`<div class="page">
    <header class=${"navbar" + (scrolled ? " scrolled" : "") + (revealed || (!large && !revealAt) ? " always" : "")}>
      <div class="navbar-row">
        <div class="navbar-left">${left}</div>
        <div class="navbar-title clamp1">${title}</div>
        <div class="navbar-right">${right}</div>
      </div>
    </header>
    ${large ? html`<h1 class="t-large large-title">${title}</h1>` : null}
    <div ref=${sentinel} style="height:1px"></div>
    ${children}
  </div>`;
}

const BackButton = ({ label = "Atrás" }) => html`<button class="nav-btn back" onClick=${() => (history.length > 1 ? history.back() : navigate("/"))}>
  <${Icon.back}/>${label}</button>`;

function Ring({ value }) {
  const r = 16, c = 2 * Math.PI * r;
  const spin = value == null;
  return html`<svg class=${"ring" + (spin ? " spin" : "")} viewBox="0 0 36 36">
    <circle class="track" cx="18" cy="18" r=${r}/>
    <circle class="val" cx="18" cy="18" r=${r} stroke-dasharray=${c} stroke-dashoffset=${spin ? c * 0.72 : c * (1 - Math.max(0.03, value / 100))}/>
  </svg>`;
}

/** Trailing control of an episode: play/pause, download progress, or retry. */
function EpisodeAction({ ep }) {
  const p = useStore((s) => s.player);
  const isCurrent = p.id === ep.id;
  const st = ep.download_status;
  if (st === "queued" || st === "downloading") {
    const label = st === "queued" ? "En cola" : `Descargando ${Math.round(ep.download_progress || 0)} %`;
    return html`<span class="round" role="img" aria-label=${label} title=${label}>
      <${Ring} value=${st === "downloading" && ep.download_progress ? ep.download_progress : null}/><span class="stopsq" style="opacity:.0"></span></span>`;
  }
  if (st === "failed" || st === "gave_up") {
    return html`<button class="round" style="color:var(--danger)" aria-label="Reintentar descarga" title=${ep.download_error || ""}
      onClick=${(e) => { e.stopPropagation(); retry(ep); }}><${Icon.refresh} class="glyph"/></button>`;
  }
  const playing = isCurrent && p.playing;
  return html`<button class=${"round" + (isCurrent ? " done" : "")} aria-label=${playing ? "Pausar" : "Reproducir"}
    onClick=${(e) => { e.stopPropagation(); isCurrent ? player.toggle() : player.load(ep); }}>
    ${playing ? html`<${Icon.pause} class="glyph"/>` : html`<${Icon.play} class="glyph" style="margin-left:2px"/>`}
  </button>`;
}

function statusLine(ep) {
  const st = ep.download_status;
  if (st === "queued") return html`<span>En cola</span>`;
  if (st === "downloading") return html`<span class="num">Descargando… ${ep.download_progress ? Math.round(ep.download_progress) + " %" : ""}</span>`;
  if (st === "failed") return html`<span class="warn">${ep.download_error || "Error al descargar"} · se reintentará</span>`;
  if (st === "gave_up") return html`<span class="warn">${ep.download_error || "No se pudo descargar"}</span>`;
  if (ep.played) return html`<span>Escuchado</span>`;
  if (ep.position > 5) return html`<span>${remaining(ep)}</span>`;
  return html`<span>${humanDuration(ep.duration)}</span>`;
}

function EpisodeRow({ ep, showChannel = true }) {
  const open = () => (ep.download_status === "done" ? player.load(ep) : null);
  return html`<div class=${"ep" + (ep.played ? " played" : "")}>
    <div onClick=${open} style="cursor:pointer"><${Thumb} ep=${ep}/></div>
    <div class="body" onClick=${open}>
      ${showChannel ? html`<span class="kicker clamp1">${ep.channel_title}</span>` : null}
      <span class="title clamp2">${ep.title}</span>
      <span class="meta dots"><span>${relDate(ep.published || ep.added_at)}</span>${statusLine(ep)}${ep.download_status === "done" && ep.filesize ? html`<span>${fileSize(ep.filesize)}</span>` : null}</span>
    </div>
    <div class="trail">
      <${EpisodeAction} ep=${ep}/>
      <button class="more-btn" aria-label="Más opciones" onClick=${() => openEpisodeActions(ep)}><${Icon.more}/></button>
    </div>
  </div>`;
}

function SkeletonRows({ n = 4 }) {
  return html`<div class="ep-list">${Array.from({ length: n }, (_, i) => html`<div class="ep" key=${i}>
    <div class="thumb16 skel"></div>
    <div class="body"><div class="skel" style="height:12px;width:40%"></div><div class="skel" style="height:15px;width:90%;margin-top:6px"></div><div class="skel" style="height:12px;width:30%;margin-top:6px"></div></div><div></div></div>`)}</div>`;
}

function Empty({ icon, title, text, children }) {
  return html`<div class="empty">${icon ? html`<${icon} class="icon"/>` : null}
    <div class="t-title3">${title}</div><p class="dim t-sub">${text}</p>${children}</div>`;
}

function Segmented({ options, value, onChange, label }) {
  const idx = Math.max(0, options.findIndex((o) => o.value === value));
  return html`<div class="segmented" role="radiogroup" aria-label=${label}>
    <span class="thumb" style=${{ width: `calc((100% - 4px) / ${options.length})`, transform: `translateX(${idx * 100}%)` }}></span>
    ${options.map((o) => html`<button role="radio" aria-checked=${o.value === value} class=${o.value === value ? "on" : ""}
      onClick=${() => { if (o.value !== value) { haptic(4); onChange(o.value); } }}>${o.label}</button>`)}
  </div>`;
}

function Switch({ checked, onChange, label }) {
  return html`<label class="switch"><input type="checkbox" role="switch" aria-label=${label} checked=${checked}
    onChange=${(e) => { haptic(4); onChange(e.target.checked); }}/><span></span></label>`;
}

// --- screens: Listen ------------------------------------------------------------------------
function ListenScreen() {
  const episodes = useStore((s) => s.episodes);
  const loaded = useStore((s) => s.episodesLoaded);
  const subs = useStore((s) => s.subs);
  const [showPlayed, setShowPlayed] = useState(false);

  const { cont, fresh, played, blocked } = useMemo(() => {
    const cont = episodes.filter((e) => !e.played && e.position > 5 && e.download_status === "done")
      .sort((a, b) => (b.progress_at || "").localeCompare(a.progress_at || "")).slice(0, 10);
    const inCont = new Set(cont.map((e) => e.id));
    return {
      cont,
      fresh: episodes.filter((e) => !e.played && !inCont.has(e.id)),
      played: episodes.filter((e) => e.played),
      blocked: episodes.some((e) => /bot/i.test(e.download_error || "")),
    };
  }, [episodes]);

  return html`<${Page} title="Escuchar">
    ${blocked ? html`<div class="banner" style="margin-top:6px"><${Icon.warn}/><div>
      <strong>YouTube está bloqueando las descargas.</strong> Se arregla subiendo tus cookies de YouTube. <a href="/ajustes" onClick=${link("/ajustes")}>Ir a Ajustes</a></div></div>` : null}

    ${cont.length ? html`<section class="section" style="margin-top:8px">
      <div class="section-head"><h2 class="t-title3">Continuar</h2></div>
      <div class="shelf">${cont.map((ep) => html`<button class="card" key=${ep.id} onClick=${() => player.load(ep)}>
        <div style="position:relative"><${Thumb} ep=${ep} showDuration=${false}/>
          <span class="play-chip"><${Icon.play}/>${remaining(ep)}</span></div>
        <div><div class="t-cap dim clamp1" style="font-weight:600;text-transform:uppercase;letter-spacing:.03em">${ep.channel_title}</div>
        <div class="t-sub clamp2" style="font-weight:600">${ep.title}</div></div>
      </button>`)}</div>
    </section>` : null}

    <section class="section" style=${cont.length ? "" : "margin-top:8px"}>
      ${cont.length || fresh.length ? html`<div class="section-head"><h2 class="t-title3">Nuevos episodios</h2></div>` : null}
      ${!loaded && !episodes.length ? html`<${SkeletonRows}/>`
        : fresh.length ? html`<div class="ep-list">${fresh.map((ep) => html`<${EpisodeRow} key=${ep.id} ep=${ep}/>`)}</div>`
        : !subs.length && !episodes.length ? html`<${Empty} icon=${Icon.listen} title="Escucha tus canales como podcasts"
              text="Sigue un canal y yt2pod descargará el audio de cada vídeo nuevo. Aparecerán aquí, listos para escuchar.">
              <button class="btn pill" style="margin-top:10px" onClick=${() => navigate("/buscar")}>Buscar canales</button><//>`
        : html`<${Empty} title="Estás al día" text="Cuando tus canales suban vídeos nuevos aparecerán aquí."/>`}
    </section>

    ${played.length ? html`<section class="section">
      <button class="link-btn small" onClick=${() => setShowPlayed(!showPlayed)}>${showPlayed ? "Ocultar escuchados" : `Ver escuchados (${played.length})`}</button>
      ${showPlayed ? html`<div class="ep-list" style="margin-top:6px">${played.map((ep) => html`<${EpisodeRow} key=${ep.id} ep=${ep}/>`)}</div>` : null}
    </section>` : null}
  <//>`;
}

// --- screens: Channels -----------------------------------------------------------------------
function ChannelsScreen() {
  const subs = useStore((s) => s.subs);
  const loaded = useStore((s) => s.subsLoaded);
  const checkNow = async () => { await api("/api/check", { method: "POST" }); toast("Buscando vídeos nuevos…"); setTimeout(() => loadEpisodes().then(loadSubs), 8000); };
  return html`<${Page} title="Canales" right=${html`
      <button class="nav-btn" aria-label="Buscar vídeos nuevos ahora" title="Buscar vídeos nuevos ahora" onClick=${checkNow}><${Icon.refresh}/></button>
      <button class="nav-btn" aria-label="Añadir canal" onClick=${() => navigate("/buscar")}><${Icon.plus}/></button>`}>
    ${!loaded && !subs.length ? html`<div class="ch-grid">${[0, 1, 2, 3].map((i) => html`<div class="ch-tile" key=${i}><div class="avatar skel" style="width:88px;height:88px"></div><div class="skel" style="height:12px;width:70%"></div></div>`)}</div>`
      : !subs.length ? html`<${Empty} icon=${Icon.channels} title="Aún no sigues ningún canal" text="Busca un canal por su nombre o pega su enlace de YouTube.">
          <button class="btn pill" style="margin-top:10px" onClick=${() => navigate("/buscar")}>Buscar canales</button><//>`
      : html`<div class="ch-grid">${subs.map((c) => html`<a class="ch-tile" key=${c.id} href=${"/canal/" + c.id} onClick=${link("/canal/" + c.id)}>
          <${Avatar} src=${c.avatar} name=${c.title} size=${88}/>
          ${c.unplayed ? html`<span class="badge" aria-label=${c.unplayed + " sin escuchar"}>${c.unplayed}</span>` : null}
          <span class="name clamp2" style="color:var(--label)">${c.title}</span>
        </a>`)}</div>`}
  <//>`;
}

// --- screens: Search ---------------------------------------------------------------------------
const searchMemo = { q: "", results: [] };
function SearchScreen() {
  const [q, setQ] = useState(searchMemo.q);
  const [results, setResults] = useState(searchMemo.results);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const subs = useStore((s) => s.subs);
  const input = useRef();
  const followed = useMemo(() => new Set(subs.map((s) => s.id)), [subs]);

  useEffect(() => { if (!q && matchMedia("(min-width: 900px)").matches) input.current?.focus(); }, []);
  useEffect(() => {
    searchMemo.q = q;
    const term = q.trim();
    if (term.length < 2) { setResults([]); setLoading(false); setError(null); return; }
    const ctrl = new AbortController();
    // Long enough not to fire on every keystroke, short enough to feel live.
    const t = setTimeout(async () => {
      setLoading(true); setError(null);
      try {
        const r = await api("/api/search?q=" + encodeURIComponent(term), { signal: ctrl.signal });
        setResults(r); searchMemo.results = r;
      } catch (e) { if (e.name !== "AbortError") setError(e.message); }
      finally { if (!ctrl.signal.aborted) setLoading(false); }
    }, 350);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [q]);

  const follow = async (c) => {
    try { await subscribe(c); toast(`Sigues a ${c.title}`); } catch (e) { toast(e.message); }
  };

  return html`<${Page} title="Buscar">
    <div class="searchbar" style="margin:2px 0 10px">
      <${Icon.search} class="glass"/>
      <input ref=${input} class="field" type="search" inputmode="search" enterkeyhint="search" autocomplete="off" autocorrect="off"
        spellcheck=${false} placeholder="Canal, @usuario o enlace" aria-label="Buscar canales" value=${q} onInput=${(e) => setQ(e.target.value)}/>
      ${loading ? html`<span class="clear"><span class="spinner" style="width:16px;height:16px;border-width:2px"></span></span>`
        : q ? html`<button class="clear" aria-label="Borrar" onClick=${() => { setQ(""); input.current.focus(); }}><${Icon.xcircle} style="width:18px;height:18px"/></button>` : null}
    </div>
    ${error ? html`<div class="banner"><${Icon.warn}/><div>${error}</div></div>` : null}
    ${q.trim().length < 2 ? html`
      ${subs.length ? html`<section class="section" style="margin-top:14px"><div class="section-head"><h2 class="t-headline">Tus canales</h2></div>
        <div>${subs.slice(0, 8).map((c) => html`<${ResultRow} key=${c.id} c=${c} following=${true}/>`)}</div></section>`
      : html`<p class="dim t-sub" style="margin:18px 4px">Escribe el nombre de un canal, su @usuario o pega el enlace de cualquiera de sus páginas.</p>`}`
    : html`<div class=${"results" + (loading ? " loading" : "")}>
        ${loading && !results.length ? [0, 1, 2].map((i) => html`<div class="result" key=${i}><div class="avatar skel" style="width:52px;height:52px"></div><div><div class="skel" style="height:15px;width:60%"></div><div class="skel" style="height:12px;width:40%;margin-top:6px"></div></div></div>`)
          : results.map((c) => html`<${ResultRow} key=${c.id} c=${c} following=${followed.has(c.id)} onFollow=${() => follow(c)}/>`)}
        ${!loading && !error && results.length === 0 ? html`<p class="dim t-sub" style="margin:18px 4px">Ningún canal coincide con «${q.trim()}».</p>` : null}
      </div>`}
  <//>`;
}

function ResultRow({ c, following, onFollow }) {
  const go = () => navigate("/canal/" + c.id);
  return html`<div class="result">
    <div onClick=${go} style="cursor:pointer"><${Avatar} src=${c.avatar} name=${c.title}/></div>
    <div class="info" onClick=${go}>
      <div class="t-headline clamp1">${c.title}${c.verified ? html`<${Icon.verified} class="verified"/>` : null}</div>
      <div class="t-foot dim clamp1">${[c.handle, subscribers(c.subscribers)].filter(Boolean).join(" · ")}</div>
    </div>
    ${following ? html`<button class="btn small gray" onClick=${go}>Siguiendo</button>`
      : onFollow ? html`<button class="btn small tinted" onClick=${onFollow}>Seguir</button>` : null}
  </div>`;
}

// --- screens: Channel --------------------------------------------------------------------------
const channelCache = new Map();
const DURATIONS = [{ value: 0, label: "Todos" }, { value: 10, label: "+10 min" }, { value: 20, label: "+20 min" }, { value: 45, label: "+45 min" }];
const VIEWS = [{ value: "all", label: "Todos" }, { value: "long", label: "Largos" }, { value: "short", label: "Cortos" }];
const LONG = 20 * 60;

function ChannelScreen({ id }) {
  const [data, setData] = useState(channelCache.get(id) || null);
  const [error, setError] = useState(null);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [view, setView] = useState("all");
  const episodes = useStore((s) => s.episodes);
  const sub = useStore((s) => s.subs.find((x) => x.id === id));

  const load = useCallback(async (refresh = false) => {
    try {
      const d = await api(`/api/channels/${id}${refresh ? "?refresh=1" : ""}`);
      channelCache.set(id, d); setData(d); setError(null);
    } catch (e) { setError(e.message); }
  }, [id]);
  useEffect(() => { setData(channelCache.get(id) || null); load(); }, [id]);
  // Keep download state in sync with the library (the store polls while things download).
  const byId = useMemo(() => new Map(episodes.map((e) => [e.id, e])), [episodes]);
  useEffect(() => { if (data) load(); }, [episodes.length]);

  if (!data) {
    return html`<${Page} title="" large=${false} left=${html`<${BackButton}/>`}>
      ${error ? html`<${Empty} icon=${Icon.warn} title="No se pudo abrir el canal" text=${error}><button class="btn pill" onClick=${() => load()}>Reintentar</button><//>`
        : html`<div class="ch-hero"><div class="avatar skel" style="width:112px;height:112px"></div><div class="skel" style="height:24px;width:180px"></div></div><${SkeletonRows} n=${5}/>`}
    <//>`;
  }
  const ch = data.channel;
  const following = !!sub;
  const videos = data.videos.map((v) => {
    const ep = byId.get(v.id);
    return ep ? { ...v, ...ep, state: "active" } : v.state === "active" ? { ...v, state: null } : v;
  });
  const shown = videos.filter((v) => view === "all" || (view === "long" ? (v.duration || 0) >= LONG : v.duration && v.duration < LONG));
  const addable = shown.filter((v) => v.state !== "active");

  const toggle = (v) => {
    if (v.state === "active") return;
    const next = new Set(selected);
    next.has(v.id) ? next.delete(v.id) : next.add(v.id);
    haptic(4);
    setSelected(next);
  };
  const add = async (ids) => {
    try {
      await addEpisodes(ids);
      toast(ids.length === 1 ? "Añadido a Escuchar" : `${ids.length} episodios añadidos a Escuchar`);
      setSelecting(false); setSelected(new Set()); load();
    } catch (e) { toast(e.message); }
  };
  const follow = async () => {
    try {
      await subscribe(ch, { auto_download: true, rules: { min_minutes: 0 } });
      toast("Te bajaremos sus próximos vídeos");
    } catch (e) { toast(e.message); }
  };
  const channelMenu = () => openActions({
    title: ch.title,
    actions: [
      { label: "Actualizar lista de vídeos", run: () => load(true) },
      { label: "Abrir en YouTube", run: () => open(ch.url || `https://www.youtube.com/channel/${ch.id}`, "_blank", "noopener") },
      ...(following ? [{ label: "Dejar de seguir", destructive: true, run: async () => { await unsubscribe(ch.id); toast(`Has dejado de seguir a ${ch.title}`); } }] : []),
    ],
  });

  return html`<${Page} title=${ch.title} large=${false} revealAt=${150} left=${html`<${BackButton}/>`}
      right=${html`<button class="nav-btn" aria-label="Más opciones" onClick=${channelMenu}><${Icon.more}/></button>`}>
    <div class="ch-hero">
      <${Avatar} src=${ch.avatar} name=${ch.title} size=${112}/>
      <div>
        <h1 class="t-title2" style="margin:0">${ch.title}</h1>
        <div class="t-sub dim">${[ch.handle, subscribers(ch.subscribers)].filter(Boolean).join(" · ")}</div>
        ${ch.description ? html`<p class="ch-desc clamp2">${ch.description}</p>` : null}
        <div class="actions">
          ${following ? html`<button class="btn pill gray" onClick=${channelMenu}><${Icon.check} style="width:16px;height:16px"/>Siguiendo</button>`
            : html`<button class="btn pill" onClick=${follow}><${Icon.plus} style="width:16px;height:16px"/>Seguir</button>`}
        </div>
      </div>
    </div>

    ${following ? html`<${AutoDownload} sub=${sub}/>` : null}

    <section class="section">
      <div class="section-head">
        <h2 class="t-title3">Vídeos</h2>
        ${addable.length ? html`<button class="link-btn small" onClick=${() => { setSelecting(!selecting); setSelected(new Set()); }}>${selecting ? "Cancelar" : "Seleccionar"}</button>` : null}
      </div>
      <div style="margin-bottom:6px"><${Segmented} label="Filtrar vídeos" options=${VIEWS} value=${view} onChange=${setView}/></div>
      ${selecting ? html`<div class="chips" style="margin:10px 0 4px">
        <button class="chip" onClick=${() => setSelected(new Set(addable.map((v) => v.id)))}>Todos (${addable.length})</button>
        <button class="chip" onClick=${() => setSelected(new Set(addable.filter((v) => (v.duration || 0) >= LONG).map((v) => v.id)))}>Solo los de +20 min</button>
        ${selected.size ? html`<button class="chip" onClick=${() => setSelected(new Set())}>Ninguno</button>` : null}
      </div>` : null}
      <div class="ep-list">
        ${shown.map((v) => html`<${VideoRow} key=${v.id} v=${v} selecting=${selecting} selected=${selected.has(v.id)}
            onToggle=${() => toggle(v)} onAdd=${() => add([v.id])}/>`)}
      </div>
      ${!shown.length ? html`<p class="dim t-sub" style="margin:16px 4px">No hay vídeos en esta vista.</p>` : null}
    </section>
    ${selecting ? html`<div style="height:72px" aria-hidden="true"></div>` : null}
    ${selecting && selected.size ? html`<div class="selbar">
      <span class="t-sub" style="font-weight:600">${selected.size} seleccionado${selected.size === 1 ? "" : "s"}</span>
      <button class="btn small" onClick=${() => add([...selected])}>Añadir a Escuchar</button>
    </div>` : null}
  <//>`;
}

function VideoRow({ v, selecting, selected, onToggle, onAdd }) {
  const inLib = v.state === "active";
  const click = () => (selecting ? onToggle() : inLib && v.download_status === "done" ? player.load(v) : null);
  const meta = [relDate(v.published), v.duration ? humanDuration(v.duration) : null].filter(Boolean).join(" · ");
  return html`<div class=${"ep" + (selecting ? " selecting" : "") + (selecting && inLib ? " dimmed" : "") + (v.played ? " played" : "")}
      onClick=${selecting ? click : undefined} role=${selecting ? "checkbox" : undefined} aria-checked=${selecting ? selected : undefined}>
    ${selecting ? html`<span class=${"check" + (selected ? " on" : "")}>${inLib ? null : html`<${Icon.check}/>`}</span>` : null}
    <div onClick=${selecting ? undefined : click} style="cursor:pointer"><${Thumb} ep=${v}/></div>
    <div class="body" onClick=${selecting ? undefined : click}>
      <span class="title clamp2">${v.title}</span>
      <span class="meta">${meta}
        ${inLib && (v.download_status !== "done" || v.played || v.position > 5) ? html`<span aria-hidden="true">·</span>${statusLine(v)}`
          : v.state === "skipped" ? html`<span class="note">· No se bajó: ${v.reason}</span>` : null}
      </span>
    </div>
    ${selecting ? null : html`<div class="trail">
      ${inLib ? html`<${EpisodeAction} ep=${v}/>`
        : html`<button class="round add" aria-label="Añadir a Escuchar" onClick=${(e) => { e.stopPropagation(); onAdd(); }}><${Icon.plus} class="glyph"/></button>`}
    </div>`}
  </div>`;
}

/** The per-channel rules, kept deliberately simple: on/off, a minimum length, optional words. */
function AutoDownload({ sub }) {
  const [auto, setAuto] = useState(!!sub.auto_download);
  const [rules, setRules] = useState({ min_minutes: 0, include_words: [], exclude_words: [], skip_shorts: true, ...sub.rules });
  const [showWords, setShowWords] = useState(!!(rules.include_words?.length || rules.exclude_words?.length));
  const timer = useRef();
  const save = (nextAuto, nextRules) => {
    setAuto(nextAuto); setRules(nextRules);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => subscribe({ id: sub.id }, { auto_download: nextAuto, rules: nextRules }).catch((e) => toast(e.message)), 350);
  };
  const durationLabel = rules.min_minutes ? `de más de ${rules.min_minutes} min` : "";
  return html`<section class="section">
    <div class="group">
      <div class="cell">
        <div class="grow"><div>Descargar vídeos nuevos</div>
          <div class="t-foot dim">${auto ? `Cada vídeo nuevo ${durationLabel} aparecerá en Escuchar.` : "Solo se descargará lo que añadas a mano."}</div></div>
        <${Switch} label="Descargar vídeos nuevos" checked=${auto} onChange=${(v) => save(v, rules)}/>
      </div>
      ${auto ? html`<div class="rules">
        <div><label class="t-foot">Duración</label>
          <${Segmented} label="Duración mínima" options=${DURATIONS} value=${rules.min_minutes || 0} onChange=${(v) => save(auto, { ...rules, min_minutes: v })}/></div>
        ${showWords ? html`
          <${WordChips} label="Solo si el título contiene" words=${rules.include_words || []} onChange=${(w) => save(auto, { ...rules, include_words: w })}/>
          <${WordChips} label="Nunca si el título contiene" words=${rules.exclude_words || []} onChange=${(w) => save(auto, { ...rules, exclude_words: w })}/>`
        : html`<button class="link-btn small" style="align-self:flex-start" onClick=${() => setShowWords(true)}>Filtrar por palabras del título…</button>`}
      </div>` : null}
    </div>
  </section>`;
}

function WordChips({ label, words, onChange }) {
  const [draft, setDraft] = useState("");
  const commit = () => {
    const w = draft.trim();
    if (w && !words.some((x) => x.toLowerCase() === w.toLowerCase())) onChange([...words, w]);
    setDraft("");
  };
  return html`<div><label class="t-foot">${label}</label>
    <div class="chips">
      ${words.map((w) => html`<button class="chip" key=${w} aria-label=${"Quitar " + w} onClick=${() => onChange(words.filter((x) => x !== w))}>${w}<${Icon.x} class="x"/></button>`)}
      <input class="chip-input" placeholder="Añadir palabra" value=${draft} enterkeyhint="done"
        onInput=${(e) => setDraft(e.target.value)} onKeyDown=${(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); } }} onBlur=${commit}/>
    </div></div>`;
}

// --- screens: Settings -------------------------------------------------------------------------
let installPrompt = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; });

function SettingsScreen() {
  const user = useStore((s) => s.session.user);
  const [me, setMe] = useState(null);
  const [users, setUsers] = useState(null);
  const [form, setForm] = useState(null); // "password" | "user"
  useEffect(() => {
    api("/api/me").then(setMe).catch(() => {});
    if (user.is_admin) api("/api/users").then(setUsers).catch(() => {});
  }, []);
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const copyRss = async () => { try { await navigator.clipboard.writeText(me.rss_url); toast("Enlace copiado"); } catch { prompt("Copia el enlace", me.rss_url); } };
  const lastCheck = me?.watcher?.last_run ? relDateTime(me.watcher.last_run) : "todavía no";

  return html`<${Page} title="Ajustes">
    <section class="section" style="margin-top:8px">
      <div class="group">
        <div class="cell"><${Avatar} name=${user.username} size=${40}/><div class="grow"><div class="t-headline">${user.username}</div>
          <div class="t-foot dim">Tu progreso se sincroniza en todos tus dispositivos</div></div></div>
        <button class="cell" onClick=${() => setForm(form === "password" ? null : "password")}><span class="grow">Cambiar contraseña</span><${Icon.chev} class="chev"/></button>
        ${form === "password" ? html`<${PasswordForm} done=${() => setForm(null)}/>` : null}
        <button class="cell" onClick=${logout}><span class="grow" style="color:var(--danger)">Cerrar sesión</span></button>
      </div>
    </section>

    ${!standalone ? html`<section class="section">
      <div class="section-head"><h2 class="t-foot dim" style="text-transform:uppercase;letter-spacing:.04em">Instalar la app</h2></div>
      <div class="group"><div class="cell"><div class="grow t-sub">
        ${ios ? html`En el iPhone: pulsa <strong>Compartir</strong> en Safari y luego <strong>Añadir a pantalla de inicio</strong>.`
          : installPrompt ? html`Instálala para abrirla como una app, con su icono y a pantalla completa.`
          : html`En Chrome o Edge, usa <strong>Instalar app</strong> en la barra de direcciones o en el menú. En Android: menú ⋮ → <strong>Añadir a pantalla de inicio</strong>.`}
        </div>${installPrompt ? html`<button class="btn small" onClick=${() => installPrompt.prompt()}>Instalar</button>` : null}</div></div>
    </section>` : null}

    <section class="section">
      <div class="section-head"><h2 class="t-foot dim" style="text-transform:uppercase;letter-spacing:.04em">Otras apps de podcasts</h2></div>
      <div class="group"><div class="cell"><div class="grow"><div>Feed RSS privado</div><div class="t-foot dim clamp1">${me?.rss_url || "…"}</div></div>
        <button class="btn small tinted" disabled=${!me} onClick=${copyRss}>Copiar</button></div></div>
      <p class="foot-note">Pégalo en Overcast, Pocket Casts o Apple Podcasts si prefieres escuchar allí. Es secreto: quien lo tenga puede escuchar tus episodios. El progreso de esas apps no se sincroniza con yt2pod.</p>
    </section>

    <${StorageSection} admin=${user.is_admin}/>

    ${user.is_admin ? html`<${CookiesSection}/>` : null}

    <section class="section">
      <div class="section-head"><h2 class="t-foot dim" style="text-transform:uppercase;letter-spacing:.04em">Servidor</h2></div>
      <div class="group">
        <div class="cell"><span class="grow">Última comprobación</span><span class="cell-value">${lastCheck}</span></div>
        <div class="cell"><span class="grow">Frecuencia</span><span class="cell-value">cada ${me ? humanDuration(me.check_interval) : "…"}</span></div>
        <div class="cell"><span class="grow">Formato de audio</span><span class="cell-value">${me?.audio_format === "m4a" ? "AAC (m4a), sin recodificar" : me?.audio_format || "…"}</span></div>
      </div>
    </section>

    ${users ? html`<section class="section">
      <div class="section-head"><h2 class="t-foot dim" style="text-transform:uppercase;letter-spacing:.04em">Cuentas</h2></div>
      <div class="group">
        ${users.map((u) => html`<div class="cell" key=${u.id}><${Avatar} name=${u.username} size=${32}/><span class="grow">${u.username}</span>${u.is_admin ? html`<span class="cell-value t-foot">Administrador</span>` : null}</div>`)}
        <button class="cell" onClick=${() => setForm(form === "user" ? null : "user")}><span class="grow" style="color:var(--accent)">Añadir cuenta</span></button>
        ${form === "user" ? html`<${NewUserForm} done=${(u) => { setForm(null); if (u) setUsers([...users, u]); }}/>` : null}
      </div>
      <p class="foot-note">Cada cuenta tiene sus canales y su progreso. Si dos cuentas siguen el mismo canal, el audio se descarga una sola vez.</p>
    </section>` : null}

    <p class="foot-note" style="text-align:center;margin:28px 0 8px">yt2pod ${getState().session.version || ""}</p>
  <//>`;
}

/** What the downloaded audio takes, with ways to free space. */
function StorageSection({ admin }) {
  const episodes = useStore((s) => s.episodes);
  const [st, setSt] = useState(null);
  const [list, setList] = useState(false);
  // Re-read the totals only when something that changes them does: a download finishing, a delete, a play.
  const key = episodes.map((e) => `${e.id}${e.download_status}${e.played}`).join();
  useEffect(() => { api("/api/storage").then(setSt).catch(() => {}); }, [key]);
  const clearPlayed = () => openActions({
    title: `${st.played_count === 1 ? "Se borrará 1 episodio ya escuchado" : `Se borrarán ${st.played_count} episodios ya escuchados`} (${fileSize(st.played_bytes)}). No se puede deshacer.`,
    actions: [{ label: "Borrar escuchados", destructive: true, run: async () => {
      try { const n = await removePlayed(); toast(`${n} episodio${n === 1 ? "" : "s"} borrado${n === 1 ? "" : "s"}`); } catch (e) { toast(e.message); }
    } }],
  });
  const biggest = episodes.filter((e) => e.download_status === "done" && e.filesize).sort((a, b) => b.filesize - a.filesize);
  const head = html`<div class="section-head"><h2 class="t-foot dim" style="text-transform:uppercase;letter-spacing:.04em">Almacenamiento</h2></div>`;
  return html`<section class="section">
    ${head}
    <div class="group">
      <div class="cell"><span class="grow">Tus episodios</span><span class="cell-value num">${st ? `${st.count} · ${fileSize(st.bytes) || "0 MB"}` : "…"}</span></div>
      <div class="cell"><div class="grow"><div>Ya escuchados</div><div class="t-foot dim num">${st ? `${st.played_count} · ${fileSize(st.played_bytes) || "0 MB"}` : "…"}</div></div>
        <button class="btn small tinted danger" disabled=${!st?.played_count} onClick=${clearPlayed}>Borrar</button></div>
      ${admin && st ? html`<div class="cell"><span class="grow">Total en el servidor</span><span class="cell-value num">${fileSize(st.server_bytes) || "0 MB"}</span></div>
        <div class="cell"><span class="grow">Espacio libre</span><span class="cell-value num">${fileSize(st.disk_free)}</span></div>` : null}
      ${biggest.length ? html`<button class="cell" onClick=${() => setList(!list)}><span class="grow" style="color:var(--accent)">${list ? "Ocultar episodios" : "Ver episodios por tamaño"}</span><${Icon.chev} class="chev" style=${list ? "transform:rotate(90deg)" : ""}/></button>` : null}
      ${list ? biggest.map((ep) => html`<div class="cell" key=${ep.id}>
        <div class="grow"><div class="clamp1">${ep.title}</div><div class="t-foot dim clamp1">${ep.channel_title}${ep.played ? " · escuchado" : ""}</div></div>
        <span class="cell-value num t-sub">${fileSize(ep.filesize)}</span>
        <button class="icon-btn" style="color:var(--danger)" aria-label=${"Eliminar " + ep.title} onClick=${() => removeEpisode(ep)}><${Icon.xcircle} style="width:22px;height:22px"/></button>
      </div>`) : null}
    </div>
    <p class="foot-note">Borrar un episodio quita su audio del servidor${admin ? " salvo que otra cuenta lo tenga en su lista" : ""}. Puedes volver a añadirlo desde la página del canal.</p>
  </section>`;
}

/** Upload a cookies.txt so YouTube stops treating the server as a bot. */
function CookiesSection() {
  const [st, setSt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [help, setHelp] = useState(false);
  const file = useRef();
  useEffect(() => { api("/api/cookies").then(setSt).catch(() => {}); }, []);
  const upload = async (e) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setBusy(true);
    try {
      const r = await api("/api/cookies", { method: "PUT", body: { content: await f.text() } });
      setSt(r);
      toast(r.retried ? `Cookies guardadas. Reintentando ${r.retried} descarga${r.retried === 1 ? "" : "s"}…` : "Cookies guardadas");
      setTimeout(() => loadEpisodes().then(pollActivity), 1500);
    } catch (err) { toast(err.message); }
    setBusy(false);
  };
  const remove = async () => { try { setSt(await api("/api/cookies", { method: "DELETE" })); toast("Cookies eliminadas"); } catch (e) { toast(e.message); } };
  return html`<section class="section">
    <div class="section-head"><h2 class="t-foot dim" style="text-transform:uppercase;letter-spacing:.04em">Acceso a YouTube</h2></div>
    <div class="group">
      <div class="cell">
        <div class="grow"><div>Cookies de YouTube</div>
          <div class="t-foot dim">${!st ? "…" : st.present ? `Configuradas ${st.updated_at ? relDate(st.updated_at) : ""}` : "Sin configurar"}</div></div>
        <input ref=${file} type="file" accept=".txt,text/plain" style="display:none" onChange=${upload}/>
        <button class="btn small tinted" disabled=${busy} onClick=${() => file.current.click()}>${busy ? "Subiendo…" : st?.present ? "Reemplazar" : "Subir cookies.txt"}</button>
      </div>
      ${st?.present && !st.from_env ? html`<button class="cell" onClick=${remove}><span class="grow" style="color:var(--danger)">Eliminar cookies</span></button>` : null}
      <button class="cell" onClick=${() => setHelp(!help)}><span class="grow">Cómo conseguir el archivo</span><${Icon.chev} class="chev" style=${{ transform: help ? "rotate(90deg)" : "none", transition: "transform 200ms" }}/></button>
      ${help ? html`<div class="cell t-sub" style="display:block;line-height:1.5">
        <ol style="margin:0;padding-left:20px">
          <li>En el ordenador, instala la extensión <strong>Get cookies.txt LOCALLY</strong> (Chrome) o <strong>cookies.txt</strong> (Firefox) y permítela en incógnito.</li>
          <li>Abre una ventana de incógnito y entra en youtube.com con una cuenta de Google secundaria (mejor no la principal).</li>
          <li>En esa pestaña ve a youtube.com/robots.txt y exporta las cookies en formato Netscape.</li>
          <li>Cierra la ventana de incógnito sin cerrar sesión, y sube aquí el archivo. Las descargas bloqueadas se reintentan solas.</li>
        </ol>
        <a href="https://github.com/Jfgm299/yt2pod/blob/main/docs/cookies.md" target="_blank" rel="noopener" style="display:inline-block;margin-top:8px">Guía completa</a></div>` : null}
    </div>
    <p class="foot-note">YouTube trata a muchos servidores en la nube como bots. Con las cookies de una cuenta, yt2pod descarga como si fueras tú. Se guardan solo en tu servidor. Si dejan de funcionar al cabo de unas semanas, vuelve a subirlas.</p>
  </section>`;
}

function relDateTime(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return "ahora mismo";
  if (mins < 60) return `hace ${mins} min`;
  return relDate(iso) + " " + new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
}

function PasswordForm({ done }) {
  const [current, setCurrent] = useState(""), [pw, setPw] = useState(""), [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { await api("/api/me/password", { method: "POST", body: { current, password: pw } }); toast("Contraseña cambiada"); done(); }
    catch (e) { setErr(e.message); }
  };
  return html`<form class="cell" style="flex-direction:column;align-items:stretch;gap:8px" onSubmit=${submit}>
    <input class="field" type="password" autocomplete="current-password" placeholder="Contraseña actual" value=${current} onInput=${(e) => setCurrent(e.target.value)}/>
    <input class="field" type="password" autocomplete="new-password" placeholder="Nueva contraseña (mín. 8)" value=${pw} onInput=${(e) => setPw(e.target.value)}/>
    ${err ? html`<div class="t-foot" style="color:var(--danger)">${err}</div>` : null}
    <button class="btn" disabled=${!current || pw.length < 8}>Guardar</button></form>`;
}

function NewUserForm({ done }) {
  const [u, setU] = useState(""), [pw, setPw] = useState(""), [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { const r = await api("/api/users", { method: "POST", body: { username: u, password: pw } }); toast(`Cuenta ${r.username} creada`); done(r); }
    catch (e) { setErr(e.message); }
  };
  return html`<form class="cell" style="flex-direction:column;align-items:stretch;gap:8px" onSubmit=${submit}>
    <input class="field" autocapitalize="none" autocomplete="off" placeholder="Usuario" value=${u} onInput=${(e) => setU(e.target.value)}/>
    <input class="field" type="password" autocomplete="new-password" placeholder="Contraseña (mín. 8)" value=${pw} onInput=${(e) => setPw(e.target.value)}/>
    ${err ? html`<div class="t-foot" style="color:var(--danger)">${err}</div>` : null}
    <button class="btn" disabled=${u.trim().length < 2 || pw.length < 8}>Crear cuenta</button></form>`;
}

// --- auth -------------------------------------------------------------------------------------
function AuthScreen({ setup }) {
  const [username, setU] = useState(""), [password, setP] = useState(""), [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = await api(setup ? "/api/setup" : "/api/login", { method: "POST", body: { username, password } });
      setState((s) => ({ session: { ...s.session, user: r.user, needs_setup: false } }));
      afterLogin();
    } catch (e) { setErr(e.message); setBusy(false); }
  };
  return html`<div class="auth"><form onSubmit=${submit}>
    <img class="appicon" src="/icons/icon-192.png" alt=""/>
    <h1 class="t-large" style="margin:0">${setup ? "Bienvenido a yt2pod" : "yt2pod"}</h1>
    <p class="dim t-sub" style="margin:0 0 12px">${setup ? "Crea tu cuenta. La usarás para entrar desde el móvil y el ordenador, con tu progreso sincronizado." : "Tus canales de YouTube, como podcasts."}</p>
    <input class="field" autocapitalize="none" autocomplete="username" placeholder="Usuario" aria-label="Usuario" value=${username} onInput=${(e) => setU(e.target.value)}/>
    <input class="field" type="password" autocomplete=${setup ? "new-password" : "current-password"} placeholder=${setup ? "Contraseña (mín. 8 caracteres)" : "Contraseña"}
      aria-label="Contraseña" value=${password} onInput=${(e) => setP(e.target.value)}/>
    <div class="err" role="alert">${err}</div>
    <button class="btn block" disabled=${busy || username.trim().length < 2 || (setup ? password.length < 8 : !password)}>${busy ? html`<span class="spinner" style="border-top-color:currentColor"></span>` : setup ? "Crear cuenta" : "Entrar"}</button>
  </form></div>`;
}

// --- sheets: spring-driven, draggable, interruptible -------------------------------------------
function useSheet(open, onClosed, { draggable = true } = {}) {
  const panel = useRef(), scrim = useRef(), anim = useRef(null), y = useRef(null);
  const height = () => panel.current?.getBoundingClientRect().height || innerHeight;
  const apply = (v) => {
    y.current = v;
    if (panel.current) panel.current.style.transform = `translate3d(0, ${Math.max(v, -40)}px, 0)`;
    if (scrim.current) scrim.current.style.opacity = String(Math.max(0, Math.min(1, 1 - v / height())));
  };
  const animateTo = (to, velocity = 0, opts = {}) => {
    anim.current?.stop();
    const from = y.current ?? height();
    anim.current = spring({ from, to, velocity, damping: opts.damping ?? 1, response: opts.response ?? 0.38, onUpdate: apply,
      onDone: () => { anim.current = null; if (to > 0) onClosed(); } });
  };
  useLayoutEffect(() => {
    if (!open) return;
    apply(height());
    animateTo(0);
    const onKey = (e) => e.key === "Escape" && animateTo(height());
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [open]);
  const close = (velocity = 0) => animateTo(height(), velocity);

  // Drag: 1:1 with the finger from where it was grabbed; velocity decides on release.
  const onPointerDown = (e) => {
    if (!draggable || e.button > 0) return;
    if (e.target.closest("button, input, .scrubber, a")) return;
    anim.current?.stop(); // grab it mid-flight
    const startY = e.clientY, startV = y.current ?? 0;
    const hist = [{ t: performance.now(), y: e.clientY }];
    let dragging = false;
    const el = e.currentTarget;
    const move = (ev) => {
      const dy = ev.clientY - startY;
      if (!dragging && Math.abs(dy) < 8) return;
      if (!dragging) { dragging = true; el.setPointerCapture?.(e.pointerId); }
      const v = startV + dy;
      apply(v < 0 ? rubberband(v, height()) : v);
      hist.push({ t: performance.now(), y: ev.clientY });
      if (hist.length > 6) hist.shift();
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up);
      if (!dragging) { if (startV !== 0) animateTo(0); return; }
      const a = hist[0], b = hist[hist.length - 1];
      const vel = b.t > a.t ? ((b.y - a.y) / (b.t - a.t)) * 1000 : 0;
      const projected = y.current + project(vel);
      if (projected > height() * 0.4 || vel > 900) close(vel);
      else animateTo(0, vel, { damping: 0.8, response: 0.3 }); // came with momentum: allow a little bounce
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", up);
  };
  return { panel, scrim, close, onPointerDown };
}

function PlayerSheet() {
  const open = useStore((s) => s.sheet === "player");
  const [mounted, setMounted] = useState(open);
  useEffect(() => { if (open) setMounted(true); }, [open]);
  const sheet = useSheet(mounted && open, () => { setMounted(false); if (getState().sheet === "player") setState({ sheet: null }); });
  useEffect(() => { if (!open && mounted) sheet.close(); }, [open]);
  if (!mounted) return null;
  return html`<div>
    <div class="scrim" ref=${sheet.scrim} onClick=${() => sheet.close()}></div>
    <div class="sheet" ref=${sheet.panel} role="dialog" aria-modal="true" aria-label="Reproductor" style="transform:translate3d(0,100%,0)">
      <div class="grabber" onPointerDown=${sheet.onPointerDown}><i></i></div>
      <div class="sheet-scroll"><${FullPlayer} onDragStart=${sheet.onPointerDown} close=${() => sheet.close()}/></div>
    </div>
  </div>`;
}

const RATES = [1, 1.25, 1.5, 1.75, 2, 0.8];
function FullPlayer({ onDragStart, close }) {
  const p = useStore((s) => s.player);
  const ep = useStore((s) => s.episodes.find((e) => e.id === s.player.id));
  const [desc, setDesc] = useState(false);
  if (!ep) return null;
  const dur = p.duration || ep.duration || 0;
  const nextRate = RATES[(RATES.indexOf(p.rate) + 1) % RATES.length] || 1;
  return html`<div class="player">
    <div class="art-wrap" onPointerDown=${onDragStart} style="touch-action:none">
      <div class=${"art" + (p.playing ? "" : " paused")}><${Thumb} ep=${ep} showDuration=${false} progress=${false}/></div>
    </div>
    <div class="who">
      <div class="grow">
        <div class="title clamp2">${ep.title}</div>
        <a class="chan" href=${"/canal/" + ep.channel_id} onClick=${(e) => { e.preventDefault(); close(); navigate("/canal/" + ep.channel_id); }}>${ep.channel_title}</a>
      </div>
      <button class="more-btn" aria-label="Más opciones" onClick=${() => openEpisodeActions(ep)}><${Icon.more}/></button>
    </div>
    <div>
      <${Scrubber} position=${p.position} duration=${dur}/>
      <div class="times"><span>${clock(p.position)}</span><span>-${clock(Math.max(0, dur - p.position))}</span></div>
    </div>
    <div class="controls">
      <button class="skip" aria-label="Retroceder 15 segundos" onClick=${() => player.skip(-15)}><${Icon.back15}/></button>
      <button class="big" aria-label=${p.playing ? "Pausar" : "Reproducir"} onClick=${() => player.toggle()}>
        ${p.loading && p.playing ? html`<span class="spinner" style="width:36px;height:36px;border-width:3px"></span>` : p.playing ? html`<${Icon.pause}/>` : html`<${Icon.play} style="margin-left:5px"/>`}
      </button>
      <button class="skip" aria-label="Avanzar 30 segundos" onClick=${() => player.skip(30)}><${Icon.fwd30}/></button>
    </div>
    <div class="bottom">
      <button class="rate-btn" aria-label=${"Velocidad " + p.rate + "x"} onClick=${() => { haptic(4); player.setRate(nextRate); }}>${String(p.rate).replace(".", ",")}×</button>
      <${SleepButton} sleepAt=${p.sleepAt}/>
      <button class="link-btn small" onClick=${() => setPlayed(ep, true)}>Marcar como escuchado</button>
    </div>
    ${ep.description ? html`<div class="desc">${desc ? ep.description : ep.description.slice(0, 280)}${!desc && ep.description.length > 280 ? html`… <button class="link-btn small" onClick=${() => setDesc(true)}>Más</button>` : null}</div>` : null}
  </div>`;
}

const SLEEP = [5, 10, 15, 30, 45, 60, 90];
/** Sleep timer: pause playback after a while. Shows the time left while it runs. */
function SleepButton({ sleepAt }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!sleepAt) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [sleepAt]);
  const left = sleepAt ? Math.max(0, (sleepAt - Date.now()) / 1000) : 0;
  const choose = () => openActions({
    title: sleepAt ? `Se pausará en ${clock(left)}` : "Pausar la reproducción dentro de…",
    actions: [
      ...SLEEP.map((m) => ({ label: m < 60 ? `${m} minutos` : m === 60 ? "1 hora" : `${m / 60} horas`.replace(".", ","), run: () => { player.setSleep(m); toast(`Se pausará en ${m} min`); } })),
      ...(sleepAt ? [{ label: "Desactivar temporizador", destructive: true, run: () => player.setSleep(null) }] : []),
    ],
  });
  return html`<button class=${"rate-btn sleep-btn" + (sleepAt ? " on" : "")} aria-label=${sleepAt ? `Temporizador: quedan ${clock(left)}` : "Temporizador"} onClick=${choose}>
    <${Icon.moon}/>${sleepAt ? html`<span>${clock(left)}</span>` : null}
  </button>`;
}

function Scrubber({ position, duration }) {
  const ref = useRef();
  const [drag, setDrag] = useState(null); // fraction while dragging
  const frac = drag ?? (duration ? position / duration : 0);
  const at = (clientX) => {
    const r = ref.current.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width));
  };
  const down = (e) => {
    if (!duration) return;
    e.stopPropagation();
    ref.current.setPointerCapture(e.pointerId);
    setDrag(at(e.clientX));
  };
  const move = (e) => { if (drag != null) setDrag(at(e.clientX)); };
  const up = (e) => { if (drag != null) { player.seek(at(e.clientX) * duration); setDrag(null); } };
  const key = (e) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); player.skip(-15); }
    if (e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); player.skip(30); }
  };
  return html`<div class=${"scrubber" + (drag != null ? " active" : "")} ref=${ref} role="slider" tabindex="0"
      aria-label="Posición" aria-valuemin="0" aria-valuemax=${Math.round(duration)} aria-valuenow=${Math.round(frac * duration)} aria-valuetext=${clock(frac * duration)}
      onPointerDown=${down} onPointerMove=${move} onPointerUp=${up} onPointerCancel=${() => setDrag(null)} onKeyDown=${key}>
    <div class="track"><i style=${{ width: frac * 100 + "%" }}></i></div>
    ${drag != null ? html`<span class="sr" aria-live="polite">${clock(drag * duration)}</span>` : null}
  </div>`;
}

function MiniPlayer() {
  const p = useStore((s) => s.player);
  const ep = useStore((s) => s.episodes.find((e) => e.id === s.player.id));
  if (!p.id || !ep) return null;
  const dur = p.duration || ep.duration || 0;
  const openSheet = () => setState({ sheet: "player" });
  return html`<div class="mini" role="region" aria-label="Reproduciendo">
    <div class="art" onClick=${openSheet}><${Thumb} ep=${ep} showDuration=${false} progress=${false}/></div>
    <div class="txt" onClick=${openSheet}>
      <div class="t-sub clamp1" style="font-weight:600">${ep.title}</div>
      <div class="t-foot dim clamp1">${ep.channel_title}</div>
    </div>
    <div class="scrub">
      <span class="t-cap dim num">${clock(p.position)}</span>
      <${Scrubber} position=${p.position} duration=${dur}/>
      <span class="t-cap dim num">-${clock(Math.max(0, dur - p.position))}</span>
      <button class="rate-btn" style="height:28px;min-width:46px;font-size:.8125rem" onClick=${() => player.setRate(RATES[(RATES.indexOf(p.rate) + 1) % RATES.length] || 1)}>${String(p.rate).replace(".", ",")}×</button>
    </div>
    <button class="icon-btn ctl desk-only" aria-label="Retroceder 15 segundos" onClick=${() => player.skip(-15)}><${Icon.back15}/></button>
    <button class="icon-btn ctl" aria-label=${p.playing ? "Pausar" : "Reproducir"} onClick=${() => player.toggle()}>${p.playing ? html`<${Icon.pause}/>` : html`<${Icon.play}/>`}</button>
    <button class="icon-btn ctl" aria-label="Avanzar 30 segundos" onClick=${() => player.skip(30)}><${Icon.fwd30}/></button>
    <div class="prog"><i style=${{ width: (dur ? (100 * p.position) / dur : 0) + "%" }}></i></div>
  </div>`;
}

// Action sheet (iOS style), used for "…" menus. Opens above whatever is showing, even the player.
export function openActions(spec) { setState({ actions: { ...spec, key: Date.now() } }); }
function openEpisodeActions(ep) {
  openActions({
    title: ep.title,
    actions: [
      ep.played ? { label: "Marcar como no escuchado", run: () => setPlayed(ep, false) } : { label: "Marcar como escuchado", run: () => setPlayed(ep, true) },
      { label: "Ir al canal", run: () => { setState({ sheet: null }); navigate("/canal/" + ep.channel_id); } },
      { label: "Ver en YouTube", run: () => open(`https://www.youtube.com/watch?v=${ep.id}`, "_blank", "noopener") },
      { label: ep.filesize ? `Eliminar descarga (${fileSize(ep.filesize)})` : "Eliminar descarga", destructive: true, run: () => removeEpisode(ep) },
    ],
  });
}

function ActionSheet() {
  const spec = useStore((s) => s.actions);
  const [shown, setShown] = useState(spec);
  useEffect(() => { if (spec) setShown(spec); }, [spec]);
  const sheet = useSheet(!!spec && shown === spec, () => { setShown(null); setState({ actions: null }); });
  if (!shown) return null;
  const choose = (a) => { sheet.close(); setTimeout(a.run, 120); };
  return html`<div key=${shown.key}>
    <div class="scrim" ref=${sheet.scrim} style="z-index:70" onClick=${() => sheet.close()}></div>
    <div class="actions-sheet" ref=${sheet.panel} style="z-index:71;transform:translate3d(0,100%,0)" role="dialog" aria-modal="true" onPointerDown=${sheet.onPointerDown}>
      <div class="group">
        ${shown.title ? html`<div class="head clamp2">${shown.title}</div>` : null}
        ${shown.actions.map((a) => html`<button class=${"act" + (a.destructive ? " destructive" : "")} onClick=${() => choose(a)}>${a.label}</button>`)}
      </div>
      <button class="act cancel" onClick=${() => sheet.close()}>Cancelar</button>
    </div>
  </div>`;
}

function Toast() {
  const t = useStore((s) => s.toast);
  if (!t) return null;
  return html`<div class="toast" key=${t.key} role="status" aria-live="polite">
    <span>${t.text}</span>
    ${t.action ? html`<button class="btn small tinted" onClick=${() => { t.action.run(); dismissToast(); }}>${t.action.label}</button>` : null}
  </div>`;
}

// --- shell --------------------------------------------------------------------------------------
const TABS = [
  { path: "/", label: "Escuchar", icon: Icon.listen },
  { path: "/canales", label: "Canales", icon: Icon.channels },
  { path: "/buscar", label: "Buscar", icon: Icon.search },
  { path: "/ajustes", label: "Ajustes", icon: Icon.gear },
];

function TabBar({ path }) {
  const active = path.startsWith("/canal/") ? "/canales" : path;
  return html`<nav class="tabbar" aria-label="Secciones">
    <div class="brand"><img src="/icons/icon-192.png" alt=""/>yt2pod</div>
    ${TABS.map((t) => html`<a class=${"tab" + (active === t.path ? " on" : "")} href=${t.path} aria-current=${active === t.path ? "page" : undefined}
      onClick=${(e) => { e.preventDefault(); if (active === t.path) scrollTo({ top: 0, behavior: "smooth" }); else navigate(t.path); }}>
      <${t.icon}/><span>${t.label}</span></a>`)}
  </nav>`;
}

function Shell() {
  const route = useRoute();
  const hasPlayer = useStore((s) => !!s.player.id);
  const { path } = route;
  let screen;
  if (path.startsWith("/canal/")) screen = html`<${ChannelScreen} key=${path} id=${decodeURIComponent(path.slice(7))}/>`;
  else if (path === "/canales") screen = html`<${ChannelsScreen}/>`;
  else if (path === "/buscar") screen = html`<${SearchScreen}/>`;
  else if (path === "/ajustes") screen = html`<${SettingsScreen}/>`;
  else screen = html`<${ListenScreen}/>`;
  return html`<div class="shell">
    <main class=${"main" + (hasPlayer ? " has-player" : "")}>${screen}</main>
    <${TabBar} path=${path}/>
    <${MiniPlayer}/>
    <${PlayerSheet}/>
    <${ActionSheet}/>
    <${Toast}/>
  </div>`;
}

function App() {
  const booting = useStore((s) => s.booting);
  const session = useStore((s) => s.session);
  if (booting) return null;
  if (!session?.user) return html`<${AuthScreen} setup=${session?.needs_setup}/><${Toast}/>`;
  return html`<${Shell}/>`;
}

render(html`<${App}/>`, document.getElementById("app"));
boot();

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
