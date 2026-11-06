"""TOV4R MUSIC V7 — URL support and preflight playback checking."""
import asyncio
import math
import ipaddress
import re
import struct
import logging
import os
import random
import shlex
import subprocess
import tempfile
import sqlite3
import time
import shutil
from pathlib import Path
from contextlib import contextmanager
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, parse_qs
import requests

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN', '').strip()
GUILD_ID = os.getenv('GUILD_ID', '').strip()
COLOR = 0xED2446
FFMPEG = os.getenv('FFMPEG_PATH', 'ffmpeg')
MAX_QUEUE = 80
MAX_PLAYLIST_IMPORT = max(1, min(50, int(os.getenv("PLAYLIST_IMPORT_LIMIT", "30"))))
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN", "").strip()
OWNER_ID = int(os.getenv('OWNER_ID', '0') or '0')
DJ_PRICE = os.getenv('DJ_PRICE', 'Prix à demander au propriétaire')
DB_PATH = os.getenv('DJ_DB_PATH', 'dj_access.sqlite3')

@contextmanager
def open_db():
    path = Path(DB_PATH).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS dj_access (user_id INTEGER PRIMARY KEY, expires_at INTEGER NOT NULL)')
        conn.commit()
        yield conn
    finally:
        conn.close()

def get_access(user_id: int) -> tuple[bool, int]:
    if OWNER_ID and user_id == OWNER_ID:
        return True, 0
    with open_db() as conn:
        row = conn.execute('SELECT expires_at FROM dj_access WHERE user_id=?', (user_id,)).fetchone()
    if not row:
        return False, 0
    return row[0] > int(time.time()), row[0]

async def require_dj(interaction: discord.Interaction) -> bool:
    valid, _ = get_access(interaction.user.id)
    if not valid:
        await interaction.response.send_message(
            embed=embed('DJ MODE • PREMIUM',
                '🔒 Had lcommande ghir l DJ Premium.\n'
                f'💳 Prix: **{DJ_PRICE}**\n'
                '📩 Tواصل مع مول البوت، وبعد ما يتأكد الأداء غادي يفعّل ليك DJ Mode.\n'
                'ℹ️ Bot ma kayjme3 ta paiement b rassou.'), ephemeral=True)
        return False
    return True


def redact(text: str) -> str:
    text = str(text)
    for secret in (TOKEN, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN):
        if secret:
            text = text.replace(secret, '[redacted]')
    return re.sub(r'https?://[^\s<>]+', '[URL]', text)


def public_error(error: Exception) -> str:
    return redact(str(error) or type(error).__name__)[:350]


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


logging.basicConfig(level=logging.INFO)
for handler in logging.getLogger().handlers:
    handler.setFormatter(RedactingFormatter('%(asctime)s | %(levelname)s | %(message)s'))
log = logging.getLogger('tov4r')


class ExtractionLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        log.warning('Extractor: %s', redact(message))

    def error(self, message):
        log.warning('Extractor: %s', redact(message))

@dataclass
class Track:
    title: str
    page_url: str
    duration: int = 0
    thumbnail: str = ''
    requester: str = 'Unknown'
    search_fallback: str = ''

@dataclass
class MusicState:
    queue: deque = field(default_factory=deque)
    current: Optional[Track] = None
    voice: Optional[discord.VoiceClient] = None
    loop: bool = False
    volume: float = 0.60
    skip_requested: bool = False
    channel: Optional[discord.abc.Messageable] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    idle_task: Optional[asyncio.Task] = None
    connection_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0
    playback_id: int = 0

states: dict[int, MusicState] = {}

def state_for(guild_id: int) -> MusicState:
    return states.setdefault(guild_id, MusicState())

def duration(seconds: int) -> str:
    if not seconds:
        return 'LIVE / ?'
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m}:{s:02}'

def embed(title: str, description: str = '') -> discord.Embed:
    e = discord.Embed(title=f'🎧 {title}', description=description, color=COLOR)
    e.set_footer(text='TOV4R MUSIC • Darija Edition | Made for the community')
    return e

# ----- Multiple music sources / playable public playlists -----
def safe_url(url: str) -> bool:
    """Avoid private/local addresses and unsupported URL schemes."""
    if len(url) > 1500:
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or not host or host.rstrip('.') == 'localhost'
                or parsed.username is not None or parsed.password is not None):
            return False
        # Accessing .port also rejects malformed/out-of-range port numbers.
        _ = parsed.port
        host = host.rstrip('.')
        if host.endswith(('.local', '.internal', '.localhost')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return '.' in host and not re.fullmatch(r'[0-9.]+', host)
    except ValueError:
        return False



DIRECT_AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.flac', '.webm', '.m3u8'}


def direct_audio_url(url: str) -> bool:
    """Explicit public HTTPS media file/stream URLs; no scraping required."""
    if not safe_url(url):
        return False
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DIRECT_AUDIO_EXTENSIONS)


