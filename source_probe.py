"""Bounded by the parent process; no login, cookies or URLs printed."""
import json
import os
import bot

if __name__ == '__main__':
    url = os.getenv('YOUTUBE_PROBE_URL', '')
    if not bot.safe_url(url):
        raise SystemExit('invalid probe URL')
    track = bot.Track(title='YouTube probe', page_url=url, requester='diagnostic')
    info, diagnostic = bot.playable_audio_sync(track)
    print(json.dumps({'playable': bool(info), 'diagnostic': diagnostic}))
