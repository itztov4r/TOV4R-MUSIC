# TOV4R AI Agent — experimental
Owner-only. Set TOV4R_AI_ALL_GUILDS=1 to allow owners in every installed guild; otherwise only the configured guild. No personal Discord token or login bypass.
Set OPENAI_API_KEY and TOV4R_AI_GUILD_ID privately in Railway; optionally TOV4R_AI_MODEL (default gpt-4o-mini).
The slash command /ai accepts one short request. Examples: "zid role VIP", "gad room gaming", "ch7al mn role".
The owner must be the actual Discord guild owner. Roles are created without permissions; deletion, role assignment, moderation, bulk changes and code execution are unsupported.
Only the explicitly entered command is sent to the API, not messages, member lists or secrets. Requests are rate limited globally to 30 per rolling 24h per process, plus the existing cooldown. Counters reset on restart; this is not a billing cap. API billing is separate.
Direct control from ChatGPT is not connected by this branch; a supported authorized integration would be needed.