def spotify_type(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(url)
        if (parsed.hostname or '').lower() not in {'open.spotify.com', 'play.spotify.com'}:
            return None
        parts = [part for part in parsed.path.split('/') if part]
        if parts and parts[0].startswith('intl-'):
            parts.pop(0)
        if len(parts) >= 2 and parts[0] in {'track', 'album', 'playlist'} and re.fullmatch(r'[A-Za-z0-9]{22}', parts[1]):
            return parts[0], parts[1]
    except (ValueError, IndexError):
        return None
    return None


def playlist_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    path = parsed.path.lower()
    if host in {'youtube.com', 'www.youtube.com', 'music.youtube.com', 'm.youtube.com', 'youtu.be'}:
        return bool(parse_qs(parsed.query).get('list')) or '/playlist' in path
    if host.endswith('soundcloud.com'):
        return '/sets/' in path
    if host.endswith('bandcamp.com'):
        return '/album/' in path
    return False


def extract_sync(query: str, search: bool = True, playlist: bool = False, limit: int = MAX_PLAYLIST_IMPORT) -> dict:
    opts = {
        'format': 'bestaudio/best', 'quiet': True, 'no_warnings': False,
        'noplaylist': not playlist, 'skip_download': True,
        'extract_flat': 'in_playlist' if playlist or query.startswith('ytsearch') else False,
        'playlistend': limit, 'default_search': 'auto', 'socket_timeout': 20,
        'retries': 2, 'ignoreerrors': playlist,
        'js_runtimes': {'node': {}},  # Node.js 22+ for YouTube JS challenges
        'logger': ExtractionLogger(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(query, download=False)
    if not result:
        raise ValueError('Source ma t7ellatch. T2kked lien public w accessible.')
    if not playlist and 'entries' in result:
        result = next((entry for entry in result['entries'] if entry), None)
    if not result:
        raise ValueError('Ma l9it ta resultat f had source.')
    return result


def info_to_track(info: dict, requester: str, search_fallback: str = '') -> Track | None:
    if not isinstance(info, dict):
        return None
    page = info.get('webpage_url') or info.get('original_url') or info.get('url') or ''
    if not isinstance(page, str):
        return None
    if not safe_url(page):
        video_id = info.get('id')
        extractor = str(info.get('ie_key') or info.get('extractor_key') or '').lower()
        if extractor in {'youtube', 'youtubeie'} and video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', str(video_id)):
            page = 'https://www.youtube.com/watch?v=' + str(video_id)
    if not safe_url(page):
        return None
    return Track(
        title=str(info.get('title') or 'Music')[:180],
        page_url=page,
        duration=int(info.get('duration') or 0),
        thumbnail=str(info.get('thumbnail') or ''),
        requester=requester,
        search_fallback=search_fallback,
    )


def lookup_tracks_sync(text: str, requester: str, max_items: int) -> list[Track]:
    text = text.strip()
    if not text or len(text) > 1500:
        raise ValueError('Kteb smiya wla lien (max 1500 caracteres).')
    if text.startswith(('http://', 'https://')):
        if not safe_url(text):
            raise ValueError('Lien HTTPS public w s7i7, bla adresse locale.')
        if spotify_type(text) or urlparse(text).hostname == 'spotify.link':
            raise ValueError('Spotify ma kay3tich son direct l Discord. Jarrab /spotifyinfo pour les infos, ou source audio autorisee.')
        if direct_audio_url(text):
            filename = urlparse(text).path.rsplit('/', 1)[-1] or 'Audio stream'
            result = [Track(title=filename[:180], page_url=text, requester=requester)]
        else:
            is_list = playlist_url(text)
            info = extract_sync(text, playlist=is_list, limit=min(max_items, MAX_PLAYLIST_IMPORT))
            if is_list and 'entries' in info:
                result = [info_to_track(entry, requester) for entry in list(info['entries'])[:max_items] if entry]
            else:
                result = [info_to_track(info, requester)]
    else:
        # Search several candidates, not only one, then retry at playback if needed.
        info = extract_sync('ytsearch5:' + text, playlist=True, limit=5)
        entries = list(info.get('entries') or [])
        result = [info_to_track(entry, requester, text) for entry in entries if entry]
        result = [next((item for item in result if item), None)]
    return [item for item in result if item][:max_items]


async def find_tracks(text: str, requester: str, max_items: int) -> list[Track]:
    if max_items <= 0:
        raise ValueError('Queue 3amra.')
    return await asyncio.wait_for(
        asyncio.to_thread(lookup_tracks_sync, text, requester, max_items), timeout=100
    )


async def find_track(text: str, requester: str) -> Track:
    result = await find_tracks(text, requester, 1)
    if not result:
        raise ValueError('Ma l9it ta resultat public.')
    return result[0]


def resolve_audio_sync(track: Track) -> dict:
    """Resolve audio freshly, or accept a direct public HTTPS audio/stream URL."""
    if direct_audio_url(track.page_url):
        return {'url': track.page_url, 'title': track.title, 'http_headers': {}}
    try:
        info = extract_sync(track.page_url, playlist=False)
        if info.get('url') and isinstance(info['url'], str):
            return info
    except Exception as exc:
        if not track.search_fallback:
            raise
        log.warning('First search result unavailable: %s', type(exc).__name__)
    if track.search_fallback:
        candidates = extract_sync('ytsearch5:' + track.search_fallback, playlist=True, limit=5)
        for item in list(candidates.get('entries') or [])[:5]:
            candidate = info_to_track(item, track.requester)
            if not candidate:
                continue
            try:
                info = extract_sync(candidate.page_url, playlist=False)
                if info.get('url'):
                    return info
            except Exception:
                continue
    raise ValueError('No playable audio from source; may be restricted or blocked.')


# Spotify links: metadata-only, official authenticated API; no Spotify streaming.
def spotify_access_token() -> str:
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
        raise ValueError('Spotify API: SPOTIFY_CLIENT_ID w SPOTIFY_CLIENT_SECRET khas-hom f Railway Variables.')
    form = {'grant_type': 'client_credentials'}
    if SPOTIFY_REFRESH_TOKEN:
        form = {'grant_type': 'refresh_token', 'refresh_token': SPOTIFY_REFRESH_TOKEN}
    r = requests.post('https://accounts.spotify.com/api/token',
                      data=form, auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET), timeout=15)
    if r.status_code != 200:
        raise ValueError(f'Spotify authentication failed: HTTP {r.status_code}')
    return str(r.json()['access_token'])


def spotify_api(path: str, token: str, params: dict | None = None) -> dict:
    r = requests.get('https://api.spotify.com/v1' + path,
                     headers={'Authorization': 'Bearer ' + token}, params=params, timeout=15)
    if r.status_code == 403:
        raise ValueError('Spotify access refuse: playlist mashi dyalk/collaborateur aw API restrictions.')
    if r.status_code != 200:
        raise ValueError(f'Spotify API error HTTP {r.status_code}')
    return r.json()


def spotify_metadata_sync(link: str) -> tuple[str, list[str]]:
    parsed = spotify_type(link)
    if not parsed:
        raise ValueError('Lien Spotify track, album ou playlist s7i7 matloub.')
    kind, sid = parsed
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET) and kind in {'track', 'album'}:
        # Public oEmbed can show a title only, not give audio or a full album listing.
        response = requests.get('https://open.spotify.com/oembed', params={'url': link}, timeout=12)
        if response.status_code != 200:
            raise ValueError(f'Spotify oEmbed HTTP {response.status_code}')
        return f'Spotify {kind} (oEmbed)', [str(response.json().get('title', 'Titre non disponible'))[:170]]
    token = spotify_access_token()
    result: list[str] = []
    def fmt(item: dict) -> str | None:
        if not item or item.get('type') != 'track':
            return None
        artists = ', '.join(a.get('name','') for a in item.get('artists', []))
        return f"{item.get('name','Music')} — {artists}"[:170]
    if kind == 'track':
        item = spotify_api(f'/tracks/{sid}', token)
        return 'Spotify track', [fmt(item) or 'Track']
    if kind == 'album':
        item = spotify_api(f'/albums/{sid}', token)
        page = spotify_api(f'/albums/{sid}/tracks', token, {'limit': 50})
        for t in page.get('items', [])[:MAX_PLAYLIST_IMPORT]:
            name = fmt(t)
            if name: result.append(name)
        return str(item.get('name') or 'Spotify album'), result
    # In the current Spotify API, playlist-item retrieval may require owner or collaborator authorization.
    if not SPOTIFY_REFRESH_TOKEN:
        raise ValueError('Playlist Spotify: user OAuth w SPOTIFY_REFRESH_TOKEN khas-hom; accès owner/collab seulement.')
    playlist = spotify_api(f'/playlists/{sid}', token)
    page = spotify_api(f'/playlists/{sid}/items', token, {'limit': min(50, MAX_PLAYLIST_IMPORT)})
    for row in page.get('items', []):
        name = fmt(row.get('item') or row.get('track') or {})
        if name: result.append(name)
    return str(playlist.get('name') or 'Spotify playlist'), result


# ----- V6: Pass site-provided stream headers to FFmpeg, and diagnose silent sources. -----
def ffmpeg_input_args(info: dict) -> list[str]:
    """Build safe FFmpeg input flags from yt-dlp's stream metadata.

    FFmpeg is launched with an argument list (never a shell). HTTP header values
    are not printed, to avoid leaking expiring URLs or credentials into logs.
    """
    args = [
        '-nostdin', '-reconnect', '1', '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5', '-rw_timeout', '20000000',
    ]
    headers = info.get('http_headers') or {}
    if not isinstance(headers, dict):
        return args
    supported = {'user-agent', 'referer', 'origin', 'accept', 'accept-language',
                 'authorization', 'cookie'}
    lines = []
    for key, value in headers.items():
        if not isinstance(key, str) or key.lower() not in supported:
            continue
        if not isinstance(value, str) or not value or len(value) > 4096:
            continue
        if '\r' in key or '\n' in key or '\r' in value or '\n' in value:
            continue
        lines.append(f'{key}: {value}\r\n')
    if lines:
        args.extend(['-headers', ''.join(lines)])
    return args


def ffmpeg_before_options(info: dict) -> str:
    # discord.py's FFmpegPCMAudio splits this string using shlex.split.
    return ' '.join(shlex.quote(x) for x in ffmpeg_input_args(info))


def ffmpeg_problem(stderr_text: str, code: int = 0) -> str:
    """Classify FFmpeg errors without sending private media URLs to Discord/logs."""
    val = stderr_text.lower()
    if '403' in val or 'forbidden' in val:
        return 'HTTP 403: source katrefed le serveur (possible restriction).'
    if '401' in val or 'unauthorized' in val:
        return 'HTTP 401: source kat7taj accès autorisé.'
    if '429' in val or 'too many requests' in val:
        return 'HTTP 429: source 7eddat demandes.'
    if '404' in val or 'not found' in val:
        return 'HTTP 404: lien audio expiré ou indisponible.'
    if 'timed out' in val or 'timeout' in val:
        return 'Timeout: source ma jawbatch l FFmpeg.'
    if 'invalid data' in val or 'invalid argument' in val:
        return 'Format non lisible par FFmpeg.'
    if 'could not resolve' in val or 'name or service not known' in val:
        return 'DNS: source ma t7ellatch men serveur.'
    if 'ssl' in val or 'tls' in val or 'certificate' in val:
        return 'HTTPS/TLS: connexion m3a source fchlat.'
    if code:
        return f'FFmpeg exit code {code}; check source availability.'
    return 'FFmpeg ma sft ta PCM: 7taj diagnostic dyal source.'


def probe_stream_sync(info: dict) -> tuple[bool, str]:
    """Try to decode up to two seconds; checks remote media access, not Discord."""
    stream_url = info.get('url')
    if not isinstance(stream_url, str) or not stream_url.startswith(('https://', 'http://')):
        return False, 'Source ma 3tat ta URL HTTP compatible.'
    cmd = [FFMPEG, '-hide_banner', '-loglevel', 'error',
           *ffmpeg_input_args(info), '-i', stream_url,
           '-t', '2', '-vn', '-f', 's16le', '-ac', '2', '-ar', '48000', 'pipe:1']
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=35, check=False)
    except subprocess.TimeoutExpired:
        return False, 'FFmpeg timeout: source ma jawbatch.'
    except (OSError, ValueError) as exc:
        return False, f'FFmpeg start failed: {type(exc).__name__}'
    decoded = len(proc.stdout)
    if decoded >= 48000 * 2 * 2 // 2:
        return True, f'FFmpeg decoded {decoded // 192000:.1f}s PCM (access OK).'
    error_text = proc.stderr.decode('utf-8', 'replace')
    return False, f'FFmpeg produced {decoded} bytes PCM. {ffmpeg_problem(error_text, proc.returncode)}'


