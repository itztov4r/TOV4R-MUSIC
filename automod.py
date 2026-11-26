"""Bounded guild-scoped moderation; no automatic bans or content retention."""
import re
import time
import logging
from collections import defaultdict,deque
from datetime import timedelta
import discord
from discord import app_commands
from discord.ext import commands
from verification_gate import public_access

log=logging.getLogger("tov4r.automod")
WINDOW=86400
RETENTION=30*WINDOW
INVITE=re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)",re.I)
LINK=re.compile(r"(?:https?://|www\.)\S+",re.I)

def content_reason(cfg,text,mentions=0,mass=False):
    words=[w.strip().casefold() for w in cfg["blocked_words"].split(",") if w.strip()]
    if cfg["security_enabled"] and (mentions>=8 or mass): return "Mass mentions"
    if any(w in text.casefold() for w in words): return "Blocked word"
    if cfg["anti_invites"] and INVITE.search(text): return "Discord invite"
    if cfg["anti_links"] and LINK.search(text): return "External link"
    return None

def escalation(count):
    return 5 if count>=5 else 1 if count>=3 else 0

def incident(store,guild,user,channel,message,reason,now):
    """At most one counted incident per member per 30 seconds; each message once."""
    with store.db:
        if store.db.execute("SELECT 1 FROM automod_incidents WHERE guild=? AND message=?",(guild,message)).fetchone(): return None
        if store.db.execute("SELECT 1 FROM automod_incidents WHERE guild=? AND user=? AND at>? LIMIT 1",(guild,user,now-30)).fetchone(): return None
        cur=store.db.execute("INSERT INTO automod_incidents(guild,user,channel,message,reason,at,action) VALUES(?,?,?,?,?,?,'removed')",(guild,user,channel,message,reason,now))
        n=store.db.execute("SELECT COUNT(*) n FROM automod_incidents WHERE guild=? AND user=? AND at>? AND dismissed=0",(guild,user,now-WINDOW)).fetchone()["n"]
        return cur.lastrowid,n

class AutoMod:
    def __init__(self,bot):
        self.bot=bot
        self.samples=defaultdict(deque)
    def prune(self,now=None):
        now=time.monotonic() if now is None else now
        for k,q in list(self.samples.items()):
            if not q or now-q[-1][0]>30: self.samples.pop(k,None)
    def reason(self,message,cfg,edited=False,now=None):
        reason=content_reason(cfg,message.content,len(set(message.raw_mentions))+len(set(message.raw_role_mentions)),message.mention_everyone)
        if reason or edited or not cfg["antispam"]: return reason
        now=time.monotonic() if now is None else now
        q=self.samples[(message.guild.id,message.author.id)]
        # Compare in memory only, using process-local hashes; no stored content.
        normalized=" ".join(message.content.casefold().split())
        fingerprint=hash(normalized) if len(normalized)>=12 else None
        q.append((now,fingerprint))
        while q and now-q[0][0]>10: q.popleft()
        if len(q)>=8: return "Message flood"
        if fingerprint is not None and sum(h==fingerprint for _,h in q)>=4: return "Repeated messages"
        return None
    async def handle(self,message,edited=False):
        member=message.author; guild=message.guild
        if member.bot or member.guild_permissions.manage_messages or member.guild_permissions.administrator or member.id==guild.owner_id: return False
        cfg=self.bot.store.config(guild.id)
        reason=self.reason(message,cfg,edited)
        if not reason: return False
        async with self.bot.locks[("automod",guild.id,member.id)]:
            try: await message.delete()
            except discord.NotFound: return True
            except discord.HTTPException:
                return True  # No strike or timeout for an unsuccessful deletion.
            record=incident(self.bot.store,guild.id,member.id,message.channel.id,message.id,reason,time.time())
            if record is None: return True
            iid,count=record
            mins=escalation(count) if cfg["automod_progressive"] and reason in ("Message flood","Repeated messages","Mass mentions") else 0
            action="removed; warning recorded"
            if mins and guild.me.guild_permissions.moderate_members and member.top_role<guild.me.top_role and not member.is_timed_out():
                try:
                    await member.timeout(timedelta(minutes=mins),reason=f"TOV4R AutoMod case {iid}: {reason}")
                    action=f"timeout {mins} min"
                except discord.HTTPException: action="removed; timeout refused"
            self.bot.store.run("UPDATE automod_incidents SET action=? WHERE guild=? AND id=?",(action,guild.id,iid))
            # A single private notice; closed DMs are fine. Never expose message contents.
            try: await member.send(f"TOV4R • {guild.name}: message removed ({reason}). Case #{iid}. {action}. Contact server staff if this was a mistake.",allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException: pass
            channel=guild.get_channel(cfg["security_channel"] or cfg["logs_channel"])
            if isinstance(channel,discord.TextChannel) and not public_access(self.bot,guild,channel):
                # Require no explicit view grants except administrators, bot, or configured support.
                safe=True
                for target,ow in channel.overwrites.items():
                    if ow.view_channel is True and target.id!=guild.me.id:
                        if not isinstance(target,discord.Role) or not (target.permissions.administrator or target.id==cfg["support_role"]):
                            safe=False; break
                if safe:
                    try:
                        await channel.send(embed=discord.Embed(title=f"TOV4R • AutoMod #{iid}",description=f"Member ID: {member.id}\nReason: {reason}\nRecent incidents: {count}\nAction: {action}",colour=0xef3340),allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException: pass
            return True

async def reply(i,text):
    if i.response.is_done(): await i.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    else: await i.response.send_message(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

class AutoModCommands(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.command(description="Review recent automatic moderation cases privately")
    async def mod_history(self,i:discord.Interaction,member:discord.Member|None=None):
        args=(i.guild_id,)
        sql="SELECT id,user,reason,at,action,dismissed FROM automod_incidents WHERE guild=?"
        if member: sql+=" AND user=?"; args+=(member.id,)
        rows=self.bot.store.all(sql+" ORDER BY id DESC LIMIT 10",args)
        lines=[f"#{r['id']} • {r['user']} • {r['reason']} • {r['action']}"+(" • dismissed" if r["dismissed"] else "") for r in rows]
        await reply(i,"\n".join(lines) or "No automatic moderation cases.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.command(description="Dismiss an incorrect AutoMod case from escalation")
    async def mod_dismiss(self,i:discord.Interaction,case_id:app_commands.Range[int,1,2147483647]):
        r=self.bot.store.one("SELECT user FROM automod_incidents WHERE guild=? AND id=?",(i.guild_id,case_id))
        if not r: return await reply(i,"Case not found.")
        target=i.guild.get_member(r["user"])
        if target and i.user.id!=i.guild.owner_id and target.top_role>=i.user.top_role: return await reply(i,"Cannot moderate this member.")
        self.bot.store.run("UPDATE automod_incidents SET dismissed=1 WHERE guild=? AND id=?",(i.guild_id,case_id))
        await reply(i,"Case dismissed. Existing timeouts are not changed; use /untimeout if appropriate.")
    async def cog_app_command_error(self,i,error):
        await reply(i,"Moderation permission required or action unavailable.")
