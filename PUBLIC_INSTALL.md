# TOV4R Bot — Public server installation

This is the **existing** community bot (application ID **1570489381624619198**), hosted in Railway. No second bot or token is needed.

## Server invitation

Public Discord OAuth2 invitation:

https://discord.com/oauth2/authorize?client_id=1570489381624619198&scope=bot%20applications.commands

For the permissions-aware URL, use the Discord `/invite` command; it generates the correct permissions needed for the bot's current features **without Administrator**.

### Developer Portal settings (the application owner must confirm these)

1. Open https://discord.com/developers/applications/1570489381624619198
2. In **Bot**, ensure **Public Bot** is enabled, and disable **Requires OAuth2 Code Grant** unless you have explicitly implemented the code grant flow.
3. In **Installation**, enable **Guild Install** and select **Discord Provided Link**. For Guild Install defaults, enable OAuth2 scopes `bot` and `applications.commands`, then select permissions for bot features (Manage Channels, Manage Roles, Manage Messages, Connect, Move Members, Kick Members, Ban Members, Moderate Members, View Channels, Send Messages, Read History, Embed Links and Attach Files). **Administrator** is not needed.
4. Ensure the bot's existing **Server Members Intent** and **Message Content Intent** are toggled on, as used by its existing code.
5. Copy the generated installation link from the Installation page. Share only this link; never share the bot token.

## After a person adds the bot

- Anyone with the Discord permissions necessary to manage/install apps on their server can authorize it.
- An admin runs `/setup` on that server. The bot creates its own dedicated categories and channels, and **does not delete their existing channels**.
- `/help` shows commands; `/server scan` performs an ephemeral read-only server review.
- The regular commands are globally registered; new installations do not need a developer to manually configure guild IDs.
- Discord role hierarchy still applies; place TOV4R Bot's role above roles it will manage.
- The one-shot unattended **1729 autopilot** is restricted to guild ID **1564283597173690380** and **never auto-runs on other servers**, regardless of their names. All optional server-manager editing commands require explicit administrator confirmations.
- The dashboard uses one private owner key across all servers: do not share it, and do not publish that dashboard as if it were a per-server admin portal.

## Operational notes

The Python bot has a single connected Railway deployment and per-guild SQLite configuration. It can serve multiple guilds, subject to Discord application policy and applicable Discord verification requirements. Guild install availability must be enabled **in the owner's Developer Portal**; changing repository code does not flip that account-side setting.

Discord documentation: https://docs.discord.com/developers/quick-start/getting-started and https://docs.discord.com/developers/resources/application