def playable_audio_sync(track: Track) -> tuple[dict, str]:
    """Preflight audio on the hosting machine before telling Discord 'now playing'.

    When a selected media format fails, try up to two alternate public audio
    formats from the same extraction result. Does not defeat access restrictions.
    """
    info = resolve_audio_sync(track)
    ok, message = probe_stream_sync(info)
    if ok:
        return info, message

    # FFmpeg can be denied a specific format even when another legitimate
    # format of the same publicly accessible item is available.
    formats = info.get('formats') or []
    if not isinstance(formats, list):
        formats = []
    seen = {str(info.get('url') or '')}
    options = []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        url = fmt.get('url')
        if not isinstance(url, str) or not url.startswith('https://') or url in seen:
            continue
        if fmt.get('acodec') in (None, 'none'):
            continue
        seen.add(url)
        # Prefer audio-only variants when possible, without requiring a single codec.
        score = (fmt.get('vcodec') not in (None, 'none'), fmt.get('ext') not in ('m4a', 'mp3', 'webm'))
        options.append((score, fmt))
    options.sort(key=lambda item: item[0])
    for _, fmt in options[:2]:
        alt = dict(info)
        alt.update(fmt)
        alt['http_headers'] = fmt.get('http_headers') or info.get('http_headers') or {}
        alt_ok, alt_message = probe_stream_sync(alt)
        if alt_ok:
            log.info('An alternate media format is playable for guild music source.')
            return alt, alt_message
        message = alt_message
    return {}, message


class MonitoredFFmpegAudio(discord.FFmpegPCMAudio):
    """Track decoded PCM bytes, so Now Playing cannot mask FFmpeg failures."""
    def __init__(self, *args, **kwargs):
        self.pcm_bytes = 0
        super().__init__(*args, **kwargs)

    def read(self) -> bytes:
        data = super().read()
        self.pcm_bytes += len(data)
        return data


async def respond(interaction: discord.Interaction, *args, **kwargs):
    """Reply through the webhook after a slow operation has been deferred."""
    if interaction.response.is_done():
        return await interaction.followup.send(*args, **kwargs)
    return await interaction.response.send_message(*args, **kwargs)


def invalidate_playback(st: MusicState):
    """Invalidate callbacks and lookups that were started before stop/leave/test."""
    st.generation += 1
    st.playback_id += 1
    st.current = None
    st.skip_requested = True
    if st.idle_task:
        st.idle_task.cancel()
        st.idle_task = None


def enqueue_tracks(st: MusicState, tracks: list[Track], generation: int,
                   *, priority: bool = False) -> list[Track]:
    # No awaits: concurrent requests cannot overfill the queue after extraction.
    if generation != st.generation or not st.voice or not st.voice.is_connected():
        raise ValueError('La demande a été annulée après stop/déconnexion.')
    accepted = tracks[:max(0, MAX_QUEUE - len(st.queue))]
    if not accepted:
        raise ValueError('Queue 3amra (80 max).')
    if priority:
        st.queue.extendleft(reversed(accepted))
    else:
        st.queue.extend(accepted)
    return accepted


async def ensure_voice(interaction: discord.Interaction, *, ephemeral: bool = False) -> Optional[MusicState]:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message('⛔ Had lcommande katkhdem ghir f server.', ephemeral=True)
        return None
    ch = interaction.user.voice.channel if interaction.user.voice else None
    if ch is None:
        await interaction.response.send_message('🔊 Dkhol l chi salon vocal louwel!', ephemeral=True)
        return None
    st = state_for(interaction.guild.id)
    # Discord requires an initial response within three seconds; voice joining
    # can take 25 seconds. Defer before the first network operation.
    await interaction.response.defer(thinking=True, ephemeral=ephemeral)
    try:
        async with st.connection_lock:
            voice = interaction.guild.voice_client
            if voice and not voice.is_connected():
                await voice.disconnect(force=True)
                invalidate_playback(st)
                voice = None
            if voice:
                st.voice = voice
                if voice.channel != ch:
                    await respond(interaction, '⛔ Khassk tkoun f nafs salon vocal m3a bot.', ephemeral=True)
                    return None
            else:
                st.voice = await ch.connect(timeout=25, reconnect=True)
    except (discord.DiscordException, asyncio.TimeoutError) as exc:
        log.warning('Voice join failed: %s', type(exc).__name__)
        await respond(interaction, '❌ Ma9drtch ndkhol vocal. Chof Connect/Speak permissions.', ephemeral=True)
        return None
    st.channel = interaction.channel
    if st.idle_task:
        st.idle_task.cancel()
    st.idle_task = asyncio.create_task(idle_disconnect(interaction.guild.id))
    return st

