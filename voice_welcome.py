"""Opt-in playback-only welcome for a single configured voice channel per guild."""
import asyncio
import logging
import os
from pathlib import Path
import time
import tempfile

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

LOG = logging.getLogger("tov4r.voice_welcome")
GREETING = "مرحبا بيك ف سيرفر توفار! أنا البوت ديال السيرفر، وهاد الصوت مولد بالذكاء الاصطناعي. نتمنى ليك وقت زوين."
MAX_AUDIO = 2_000_000


def can_manage_voice_welcome(interaction):
    # Use Discord's resolved permissions in this guild, not role names.
    return bool(interaction.guild is not None and (
        interaction.user.id == interaction.guild.owner_id
        or interaction.permissions.administrator
    ))


def should_greet(member, before, after, channel_id):
    return bool(channel_id and not member.bot and after.channel
                and before.channel != after.channel and after.channel.id == channel_id
                and isinstance(after.channel, discord.VoiceChannel))


class VoiceWelcome(commands.Cog):
    welcome = app_commands.Group(name="voicewelcome", description="Configure automatic voice greetings")

    def __init__(self, bot):
        self.bot = bot
        self.audio = Path(os.getenv("DATABASE_PATH", "data/community.db")).resolve().parent / "welcome-darija-v1.mp3"
        self.audio_lock = asyncio.Lock()
        self.last = {}
        self.clients = {}  # Only connections created by this module.

    async def cog_load(self):
        self.keep_connected.start()

    async def cog_unload(self):
        self.keep_connected.cancel()

    async def ensure_connection(self, guild, channel):
        me = guild.me
        perms = channel.permissions_for(me) if me else None
        if not perms or not all((perms.view_channel, perms.connect, perms.speak)):
            return None
        client = guild.voice_client
        if client is not None:
            if self.clients.get(guild.id) is not client:
                return None  # Never move or interrupt another voice feature.
            if client.is_connected() and client.channel == channel:
                return client
            await client.disconnect(force=True)
            self.clients.pop(guild.id, None)
        client = await channel.connect(timeout=15, reconnect=True, self_deaf=True)
        self.clients[guild.id] = client
        return client

    @tasks.loop(seconds=30)
    async def keep_connected(self):
        if not self.audio.is_file():
            return
        for guild in list(self.bot.guilds):
            channel_id = self.bot.store.config(guild.id)['voice_welcome_channel']
            if not channel_id or guild.unavailable:
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                continue
            lock = self.bot.locks[('voice_welcome', guild.id)]
            if lock.locked():
                continue
            async with lock:
                if self.bot.store.config(guild.id)['voice_welcome_channel'] != channel.id:
                    continue
                try:
                    await self.ensure_connection(guild, channel)
                except (discord.DiscordException, asyncio.TimeoutError, OSError, RuntimeError):
                    LOG.warning('Voice welcome connection unavailable guild=%s', guild.id)

    @keep_connected.before_loop
    async def before_keep_connected(self):
        await self.bot.wait_until_ready()

    async def prepare(self):
        async with self.audio_lock:
            if self.audio.is_file() and 0 < self.audio.stat().st_size <= MAX_AUDIO:
                return
            # A fixed, locally synthesized clip is bundled with the container.
            # No per-join network call, quota or paid speech API is necessary.
            bundled = Path(__file__).resolve().parent / "welcome-offline.mp3"
            if not bundled.is_file() or not 0 < bundled.stat().st_size <= MAX_AUDIO:
                raise ValueError("Offline welcome clip is missing or invalid.")
            self.audio.parent.mkdir(parents=True, exist_ok=True)
            temp = self.audio.with_suffix(".tmp")
            temp.write_bytes(bundled.read_bytes())
            temp.replace(self.audio)

    def custom_audio(self, guild_id):
        return self.audio.parent / f'welcome-custom-{int(guild_id)}.ogg'

    def playback_path(self, guild_id):
        custom = self.custom_audio(guild_id)
        return custom if custom.is_file() and 0 < custom.stat().st_size <= MAX_AUDIO else self.audio

    @welcome.command(name='audio', description='Upload an authorized custom welcome recording (max 2 MB, first 15 seconds)')
    @app_commands.guild_only()
    async def upload_audio(self, i: discord.Interaction, file: discord.Attachment):
        if not can_manage_voice_welcome(i):
            return await i.response.send_message('Server owner or Administrator only.', ephemeral=True)
        formats = {'.mp3': 'mp3', '.wav': 'wav', '.ogg': 'ogg', '.m4a': 'mov'}
        fmt = formats.get(Path(file.filename).suffix.lower())
        if not fmt or not 0 < file.size <= MAX_AUDIO:
            return await i.response.send_message('Upload MP3, WAV, OGG or M4A, up to 2 MB. Use only audio you have permission to play.', ephemeral=True)
        await i.response.defer(ephemeral=True, thinking=True)
        target = self.custom_audio(i.guild_id)
        try:
            async with self.audio_lock:
                data = await asyncio.wait_for(file.read(), timeout=15)
                if not 0 < len(data) <= MAX_AUDIO:
                    raise ValueError('Invalid audio size')
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
                    source = Path(tmp) / 'input'
                    output = Path(tmp) / 'welcome.ogg'
                    source.write_bytes(data)
                    proc = await asyncio.create_subprocess_exec('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-protocol_whitelist', 'file,pipe', '-f', fmt, '-i', str(source), '-map', '0:a:0', '-vn', '-t', '15', '-af', 'loudnorm=I=-18:TP=-2:LRA=7', '-ar', '48000', '-ac', '2', '-c:a', 'libopus', '-b:a', '96k', '-y', str(output), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=20)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                        raise
                    if proc.returncode or not output.is_file() or not 0 < output.stat().st_size <= MAX_AUDIO:
                        raise ValueError('Audio conversion failed')
                    output.replace(target)
        except (ValueError, OSError, discord.DiscordException, asyncio.TimeoutError, aiohttp.ClientError):
            return await i.followup.send('Invalid audio or conversion failed. Existing greeting unchanged.', ephemeral=True)
        await i.followup.send('Custom welcome saved for this server. Audio is normalized and converted to Opus. Existing voice room settings are unchanged.', ephemeral=True)

    @welcome.command(name='reset_audio', description='Remove custom recording and return to the default voice')
    @app_commands.guild_only()
    async def reset_audio(self, i: discord.Interaction):
        if not can_manage_voice_welcome(i):
            return await i.response.send_message('Server owner or Administrator only.', ephemeral=True)
        self.custom_audio(i.guild_id).unlink(missing_ok=True)
        await i.response.send_message('Default greeting restored.', ephemeral=True)

    @welcome.command(name="setup", description="Enable free offline greeting in one voice room")
    @app_commands.guild_only()
    @app_commands.describe(channel="Voice channel to greet in", confirm="Confirm automatic playback in this room")
    async def setup(self, i: discord.Interaction, channel: discord.VoiceChannel, confirm: bool = False):
        if not can_manage_voice_welcome(i):
            return await i.response.send_message("Server owner or Administrator only.", ephemeral=True)
        if channel.id == self.bot.store.config(i.guild_id)["voice_creator"]:
            return await i.response.send_message("Choose a normal voice room, not the TAB creator.", ephemeral=True)
        me = i.guild.me
        perms = channel.permissions_for(me) if me else None
        if not perms or not all((perms.view_channel, perms.connect, perms.speak)):
            return await i.response.send_message("Bot needs View Channel, Connect and Speak in that room.", ephemeral=True)
        if not confirm:
            return await i.response.send_message("Preview: the bot stays in this voice room even when empty and plays a fixed locally synthesized Arabic greeting on entry. No member audio is recorded and no speech API is required. Run again with confirm=True to enable.", ephemeral=True)
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await self.prepare()
        except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            LOG.warning("Voice welcome preparation failed: %s", type(exc).__name__)
            return await i.followup.send(str(exc) if isinstance(exc, ValueError) else "Voice generation failed; existing settings unchanged.", ephemeral=True)
        async with self.bot.locks[('voice_welcome', i.guild_id)]:
            self.bot.store.update(i.guild_id, {'voice_welcome_channel': channel.id})
            try:
                client = await self.ensure_connection(i.guild, channel)
            except (discord.DiscordException, asyncio.TimeoutError, OSError, RuntimeError):
                client = None
        status = 'Connected; the bot stays in the room even when empty.' if client else 'Saved, but not connected yet. Check voice permissions or another active voice connection.'
        await i.followup.send(f'Voice welcome enabled in {channel.mention}. {status} Playback only; no recording.', ephemeral=True)

    @welcome.command(name="off", description="Disable automatic voice greeting")
    @app_commands.guild_only()
    async def off(self, i: discord.Interaction):
        if not can_manage_voice_welcome(i):
            return await i.response.send_message("Server owner or Administrator only.", ephemeral=True)
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('voice_welcome', i.guild_id)]:
            self.bot.store.update(i.guild_id, {'voice_welcome_channel': 0})
            client = self.clients.pop(i.guild_id, None)
            if client is not None and i.guild.voice_client is client:
                try:
                    await client.disconnect(force=True)
                except (discord.DiscordException, OSError, RuntimeError):
                    return await i.followup.send('Disabled; voice disconnect could not be verified.', ephemeral=True)
        await i.followup.send('Voice welcome disabled.', ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        cfg = self.bot.store.config(member.guild.id)
        if not should_greet(member, before, after, cfg["voice_welcome_channel"]):
            return
        if not self.audio.is_file():
            return
        guild, channel = member.guild, after.channel
        lock = self.bot.locks[("voice_welcome", guild.id)]
        # Avoid overlapping greetings or interrupting another voice feature.
        if lock.locked() or time.monotonic() - self.last.get(guild.id, float("-inf")) < 8:
            return
        async with lock:
            if not member.voice or member.voice.channel != channel:
                return
            if self.bot.store.config(guild.id)["voice_welcome_channel"] != channel.id:
                return
            self.last[guild.id] = time.monotonic()
            client = None
            started_playback = False
            try:
                client = await self.ensure_connection(guild, channel)
                if client is None or client.is_playing() or not any(not m.bot for m in channel.members):
                    return
                done = asyncio.Event()
                loop = asyncio.get_running_loop()
                def finished(error):
                    if error:
                        LOG.warning("Welcome playback failed: %s", type(error).__name__)
                    loop.call_soon_threadsafe(done.set)
                # Give the joining Discord client time to establish its audio stream.
                await asyncio.sleep(1.2)
                if not member.voice or member.voice.channel != channel or not client.is_connected():
                    return
                clip = self.playback_path(guild.id)
                if clip.suffix == '.ogg':
                    source = discord.FFmpegOpusAudio(str(clip), codec='copy', options='-vn')
                else:
                    source = discord.FFmpegOpusAudio(str(clip), bitrate=96, options='-vn -af loudnorm=I=-18:TP=-2:LRA=7')
                try:
                    client.play(source, after=finished)
                    started_playback = True
                except Exception:
                    source.cleanup()
                    raise
                await asyncio.wait_for(done.wait(), timeout=25)
            except (discord.DiscordException, asyncio.TimeoutError, OSError, RuntimeError) as exc:
                LOG.warning("Voice welcome failed guild=%s: %s", guild.id, type(exc).__name__)
            finally:
                if started_playback and client is not None and client.is_playing():
                    client.stop()
                # Stay connected after playback, including when the room is empty.
