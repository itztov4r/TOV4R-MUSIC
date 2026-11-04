"""TOV4R MUSIC — free self-hosted Darija Discord music bot."""
import asyncio
import math
import struct
import logging
import os
import random
import sqlite3
import time
import shutil
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

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
OWNER_ID = int(os.getenv('OWNER_ID', '0') or '0')
DJ_PRICE = os.getenv('DJ_PRICE', 'Prix à demander au propriétaire')
DB_PATH = os.getenv('DJ_DB_PATH', 'dj_access.sqlite3')

def open_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute('CREATE TABLE IF NOT EXISTS dj_access (user_id INTEGER PRIMARY KEY, expires_at INTEGER NOT NULL)')
    conn.commit()
    return conn

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


logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger('tov4r')

@dataclass
class Track:
    title: str
    page_url: str
    duration: int = 0
    thumbnail: str = ''
    requester: str = 'Unknown'

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

def extract_sync(query: str, search: bool = True) -> dict:
    opts = {
        'format': 'bestaudio/best', 'quiet': True, 'no_warnings': True,
        'noplaylist': True, 'skip_download': True,
        'extract_flat': 'in_playlist' if search else False,
        'default_search': 'ytsearch1', 'socket_timeout': 20,
        'retries': 2,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(query, download=False)
    if not result:
        raise ValueError('Ma l9it ta résultat.')
    if 'entries' in result:
        result = next((i for i in result['entries'] if i), None)
    if not result:
        raise ValueError('Ma l9it ta résultat.')
    return result

def safe_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == 'https' and bool(p.netloc)

async def find_track(text: str, requester: str) -> Track:
    text = text.strip()
    if not text or len(text) > 250:
        raise ValueError('Kteb smiya dyal lghnya (max 250 caractères).')
    if text.startswith(('http://', 'https://')):
        if not safe_url(text):
            raise ValueError('3tini lien HTTPS s7i7.')
        host = (urlparse(text).hostname or '').lower()
        if host == 'spotify.com' or host.endswith('.spotify.com') or host == 'open.spotify.com':
            raise ValueError('Spotify links ma fihomch source audio direct. Jarrab lien YouTube ou SoundCloud.')
        query = text
    else:
        query = 'ytsearch1:' + text
    info = await asyncio.to_thread(extract_sync, query)
    url = info.get('webpage_url') or info.get('original_url') or info.get('url')
    if not url or not safe_url(url):
        raise ValueError('Had source ma t9drch ttsma3.')
    return Track(str(info.get('title') or text)[:180], url, int(info.get('duration') or 0), str(info.get('thumbnail') or ''), requester)

async def ensure_voice(interaction: discord.Interaction) -> Optional[MusicState]:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message('⛔ Had lcommande katkhdem ghir f server.', ephemeral=True)
        return None
    ch = interaction.user.voice.channel if interaction.user.voice else None
    if ch is None:
        await interaction.response.send_message('🔊 Dkhol l chi salon vocal louwel!', ephemeral=True)
        return None
    st = state_for(interaction.guild.id)
    try:
        if interaction.guild.voice_client:
            st.voice = interaction.guild.voice_client
            if st.voice.channel != ch:
                # Prevent remote control from a different voice channel.
                await interaction.response.send_message('⛔ Khassk tkoun f nafs salon vocal m3a bot.', ephemeral=True)
                return None
        else:
            st.voice = await ch.connect(timeout=25, reconnect=True)
    except (discord.DiscordException, asyncio.TimeoutError) as exc:
        log.warning('Voice join failed: %s', exc)
        await interaction.response.send_message('❌ Ma9drtch ndkhol vocal. Chof Connect/Speak permissions.', ephemeral=True)
        return None
    st.channel = interaction.channel
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
        if not st.current and not st.queue and st.voice and st.voice.is_connected():
            await st.voice.disconnect()
            st.voice = None
    except asyncio.CancelledError:
        return

async def advance(guild_id: int):
    st = state_for(guild_id)
    async with st.lock:
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
        if not st.voice or not st.voice.is_connected():
            st.current = None
            st.queue.clear()
            return
        try:
            # Refresh stream URLs at actual playback time to avoid expired URLs.
            info = await asyncio.to_thread(extract_sync, next_track.page_url, False)
            stream_url = info.get('url')
            if not stream_url:
                raise ValueError('Source audio indisponible')
            audio = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(
                    stream_url, executable=FFMPEG,
                    before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                    options='-vn -loglevel error',
                ), volume=st.volume,
            )
            loop = asyncio.get_running_loop()
            def after_play(error):
                if error:
                    log.warning('Playback error: %s', error)
                else:
                    log.info('Playback ended: %s', next_track.title)
                asyncio.run_coroutine_threadsafe(advance(guild_id), loop)
            st.voice.play(audio, after=after_play)
            if st.channel:
                try:
                    e = embed('DABA KATTSMA3', f'**[{next_track.title}]({next_track.page_url})**\n⏱️ `{duration(next_track.duration)}` • 🎤 `{next_track.requester}`')
                    if next_track.thumbnail and safe_url(next_track.thumbnail):
                        e.set_thumbnail(url=next_track.thumbnail)
                    await st.channel.send(embed=e, view=Controls())
                except discord.HTTPException:
                    pass
        except Exception as exc:
            log.warning('Cannot play %s: %s', next_track.title, exc)
            st.current = None
            if st.channel:
                try:
                    await st.channel.send(embed=embed('SOURCE MA KHEDMATCH', 'Had lghnya ma khdematch daba. Ghadi njarrbo li mn b3dha.'))
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
    log.info('LEFT SERVER: %s | ID: %s', guild.name, guild.id)

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
@app_commands.describe(ghnya='Smiya dyal lghnya wla lien HTTPS')
async def sma3(interaction: discord.Interaction, ghnya: str):
    st = await ensure_voice(interaction)
    if not st: return
    await interaction.response.defer(thinking=True)
    if len(st.queue) >= MAX_QUEUE:
        await interaction.followup.send('📛 Queue 3amra (80 max).', ephemeral=True)
        return
    try:
        track = await find_track(ghnya, interaction.user.display_name)
    except Exception as exc:
        log.warning('Track lookup failed: %s', exc)
        await interaction.followup.send(embed=embed('MA L9ITOUCH', 'Ma l9itch had lghnya. Jarrab smiya okhra wla lien s7i7.'), ephemeral=True)
        return
    st.queue.append(track)
    await interaction.followup.send(embed=embed('TZADAT L QUEUE', f'✅ **{track.title}**\n🎤 {track.requester} • ⏱️ {duration(track.duration)}'))
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
    await interaction.response.defer(thinking=True)
    if len(st.queue) >= MAX_QUEUE:
        await interaction.followup.send('Queue 3amra.', ephemeral=True)
        return
    try:
        track = await find_track(mood.value, interaction.user.display_name)
    except Exception as exc:
        log.warning('Mood search failed: %s', exc)
        await interaction.followup.send('❌ Mal9itch music l had jaw daba.', ephemeral=True)
        return
    st.queue.append(track)
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
    st.skip_requested = True
    if st.voice and (st.voice.is_playing() or st.voice.is_paused()):
        st.voice.stop()
    else:
        st.current = None
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
    st.current = None
    st.skip_requested = True
    if st.voice:
        await st.voice.disconnect(force=True)
        st.voice = None
    await interaction.response.send_message(embed=embed('BSSLAMA', '👋 TOV4R khrej mn vocal.'))

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
@app_commands.describe(ghnya='Smiya wla lien HTTPS')
async def djplay(interaction: discord.Interaction, ghnya: str):
    if not await require_dj(interaction): return
    st = await ensure_voice(interaction)
    if not st: return
    await interaction.response.defer(thinking=True, ephemeral=True)
    if len(st.queue) >= MAX_QUEUE:
        await interaction.followup.send('Queue 3amra.', ephemeral=True)
        return
    try:
        track = await find_track(ghnya, interaction.user.display_name)
    except Exception as exc:
        log.warning('DJ play lookup failed: %s', exc)
        await interaction.followup.send('❌ Ma l9itch had lghnya.', ephemeral=True)
        return
    st.queue.appendleft(track)
    await interaction.followup.send(embed=embed('DJ PRIORITY', f'💎 **{track.title}** tzadat louwla f queue!'), ephemeral=True)
    if not st.current and not st.lock.locked():
        asyncio.create_task(advance(interaction.guild.id))

