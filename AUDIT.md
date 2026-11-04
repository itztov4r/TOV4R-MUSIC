# Repository audit — 4 November 2026

Source reviewed: `2464291afb51e1ab213f0955153706677efea2de` on `main`. Changes are proposed on `audit/repository-improvements-2026-11-04`; nothing was merged or deployed.

## Changes

- Include server_setup.py in the Docker image; setup_hook imports it during startup.
- Reject credential-bearing URLs, unsupported ports, local numeric aliases and trailing-dot local hostnames.
- Require a hostname boundary for SoundCloud/Bandcamp playlist recognition and revalidate direct FFmpeg source URLs.
- Clamp playlist import limits to at least one and fix fractional PCM duration reporting.
- Add offline safety/header/container tests and CI without requiring a Discord token.

## Verification

4 automated behavioral tests passed. All 4 recorded source/test checks passed. Local verification used Node.js 26.12.0 and Python 3.15.0; CI should also confirm the configured deployment versions.

- `python -m unittest discover -s tests -v`
- `Python AST parse for source files`

Local HTML asset references were compared with the complete GitHub file inventory. Real desktop/mobile visual verification was blocked in this environment: the in-app preview could not reach the local server and the desktop browser did not expose a usable window. No claim of browser/device acceptance is made. Automated DOM/canvas simulations, where present, do not replace that check.

## Remaining limits and rollout

- Discord voice, Spotify OAuth, yt-dlp extraction and Docker startup were not exercised against live services. No credentials were read.
- Hostname syntax checks are not a complete SSRF boundary: DNS rebinding, redirects and media playlists require hosting-level outbound network restrictions.
- Dependencies could not be installed from the restricted terminal network; no package vulnerability audit was completed.
