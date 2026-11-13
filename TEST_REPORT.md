# Validation — 12 novembre 2026

Environment: Python 3.12, discord.py 2.7.1, aiohttp 3.14.5.

Result: **15 automated tests passed** (`python -m unittest discover -s tests -v`).

- All 22 slash commands load and are guild-only; sensitive commands have permission checks.
- All button panels have stable IDs and persistent views.
- Non-owner room access is rejected.
- Only empty tracked rooms are deleted.
- Giveaway concurrency produces one draw with distinct winners.
- Failed giveaway delivery preserves the same winners for retry.
- Dashboard rejects unauthenticated access and non-JSON writes.
- Dashboard assets and authenticated state endpoint respond correctly.
- Configuration persists across SQLite reopen and stays isolated by server.
- Invalid settings and unsafe role permissions are rejected.
- XP cooldown and level progression work with guild isolation.
- Only one open ticket is allowed per member and guild.
- Giveaway participation is unique and persists.

Python compile checks: passed for bot.py, store.py, dashboard.py, configure.py.
JavaScript syntax check: passed (`node --check web/app.js`).

Tests use temporary SQLite databases and Discord mocks where needed. No Discord network session, real permission changes, browser visual test, Windows launcher run, Docker build or hosted deployment was performed. Live validation requires the new bot's token, enabled intents, correct role hierarchy and a server invitation. No secret, database or virtual environment is included in the ZIP.

Multi-server regression: global sync occurs with or without legacy server ID; legacy guild commands are removed only for the specified bot guild scope. Both tests passed.
