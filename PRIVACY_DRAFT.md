# TOV4R Bot — Privacy notice (draft, operator review required)

Release candidate V5.0. This notice describes the implementation, not a verified legal compliance assessment. Before public release, the operator must confirm their identity, a functioning privacy contact, applicable law, hosting and processor arrangements, and retention/deletion procedures.

## Data and purpose
- Discord identity, server, channel, role and member identifiers; bot settings and moderation metadata are used for server administration.
- Optional progression tracks eligible messages and voice-state activity; the current voice module does not record audio. Private ticket messages are excluded from XP in this release.
- Moderation processes messages for configured rules. AutoMod incident records expire after 30 days. Other warning and audit records do not yet have a documented automatic expiry.
- Support tickets can retain text transcripts (up to 500 messages and 1 MB, no attachment contents) for seven days after closing if saved. Authorized staff can export copies; exported copies are outside bot storage.
- Server backups contain structure, permissions and settings, not messages; up to five backups per server are kept.
- Dashboard sign-in uses Discord OAuth identify and guilds. Access tokens are held server-side in memory for a session of at most one hour, with an HttpOnly cookie and CSRF protection. Login states and setup previews expire after ten minutes.
- Optional AI administration sends the explicit owner request to OpenAI with storage disabled in the API request; this does not establish that the provider never retains data. It does not send ticket transcripts or server message history automatically. Do not submit personal or confidential information.
- Railway hosts the bot and its SQLite volume; Discord and the optional AI provider process data as part of service operation.

## Access, retention and user requests
Guild-specific dashboard access is limited to authorized managers. The bot operator has broader administration access. Some records persist until removed; a comprehensive deletion workflow is not yet verified. Users may contact the server manager to coordinate with the bot operator for access or deletion requests. A public operator contact and verified handling process must be supplied before public launch.

## Outstanding operator decisions
Confirm contact and responsible operator, lawful grounds, data location/transfers, all retention schedules, removal on uninstall, user rights and complaint process, and applicable requirements for younger users. Do not present this draft as a finalized privacy notice.
