# TOV4R Server Studio V4 — Web Dashboard + Setup Wizard

This feature **extends the existing TOV4R Bot**, preserving its Discord slash commands, SQLite data, tickets, roles, voice rooms, XP, moderation and Railway deployment. The legacy owner dashboard remains available at `/`, and the new Server Studio lives at **`/wizard`**.

## Features

- Multi-server Discord OAuth2 login (`identify guilds` scopes) with server-side sessions and **Manage Server** authorization checked against Discord's guild list before viewing or changing each guild.
- Existing private dashboard-key bearer login remains available as an alternative to OAuth2 for the application owner.
- Server selector showing **real** Discord member/channel/role/bot counts, current theme and recent events.
- Preview-first Setup Wizard for Movies, Gaming, Music, Anime, Study, Tech, Sports, Friends and General Community.
- Preview shows the chosen theme's emoji categories, text/voice channels, read-only channels, roles, approximate new-resource counts, and approximate Focus Mode archive count.
- The preview creates no channels, roles or other server modifications.
- The **Apply approved setup** action uses a short-lived, one-use plan token bound to the current actor, guild, theme, focus flag and Discord structure fingerprint.
- The same shared setup engine powers **/setup** and the web wizard, avoiding behavioral drift.
- Setup is idempotent and keeps existing content. Optional Focus Mode hides off-theme public channels in a recoverable archive; the web wizard never executes destructive `cleanup:true`.
- Safe per-server settings: XP, anti-spam, anti-invites, welcome message, cooldown and blocked words.
- Restore previously archived channels through a separate explicit admin action.

## Dashboard URL and activation

The current Railway service must have a public HTTPS domain. A dashboard URL isn't created automatically by GitHub code alone.

1. In Railway, generate or configure a public service domain pointing to the dashboard's `PORT` (normally 8080), and leave **`DASHBOARD_KEY`** configured as a strong private secret.
2. In the [Discord Developer Portal](https://discord.com/developers/applications/1570489381624619198), add this exact redirect URI under OAuth2:

   `https://YOUR-HTTPS-DOMAIN/auth/callback`

3. Add these **private Railway environment variables** (never commit or post their values in a chat):
   - `DASHBOARD_BASE_URL=https://YOUR-HTTPS-DOMAIN`
   - `DISCORD_OAUTH_CLIENT_SECRET` = the application's **OAuth2 client secret**, **not** the bot token
4. Keep `DASHBOARD_HOST=0.0.0.0` and `PORT` on the public service so Railway routes HTTPS traffic correctly.
5. Visit `https://YOUR-HTTPS-DOMAIN/wizard`, use **Continue with Discord**, and choose a server on which you have **Manage Server**.
6. If the OAuth settings are absent, the site displays why Discord login is unavailable, but the owner can continue through the original private dashboard key.

The client secret must be stored in Railway's secrets management. Do not add a token to code, logs, a GitHub issue, a screenshot, or a URL.

## Security

- OAuth login uses authorization code, cryptographically random state with matching short-lived HttpOnly SameSite cookie, exact configured redirect URI and `identify guilds` only.
- OAuth access tokens live in server memory and expire after at most one hour; browser stores only a secure, HttpOnly session cookie. Page refreshes do not require localStorage.
- Each authenticated request is restricted to the intersection of bot-installed guilds and the user's currently authorized **Manage Guild** / ownership permissions from Discord.
- Mutations require a per-session CSRF header; original owner bearer-key requests continue to be authorized independently.
- Preview tokens expire after 10 minutes, are single-use, and are invalidated if the current server channel/role structure has changed.
- Original owner dashboard `/api/state` and `/api/guild/*` endpoints continue to require the private owner bearer key. OAuth users **cannot** access those owner-only routes.
- No third-party cookies, external JavaScript, user content HTML injection, credential storage in URL/localStorage, or mass deletion.
- Existing CSP, no-store response headers, clickjacking protection and no-referrer policy apply to all routes.

## Testing

GitHub Actions runs:

```bash
python -m compileall -q bot.py dashboard.py web_dashboard_v2.py setup_service.py
python -m unittest discover -s tests -p 'test_server_templates.py' -v
python -m unittest discover -s tests -p 'test_web_wizard.py' -v
node --check web/wizard.js
```

Tests do not connect to Discord or need a real account/token. Once OAuth is enabled, use a **test server** to validate login, guild permissions, preview, apply, preservation of old channels, and restore.

## Migration safety

The SQLite schema and existing tables are unchanged by the web feature. The shared setup engine preserves the same configured categories, voice creator, support role, welcome/log channels, existing panels, template role IDs, and focus history. No automatic migration or new background job is added.