async def member_state(interaction: discord.Interaction) -> Optional[MusicState]:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message('⛔ Had lcommande ghir f server.', ephemeral=True)
        return None
    st = state_for(interaction.guild.id)
    voice = interaction.guild.voice_client
    if not voice or not interaction.user.voice or interaction.user.voice.channel != voice.channel:
        await interaction.response.send_message('🔊 Khassk tkoun f nafs vocal m3a TOV4R.', ephemeral=True)
        return None
    st.voice = voice
    return st

async def idle_disconnect(guild_id: int):
    try:
        await asyncio.sleep(180)
        st = state_for(guild_id)
        if (not st.current and not st.queue and st.voice and st.voice.is_connected()
                and not st.voice.is_playing() and not st.voice.is_paused()):
            # Do not cancel this task itself while it is disconnecting.
            st.idle_task = None
            invalidate_playback(st)
            await st.voice.disconnect()
            st.voice = None
    except asyncio.CancelledError:
        return

async def advance(guild_id: int):
    st = state_for(guild_id)
    async with st.lock:
        if st.voice and (st.voice.is_playing() or st.voice.is_paused()):
            return
        if st.idle_task:
            st.idle_task.cancel()
            st.idle_task = None
        if st.loop and st.current and not st.skip_requested:
            next_track = st.current
        elif st.queue:
            next_track = st.queue.popleft()
        else:
            st.current = None
            if st.voice and st.voice.is_connected():
                st.idle_task = asyncio.create_task(idle_disconnect(guild_id))
            return
        st.skip_requested = False
        st.current = next_track
        generation = st.generation
        st.playback_id += 1
        playback_id = st.playback_id
        if not st.voice or not st.voice.is_connected():
            st.current = None
            st.queue.clear()
            return
        try:
            # Refresh stream URLs at actual playback time to avoid expired URLs.
            info, diagnostic = await asyncio.to_thread(playable_audio_sync, next_track)
            if (generation != st.generation or playback_id != st.playback_id
                    or not st.voice or not st.voice.is_connected()):
                return
            if not info:
                # A playback error is not the same thing as a song not existing.
                raise ValueError(diagnostic)
            stream_url = info.get('url')
            if not stream_url:
                raise ValueError('Source audio indisponible')
            # FFmpeg needs the same request headers yt-dlp used to resolve the stream.
            # Capture errors privately: FFmpeg diagnostics may otherwise include signed URLs.
            ffmpeg_stderr = tempfile.TemporaryFile(mode='w+b')
            try:
                monitored_audio = MonitoredFFmpegAudio(
                    stream_url, executable=FFMPEG,
                    before_options=ffmpeg_before_options(info),
                    options='-vn -loglevel error',
                    stderr=ffmpeg_stderr,
                )
            except Exception:
                ffmpeg_stderr.close()
                raise
            audio = discord.PCMVolumeTransformer(monitored_audio, volume=st.volume)
            loop = asyncio.get_running_loop()
            async def finished(failed: bool):
                if generation != st.generation or playback_id != st.playback_id:
                    return
                if failed:
                    # Never loop a failed stream forever.
                    st.current = None
                await advance(guild_id)

            def after_play(error):
                # The audio player calls this once the FFmpeg source has stopped.
                try:
                    ffmpeg_stderr.seek(0)
                    stderr_text = ffmpeg_stderr.read(16000).decode('utf-8', 'replace')
                except (OSError, ValueError):
                    stderr_text = ''
                finally:
                    ffmpeg_stderr.close()
                pcm_seconds = monitored_audio.pcm_bytes / 192000
                if generation != st.generation or playback_id != st.playback_id:
                    return
                if st.skip_requested:
                    asyncio.run_coroutine_threadsafe(finished(False), loop)
                    return
                if error or monitored_audio.pcm_bytes < 96000:
                    reason = ffmpeg_problem(stderr_text, 0)
                    log.warning('STREAM FAILED: guild=%s pcm=%.2fs reason=%s player_error=%s',
                                guild_id, pcm_seconds, reason, type(error).__name__ if error else 'none')
                    if st.channel:
                        asyncio.run_coroutine_threadsafe(st.channel.send(
                            embed=embed('STREAM AUDIO MA KHEDMATCH',
                                '❌ yt-dlp l9a lghnya, walakin FFmpeg ma 9rach son.\n'
                                f'**Diagnostic:** {reason}\n'
                                'Jarrab `/testlink` b nafs lien, ou `/testffmpeg`.'),
                        ), loop)
                else:
                    log.info('STREAM FINISHED: guild=%s title=%r decoded=%.1fs',
                             guild_id, next_track.title[:80], pcm_seconds)
                asyncio.run_coroutine_threadsafe(finished(bool(error) or monitored_audio.pcm_bytes < 96000), loop)
            try:
                st.voice.play(audio, after=after_play)
            except Exception:
                audio.cleanup()
                ffmpeg_stderr.close()
                raise
            if st.channel:
                try:
                    e = embed('DABA KATTSMA3', f'**[{next_track.title}]({next_track.page_url})**\n⏱️ `{duration(next_track.duration)}` • 🎤 `{next_track.requester}`')
                    if next_track.thumbnail and safe_url(next_track.thumbnail):
                        e.set_thumbnail(url=next_track.thumbnail)
                    await st.channel.send(embed=e, view=Controls())
                except discord.HTTPException:
                    pass
        except Exception as exc:
            if generation != st.generation:
                return
            log.warning('Playback failed: guild=%s error=%s', guild_id, type(exc).__name__)
            st.current = None
            if st.channel:
                try:
                    # A short, sanitized diagnosis is much more useful than 'not found'.
                    reason = public_error(exc)
                    await st.channel.send(embed=embed('SOURCE MA KHEDMATCH',
                        '❌ Had source ma qdrach yqraha serveur.\n'
                        f'**Cause:** {reason[:160]}\n'
                        '⚠️ HTTP 403 kay3ni source rafdat l acces; ma kaynch fix garanti.\n'
                        'Ghadi njarrbo li mn b3dha.'))
                except discord.HTTPException:
                    pass
            asyncio.get_running_loop().call_soon(lambda: asyncio.create_task(advance(guild_id)))

async def do_pause(interaction: discord.Interaction):
    st = await member_state(interaction)
    if not st: return
    if st.voice and st.voice.is_playing():
        st.voice.pause()
        await interaction.response.send_message(embed=embed('SKOT', '⏸️ W9eft lmusic chwya.'))
    else:
        await interaction.response.send_message('Ma kayn ta music khddama daba.', ephemeral=True)

async def do_resume(interaction: discord.Interaction):
    st = await member_state(interaction)
    if not st: return
    if st.voice and st.voice.is_paused():
        st.voice.resume()
        await interaction.response.send_message(embed=embed('KML', '▶️ Rj3na lmusic!'))
    else:
        await interaction.response.send_message('Ma kaynach music mpaused.', ephemeral=True)

