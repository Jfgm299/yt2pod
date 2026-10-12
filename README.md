# yt2pod

Convierte canales y listas de reproducción de YouTube en **podcasts autoalojados**.
yt2pod vigila las fuentes que elijas, descarga solo el audio de los vídeos que pasan tus filtros
y publica un feed RSS que puedes añadir a cualquier app de podcasts (AntennaPod, Pocket Casts,
Overcast, Apple Podcasts…).

*Turn YouTube channels and playlists into self-hosted podcast feeds, with per-feed filters
(minimum duration, title regex, no Shorts…). English summary at the bottom.*

## Características

- **Fuentes**: canal (`@handle`, URL o `UC…`) o una lista de reproducción concreta.
- **Filtros por feed**: duración mínima/máxima (para quedarte con los episodios largos e ignorar los clips),
  regex de inclusión/exclusión en el título, exclusión por descripción, sin Shorts, sin directos,
  fecha mínima de publicación.
- **Vigilancia**: `yt2pod run` comprueba cada fuente cada `check_interval` y descarga lo nuevo.
- **Primera sincronización prudente**: solo descarga los `backfill` vídeos más recientes, no todo el catálogo.
- **Explorar el catálogo**: `yt2pod browse` lista los vídeos existentes y te dice cuáles pasan los filtros;
  `yt2pod add` descarga cualquiera a mano.
- **Retención**: `max_episodes` borra los audios más antiguos.
- **SponsorBlock** opcional para recortar patrocinios, intros, etc.
- Formatos `m4a` (sin recodificar), `mp3` u `opus`.
- Servidor HTTP integrado con soporte de `Range` (para avanzar/retroceder en la app) y export OPML.

## Instalación

Requisitos: Python 3.10+ y `ffmpeg`.

```bash
git clone https://github.com/<tu-usuario>/yt2pod && cd yt2pod
pip install .
cp config.example.yaml config.yaml   # edítalo
```

O con Docker:

```bash
cp config.example.yaml config.yaml
docker compose up -d
```

## Uso

```bash
yt2pod browse full-episodes -r      # ver los vídeos del canal y por qué se filtran
yt2pod sync --dry-run               # qué se descargaría
yt2pod sync                         # comprobar una vez y descargar
yt2pod add full-episodes dQw4w9WgXcQ  # añadir un vídeo concreto (ignora filtros)
yt2pod run                          # servidor + comprobación periódica (modo servidor)
```

Después abre `http://<base_url>/` y copia la URL del feed (`/feeds/<id>.xml`) en tu app de podcasts.

`browse` muestra una línea por vídeo:

```
✔ OK  aBcD123  1:42:10  2026-10-01  #412 – Entrevista completa
  --  eFgH456     8:31              El mejor momento de #412      [too short (8:31 < 40:00)]
```

## Configuración

Ver [`config.example.yaml`](config.example.yaml). Resumen:

| Clave | Dónde | Descripción |
|---|---|---|
| `server.base_url` | global | URL pública con la que tu móvil llega al servidor (IP de LAN, Tailscale, dominio). |
| `check_interval` | global | Cada cuánto revisa `run` (`30m`, `1h`…). |
| `cookies_file` | global | `cookies.txt` para vídeos con restricción de edad o de miembros. |
| `source` | feed | `@handle`, URL de canal, `UC…`, URL de playlist o `PL…`. |
| `scan_limit` | feed/defaults | Cuántos vídeos recientes mira en cada comprobación (30). |
| `backfill` | feed/defaults | Cuántos descarga en la primera sincronización (3). |
| `max_episodes` | feed/defaults | Máximo de episodios guardados. |
| `audio_format` | feed/defaults | `m4a`, `mp3` u `opus`. |
| `sponsorblock_remove` | feed/defaults | Categorías de SponsorBlock a recortar. |
| `filters.min_duration` / `max_duration` | feed/defaults | `90s`, `20m`, `1h30m`, `1:15:00`. |
| `filters.title_include` / `title_exclude` | feed/defaults | Listas de regex (sin distinguir mayúsculas). |
| `filters.description_exclude` | feed/defaults | Regex sobre la descripción. |
| `filters.exclude_shorts` / `exclude_live` | feed/defaults | `true` por defecto. |
| `filters.published_after` | feed/defaults | `YYYY-MM-DD`. |

Los filtros de `defaults` se combinan con los de cada feed (el feed gana).

Si cambias los filtros, los vídeos ya filtrados se vuelven a evaluar en la siguiente comprobación
(solo se recuerdan los descargados, los omitidos del catálogo inicial y los fallidos, que se reintentan).

## Cómo funciona

1. Lista los `scan_limit` vídeos más recientes de la fuente con yt-dlp (listado plano, una sola petición).
2. Aplica los filtros baratos (duración, título, Shorts). Solo pide los metadatos completos de los
   candidatos (fecha, descripción), y los guarda en caché en SQLite.
3. Descarga el audio, regenera `data/feeds/<id>.xml` y aplica la retención.

Datos en `data_dir`: `audio/<feed>/<video>.m4a`, `feeds/<feed>.xml`, `yt2pod.sqlite3`.

## Desarrollo

```bash
pip install -e '.[dev]'
pytest
```

## Aviso

Úsalo para uso personal y respeta los términos de YouTube y los derechos de los creadores.
Si YouTube bloquea las descargas, actualiza yt-dlp (`pip install -U yt-dlp`).

---

### English

yt2pod watches YouTube channels or playlists, downloads the audio of videos that match per-feed
filters (min/max duration, title regex, no Shorts/live, publish date) and serves podcast RSS feeds.
Configure `config.yaml` (see `config.example.yaml`), then run `yt2pod run` (or `docker compose up -d`)
and subscribe to `http://<base_url>/feeds/<id>.xml`. Use `yt2pod browse <feed>` to explore a
channel's back catalogue and `yt2pod add <feed> <video>` to add individual videos. MIT licensed.
