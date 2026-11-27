"""Opt-in playback-only welcome for a single configured voice channel per guild."""
import asyncio
import logging
import os
from pathlib import Path
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

LOG = logging.getLogger("tov4r.voice_welcome")
GREETING = "مرحبا بيك ف سيرفر توفار! أنا البوت ديال السيرفر، وهاد الصوت مولد بالذكاء الاصطناعي. نتمنى ليك وقت زوين."
MAX_AUDIO = 2_000_000


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

    async def prepare(self):
        async with self.audio_lock:
            if self.audio.is_file() and 0 < self.audio.stat().st_size <= MAX_AUDIO:
                return
            key = os.getenv("OPENAI_API_KEY", "").strip()
            if not key:
                raise ValueError("OpenAI API key is not configured.")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
                async with session.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": "Bearer " + key},
                    json={"model": "gpt-4o-mini-tts", "voice": "coral", "input": GREETING,
                          "instructions": "Speak naturally in Moroccan Darija, friendly and clear.",
                          "response_format": "mp3"},
                ) as response:
                    if response.status != 200:
                        raise ValueError(f"Voice generation unavailable (HTTP {response.status}).")
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_AUDIO:
                            raise ValueError("Generated audio exceeds the allowed size.")
            if not data:
                raise ValueError("Empty voice response.")
            self.audio.parent.mkdir(parents=True, exist_ok=True)
            temp = self.audio.with_suffix(".tmp")
            temp.write_bytes(data)
            temp.replace(self.audio)

    @welcome.command(name="setup", description="Enable greeting in one voice room; first generation may incur API cost")
    @app_commands.guild_only()
    @app_commands.describe(channel="Voice channel to greet in", confirm="Approve one-time AI audio generation")
    async def setup(self, i: discord.Interaction, channel: discord.VoiceChannel, confirm: bool = False):
        if i.user.id != i.guild.owner_id:
            return await i.response.send_message("Server owner only.", ephemeral=True)
        if channel.id == self.bot.store.config(i.guild_id)["voice_creator"]:
            return await i.response.send_message("Choose a normal voice room, not the TAB creator.", ephemeral=True)
        me = i.guild.me
        perms = channel.permissions_for(me) if me else None
        if not perms or not all((perms.view_channel, perms.connect, perms.speak)):
            return await i.response.send_message("Bot needs View Channel, Connect and Speak in that room.", ephemeral=True)
        if not confirm:
            return await i.response.send_message("Preview: a fixed Darija AI-generated greeting plays on entry. No member audio or names are sent to the provider. First generation may incur a small API charge. Run again with confirm=True to enable.", ephemeral=True)
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await self.prepare()
        except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            LOG.warning("Voice welcome preparation failed: %s", type(exc).__name__)
            return await i.followup.send(str(exc) if isinstance(exc, ValueError) else "Voice generation failed; existing settings unchanged.", ephemeral=True)
        self.bot.store.update(i.guild_id, {"voice_welcome_channel": channel.id})
        await i.followup.send(f"AI-generated Darija greeting enabled in {channel.mention}. Playback only; no recording.", ephemeral=True)

    @welcome.command(name="off", description="Disable automatic voice greeting")
    @app_commands.guild_only()
    async def off(self, i: discord.Interaction):
        if i.user.id != i.guild.owner_id:
            return await i.response.send_message("Server owner only.", ephemeral=True)
        self.bot.store.update(i.guild_id, {"voice_welcome_channel": 0})
        await i.response.send_message("Voice welcome disabled.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        cfg = self.bot.store.config(member.guild.id)
        if not should_greet(member, before, after, cfg["voice_welcome_channel"]):
            return
        if not self.audio.is_file():
            return
        guild, channel = member.guild, after.channel
        lock = self.bot.locks[("voice_welcome", guild.id)]
        # Avoid queueing bursts, interrupting another voice feature, or joining an empty room.
        if lock.locked() or time.monotonic() - self.last.get(guild.id, float("-inf")) < 8:
            return
        async with lock:
            if guild.voice_client or not member.voice or member.voice.channel != channel:
                return
            if self.bot.store.config(guild.id)["voice_welcome_channel"] != channel.id:
                return
            self.last[guild.id] = time.monotonic()
            client = None
            try:
                client = await channel.connect(timeout=15, reconnect=False, self_deaf=True)
                if not any(not m.bot for m in channel.members):
                    return
                done = asyncio.Event()
                loop = asyncio.get_running_loop()
                def finished(error):
                    if error:
                        LOG.warning("Welcome playback failed: %s", type(error).__name__)
                    loop.call_soon_threadsafe(done.set)
                source = discord.FFmpegPCMAudio(str(self.audio), options="-vn")
                try:
                    client.play(source, after=finished)
                except Exception:
                    source.cleanup()
                    raise
                await asyncio.wait_for(done.wait(), timeout=25)
            except (discord.DiscordException, asyncio.TimeoutError, OSError, RuntimeError) as exc:
                LOG.warning("Voice welcome failed guild=%s: %s", guild.id, type(exc).__name__)
            finally:
                if client is not None:
                    try:
                        await client.disconnect(force=True)
                    except discord.DiscordException:
                        LOG.warning("Voice welcome disconnect failed guild=%s", guild.id)
