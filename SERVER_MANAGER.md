# TOV4R Bot — Server Manager V1

This feature is added to the **existing community bot**; it does not replace music, voice, ticket, giveaway or moderation commands.

## Discord slash commands

| Command | What it does | Changes server? |
|---|---|---|
| /server scan | Private audit: role permissions, channels, categories, permission overwrites, visible bots, duplicates and security findings. Attaches JSON snapshot. | No |
| /server bots | Lists visible bots and elevated permissions. | No |
| /server fix_everyone | Preview and confirm removal of dangerous guild-wide @everyone permissions. | Only with confirmation |
| /server harden_role role:<role> | Preview and confirm removal of high-risk permissions from one chosen role. | Only with confirmation |
| /server remove_role role:<role> | Server-owner-only removal of **one empty, non-managed, non-protected role**, after confirmation. | Only with confirmation |
| /server move_channel channel:<text-channel> category:<category> | Move one text channel, preserving its permission overwrites (no sync), after confirmation. | Only with confirmation |

## Scope and safeguards

- A server manager needs **Manage Server** to run read-only scans. Mutating commands also require the relevant **Manage Roles** or **Manage Channels** permissions.
- Each change requires an ephemeral confirmation button from the same user. It expires in 120 seconds.
- The bot checks its own permissions and role hierarchy; it blocks operations on managed/default roles and roles above its hierarchy.
- Role deletion is restricted to the server owner, checks that the member cache is complete, and refuses roles with members or staff/owner-related names.
- It never automatically removes bots, creates external bot invitations, bulk-deletes channels or alters message history.
- The audit flags potentially exposed sensitive channels by *name* for manual review only. Names and duplicate labels are not proof of a problem.
- Third-party bot behavior and settings cannot be verified from normal Discord bot permissions. New bots require a human OAuth2 approval.
- **Do not share the exported JSON report publicly**: it contains server structure and permissions.

## Rollout

Changes were developed on a separate branch to protect the currently running Railway bot.

1. Inspect the diff and the audit commands before merging into \`community/tov4r-bot\`.
2. Merge the PR when ready; the Railway service watches \`community/tov4r-bot\`.
3. Monitor the Railway service logs and test \`/server scan\` first.
4. If commands do not appear instantly, allow global Discord command synchronization and verify bot permissions.
5. No new secrets or environment variables are needed; the existing bot still uses \`DISCORD_TOKEN\` stored privately in Railway.
