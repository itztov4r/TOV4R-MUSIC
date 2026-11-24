# TOV4R Voice AI (experimental)

- /voiceai ask question:... — joins your solo voice channel, speaks an AI response, leaves.
- /voiceai ask audio:... — explicitly upload your own audio recording (max 2 MB, first 30 seconds). Audio is sent to OpenAI. No Discord voice recording or live microphone listening.
- /voiceai leave — cancels the request and disconnects.
- Requires OPENAI_API_KEY and TOV4R_AI_GUILD_ID, actual guild owner, Connect/Speak permissions, FFmpeg, Opus, PyNaCl and DAVE voice dependencies.
- Uses OpenAI Realtime API, with API usage billed separately. Does not store audio locally or retain session history. Provider data handling is governed by OpenAI API terms.
- Live microphone receive is not enabled: upstream DAVE receive support is not stable/verified in the existing discord.py stack.
- Draft only: test voice handshake, playback, cancellation, attachment decoding and API response on a private test guild before deployment.
