# TOV4R Bot — Auto Server Setup by Type

A Discord server administrator can now type **`/setup`**, select **`type`** in the Discord slash-command options, then choose the theme. The bot creates the required categories, text channels, voice rooms, cosmetic interest roles and a persistent self-assignable role panel.

## Types in the dropdown

| Type | Example channels | Example roles |
|---|---|---|
| 🎬 Movies & Cinema | cinema-rules, movie-chat, movie-recommendations, movie-reviews, movie-spoilers, watch-parties, 🍿 Watch Party voice | Movie Fan, Watch Party, Reviewer, Classics |
| 🎮 Gaming | game-chat, looking-for-group, gaming-clips, tournaments, squads | Gamer, Competitive, Clip Creator |
| 🎵 Music | music-chat, music-recommendations, your-creations, playlists, Jam Session | Listener, Artist, Producer, DJ |
| 🌸 Anime & Manga | anime-chat, manga-chat, anime-spoilers, watch-events | Anime Fan, Manga Reader, Fan Artist |
| 📚 Study & School | homework-help, resources, study-plans, Group Revision voice | Student, Study Buddy, Coding |
| 💻 Tech & Developers | programming-help, project-showcase, hardware-help, cybersecurity-learning | Developer, Cyber Learner, Designer |
| ⚽ Sports | sports-chat, match-discussion, sports-highlights, match voice | Football, Basketball, Motorsports |
| 👋 Friends & Hangout | general-chat, memes, plans, Hangout voice | Friend, Game Night, Movie Night |
| 🌐 General Community | general-chat, introductions, media-sharing, events voice | Member, Active, Event Fan |

The bot also retains the existing generic TOV4R setup: welcome, private support/ticket category, private logs, join-to-create voice and the tickets/voice panels.

## Examples

- `/setup type: 🎬 Movies & Cinema` — complete movies server
- `/setup type: 🎮 Gaming` — complete gaming server
- `/setup` — use the previously selected type for that server; defaults to General Community

> Discord shows the labels in a dropdown; do not type the emojis manually.

## Safety, permissions and idempotence

- User must have Discord **Manage Server**. Bot must have **Manage Channels**, **Manage Roles**, **Send Messages** and **Embed Links**.
- Created thematic roles have **zero moderation or admin permissions**; the self-roles panel only offers roles verified to be below the bot in the role hierarchy.
- `/setup` only **creates missing resources**. It tracks Discord IDs per guild and per template in a new SQLite `template_items` table. It preserves renamed existing resources that were previously created by the bot.
- Preexisting channels, categories, messages and roles are **never deleted, moved or overwritten**. Repeating `/setup` does not duplicate the generated role selector panel.
- `#rules` is a starter rules example; server owners should adapt rules to their community.
- Switching to another type preserves the previous type's resources; removing them is intentionally manual.
- The bot does **not** host or distribute movies, music or other copyrighted media; watch-parties are for coordinating legitimate viewing.
- The existing 1729 unattended security audit remains restricted to that server by its exact Discord guild ID.

This setup works in every server where the Discord application is installed with the appropriate permissions and the owner has enabled public Guild Install.