async def do_skip(interaction: discord.Interaction):
    st = await member_state(interaction)
    if not st: return
    if st.voice and (st.voice.is_playing() or st.voice.is_paused()):
        st.skip_requested = True
        st.voice.stop()
        await interaction.response.send_message(embed=embed('NEXT', '⏭️ Dzna lghnya!'))
    elif st.current and st.lock.locked():
        # Skip can also cancel a source while yt-dlp/FFmpeg is preparing it.
        st.playback_id += 1
        st.current = None
        st.skip_requested = True
        asyncio.create_task(advance(interaction.guild.id))
        await interaction.response.send_message(embed=embed('NEXT', '⏭️ Dzna lghnya li katwjed!'))
    else:
        await interaction.response.send_message('Ma kayn walo bach tskipih.', ephemeral=True)

class Controls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Pause', emoji='⏸️', style=discord.ButtonStyle.secondary, custom_id='tov4r:pause')
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await do_pause(interaction)

    @discord.ui.button(label='Resume', emoji='▶️', style=discord.ButtonStyle.success, custom_id='tov4r:resume')
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await do_resume(interaction)

    @discord.ui.button(label='Next', emoji='⏭️', style=discord.ButtonStyle.primary, custom_id='tov4r:skip')
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await do_skip(interaction)

    @discord.ui.button(label='Queue', emoji='📜', style=discord.ButtonStyle.secondary, custom_id='tov4r:queue')
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message('Server only', ephemeral=True)
            return
        await interaction.response.send_message(embed=queue_embed(state_for(interaction.guild.id)), ephemeral=True)

class Tov4rBot(commands.Bot):
    async def setup_hook(self):
        from server_setup import register_server_commands
        register_server_commands(self)
        self.add_view(Controls())
        # IMPORTANT: Always publish the global command tree.
        # Previously, setting GUILD_ID meant that only one server received commands.
        global_commands = await self.tree.sync()
        log.info('GLOBAL SYNC OK: %d slash commands available for installed servers.', len(global_commands))
        # Optional quick-sync for the original testing server; it does NOT replace global sync.
        if GUILD_ID:
            if not GUILD_ID.isdecimal():
                log.warning('GUILD_ID must be numeric or empty. Ignoring: %r', GUILD_ID)
            else:
                try:
                    test_guild = discord.Object(id=int(GUILD_ID))
                    self.tree.copy_global_to(guild=test_guild)
                    test_commands = await self.tree.sync(guild=test_guild)
                    log.info('TEST GUILD SYNC OK: %d commands in %s.', len(test_commands), GUILD_ID)
                except discord.DiscordException as error:
                    log.warning('Test server sync failed (global sync still active): %s', error)

intents = discord.Intents.default()
intents.voice_states = True
bot = Tov4rBot(command_prefix='!', intents=intents, help_command=None)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name='/sma3 • TOV4R MUSIC'))
    log.info('Online as %s', bot.user)
    log.info('SERVERS CONNECTED: %d | IDs: %s', len(bot.guilds), ', '.join(str(g.id) for g in bot.guilds) or '(none)')

@bot.event
async def on_guild_join(guild: discord.Guild):
    log.info('JOINED NEW SERVER: %s | ID: %s', guild.name, guild.id)

@bot.event
async def on_guild_remove(guild: discord.Guild):
    st = states.pop(guild.id, None)
    if st:
        invalidate_playback(st)
    log.info('LEFT SERVER: %s | ID: %s', guild.name, guild.id)


@bot.event
async def on_voice_state_update(member, before, after):
    if bot.user and member.id == bot.user.id and before.channel and after.channel is None:
        st = state_for(member.guild.id)
        invalidate_playback(st)
        st.queue.clear()
        st.loop = False
        st.voice = None

