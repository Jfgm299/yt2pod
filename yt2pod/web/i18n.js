// Language and theme. Texts are written in Spanish in the code; t() looks up the English version.
// Both choices are per device (localStorage) and apply before the first paint.

const read = (k) => { try { return localStorage.getItem("yt2pod:" + k); } catch { return null; } };
const write = (k, v) => { try { v == null ? localStorage.removeItem("yt2pod:" + k) : localStorage.setItem("yt2pod:" + k, v); } catch {} };

// --- language -----------------------------------------------------------------------------
export const langPref = ["es", "en"].includes(read("lang")) ? read("lang") : "auto";
export const lang = langPref !== "auto" ? langPref : (navigator.languages || [navigator.language]).some((l) => /^es\b/i.test(l)) ? "es" : "en";
document.documentElement.lang = lang;

/** "auto" follows the browser. The page reloads so every text, date and number switches at once. */
export function setLang(pref) {
  write("lang", pref === "auto" ? null : pref);
  location.reload();
}

/** Translate a Spanish text; {name} placeholders are filled from vars. Unknown texts pass through. */
export function t(es, vars) {
  let s = es == null ? "" : String(es);
  if (lang === "en" && s) s = EN[s] ?? pattern(s) ?? s;
  return vars ? s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? "")) : s;
}

/** t() for a count: picks the singular or plural text and passes {n}. */
export const tn = (n, one, many, vars) => t(n === 1 ? one : many, { n, ...vars });

// Messages the server builds with values inside (download errors, filter reasons).
const PATTERNS = [
  [/^Dura menos de (\d+) min$/, (m) => `Shorter than ${m[1]} min`],
  [/^Dura más de (\d+) min$/, (m) => `Longer than ${m[1]} min`],
  [/^El título contiene «(.*)»$/, (m) => `The title contains “${m[1]}”`],
  [/^YouTube no respondió: (.*)$/s, (m) => `YouTube didn't respond: ${m[1]}`],
  [/^No parece un cookies\.txt de YouTube/, () => "This doesn't look like a YouTube cookies.txt. Export it in Netscape format while on youtube.com and signed in."],
];
function pattern(s) {
  for (const [re, fn] of PATTERNS) { const m = s.match(re); if (m) return fn(m); }
  return null;
}

// --- theme ----------------------------------------------------------------------------------
export const themePref = () => (["light", "dark"].includes(read("theme")) ? read("theme") : "auto");

export function applyTheme(pref = themePref()) {
  const root = document.documentElement;
  if (pref === "auto") root.removeAttribute("data-theme"); else root.dataset.theme = pref;
  const dark = pref === "dark" || (pref === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
    m.removeAttribute("media");
    m.content = dark ? "#000000" : "#f2f2f7";
  });
}
export function setTheme(pref) {
  write("theme", pref === "auto" ? null : pref);
  applyTheme(pref);
}
applyTheme();
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyTheme());

