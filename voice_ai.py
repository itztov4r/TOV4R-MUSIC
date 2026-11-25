"""Opt-in spoken AI replies, with text or explicitly submitted audio questions.
No listening to Discord voice channels and no persistent audio files.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from urllib.parse import urlencode

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

LOG = logging.getLogger("tov4r.voice_ai")
MAX_AUDIO = 2 * 1024 * 1024
PCM_FORMAT = {"type": "audio/pcm", "rate": 24000}
MODEL = "gpt-realtime-2.1-mini"
VOICE = "marin"
INSTRUCTIONS = (
    "You are TOV4R, an AI voice assistant, not a person. Respond in the language "
    "of the question, including Moroccan Darija, French or English. Keep answers "
    "brief, clear, and suitable for users aged 13–17. Avoid explicit sexual or "
    "graphic violent content, self-harm details, dangerous instructions, harmful "
    "body advice, and romantic or intimate roleplay. Do not claim to execute server "
    "actions, access live information, or be the user's human friend. Never ask for "
    "credentials or private information. No tools are available."
)

def valid_question(question, attachment):
    return bool(question) != bool(attachment) and (not question or 1 <= len(question.strip()) <= 500)

async def convert_audio(data):
    if not data or len(data) > MAX_AUDIO:
        raise ValueError("Audio too large")
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "pipe",
        "-i", "pipe:0", "-t", "30", "-vn", "-ac", "1", "-ar", "24000",
        "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        pcm, _ = await asyncio.wait_for(process.communicate(data), 12)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode or len(pcm) < 4800 or len(pcm) > 1440000:
        raise ValueError("Unsupported or empty audio")
    return pcm

async def generate_audio(key, question=None, pcm=None):
    url = "wss://api.openai.com/v1/realtime?" + urlencode({"model": MODEL})
    result = bytearray()
    async with asyncio.timeout(55):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, headers={"Authorization": "Bearer " + key},
                                          max_msg_size=4 * 1024 * 1024) as ws:
                await ws.send_json({"type": "session.update", "session": {
                    "type": "realtime", "instructions": INSTRUCTIONS,
                    "output_modalities": ["audio"], "max_output_tokens": 512,
                    "audio": {"input": {"format": PCM_FORMAT, "turn_detection": None},
                              "output": {"format": PCM_FORMAT, "voice": VOICE}},
                    "tools": [], "tool_choice": "none",
                }})
                while True:
                    event = await ws.receive_json()
                    if event["type"] == "error":
                        raise RuntimeError("Realtime setup failed")
                    if event["type"] == "session.updated":
                        break
                part = ({"type": "input_audio", "audio": base64.b64encode(pcm).decode("ascii")}
                        if pcm is not None else {"type": "input_text", "text": question})
                await ws.send_json({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "user", "content": [part]}})
                await ws.send_json({"type": "response.create"})
                while True:
                    event = await ws.receive_json()
                    if event["type"] == "error":
                        raise RuntimeError("Realtime response failed")
                    if event["type"] == "response.output_audio.delta":
                        result.extend(base64.b64decode(event["delta"], validate=True))
                        if len(result) > 2880000:
                            raise ValueError("Output exceeds one minute")
                    elif event["type"] == "response.done":
                        if event.get("response", {}).get("status") != "completed":
                            raise RuntimeError("Incomplete response")
                        break
    if not result:
        raise ValueError("Empty AI response")
    return bytes(result)


class VoiceAI(commands.GroupCog, group_name="voiceai", group_description="TOV4R spoken AI replies"):
    def __init__(self, bot):
        self.bot = bot
        self.key = os.getenv("OPENAI_API_KEY", "").strip()
        self.guild_id = os.getenv("TOV4R_AI_GUILD_ID", "").strip()
        self.all_guilds = os.getenv("TOV4R_AI_ALL_GUILDS", "") == "1"
        self.quota = []
        self.active = {}
        self.last = 0.0

    def permitted(self, i):
        return (bool(self.key) and i.guild is not None
                and (self.all_guilds or (self.guild_id.isdecimal() and i.guild.id == int(self.guild_id)))
                and i.user.id == i.guild.owner_id)

    def private_channel(self, i):
        channel = getattr(getattr(i.user, "voice", None), "channel", None)
        if not isinstance(channel, discord.VoiceChannel):
            return None
        return channel if all(m.id in (i.user.id, self.bot.user.id) for m in channel.members) else None

    @app_commands.command(name="ask", description="Send a question or your own audio to OpenAI; spoken answer")
    @app_commands.describe(question="Your question (max 500 characters)",
                           audio="Your own audio file, max 2 MB / first 30 seconds; sent to OpenAI")
    @app_commands.guild_only()
    async def ask(self, i: discord.Interaction, question: str = "", audio: discord.Attachment | None = None):
        if not self.permitted(i):
            await i.response.send_message("Voice AI غير مفعّل لهاد الحساب.", ephemeral=True)
            return
        channel = self.private_channel(i)
        if not channel or i.guild.voice_client:
            await i.response.send_message("دخل لروم صوتية بوحدك وخلي البوت يكون خارج أي اتصال.", ephemeral=True)
            return
        if not valid_question(question, audio) or (audio and audio.size > MAX_AUDIO):
            await i.response.send_message("سؤال مكتوب واحد أو تسجيل صوتك أنت (2MB كحد أقصى). التسجيل كيتسيفط لـ OpenAI.", ephemeral=True)
            return
        if i.guild.id in self.active or time.monotonic() - self.last < 15:
            await i.response.send_message("كاين طلب جاري أو خاصك تستنى شوية.", ephemeral=True)
            return
        now = time.monotonic()
        self.quota = [t for t in self.quota if now - t < 86400]
        if len(self.quota) >= 10:
            await i.response.send_message("Voice AI: limite globale de requêtes atteinte.", ephemeral=True)
            return
        self.quota.append(now)
        self.last = now
        task = asyncio.current_task()
        self.active[i.guild.id] = task
        vc = None
        try:
            await i.response.defer(ephemeral=True, thinking=True)
            pcm = await convert_audio(await asyncio.wait_for(audio.read(), 10)) if audio else None
            answer = await generate_audio(self.key, question=question.strip(), pcm=pcm)
            if not self.permitted(i) or self.private_channel(i) != channel or i.guild.voice_client:
                raise ValueError("Voice room changed")
            me = i.guild.me
            if not me or not channel.permissions_for(me).connect or not channel.permissions_for(me).speak:
                raise ValueError("Missing voice permissions")
            vc = await channel.connect(timeout=15, reconnect=False, self_deaf=True)
            if self.private_channel(i) != channel:
                raise ValueError("Voice room changed")
            await i.followup.send("🔊 جواب بصوت مولّد بالذكاء الاصطناعي. البوت ما كيسمعش الميكرو.", ephemeral=True)
            source = discord.FFmpegPCMAudio(io.BytesIO(answer), pipe=True,
                                          before_options="-f s16le -ar 24000 -ac 1",
                                          options="-vn")
            ended = asyncio.get_running_loop().create_future()
            loop = asyncio.get_running_loop()
            def finish(error):
                def resolve():
                    if not ended.done():
                        ended.set_result(error)
                loop.call_soon_threadsafe(resolve)
            try:
                vc.play(source, after=finish)
            except BaseException:
                source.cleanup()
                raise
            error = await asyncio.wait_for(ended, 75)
            if error:
                raise RuntimeError("Playback failed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("Voice AI failed: %s", type(exc).__name__)
            if i.response.is_done():
                await i.followup.send("ما قدرش البوت يجاوب بالصوت. راجع الإعدادات والصلاحيات.", ephemeral=True)
        finally:
            try:
                if vc:
                    await vc.disconnect(force=True)
            finally:
                self.active.pop(i.guild.id, None)

    @app_commands.command(name="leave", description="Stop the AI voice session")
    @app_commands.guild_only()
    async def leave(self, i: discord.Interaction):
        if not self.permitted(i):
            await i.response.send_message("غير صاحب السيرفر يقدر يوقف AI.", ephemeral=True)
            return
        task = self.active.get(i.guild.id)
        if task:
            task.cancel()
        await i.response.send_message("تم طلب إيقاف AI." if task else "AI ما خدامش دابا.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        task = self.active.get(member.guild.id)
        vc = member.guild.voice_client
        if task and vc and vc.channel:
            if (member.id == member.guild.owner_id and after.channel != vc.channel
                or member.id not in (member.guild.owner_id, self.bot.user.id)
                and after.channel == vc.channel):
                task.cancel()