@bot.tree.command(name='ping', description='✅ TOV4R MUSIC: verifier que le bot repond dans ce serveur')
async def ping(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message('📍 Khassek tjarreb had commande f chi serveur Discord.', ephemeral=True)
        return
    await interaction.response.send_message(
        f'✅ **TOV4R MUSIC ONLINE** f **{interaction.guild.name}**!\n'
        f'🎵 `/sma3` — chghl music | 🎛️ `/tov4r` — help\n'
        f'📡 Ping: {round(bot.latency * 1000)} ms', ephemeral=True)

@bot.tree.command(name='sma3', description='🎵 Sma3 chi music: kteb smiya wla lien HTTPS')
@app_commands.describe(ghnya='Smiya, lien public ou fichier audio direct HTTPS')
async def sma3(interaction: discord.Interaction, ghnya: str):
    st = await ensure_voice(interaction)
    if not st: return
    generation = st.generation
    if len(st.queue) >= MAX_QUEUE:
        await interaction.followup.send('📛 Queue 3amra (80 max).', ephemeral=True)
        return
    try:
        tracks = await find_tracks(ghnya, interaction.user.display_name, min(MAX_QUEUE - len(st.queue), MAX_PLAYLIST_IMPORT))
        if not tracks:
            raise ValueError('Ma l9it ta track public w playable f had link.')
        tracks = enqueue_tracks(st, tracks, generation)
    except Exception as exc:
        log.warning('Track lookup failed: %s', type(exc).__name__)
        await interaction.followup.send(embed=embed('SOURCE NON DISPONIBLE', public_error(exc)), ephemeral=True)
        return
    detail = f'✅ **{len(tracks)}** track(s) tzado l Queue.\n🎵 **{tracks[0].title}**'
    if len(tracks) > 1: detail += f'\n📜 Playlist ({len(tracks)} titres) — `/list` bach tchouf lqueue.'
    await interaction.followup.send(embed=embed('TZADAT L QUEUE', detail))
    if not st.current and not st.lock.locked():
        asyncio.create_task(advance(interaction.guild.id))

@bot.tree.command(name='jaw', description='🔥 Khtar mood w TOV4R y9lleb lik 3la music')
@app_commands.describe(mood='Khtar jaw dyalk')
@app_commands.choices(mood=[
    app_commands.Choice(name='🌙 Lofi chill', value='lofi chill instrumental'),
    app_commands.Choice(name='🎮 Gaming focus', value='gaming background music instrumental'),
    app_commands.Choice(name='🇲🇦 Chaabi', value='moroccan chaabi music'),
    app_commands.Choice(name='🔥 Moroccan rap', value='moroccan rap music'),
    app_commands.Choice(name='⚡ Phonk', value='phonk music'),
])
async def jaw(interaction: discord.Interaction, mood: app_commands.Choice[str]):
    st = await ensure_voice(interaction)
    if not st: return
    generation = st.generation
    if len(st.queue) >= MAX_QUEUE:
        await interaction.followup.send('Queue 3amra.', ephemeral=True)
        return
    try:
        track = await find_track(mood.value, interaction.user.display_name)
        enqueue_tracks(st, [track], generation)
    except Exception as exc:
        log.warning('Mood search failed: %s', type(exc).__name__)
        await interaction.followup.send('❌ Mal9itch music l had jaw daba.', ephemeral=True)
        return
    await interaction.followup.send(embed=embed('JAW TOV4R', f'🔥 **{mood.name}**\n✅ Tzadat: **{track.title}**'))
    if not st.current and not st.lock.locked():
        asyncio.create_task(advance(interaction.guild.id))

@bot.tree.command(name='skot', description='⏸️ W9ef lmusic chwya')
async def skot(interaction: discord.Interaction):
    await do_pause(interaction)

@bot.tree.command(name='kml', description='▶️ Kmmel lmusic')
async def kml(interaction: discord.Interaction):
    await do_resume(interaction)

@bot.tree.command(name='next', description='⏭️ Doz lghnya')
async def next_song(interaction: discord.Interaction):
    await do_skip(interaction)

@bot.tree.command(name='saf', description='🧹 W9ef w khwi queue')
async def saf(interaction: discord.Interaction):
    st = await member_state(interaction)
    if not st: return
    st.queue.clear()
    st.loop = False
    invalidate_playback(st)
    if st.voice and (st.voice.is_playing() or st.voice.is_paused()):
        st.voice.stop()
    if st.voice and st.voice.is_connected():
        st.idle_task = asyncio.create_task(idle_disconnect(interaction.guild.id))
    await interaction.response.send_message(embed=embed('SAFI', '🧹 Queue tms7at w music w9fat.'))

def queue_embed(st: MusicState) -> discord.Embed:
    lines = [f'**🎶 Daba:** {st.current.title}' if st.current else '**🎶 Daba:** Walo']
    lines.extend(f'`{i:02}.` {t.title[:65]} • `{duration(t.duration)}`' for i,t in enumerate(list(st.queue)[:15], 1))
    if len(st.queue) > 15:
        lines.append(f'… w {len(st.queue)-15} okhrin')
    lines.append(f'\n🔁 Loop: **{"ON" if st.loop else "OFF"}** • 🔊 Volume: **{round(st.volume*100)}%**')
    return embed('TOV4R QUEUE', '\n'.join(lines))

@bot.tree.command(name='list', description='📜 Chof lqueue')
async def queue_list(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message('Server only', ephemeral=True)
        return
    await interaction.response.send_message(embed=queue_embed(state_for(interaction.guild.id)))

@bot.tree.command(name='chno', description='🎧 Chno kattsma3 daba?')
async def chno(interaction: discord.Interaction):
    st = state_for(interaction.guild.id) if interaction.guild else MusicState()
    if st.current:
        e = embed('DABA KATTSMA3', f'**[{st.current.title}]({st.current.page_url})**\n🎤 {st.current.requester}\n⏱️ {duration(st.current.duration)}')
        if st.current.thumbnail and safe_url(st.current.thumbnail):
            e.set_thumbnail(url=st.current.thumbnail)
        await interaction.response.send_message(embed=e, view=Controls())
    else:
        await interaction.response.send_message('🎵 Ma kayn ta chi ghnya daba.', ephemeral=True)

@bot.tree.command(name='dour', description='🔁 3awd nafs lghnya automatiquement')
async def dour(interaction: discord.Interaction):
    st = await member_state(interaction)
    if not st: return
    st.loop = not st.loop
    await interaction.response.send_message(embed=embed('LOOP MODE', f'🔁 Loop **{"ON ✅" if st.loop else "OFF ❌"}**'))

@bot.tree.command(name='mix', description='🔀 Khellt lqueue')
async def mix(interaction: discord.Interaction):
    st = await member_state(interaction)
    if not st: return
    songs = list(st.queue)
    random.shuffle(songs)
    st.queue = deque(songs)
    await interaction.response.send_message(embed=embed('MIX', f'🔀 Tkhlltat {len(songs)} ghnya!'))

@bot.tree.command(name='volume', description='🔊 Bdell volume dyal lmusic (0-100)')
@app_commands.describe(niveau='Volume bin 0 w 100')
async def volume(interaction: discord.Interaction, niveau: app_commands.Range[int, 0, 100]):
    st = await member_state(interaction)
    if not st: return
    st.volume = niveau / 100
    if st.voice and isinstance(st.voice.source, discord.PCMVolumeTransformer):
        st.voice.source.volume = st.volume
    await interaction.response.send_message(embed=embed('VOLUME', f'🔊 Volume daba **{niveau}%**'))

@bot.tree.command(name='khrej', description='👋 Khrej TOV4R mn vocal')
async def khrej(interaction: discord.Interaction):
    st = await member_state(interaction)
    if not st: return
    st.queue.clear()
    st.loop = False
    invalidate_playback(st)
    await interaction.response.defer(thinking=True)
    if st.voice:
        await st.voice.disconnect(force=True)
        st.voice = None
    await interaction.followup.send(embed=embed('BSSLAMA', '👋 TOV4R khrej mn vocal.'))

@bot.tree.command(name='dj', description='💎 DJ Premium status, prix w accès')
async def dj(interaction: discord.Interaction):
    valid, expiry = get_access(interaction.user.id)
    if valid:
        status = '👑 OWNER • accès gratuit à vie' if OWNER_ID == interaction.user.id else f'✅ DJ Premium actif jusqu’au <t:{expiry}:F>'
    else:
        status = '🔒 DJ Premium non actif'
    await interaction.response.send_message(embed=embed('DJ MODE',
        f'{status}\n\n💳 **Prix:** {DJ_PRICE}\n'
        'Pour acheter: tواصل مع propriétaire du bot. Activation manuelle après confirmation.\n\n'
        '**Commandes DJ:** `/djplay`, `/djremove`, `/djmove`, `/djclear`.\n'
        '**Gestion owner:** `/djgrant`, `/djrevoke`.\n'
        '🎵 Les commandes normales restent gratuites.'), ephemeral=True)

@bot.tree.command(name='djgrant', description='👑 Owner: active DJ Premium pour un membre')
@app_commands.describe(membre='Membre à activer', jours='Durée du pass, en jours (1-365)')
async def djgrant(interaction: discord.Interaction, membre: discord.Member, jours: app_commands.Range[int, 1, 365] = 30):
    if not OWNER_ID or interaction.user.id != OWNER_ID:
        await interaction.response.send_message('⛔ Hadi ghir l owner dyal TOV4R.', ephemeral=True)
        return
    if membre.bot:
        await interaction.response.send_message('⛔ Ma ymknch t3ti DJ Pass l bots.', ephemeral=True)
        return
    now = int(time.time())
    _, previous = get_access(membre.id)
    expiry = max(now, previous) + int(jours) * 86400
    with open_db() as conn:
        conn.execute('INSERT INTO dj_access (user_id, expires_at) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at', (membre.id, expiry))
        conn.commit()
    await interaction.response.send_message(embed=embed('DJ PASS ACTIVÉ',
        f'✅ {membre.mention} 3ndo DJ Premium 7tta <t:{expiry}:F>\n'
        '⚠️ T2kked mn paiement qbel ma tactivih.'), ephemeral=True)

@bot.tree.command(name='djrevoke', description='👑 Owner: supprime le DJ Premium d’un membre')
@app_commands.describe(membre='Membre à désactiver')
async def djrevoke(interaction: discord.Interaction, membre: discord.Member):
    if not OWNER_ID or interaction.user.id != OWNER_ID:
        await interaction.response.send_message('⛔ Hadi ghir l owner.', ephemeral=True)
        return
    if membre.id == OWNER_ID:
        await interaction.response.send_message('👑 Owner kayb9a free à vie.', ephemeral=True)
        return
    with open_db() as conn:
        conn.execute('DELETE FROM dj_access WHERE user_id=?', (membre.id,))
        conn.commit()
    await interaction.response.send_message(f'✅ DJ Premium t7yed mn {membre.mention}.', ephemeral=True)

@bot.tree.command(name='djplay', description='💎 DJ: 7ett chi ghnya louwla f queue')
@app_commands.describe(ghnya='Smiya, lien ou playlist publique')
async def djplay(interaction: discord.Interaction, ghnya: str):
    if not await require_dj(interaction): return
    st = await ensure_voice(interaction, ephemeral=True)
    if not st: return
    generation = st.generation
    if len(st.queue) >= MAX_QUEUE:
        await interaction.followup.send('Queue 3amra.', ephemeral=True)
        return
    try:
        tracks = await find_tracks(ghnya, interaction.user.display_name, min(MAX_QUEUE - len(st.queue), MAX_PLAYLIST_IMPORT))
        if not tracks:
            raise ValueError('Playlist/source non disponible.')
        tracks = enqueue_tracks(st, tracks, generation, priority=True)
    except Exception as exc:
        log.warning('DJ play lookup failed: %s', type(exc).__name__)
        await interaction.followup.send(f'❌ {public_error(exc)}', ephemeral=True)
        return
    await interaction.followup.send(embed=embed('DJ PRIORITY', f'💎 **{len(tracks)} tracks** tzado louwlin!\n🎧 {tracks[0].title}'), ephemeral=True)
    if not st.current and not st.lock.locked():
        asyncio.create_task(advance(interaction.guild.id))

@bot.tree.command(name='djremove', description='💎 DJ: 7yed ghnya mn queue b numéro')
@app_commands.describe(numero='Numéro depuis /list (1,2,3...)')
async def djremove(interaction: discord.Interaction, numero: app_commands.Range[int, 1, 80]):
    if not await require_dj(interaction): return
    st = await member_state(interaction)
    if not st: return
    items = list(st.queue)
    if numero > len(items):
        await interaction.response.send_message('❌ Had numéro makaynch f queue.', ephemeral=True)
        return
    removed = items.pop(numero - 1)
    st.queue = deque(items)
    await interaction.response.send_message(embed=embed('DJ REMOVE', f'🗑️ T7ydat **{removed.title}**.'), ephemeral=True)

@bot.tree.command(name='djmove', description='💎 DJ: bddl tartib dyal queue')
@app_commands.describe(mn='Numéro l9dim', l='Numéro jdid')
async def djmove(interaction: discord.Interaction, mn: app_commands.Range[int, 1, 80], l: app_commands.Range[int, 1, 80]):
    if not await require_dj(interaction): return
    st = await member_state(interaction)
    if not st: return
    items = list(st.queue)
    if mn > len(items) or l > len(items):
        await interaction.response.send_message('❌ Numéro kharej queue.', ephemeral=True)
        return
    track = items.pop(mn - 1)
    items.insert(l - 1, track)
    st.queue = deque(items)
    await interaction.response.send_message(embed=embed('DJ MOVE', f'🎛️ **{track.title}**: `{mn}` → `{l}`'), ephemeral=True)

@bot.tree.command(name='djclear', description='💎 DJ: khwi queue bla ma tstop music')
async def djclear(interaction: discord.Interaction):
    if not await require_dj(interaction): return
    st = await member_state(interaction)
    if not st: return
    count = len(st.queue)
    st.queue.clear()
    await interaction.response.send_message(embed=embed('DJ CLEAR', f'🧹 Tmsa7o {count} ghnya mn queue.'), ephemeral=True)

@bot.tree.command(name='status', description='🩺 Wach TOV4R connected w kaytsma3 f vocal?')
async def status(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message('⛔ Server only.', ephemeral=True)
        return
    st = state_for(interaction.guild.id)
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        state = '🔴 Ma dakhloch l vocal'
    elif vc.is_paused():
        state = '⏸️ Music paused'
    elif vc.is_playing():
        state = '🟢 Discord voice player kaykhdem (ma kaydmench belli l audio kaytsma3)'
    else:
        state = '🟠 F vocal walakin ma kayplayi ta audio daba'
    channel_name = vc.channel.name if vc and vc.channel else '—'
    current = st.current.title if st.current else '—'
    await interaction.response.send_message(embed=embed('TOV4R • STATUS',
        f'**Statut:** {state}\n**Salon:** {channel_name}\n'
        f'**Track:** {current}\n**FFmpeg:** {"✅ Kayn f PATH" if shutil.which(FFMPEG) else "❌ Ma tl9ach"}\n\n'
        '💡 Ila silent, dir `/testaudio` (pure Python), puis `/testffmpeg`. Sift CMD logs.'), ephemeral=True)

class PureTone(discord.AudioSource):
    """12s, 440 Hz, 48kHz stereo PCM — no FFmpeg or internet needed."""
    def __init__(self, seconds: int = 12):
        self.packet = 0
        self.packets = seconds * 50
        self.one_second = bytearray()
        for i in range(48_000):
            sample = int(10000 * math.sin(2 * math.pi * 440 * i / 48_000))
            self.one_second.extend(struct.pack('<hh', sample, sample))
        self.one_second = bytes(self.one_second)

    def read(self) -> bytes:
        if self.packet >= self.packets:
            log.info('PURE AUDIO: all %s PCM packets delivered to voice player', self.packet)
            return b''
        offset = (self.packet % 50) * 3840
        self.packet += 1
        if self.packet == 1 or self.packet % 100 == 0:
            log.info('PURE AUDIO: generated packet %s/%s', self.packet, self.packets)
        return self.one_second[offset:offset + 3840]

    def is_opus(self) -> bool:
        return False


async def prepare_audio_test(interaction: discord.Interaction):
    st = await ensure_voice(interaction, ephemeral=True)
    if not st:
        return None
    vc = st.voice
    if not vc or not vc.is_connected():
        await interaction.followup.send('❌ Bot ma connectach l vocal.', ephemeral=True)
        return None
    # Cancel ongoing music before testing (otherwise testaudio only said "busy").
    st.loop = False
    st.queue.clear()
    invalidate_playback(st)
    if vc.is_playing() or vc.is_paused():
        vc.stop()
        await asyncio.sleep(0.4)
    st.current = None
    st.idle_task = asyncio.create_task(idle_disconnect(interaction.guild.id))
    return vc


@bot.tree.command(name='testaudio', description='🔊 Test son 12s b pure Python, bla FFmpeg/YouTube')
async def testaudio(interaction: discord.Interaction):
    vc = await prepare_audio_test(interaction)
    if not vc:
        return
    try:
        sound = PureTone(seconds=12)
        log.info('PURE AUDIO START: opus_loaded=%s voice_connected=%s', discord.opus.is_loaded(), vc.is_connected())
        def after_tone(error):
            if error:
                log.error('PURE AUDIO error: %r', error)
            else:
                log.info('PURE AUDIO ended normally; sent %s/%s PCM packets', sound.packet, sound.packets)
        vc.play(sound, after=after_tone)
        await interaction.followup.send(embed=embed('TEST AUDIO • PYTHON',
            '🔊 **Daba 12 secondes dyal son test!** 7ett volume bot 100%, o sm3 mzyan.\n'
            'Had test ma kayst3ml la FFmpeg la YouTube: ila b9a silent rah khassna nsaybo Discord voice/Opus.\n'
            '📋 F CMD khassk tchouf: `PURE AUDIO START`, `PURE AUDIO: generated packet` o `PURE AUDIO ended normally`.\n'
            '❗ Ila ma sme3ti walo, sift screenshot CMD mn b3d 12s, bla Token.'), ephemeral=True)
    except Exception as exc:
        log.exception('PURE AUDIO FAILED: %s', exc)
        await interaction.followup.send(embed=embed('TEST AUDIO • ERROR',
            f'❌ Error: `{type(exc).__name__}`. Sift screenshot CMD, bla Token.'), ephemeral=True)


@bot.tree.command(name='testffmpeg', description='🎚️ Test FFmpeg direct 12 secondes, bla internet')
async def testffmpeg(interaction: discord.Interaction):
    vc = await prepare_audio_test(interaction)
    if not vc:
        return
    try:
        audio = discord.FFmpegPCMAudio(
            'sine=frequency=523:sample_rate=48000:duration=12',
            executable=FFMPEG, before_options='-f lavfi',
            options='-ac 2 -ar 48000 -loglevel warning',
        )
        log.info('FFMPEG AUDIO START: ffmpeg=%s', FFMPEG)
        vc.play(audio, after=lambda error: log.error('FFMPEG TEST error: %r', error) if error else log.info('FFMPEG TEST ended normally'))
        await interaction.followup.send(embed=embed('TEST FFMPEG',
            '🎚️ Ghadi tsma3 son test 12s. Ila `/testaudio` khddam o hadi la, lmochkil f FFmpeg.\n'
            '📋 Sift screenshot dyal CMD ila ma sm3ti walo.'), ephemeral=True)
    except Exception as exc:
        log.exception('FFMPEG TEST failed: %s', exc)
        await interaction.followup.send(f'❌ FFmpeg test error: `{type(exc).__name__}`. Sift CMD.', ephemeral=True)


@bot.tree.command(name='testlink', description='🔎 Chof wach lien/music t9dr tt7el b yt-dlp')
@app_commands.describe(lien='Lien HTTPS public: song, stream mp3 ou m3u8')
async def testlink(interaction: discord.Interaction, lien: str):
    if not interaction.guild:
        await interaction.response.send_message('⛔ Server only.', ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not safe_url(lien) or len(lien) > 500:
        await interaction.followup.send('❌ Lien HTTPS s7i7, 500 caractères max.', ephemeral=True)
        return
    try:
        track = await find_track(lien, interaction.user.display_name)
        info, diagnostic = await asyncio.wait_for(
            asyncio.to_thread(playable_audio_sync, track), timeout=130)
        works = bool(info and info.get('url'))
        await interaction.followup.send(embed=embed('TEST LINK • SOURCE AUDIO',
            f'✅ Lien t9ra: **{track.title}**\n'
            + ('✅ **FFmpeg qra son:** ' if works else '❌ **FFmpeg ma 9rach son:** ')
            + diagnostic + '\n\n'
            + ('Jarrab `/sma3` daba.' if works else
               'Hadchi y3ni lmochkil f source/FFmpeg, machi f Discord Voice.')),
            ephemeral=True)
    except Exception as exc:
        log.warning('Test link failed: %s: %s', type(exc).__name__, public_error(exc))
        await interaction.followup.send(embed=embed('TEST LINK • MA KHEDMATCH',
            f'❌ Had lien ma t7ellch: **{type(exc).__name__}**\n'
            'Jarrab lien YouTube/SoundCloud akhor, ou met a jour yt-dlp. '
            'Chof CMD logs bla ma tsift token.'), ephemeral=True)

@bot.tree.command(name='spotifyinfo', description='🟢 Lien Spotify: afficher infos playlist/album/track (pas de stream)')
@app_commands.describe(lien='Lien Spotify track/album/playlist')
async def spotifyinfo(interaction: discord.Interaction, lien: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not spotify_type(lien):
        await interaction.followup.send('❌ Lien open.spotify.com/track, /album ou /playlist requis.', ephemeral=True)
        return
    try:
        title, titles = await asyncio.wait_for(asyncio.to_thread(spotify_metadata_sync, lien), timeout=65)
        lines = '\n'.join(f'`{n:02}` {t[:130]}' for n, t in enumerate(titles[:15], 1))
        extra = f'\n…et {len(titles)-15} autres.' if len(titles)>15 else ''
        await interaction.followup.send(embed=embed('SPOTIFY • METADATA ONLY',
            f'**{title}**\n{lines}{extra}\n\n🔗 [Ouvrir sur Spotify]({lien})\n'
            '⚠️ L API fournit les infos, pas le son a diffuser sur Discord. '
            'Pour jouer: utilise un lien audio qui permet sa diffusion.'), ephemeral=True)
    except Exception as exc:
        log.warning('Spotify metadata failed: %s', type(exc).__name__)
        await interaction.followup.send(embed=embed('SPOTIFY NON DISPONIBLE', public_error(exc)), ephemeral=True)


@bot.tree.command(name='tov4r', description='🎛️ Panel + ga3 commandes + DJ Premium')
async def tov4r(interaction: discord.Interaction):
    e = embed('TOV4R MUSIC • GUIDE & CONTROL ROOM',
        '🎧 **Mer7ba bik f TOV4R MUSIC!**\n'
        'Les commandes normales **FREE**, DJ Mode **Premium**.\n'
        '🔊 Dkhol l salon vocal, w kteb `/sma3` bach tbda!')
    e.add_field(name='🎵 MUSIC • FREE', value=(
        '`/sma3` — smiya, YouTube/SoundCloud/Bandcamp playlists\n'
        '`/spotifyinfo` — infos Spotify (pas de streaming)\n'
        '`/jaw` — choix dyal vibe: lofi, gaming, chaabi, rap, phonk\n'
        '`/skot` — pause lmusic\n'
        '`/kml` — kmmel lmusic\n'
        '`/next` — doz lghnya li mn b3d\n'
        '`/saf` — 7bes lmusic w khwi queue\n'
        '`/list` — chof queue\n'
        '`/chno` — chno kattsma3 daba\n'
        '`/dour` — repeat lghnya\n'
        '`/mix` — khllat queue\n'
        '`/volume` — bdell volume 0-100\n'
        '`/khrej` — khrej bot mn vocal\n'
        '`/status` — statut dyal bot f vocal\n'
        '`/ping` — chof wach bot kayjawb f had serveur\n'
        '`/testaudio` — 12 secondes test bla FFmpeg / lien\n'
        '`/testffmpeg` — test FFmpeg direct 12s\n'
        '`/testlink` — test lien w yt-dlp'), inline=False)
    e.add_field(name='💎 DJ MODE • PREMIUM', value=(
        '`/dj` — chof prix w status dyal abonnement\n'
        '`/djplay` — zid ghnya louwla f queue\n'
        '`/djremove` — 7yed chi ghnya b numéro\n'
        '`/djmove` — bddl tartib dyal queue\n'
        '`/djclear` — khwi queue bla stop dyal lmusic\n'
        f'💳 **Prix:** {DJ_PRICE} • Activation manuelle mn owner'), inline=False)
    e.add_field(name='👑 OWNER ONLY', value=(
        '`/djgrant` — f3el DJ Premium ba3d paiement\n'
        '`/djrevoke` — 7yed DJ Premium\n'
        '👑 Owner 3ndo DJ access free à vie.'), inline=False)
    await interaction.response.send_message(embed=e, view=Controls())

@bot.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception('Command failed: %s', error)
    msg = '❌ Wa9e3 mochkil. 3awed jarrab w chof logs ila b9a.'
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass

if __name__ == '__main__':
    if not TOKEN:
        raise SystemExit('DISCORD_TOKEN missing. Copy .env.example to .env and fill token.')
    if not shutil.which(FFMPEG):
        raise SystemExit('FFmpeg not found. Install FFmpeg and put it in PATH, or set FFMPEG_PATH.')
    bot.run(TOKEN, log_handler=None)
