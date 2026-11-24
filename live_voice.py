"""Experimental /tl3 live voice loop. Disabled until receive dependency is reviewed.
Only the guild owner in a solo voice channel can opt in. No disk recordings.
"""
from __future__ import annotations

import asyncio
import audioop
import io
import logging
import os
import time
import discord
from discord import app_commands
from discord.ext import commands
from voice_ai import generate_audio

LOG = logging.getLogger("tov4r.live_voice")
MAX_SESSION = 300
MAX_TURN_BYTES = 24_000 * 2 * 20

class Turn:
    def __init__(self):
        self.pcm = bytearray()
        self.last_loud = None
        self.rate_state = None

    def feed(self, frame, now):
        if len(frame) % 4 or len(frame) > 7680:
            return
        mono = audioop.tomono(frame, 2, 0.5, 0.5)
        pcm, self.rate_state = audioop.ratecv(mono, 2, 1, 48000, 24000, self.rate_state)
        loud = audioop.rms(pcm, 2) > 400
        if loud:
            self.last_loud = now
        if self.last_loud is not None and len(self.pcm) < MAX_TURN_BYTES:
            self.pcm.extend(pcm[:MAX_TURN_BYTES-len(self.pcm)])

    def ready(self, now):
        return self.last_loud is not None and (
            now - self.last_loud >= 0.85 or len(self.pcm) >= MAX_TURN_BYTES
        )

    def take(self):
        data = bytes(self.pcm)
        self.pcm.clear()
        self.last_loud = None
        self.rate_state = None
        return data if len(data) >= 12000 else b""


class LiveVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.key = os.getenv("OPENAI_API_KEY", "").strip()
        self.guild_id = os.getenv("TOV4R_AI_GUILD_ID", "").strip()
        self.enabled = os.getenv("TOV4R_LIVE_VOICE", "") == "1"
        self.sessions = {}

    def allowed(self, i):
        return bool(self.enabled and self.key and self.guild_id.isdecimal()
                    and i.guild and i.guild.id == int(self.guild_id)
                    and i.user.id == i.guild.owner_id)

    def alone(self, channel, owner):
        return (any(m.id == owner for m in channel.members)
                and all(m.id in (owner, self.bot.user.id) for m in channel.members))

    @app_commands.command(name="tl3", description="Join solo voice and listen to your microphone for AI replies")
    @app_commands.guild_only()
    async def join(self, i: discord.Interaction):
        if not self.allowed(i):
            await i.response.send_message("Live AI غير مفعّل أو ما عندكش الصلاحية.", ephemeral=True)
            return
        channel = getattr(getattr(i.user, "voice", None), "channel", None)
        if not isinstance(channel, discord.VoiceChannel) or not self.alone(channel, i.user.id):
            await i.response.send_message("دخل لروم صوتية بوحدك أولاً.", ephemeral=True)
            return
        if i.guild.id in self.sessions or i.guild.voice_client:
            await i.response.send_message("كاين اتصال صوتي من قبل.", ephemeral=True)
            return
        try:
            from discord.ext import voice_recv
        except ImportError:
            await i.response.send_message("Live voice receiver غير مثبت.", ephemeral=True)
            return
        # Explicitly disclose transmission before opening the microphone receiver.
        await i.response.send_message(
            "🎙️ /tl3 كيشغّل الاستماع لصوتك وكيصيفط أسئلتك لـ OpenAI. "
            "الجواب صوت AI. /hbt باش توقف. الجلسة 5 دقايق كحد أقصى.",
            ephemeral=True)
        if i.guild.id in self.sessions or i.guild.voice_client:
            return
        task = asyncio.create_task(self.run(i, channel, voice_recv))
        self.sessions[i.guild.id] = task

    async def run(self, i, channel, voice_recv):
        vc = None
        owner = i.user.id
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=100)
        accepting = False
        active = True
        receiver_failed = asyncio.Event()
        turn = Turn()

        def offer(data):
            if active and accepting and self.alone(channel, owner) and not queue.full():
                queue.put_nowait(data)

        class Sink(voice_recv.AudioSink):
            def wants_opus(self):
                return False
            def write(self, user, data):
                if active and accepting and user and user.id == owner:
                    pcm = data.pcm
                    if pcm and len(pcm) <= 7680:
                        loop.call_soon_threadsafe(offer, bytes(pcm))
            def cleanup(self):
                pass

        def after_receive(error):
            loop.call_soon_threadsafe(receiver_failed.set)

        try:
            if not self.alone(channel, owner):
                return
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False,
                                       timeout=15, reconnect=False)
            if not self.alone(channel, owner):
                return
            vc.listen(Sink(), after=after_receive)
            accepting = True
            async with asyncio.timeout(MAX_SESSION):
                while vc.is_connected() and self.alone(channel, owner):
                    if receiver_failed.is_set():
                        raise RuntimeError("Voice receiver stopped")
                    try:
                        data = await asyncio.wait_for(queue.get(), 0.1)
                        turn.feed(data, time.monotonic())
                    except asyncio.TimeoutError:
                        pass
                    if not turn.ready(time.monotonic()):
                        continue
                    pcm = turn.take()
                    if not pcm:
                        continue
                    accepting = False
                    while not queue.empty():
                        queue.get_nowait()
                    if not self.alone(channel, owner):
                        break
                    answer = await generate_audio(self.key, pcm=pcm)
                    if not self.alone(channel, owner):
                        break
                    source = discord.FFmpegPCMAudio(io.BytesIO(answer), pipe=True,
                        before_options="-f s16le -ar 24000 -ac 1", options="-vn")
                    finished = loop.create_future()
                    def on_play(error):
                        def done():
                            if not finished.done():
                                finished.set_result(error)
                        loop.call_soon_threadsafe(done)
                    try:
                        vc.play(source, after=on_play)
                    except BaseException:
                        source.cleanup()
                        raise
                    if await asyncio.wait_for(finished, 70):
                        raise RuntimeError("Playback failed")
                    accepting = True
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            LOG.warning("Live voice ended: %s", type(exc).__name__)
            try:
                await i.followup.send("Live voice توقف بسبب مشكل فالاتصال أو الاستقبال.", ephemeral=True)
            except discord.HTTPException:
                pass
        finally:
            active = False
            accepting = False
            try:
                if vc:
                    await vc.disconnect(force=True)
            finally:
                self.sessions.pop(i.guild.id, None)

    @app_commands.command(name="hbt", description="Stop AI microphone listening and disconnect")
    @app_commands.guild_only()
    async def leave(self, i: discord.Interaction):
        if not i.guild or i.user.id != i.guild.owner_id:
            await i.response.send_message("غير صاحب السيرفر.", ephemeral=True)
            return
        task = self.sessions.get(i.guild.id)
        if task:
            task.cancel()
        await i.response.send_message("طلب إيقاف AI." if task else "AI ما خدامش.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        task = self.sessions.get(member.guild.id)
        vc = member.guild.voice_client
        if task and vc and vc.channel and not self.alone(vc.channel, member.guild.owner_id):
            task.cancel()
