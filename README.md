# TOV4R MUSIC

Discord music bot with slash commands, queue controls, local SQLite DJ access records, optional Spotify metadata, and public media extraction through yt-dlp/FFmpeg.

## Run locally

Use Python 3.13, Node.js 22+ and FFmpeg. Install `requirements.txt` into a virtual environment. Set `DISCORD_TOKEN` in a private environment variable (or an untracked `.env`) and run `python bot.py`. Never put a bot token into a website, screenshot, commit or log.

Optional environment variables: `GUILD_ID` for quick test-guild command sync, `OWNER_ID` for DJ access management, `DJ_DB_PATH` for the SQLite file, `FFMPEG_PATH`, `PLAYLIST_IMPORT_LIMIT` (clamped to 1–50), `DJ_PRICE`, and Spotify client/OAuth variables for metadata features. Spotify URLs do not provide a Discord audio stream.

## Docker

```sh
docker build -t tov4r-music .
docker run --rm --env-file .env -v tov4r-data:/data -e DJ_DB_PATH=/data/dj_access.sqlite3 tov4r-music
```

`bot.py` and `server_setup.py` must both be present in the image. Store DJ data in a persistent volume. Server setup commands preserve existing channels/roles and require Manage Guild, the authorized guild and explicit in-app confirmation. Do not run them as part of an unattended smoke test.

## Offline checks

```sh
python -m unittest discover -s tests -v
python -m py_compile bot.py server_setup.py
```

The offline safety tests need only Python's standard library; they do not connect a bot or modify a Discord server. See [AUDIT.md](AUDIT.md) for integration checks that remain unverified and the limits of URL filtering. Apply outbound network restrictions on the deployment host: URL syntax validation alone does not stop DNS rebinding, private-address redirects or malicious media playlists.
