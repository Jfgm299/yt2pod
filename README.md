# yt2pod

Follow YouTube channels and listen to them **as podcasts**. yt2pod is an installable web app (PWA)
for phone and desktop: find a channel, follow it, and every new video shows up in **Listen** with
the audio already downloaded. Your progress is stored on the server, so you can start on your phone
and pick up on your computer right where you left off.

## Features

- **Search channels** by name, `@handle` or link, with their avatar and subscriber count, as you type.
- **Follow** a channel: yt2pod checks its official RSS feed every 30 min and downloads the audio of new videos.
  Simple per-channel rules: download automatically or not, minimum length (10+, 20+, 45+ min) and,
  optionally, words the title must or must not contain. Shorts and live streams are skipped.
- **Pick older episodes** from a channel one by one, or use *Select → Only 20+ min*.
  *Load older videos* goes further back into the channel's archive, and the channel search box finds a specific video.
- **Listen**: *Continue* (what you're halfway through) and *New episodes*, with live download status.
- **Player** with ±15/30 s skips, playback speed, a sleep timer (pauses after 5–90 min), lock-screen
  controls and keyboard shortcuts (space, ←, →). Your position is saved every few seconds and when you leave.
- **Storage**: every episode shows its size; *Settings* shows the total and free space, and lets you
  delete everything you've already played at once, or the biggest episodes one by one.
- **English and Spanish, light and dark mode** in *Settings → Appearance*. By default it follows the
  browser's language and the system theme.
- **Accounts**: sign in with the same user on your phone and computer. You can create accounts for
  other people; each has its own channels and progress, and shared audio is downloaded only once.
- **Private RSS feed** per account, if you'd rather use Overcast, Pocket Casts or Apple Podcasts.
- Audio in **m4a (AAC), not re-encoded** by default: same quality as on YouTube, and it plays on iPhone.

## Where to run it

yt2pod is a server that needs to stay on (to watch your channels) and that your phone and computer
can reach. Two options:

| | Home server (recommended) | VPS |
|---|---|---|
| Examples | Raspberry Pi, NAS, a PC you leave on | Hetzner, DigitalOcean… |
| YouTube | Works: you have a residential IP | Often blocks data-center IPs ("Sign in to confirm you're not a bot"). Fixed by uploading your YouTube cookies in *Settings → YouTube access* ([guide](docs/cookies.md)). |
| Remote access | [Tailscale](https://tailscale.com) (free): `tailscale serve --bg 8765` gives you `https://<machine>.<tailnet>.ts.net` with HTTPS | Domain + HTTPS (Caddy, Traefik or similar) |

If YouTube blocks downloads ("not a bot"), follow the **[YouTube cookies](docs/cookies.md)** guide:
it explains step by step how to export them, how long they last and what to do when they expire.

**The PWA needs HTTPS** to be installed and to work offline (except on `localhost`).
Tailscale or Caddy give you that with no extra work.

## Installation

With Docker:

```bash
docker compose up -d
```

The image listens on port `8765` and keeps everything in the `/data` volume. On Dokploy or similar,
use the Dockerfile build type, mount a volume at `/data` and point your domain at port `8765`.

Without Docker (Python 3.10+, `ffmpeg` and [deno](https://deno.com), which yt-dlp uses to read YouTube):

```bash
pip install .
yt2pod serve            # http://localhost:8765
```

Open the site, create your account (the first one is the admin) and search for a channel.
To install it: on iPhone, *Share → Add to Home Screen*; on Android or desktop Chrome/Edge, *Install app*.

Other commands:

```bash
yt2pod doctor            # checks ffmpeg, yt-dlp and whether YouTube lets you download
yt2pod user add ana      # create an account from the terminal
yt2pod user passwd jf    # reset a forgotten password
```

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `YT2POD_DATA_DIR` | `data` | Database and audio files. |
| `YT2POD_PORT` / `YT2POD_HOST` | `8765` / `0.0.0.0` | Where the server listens. |
| `YT2POD_CHECK_INTERVAL` | `30m` | How often to look for new videos (minimum 5 min). |
| `YT2POD_AUDIO_FORMAT` | `m4a` | `m4a`, `best` (Opus), `opus` or `mp3`. |
| `YT2POD_BASE_URL` | — | Public URL used for links in the RSS feeds. |
| `YT2POD_COOKIES_FILE` | `<data>/cookies.txt` | YouTube `cookies.txt` if you get blocked (mostly on a VPS). The easiest way is to upload it from *Settings*. |
| `YT2POD_SPONSORBLOCK` | — | Segment categories to cut out, e.g. `sponsor,selfpromo`. |
| `YT2POD_OPEN_SIGNUP` | `false` | Let anyone create an account from the sign-in screen. |

## How it works

- A single process (FastAPI + uvicorn) serves the PWA, the API and the feeds, and runs two threads:
  the **watcher** (reads each followed channel's RSS feed, with random jitter between checks) and the
  **download queue** (one at a time; retries with growing back-off and gives up on permanent errors
  such as private videos).
- Only videos published **after** you follow a channel are downloaded automatically; older ones are
  picked by hand.
- SQLite stores accounts, channels, videos, downloads and progress. If an episode is played on two
  devices, the most recent position wins.
- The UI is Preact + htm with no build step (`yt2pod/web/`), with an iOS-inspired design
  (see `.claude/skills/apple-design/SKILL.md`). Interface texts live in `yt2pod/web/i18n.js`.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Tests use a fake YouTube (`tests/fakes.py`), with no network access.

## Disclaimer

For personal use. Respect YouTube's terms and creators' rights; don't redistribute the content.
If downloads start failing, update yt-dlp (`pip install -U yt-dlp`; the Docker image does it on start).
