# TOV4R MUSIC — Free 24/7 hosting preparation (Altare)

This checklist prepares the **existing** Python music bot for a free shared host. A real 24/7 deployment requires a host account and a successful runtime test. The GitHub repository by itself does **not** keep the bot online.

## Why Altare?

As advertised by Altare on 5 November 2026, the free team pool includes 6 GB RAM, 150% CPU, 10 GB disk, and 4 servers, with no idle sleep or expiration. These are shared resources, not guaranteed uptime. Host terms and limits may change. Minors need parent/guardian permission according to the provider's Terms.

- Official homepage: https://altare.gg/en/
- Free tier: https://altare.gg/en/help/getting-started/free-package
- Python deployment guide: https://altare.gg/en/help/discord-bots/deploy-python-bot
- Music bot requirements: https://altare.gg/en/help/discord-bots/music-bots-lavalink

## Deployment checklist

1. Create an account on https://console.altare.gg/ and create a Python server. For a music bot, consider **1–2 GB RAM**, and adjust using actual measured memory usage.
2. Upload **bot.py**, **requirements.txt**, and **host_check.py** from this repository. Never upload local virtualenv or .git folders.
3. Select a Python 3.13 runtime if available. In Startup, set the main file to `bot.py`. The Python template should install from `requirements.txt`.
4. Set `DISCORD_TOKEN` securely in the host's private environment, **not** in public code. If the template does not allow arbitrary environment variables, keep a private root `.env` file as described by the host. Use `.env.example` as a names-only reference; do not commit a filled file.
5. Set optional `OWNER_ID`, `GUILD_ID` and Spotify metadata credentials only if needed. The `DJ_DB_PATH` SQLite file must live on persistent storage if you want DJ access grants to survive restarts.
6. Before starting the bot, run `python host_check.py` from the server console/terminal (if this feature is available). It checks Python version, FFmpeg, Node.js 22+, required Python libraries and the **presence** of your token without displaying it.
7. Start the bot and verify that it connects in the host logs, then test Discord `/ping`, `/status`, `/testaudio`, `/testffmpeg` and `/sma3` with a permitted public audio source.
8. Once running on the host, stop the duplicate bot process on your own PC. Two identical gateway sessions may produce surprising behaviour.

## Important audio compatibility notes

This project uses **discord.py voice**, `FFmpegPCMAudio`, `yt-dlp`, and **Node.js 22+** for YouTube JS/EJS extraction. A generic Python host may not bundle FFmpeg or Node.js. The built-in `Dockerfile` installs both; use it **only if the provider supports custom container images**, or ask host support whether binaries are available. Do not assume the Python template supports Docker, apt or root access.

FFmpeg working locally does not guarantee every URL will stream on the host: source geo-blocking, access restrictions, 403 errors, Discord connection issues and external outages are separate problems. YouTube and Spotify do not provide unrestricted, direct music streaming.

## Security

- Never send the Discord token through chat, screenshots, issues, GitHub code, README or a public .env file.
- Rotate the token in Discord Developer Portal immediately if it has been exposed.
- Keep the bot's API secrets in the host's private environment; `.env` and `*.sqlite3` are ignored by Git.
- Read and follow host and Discord terms, including permissions for copyrighted audio.

## Alternative

Wispbyte (https://wispbyte.com/fr/free-discord-bot-hosting) advertises a free 512 MB RAM / 1 GB disk plan with no idle sleep, but you must log in every 14 days. Confirm FFmpeg and Node 22 support before using the music features.
