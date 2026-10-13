# YouTube cookies

If yt2pod runs on a VPS, YouTube will most likely block downloads with
**"Sign in to confirm you're not a bot"** (in the app: *YouTube wants to check you're not a bot*).
YouTube treats almost every data-center IP as a bot. The fix is to give yt2pod the cookies of a
YouTube session, so it downloads as if it were you in your own browser.

On a home server you usually don't need this.

## Before you start

- **Use a secondary Google account**, not your main one. The cookies give access to that account,
  and YouTube may limit or block accounts that download a lot. A new, empty account is fine.
- Do it **on a computer**. Exporting cookies from a phone isn't practical.
- You don't need YouTube Premium.

## Step by step (Chrome, Edge or Brave)

1. Install the **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)**
   extension. It's open source and doesn't send your cookies anywhere. Avoid the old "Get cookies.txt"
   (without *LOCALLY*): it was pulled for sending data out.
2. Allow it in incognito: extensions menu → *Get cookies.txt LOCALLY* → *Details* →
   turn on **Allow in Incognito**.
3. Open an **incognito window** and sign in to <https://www.youtube.com> with the secondary account.
4. In that same tab, go to <https://www.youtube.com/robots.txt>. This leaves the session on a page that
   won't touch the cookies again.
5. Click the extension's icon → **Netscape** format → **Export**. A file like
   `www.youtube.com_cookies.txt` is downloaded.
6. **Close the incognito window without signing out.** Signing out invalidates the cookies immediately,
   and if you keep browsing YouTube in that window, YouTube rotates them and the exported ones expire sooner.
7. In yt2pod: **Settings → YouTube access → Upload cookies.txt** and pick the file. Downloads that were
   blocked are retried automatically.

## Firefox

Same as above, with the **[cookies.txt](https://addons.mozilla.org/firefox/addon/cookies-txt/)**
extension in a **private window** (on the extension's page: *Run in Private Windows → Allow*).
Export only the current site's cookies.

## From the terminal (alternative)

If you have yt-dlp on your computer and are signed in to YouTube in Firefox:

```bash
yt-dlp --cookies-from-browser firefox --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```

That writes `cookies.txt` from your Firefox session. Outside incognito the browser keeps using the
session and YouTube rotates the cookies sooner, so the private-window method above is better.

## Without the app

The file lives at `<YT2POD_DATA_DIR>/cookies.txt`. With Docker or Dokploy that's `/data/cookies.txt`
inside the volume. You can also copy it there by hand, or point to another path with
`YT2POD_COOKIES_FILE`. yt2pod reads it on every download, so no restart is needed.

## How long do they last?

There's no fixed date. Exported as described above and with nobody using that session again, they
usually last **weeks or months**. They expire sooner if:

- you sign out of that account,
- you keep browsing YouTube with the same session,
- you change the account's password or Google flags it as suspicious.

When they stop working, downloads fail again with the bot message. Repeat the steps and upload new ones.

## If it still fails

- Check that the file starts with `# Netscape HTTP Cookie File` and has `.youtube.com` lines.
  yt2pod rejects the file if it can't find them.
- Update yt-dlp: the Docker image does it on every start (*Deploy* in Dokploy).
- Some VPS IPs are flagged so heavily that YouTube blocks them even with cookies. In that case the most
  reliable option is to run yt2pod at home (a Raspberry Pi or a PC that stays on) with Tailscale.

## Security

Cookies are like an open session of that account. yt2pod stores them only on your server, with
`600` permissions. Only the admin account can upload or delete them, and the app never shows them.
Deleting them from *Settings* removes the file.