// --- English ----------------------------------------------------------------------------------
const EN = {
  // navigation and common
  "Escuchar": "Listen", "Canales": "Channels", "Buscar": "Search", "Ajustes": "Settings", "Secciones": "Sections",
  "Atrás": "Back", "Cancelar": "Cancel", "Reintentar": "Retry", "Guardar": "Save", "Copiar": "Copy", "Borrar": "Delete",
  "Más opciones": "More options", "Más": "More", "Todos": "All", "Ninguno": "None",
  "Sin conexión con el servidor": "Can't reach the server", "Error {n}": "Error {n}",
  "hoy": "today", "ayer": "yesterday", "quedan {d}": "{d} left", "menos de 1 min": "less than 1 min",
  "{n} suscriptores": "{n} subscribers", "ahora mismo": "just now", "hace {n} min": "{n} min ago", "todavía no": "not yet",

  // episodes
  "En cola": "Queued", "Descargando {n} %": "Downloading {n}%", "Descargando…": "Downloading…",
  "Reintentar descarga": "Retry download", "Error al descargar": "Download failed", "se reintentará": "will retry",
  "No se pudo descargar": "Couldn't download", "Escuchado": "Played", "escuchado": "played",
  "Pausar": "Pause", "Reproducir": "Play", "Episodio eliminado": "Episode deleted", "Deshacer": "Undo",
  "No se pudo cargar el audio": "Couldn't load the audio", "La descarga falló; prueba a reintentarla": "The download failed; try again",
  "Aún se está descargando": "Still downloading",
  "Marcar como escuchado": "Mark as played", "Marcar como no escuchado": "Mark as unplayed", "Ir al canal": "Go to channel",
  "Ver en YouTube": "Watch on YouTube", "Eliminar descarga": "Delete download", "Eliminar descarga ({s})": "Delete download ({s})",

  // listen
  "YouTube está bloqueando las descargas.": "YouTube is blocking downloads.",
  "Se arregla subiendo tus cookies de YouTube.": "Upload your YouTube cookies to fix it.", "Ir a Ajustes": "Go to Settings",
  "Continuar": "Continue", "Nuevos episodios": "New episodes",
  "Escucha tus canales como podcasts": "Listen to your channels as podcasts",
  "Sigue un canal y yt2pod descargará el audio de cada vídeo nuevo. Aparecerán aquí, listos para escuchar.":
    "Follow a channel and yt2pod will download the audio of every new video. They'll show up here, ready to play.",
  "Buscar canales": "Search channels", "Estás al día": "You're all caught up",
  "Cuando tus canales suban vídeos nuevos aparecerán aquí.": "New videos from your channels will show up here.",
  "Ocultar escuchados": "Hide played", "Ver escuchados ({n})": "Show played ({n})",

  // channels
  "Buscando vídeos nuevos…": "Checking for new videos…", "Buscar vídeos nuevos ahora": "Check for new videos now",
  "Añadir canal": "Add channel", "Aún no sigues ningún canal": "You don't follow any channels yet",
  "Busca un canal por su nombre o pega su enlace de YouTube.": "Search for a channel by name or paste its YouTube link.",
  "{n} sin escuchar": "{n} unplayed",

  // search
  "Sigues a {c}": "Following {c}", "Canal, @usuario o enlace": "Channel, @handle or link", "Tus canales": "Your channels",
  "Escribe el nombre de un canal, su @usuario o pega el enlace de cualquiera de sus páginas.":
    "Type a channel's name or @handle, or paste a link to any of its pages.",
  "Ningún canal coincide con «{q}».": "No channels match “{q}”.", "Siguiendo": "Following", "Seguir": "Follow",

  // channel
  "Largos": "Long", "Cortos": "Short", "No se pudo abrir el canal": "Couldn't open the channel",
  "Añadido a Escuchar": "Added to Listen", "{n} episodios añadidos a Escuchar": "{n} episodes added to Listen",
  "Te bajaremos sus próximos vídeos": "Their next videos will be downloaded for you",
  "Actualizar lista de vídeos": "Refresh video list", "Abrir en YouTube": "Open in YouTube", "Dejar de seguir": "Unfollow",
  "Has dejado de seguir a {c}": "You unfollowed {c}", "Vídeos": "Videos", "Seleccionar": "Select", "Filtrar vídeos": "Filter videos",
  "Todos ({n})": "All ({n})", "Solo los de +20 min": "Only 20+ min", "No hay vídeos en esta vista.": "No videos in this view.",
  "1 seleccionado": "1 selected", "{n} seleccionados": "{n} selected", "Añadir a Escuchar": "Add to Listen",
  "No se bajó: {r}": "Not downloaded: {r}",
  "Buscar en este canal": "Search this channel", "Buscando también en YouTube…": "Also searching YouTube…",
  "Ningún vídeo coincide con «{q}».": "No videos match “{q}”.", "Ya no hay vídeos más antiguos.": "There are no older videos.",
  "Cargando…": "Loading…", "Cargar vídeos anteriores": "Load older videos",
  "Descargar vídeos nuevos": "Download new videos",
  "Cada vídeo nuevo aparecerá en Escuchar.": "Every new video will show up in Listen.",
  "Cada vídeo nuevo de más de {n} min aparecerá en Escuchar.": "Every new video over {n} min will show up in Listen.",
  "Solo se descargará lo que añadas a mano.": "Only what you add by hand will be downloaded.",
  "Duración": "Length", "Duración mínima": "Minimum length",
  "Solo si el título contiene": "Only if the title contains", "Nunca si el título contiene": "Never if the title contains",
  "Filtrar por palabras del título…": "Filter by words in the title…", "Quitar {w}": "Remove {w}", "Añadir palabra": "Add word",

  // settings
  "Tu progreso se sincroniza en todos tus dispositivos": "Your progress syncs across all your devices",
  "Cambiar contraseña": "Change password", "Cerrar sesión": "Sign out",
  "Apariencia": "Appearance", "Tema": "Theme", "Automático": "Automatic", "Claro": "Light", "Oscuro": "Dark", "Idioma": "Language",
  "Automático sigue el ajuste del sistema y el idioma del navegador. Se guarda en este dispositivo.":
    "Automatic follows your system setting and browser language. Saved on this device.",
  "Instalar la app": "Install the app", "Instalar": "Install",
  "Instálala para abrirla como una app, con su icono y a pantalla completa.": "Install it to open it like an app, with its own icon and full screen.",
  "Otras apps de podcasts": "Other podcast apps", "Feed RSS privado": "Private RSS feed", "Enlace copiado": "Link copied", "Copia el enlace": "Copy the link",
  "Pégalo en Overcast, Pocket Casts o Apple Podcasts si prefieres escuchar allí. Es secreto: quien lo tenga puede escuchar tus episodios. El progreso de esas apps no se sincroniza con yt2pod.":
    "Paste it into Overcast, Pocket Casts or Apple Podcasts if you'd rather listen there. Keep it secret: anyone with it can play your episodes. Progress in those apps doesn't sync with yt2pod.",
  "Servidor": "Server", "Última comprobación": "Last check", "Frecuencia": "Frequency", "cada {d}": "every {d}",
  "Formato de audio": "Audio format", "AAC (m4a), sin recodificar": "AAC (m4a), not re-encoded",
  "Cuentas": "Accounts", "Administrador": "Admin", "Añadir cuenta": "Add account",
  "Cada cuenta tiene sus canales y su progreso. Si dos cuentas siguen el mismo canal, el audio se descarga una sola vez.":
    "Each account has its own channels and progress. If two accounts follow the same channel, the audio is downloaded only once.",

  // storage
  "Almacenamiento": "Storage", "Tus episodios": "Your episodes", "Ya escuchados": "Already played",
  "Total en el servidor": "Total on the server", "Espacio libre": "Free space",
  "Ver episodios por tamaño": "Show episodes by size", "Ocultar episodios": "Hide episodes", "Eliminar {t}": "Delete {t}",
  "Se borrará 1 episodio ya escuchado ({s}). No se puede deshacer.": "1 played episode will be deleted ({s}). This can't be undone.",
  "Se borrarán {n} episodios ya escuchados ({s}). No se puede deshacer.": "{n} played episodes will be deleted ({s}). This can't be undone.",
  "Borrar escuchados": "Delete played", "1 episodio borrado": "1 episode deleted", "{n} episodios borrados": "{n} episodes deleted",
  "Borrar un episodio quita su audio del servidor. Puedes volver a añadirlo desde la página del canal.":
    "Deleting an episode removes its audio from the server. You can add it again from the channel page.",
  "Borrar un episodio quita su audio del servidor salvo que otra cuenta lo tenga en su lista. Puedes volver a añadirlo desde la página del canal.":
    "Deleting an episode removes its audio from the server unless another account has it in its list. You can add it again from the channel page.",

  // cookies
  "Acceso a YouTube": "YouTube access", "Cookies de YouTube": "YouTube cookies", "Configuradas {d}": "Set up {d}",
  "Sin configurar": "Not set up", "Subiendo…": "Uploading…", "Reemplazar": "Replace", "Subir cookies.txt": "Upload cookies.txt",
  "Eliminar cookies": "Delete cookies", "Cómo conseguir el archivo": "How to get the file", "Guía completa": "Full guide",
  "Cookies guardadas": "Cookies saved", "Cookies guardadas. Reintentando 1 descarga…": "Cookies saved. Retrying 1 download…",
  "Cookies guardadas. Reintentando {n} descargas…": "Cookies saved. Retrying {n} downloads…", "Cookies eliminadas": "Cookies deleted",
  "YouTube trata a muchos servidores en la nube como bots. Con las cookies de una cuenta, yt2pod descarga como si fueras tú. Se guardan solo en tu servidor. Si dejan de funcionar al cabo de unas semanas, vuelve a subirlas.":
    "YouTube treats many cloud servers as bots. With an account's cookies, yt2pod downloads as if it were you. They're stored only on your server. If they stop working after a few weeks, upload them again.",

  // accounts and sign in
  "Contraseña cambiada": "Password changed", "Contraseña actual": "Current password", "Nueva contraseña (mín. 8)": "New password (min. 8)",
  "Usuario": "Username", "Contraseña": "Password", "Contraseña (mín. 8)": "Password (min. 8)", "Contraseña (mín. 8 caracteres)": "Password (min. 8 characters)",
  "Crear cuenta": "Create account", "Cuenta {u} creada": "Account {u} created", "Entrar": "Sign in", "Bienvenido a yt2pod": "Welcome to yt2pod",
  "Crea tu cuenta. La usarás para entrar desde el móvil y el ordenador, con tu progreso sincronizado.":
    "Create your account. You'll use it to sign in on your phone and computer, with your progress in sync.",
  "Tus canales de YouTube, como podcasts.": "Your YouTube channels, as podcasts.",

  // player
  "Reproductor": "Player", "Reproduciendo": "Now playing", "Posición": "Position", "Velocidad {r}x": "Speed {r}x",
  "Retroceder 15 segundos": "Back 15 seconds", "Avanzar 30 segundos": "Forward 30 seconds",
  "Temporizador": "Sleep timer", "Temporizador: quedan {t}": "Sleep timer: {t} left", "Se pausará en {t}": "Pausing in {t}",
  "Pausar la reproducción dentro de…": "Pause playback in…", "{n} minutos": "{n} minutes", "1 hora": "1 hour", "{n} horas": "{n} hours",
  "Se pausará en {n} min": "Pausing in {n} min", "Desactivar temporizador": "Turn off timer",
  "Temporizador: reproducción en pausa": "Sleep timer: playback paused",

  // messages from the server
  "Inicia sesión": "Please sign in", "Solo para administradores": "Admins only", "Demasiados intentos. Espera unos minutos.": "Too many attempts. Wait a few minutes.",
  "Ya hay una cuenta creada": "An account already exists", "Usuario o contraseña incorrectos": "Wrong username or password",
  "La contraseña actual no es correcta": "The current password is wrong", "Canal no válido": "Invalid channel",
  "Canal desconocido; búscalo primero": "Unknown channel; search for it first", "No tienes este episodio": "You don't have this episode",
  "El audio aún no está descargado": "The audio isn't downloaded yet",
  "El nombre de usuario debe tener entre 2 y 40 caracteres": "The username must be 2 to 40 characters",
  "La contraseña debe tener al menos 8 caracteres": "The password must be at least 8 characters", "Ese nombre de usuario ya existe": "That username is taken",
  "YouTube pide verificar que no eres un bot. Sube tus cookies de YouTube en Ajustes.": "YouTube wants to check you're not a bot. Upload your YouTube cookies in Settings.",
  "El vídeo es privado.": "The video is private.", "Vídeo solo para miembros del canal.": "Members-only video.",
  "El vídeo ya no está disponible.": "The video is no longer available.",
  "YouTube está limitando las peticiones. Se reintentará más tarde.": "YouTube is rate limiting requests. It will retry later.",
  "Es un Short": "It's a Short", "El título no contiene ninguna de tus palabras": "The title doesn't contain any of your words",
};
