# TOV4R 1729 Autopilot — One-shot Security Baseline

This is an extension of the existing deployed TOV4R community bot. It only runs on a server whose **name is exactly 1729**.

## Automatic behavior at startup (one time)

1. Wait for Discord connection and the target guild.
2. Fetch the server member list if possible (for safe role checks).
3. Create a private before snapshot in the existing Railway volume next to the SQLite database, under \`server-audits/\`.
4. Identify roles, permissions, channels, categories, permission overwrites, bots, duplicates, and likely security issues.
5. Remove explicitly risky **server-wide** permissions from \`@everyone\`, if the bot has Manage Roles.
6. Also remove those permissions from standard non-managed community roles with exact names \`member\`, \`members\`, \`verified\`, \`unverified\`, \`new member\`, \`newbie\`, \`guest\`, or \`gamer\`, only when the bot is above the role, the member cache is complete, and no bots hold it.
7. Save after snapshot and a reversible change log (with original permission bitsets) to the Railway volume.
8. Mark the process done in the persistent volume; reconnects/redeployments do not repeat changes.
9. Write only a short status summary to an already configured audit channel and Railway deploy logs.

## What it intentionally does NOT do

- Delete roles, channels, messages, webhooks or bots.
- Automatically invite/install third-party bots (Discord OAuth authorization is required).
- Change staff/owner/admin/mod roles or grant any new permissions.
- Guess which unused-looking resources can be deleted.
- Reorganize channels by guesses, sync channel overwrites, or break ticket/privacy structures.
- Change any other servers where this public bot is invited.

## Inspecting the result

Run **\`/autopilot report\`** in Discord as a user with Manage Server. The message and report are ephemeral. It contains private before and after snapshots and a record of changed permissions and skipped items.

The existing **\`/server scan\`** is read-only and can be run any time. Any further discretionary/destructive changes still use the previously implemented confirmation flow.

If restoring an old permission configuration is required, the original permission integer bitsets are saved in the report. A server administrator should review them before restoration.

## Deployment

Railway watches the \`community/tov4r-bot\` branch. Changes are reviewed and tested in a feature branch before merging. The audit is one-shot: no recurring background monitor is created.
