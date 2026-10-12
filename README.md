# yt2pod

Sigue canales de YouTube y escúchalos **como podcasts**. yt2pod es una app web instalable (PWA)
para móvil y ordenador: buscas un canal, lo sigues, y cada vídeo nuevo aparece en **Escuchar**
con el audio ya descargado. Tu progreso se guarda en el servidor, así que empiezas en el móvil
y sigues en el ordenador justo donde lo dejaste.

*Follow YouTube channels and listen to them as podcasts: a self-hosted PWA with accounts,
automatic downloads of new uploads and listening progress synced across devices.*

## Qué hace

- **Buscar canales** por nombre, `@usuario` o enlace, con su foto y suscriptores, mientras escribes.
- **Seguir** un canal: yt2pod mira su RSS oficial cada 30 min y descarga el audio de los vídeos nuevos.
  Reglas sencillas por canal: descargar o no automáticamente, duración mínima (+10, +20, +45 min)
  y, si quieres, palabras que el título debe contener o no. Los Shorts y directos se ignoran.
- **Elegir episodios anteriores** del canal: tócalos uno a uno o usa *Seleccionar → Solo los de +20 min*.
- **Escuchar**: *Continuar* (lo que tienes a medias) y *Nuevos episodios*, con estado de descarga en vivo.
- **Reproductor** con ±15/30 s, velocidad, controles en la pantalla de bloqueo y atajos de teclado
  (espacio, ←, →). La posición se guarda cada pocos segundos y al salir.
- **Cuentas**: entra con el mismo usuario desde el móvil y el ordenador. Puedes crear cuentas para
  otras personas; cada una tiene sus canales y su progreso, y el audio se descarga una sola vez.
- **Feed RSS privado** por cuenta, por si prefieres Overcast, Pocket Casts o Apple Podcasts.
- Audio en **m4a (AAC) sin recodificar** por defecto: suena igual que en YouTube y funciona en iPhone.

## ¿Dónde lo instalo?

yt2pod es un servidor que tiene que estar encendido siempre (para vigilar los canales) y al que
llegan tu móvil y tu ordenador. Dos opciones:

| | Servidor en casa (recomendado) | VPS |
|---|---|---|
| Ejemplos | Raspberry Pi, NAS, un PC que no apagues | Hetzner, DigitalOcean… |
| YouTube | Funciona: tu IP es doméstica | A menudo bloquea IPs de centros de datos («Sign in to confirm you're not a bot»). Hay que pasarle cookies y aun así puede fallar. |
| Acceso desde fuera | [Tailscale](https://tailscale.com) (gratis): `tailscale serve --bg 8765` te da `https://<equipo>.<tailnet>.ts.net` con HTTPS | Dominio + HTTPS (Caddy o similar) |

**La PWA necesita HTTPS** para instalarse y funcionar sin conexión (salvo en `localhost`).
Con Tailscale o Caddy lo tienes sin hacer nada más.

## Instalación

Con Docker:

```bash
docker compose up -d
```

Sin Docker (Python 3.10+ y `ffmpeg`):

```bash
pip install .
yt2pod serve            # http://localhost:8765
```

Abre la web, crea tu cuenta (la primera es la de administrador) y busca un canal.
Para instalarla: en iPhone, *Compartir → Añadir a pantalla de inicio*; en Android o en Chrome/Edge
de escritorio, *Instalar app*.

Otros comandos:

```bash
yt2pod doctor            # comprueba ffmpeg, yt-dlp y si YouTube te deja descargar
yt2pod user add ana      # crear una cuenta desde la terminal
yt2pod user passwd jf    # cambiar una contraseña olvidada
```

## Configuración (variables de entorno)

| Variable | Por defecto | Para qué |
|---|---|---|
| `YT2POD_DATA_DIR` | `data` | Base de datos y audios. |
| `YT2POD_PORT` / `YT2POD_HOST` | `8765` / `0.0.0.0` | Dónde escucha el servidor. |
| `YT2POD_CHECK_INTERVAL` | `30m` | Cada cuánto mira si hay vídeos nuevos (mínimo 5 min). |
| `YT2POD_AUDIO_FORMAT` | `m4a` | `m4a`, `best` (Opus), `opus` o `mp3`. |
| `YT2POD_BASE_URL` | — | URL pública para los enlaces del feed RSS. |
| `YT2POD_COOKIES_FILE` | — | `cookies.txt` de YouTube si te bloquean (sobre todo en un VPS). |
| `YT2POD_SPONSORBLOCK` | — | Categorías a recortar, p. ej. `sponsor,selfpromo`. |
| `YT2POD_OPEN_SIGNUP` | `false` | Permitir que cualquiera cree cuenta desde la pantalla de inicio. |

## Cómo funciona

- Un solo proceso (FastAPI + uvicorn) sirve la PWA, la API y los feeds, y ejecuta dos hilos:
  el **vigilante** (lee el RSS de cada canal seguido, con un margen aleatorio entre comprobaciones)
  y la **cola de descargas** (de una en una; reintenta con espera creciente y se rinde con los errores
  permanentes, como vídeos privados).
- Solo se descargan automáticamente los vídeos publicados **después** de seguir el canal; los anteriores
  se eligen a mano.
- SQLite guarda cuentas, canales, vídeos, descargas y progreso. Si un episodio se escucha en dos
  dispositivos, gana la posición más reciente.
- La interfaz es Preact + htm sin paso de compilación (`yt2pod/web/`), con diseño inspirado en iOS
  (ver `.claude/skills/apple-design/SKILL.md`).

## Desarrollo

```bash
pip install -e '.[dev]'
pytest
```

Los tests usan un YouTube simulado (`tests/fakes.py`), sin red.

## Aviso

Para uso personal. Respeta los términos de YouTube y los derechos de los creadores; no redistribuyas
el contenido. Si las descargas fallan, actualiza yt-dlp (`pip install -U yt-dlp`; la imagen Docker lo
hace al arrancar).