@bot.tree.command(name='djremove', description='💎 DJ: 7yed ghnya mn queue b numéro')
@app_commands.describe(numero='Numéro depuis /list (1,2,3...)')
async def djremove(interaction: discord.Interaction, numero: app_commands.Range[int, 1, 80]):
    if not await require_dj(interaction): return
    st = await member_state(interaction)
    if not st: return
    async with st.lock:
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
    async with st.lock:
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
    async with st.lock:
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
    st = await ensure_voice(interaction)
    if not st:
        return None
    await interaction.response.defer(thinking=True, ephemeral=True)
    vc = st.voice
    if not vc or not vc.is_connected():
        await interaction.followup.send('❌ Bot ma connectach l vocal.', ephemeral=True)
        return None
    # Cancel ongoing music before testing (otherwise testaudio only said "busy").
    st.loop = False
    st.queue.clear()
    st.skip_requested = True
    if vc.is_playing() or vc.is_paused():
        vc.stop()
        await asyncio.sleep(0.4)
    st.current = None
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
@app_commands.describe(lien='Lien HTTPS dyal chi song (YouTube/SoundCloud etc.)')
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
        info = await asyncio.wait_for(asyncio.to_thread(extract_sync, track.page_url, False), timeout=40)
        stream_url = info.get('url')
        if not stream_url or not isinstance(stream_url, str):
            raise ValueError('Aucune URL audio directe')
        await interaction.followup.send(embed=embed('TEST LINK • METADATA OK',
            f'✅ yt-dlp 9ra lien: **{track.title}**\n'
            '✅ L9a source audio potentielle.\n\n'
            '⚠️ Had test ma kaydmench l audio fih son ou FFmpeg y9dr ytsal b serveur. '
            'Jarrab `/sma3` w chof logs dyal CMD; ila `/testaudio` khddam, lmochkil f source.'), ephemeral=True)
    except Exception as exc:
        log.warning('Test link failed: %s: %s', type(exc).__name__, str(exc)[:350])
        await interaction.followup.send(embed=embed('TEST LINK • MA KHEDMATCH',
            f'❌ Had lien ma t7ellch: **{type(exc).__name__}**\n'
            'Jarrab lien YouTube/SoundCloud akhor, ou met a jour yt-dlp. '
            'Chof CMD logs bla ma tsift token.'), ephemeral=True)

@bot.tree.command(name='tov4r', description='🎛️ Panel + ga3 commandes + DJ Premium')
async def tov4r(interaction: discord.Interaction):
    e = embed('TOV4R MUSIC • GUIDE & CONTROL ROOM',
        '🎧 **Mer7ba bik f TOV4R MUSIC!**\n'
        'Les commandes normales **FREE**, DJ Mode **Premium**.\n'
        '🔊 Dkhol l salon vocal, w kteb `/sma3` bach tbda!')
    e.add_field(name='🎵 MUSIC • FREE', value=(
        '`/sma3` — chghl ghnya b smiya wla lien\n'
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
