from __future__ import annotations
import asyncio
import collections
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import time
from datetime import timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from store import Store, level
from security_monitor import SecurityMonitor
from server_templates import TEMPLATE_CHOICES, TEMPLATES, apply_template, format_result
from focus_setup import focus_server, restore_server, restore_active_template
from setup_service import provision_server, provision_modules, reserved_staff_role
from level_rewards import TIERS, reward_role, tracked_reward, grant_rewards
from cosmetics import CATALOG, STARTER_CREDITS
from community_tools import eligible, save_backup, read_backup, restore_settings
from localization import tr
from unified_setup import provision_all
from automod import AutoMod, AutoModCommands, RETENTION
from verification_gate import active as gate_active, admitted, gate_channel, public_access

load_dotenv()
log = logging.getLogger('tov4r')
ROOT = Path(__file__).parent
COLOR = 0xef3340

def embed(title, description=''):
    e = discord.Embed(title=title, description=description, color=COLOR)
    e.set_footer(text='Tov4r Bot • Made for your people')
    return e

async def ticket_transcript_bytes(channel):
    # Bound storage and disclose the limit. Keep message text only, not attachments.
    parts=[]
    size=0
    async for m in channel.history(limit=500,oldest_first=False):
        line=f'{m.created_at.isoformat()} | {m.author} ({m.author.id})\n{m.content}\n\n'.encode('utf-8')
        if size+len(line)>1000000: break
        parts.append(line)
        size+=len(line)
    return b'TOV4R ticket transcript - latest 500 messages, max 1 MB; text only.\n\n'+b''.join(reversed(parts))

async def reply(i, text):
    if i.response.is_done(): await i.followup.send(text, ephemeral=True)
    else: await i.response.send_message(text, ephemeral=True)

def is_staff(member, cfg):
    return member.guild_permissions.manage_guild or any(r.id == cfg['support_role'] and not r.is_default() for r in member.roles)

def safe_role(role, guild):
    # Self-service must never grant moderation/administrative permissions.
    dangerous = ('administrator','manage_guild','manage_roles','manage_channels','kick_members',
                 'ban_members','moderate_members','manage_messages','manage_webhooks','mention_everyone',
                 'manage_threads','manage_events','mute_members','deafen_members','move_members','view_audit_log')
    return (not role.is_default() and not role.managed and guild.me is not None
            and role < guild.me.top_role and not any(getattr(role.permissions,k,False) for k in dangerous))

class Community(commands.Bot):
    def __init__(self, store=None):
        intents=discord.Intents.default(); intents.members=True; intents.message_content=True; intents.moderation=True
        super().__init__(command_prefix=".", case_insensitive=True, intents=intents,
                         allowed_mentions=discord.AllowedMentions.none())
        self.store=store or Store(os.getenv('DATABASE_PATH','data/community.db'))
        self.locks=collections.defaultdict(asyncio.Lock)
        self.spam=collections.defaultdict(collections.deque)
        self.web_runner=None
        self.voice_previous=set()
        self.voice_sample_at=None
        self.security=SecurityMonitor()
        self.automod=AutoMod(self)
    async def setup_hook(self):
        expected=os.getenv('DISCORD_APPLICATION_ID','').strip()
        if expected and self.application_id != int(expected):
            raise RuntimeError(f'Wrong bot token: authenticated application {self.application_id}, expected {expected}. Update DISCORD_TOKEN; no commands were changed.')
        log.info('Authenticated application ID: %s',self.application_id)
        await self.add_cog(Features(self))
        await self.add_cog(AutoModCommands(self))
        from community_social import CommunitySocial, SocialView
        await self.add_cog(CommunitySocial(self))
        for r in self.store.all("SELECT * FROM community_posts WHERE closed=0"):
            self.add_view(SocialView(self,json.loads(r['choices'])),message_id=r['message'])
        from community_premium import VerifyView, MultiRoleView
        self.add_view(VerifyView(self))
        for r in self.store.all('SELECT * FROM community_role_menus'):
            self.add_view(MultiRoleView(self,json.loads(r['roles'])),message_id=r['message'])
        from ticket_premium import TicketPremium, TicketReopen
        await self.add_cog(TicketPremium(self))
        self.add_view(TicketReopen(self))
        from voice_commands import VoiceCommands
        await self.add_cog(VoiceCommands(self))
        from server_manager import ServerManager
        await self.add_cog(ServerManager(self))
        from autopilot import ServerAutopilot
        await self.add_cog(ServerAutopilot(self))
        self.add_view(VoicePanel(self)); self.add_view(TicketPanel(self)); self.add_view(CloseTicket(self))
        for r in self.store.all("SELECT * FROM giveaways WHERE state IN ('open','drawn')"):
            self.add_view(GiveawayPanel(self,r['message']),message_id=r['message'])
        for r in self.store.all('SELECT * FROM role_panels'):
            self.add_view(RolePanel(self,json.loads(r['roles'])),message_id=r['message'])
        await self.sync_public_commands()
        from dashboard import start_dashboard
        self.web_runner=await start_dashboard(self)
        self.maintenance.start()
        self.voice_activity.start()
    async def sync_public_commands(self):
        # Global commands remain available to every server that installs this app.
        await self.tree.sync()
        # Remove legacy guild copies created by version 1, avoiding stale overrides.
        legacy=os.getenv('DISCORD_GUILD_ID','').strip()
        if legacy:
            target=discord.Object(id=int(legacy))
            self.tree.clear_commands(guild=target)
            await self.tree.sync(guild=target)
    def invite_url(self):
        permissions=discord.Permissions(view_channel=True,send_messages=True,embed_links=True,
            attach_files=True,read_message_history=True,manage_channels=True,manage_roles=True,view_audit_log=True,
            manage_messages=True,connect=True,move_members=True,kick_members=True,
            ban_members=True,moderate_members=True)
        return discord.utils.oauth_url(self.application_id,permissions=permissions,
                                       scopes=('bot','applications.commands'))
    async def close(self):
        self.maintenance.cancel()
        self.voice_activity.cancel()
        if self.web_runner: await self.web_runner.cleanup()
        await super().close()
        self.store.db.close()
    async def on_ready(self):
        log.info('Connected as %s, %s servers',self.user,len(self.guilds))
        await self.change_presence(activity=discord.Game('/help • Tov4r Bot'))
    async def audit(self,guild,kind,detail):
        self.store.event(guild.id,kind,detail)
        c=guild.get_channel(self.store.config(guild.id)['logs_channel'])
        if isinstance(c,discord.TextChannel):
            try: await c.send(embed=embed(kind,detail[:3900]))
            except discord.HTTPException: log.warning('Cannot send log to guild %s',guild.id)
    async def security_alert(self,guild,kind,detail):
        self.store.event(guild.id,'Security '+kind,detail)
        channel=guild.get_channel(self.store.config(guild.id)['security_channel'])
        # Never post security alerts in a public channel.
        if isinstance(channel,discord.TextChannel) and not public_access(self,guild,channel):
            try: await channel.send(embed=embed('🛡 TOV4R Security • '+kind,detail))
            except discord.HTTPException: log.warning('Security alert delivery failed guild=%s',guild.id)

    async def on_audit_log_entry_create(self,entry):
        guild=entry.guild
        if not self.store.config(guild.id)['security_enabled']: return
        if entry.user_id==self.user.id: return
        if entry.action in (discord.AuditLogAction.channel_delete,discord.AuditLogAction.role_delete,discord.AuditLogAction.ban,discord.AuditLogAction.kick):
            if self.security.observe(guild.id,'destructive'):
                await self.security_alert(guild,'Destructive activity','Unusual burst of channel/role deletions or moderation actions. Review Discord audit log. No automatic punishment.')

    async def on_member_join(self,member):
        cfg=self.store.config(member.guild.id)
        if cfg['security_enabled']:
            if member.bot:
                if self.security.observe(member.guild.id,'bot'):
                    await self.security_alert(member.guild,'Bot joined','A bot joined the server. Verify that it was authorized.')
            elif self.security.observe(member.guild.id,'join'):
                await self.security_alert(member.guild,'Join burst','Unusual member joins detected. Automatic roles temporarily paused; check new members manually.')
        role=member.guild.get_role(cfg['autorole'])
        if admitted(self,member) and role and not tracked_reward(self,member.guild.id,role.id) and not reserved_staff_role(self,member.guild,role) and safe_role(role,member.guild) and not (cfg['security_enabled'] and (member.bot or self.security.paused(member.guild.id))):
            try: await member.add_roles(role,reason='TOV4R Auto-role')
            except discord.HTTPException: await self.audit(member.guild,'Auto-role error',str(member.id))
        from community_premium import THEMES, language, welcome_destination
        c=welcome_destination(self,member.guild)
        if c is not None and not member.bot:
            accent, colour=THEMES.get(cfg['welcome_theme'],THEMES['ruby'])
            greeting,title,*_=language(self,member.guild.id)
            image=Image.new('RGB',(1000,340),'#101117'); d=ImageDraw.Draw(image)
            d.rounded_rectangle((22,22,978,318),radius=28,fill='#1a1b25',outline=accent,width=3)
            def font(size):
                for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','C:/Windows/Fonts/arialbd.ttf'):
                    if Path(p).exists(): return ImageFont.truetype(p,size)
                return ImageFont.load_default(size=size)
            d.text((60,55),'TOV4R / COMMUNITY',fill=accent,font=font(25))
            d.text((60,116),greeting,fill='white',font=font(52))
            # Avoid font failures on arbitrary display names by leaving them in the embed.
            d.text((60,220),f'MEMBER #{member.guild.member_count}',fill='#aaaabc',font=font(24))
            data=io.BytesIO(); image.save(data,format='PNG'); data.seek(0)
            text=cfg['welcome_text'].replace('{mention}',member.mention).replace('{server}',member.guild.name).replace('{name}',member.display_name)
            e=embed(title+' '+member.display_name,text); e.colour=discord.Colour(colour); e.set_thumbnail(url=member.display_avatar.url); e.set_image(url='attachment://welcome.png')
            try:
                if c.permissions_for(member.guild.me).attach_files:
                    await c.send(embed=e,file=discord.File(data,filename='welcome.png'))
                else:
                    e.set_image(url=None)
                    await c.send(embed=e)
            except discord.HTTPException: log.exception('Welcome failed')
        await self.audit(member.guild,'Member joined',f'{member} • {member.id}')
    async def on_member_remove(self,member):
        await self.audit(member.guild,'Member left',f'{member} • {member.id}')
        self.store.run('DELETE FROM community_birthdays WHERE guild=? AND user=?',(member.guild.id,member.id))
        self.store.run('DELETE FROM verification_migration WHERE guild=? AND user=?',(member.guild.id,member.id))
        cfg=self.store.config(member.guild.id)
        from community_premium import welcome_destination
        c=welcome_destination(self,member.guild)
        if not member.bot and cfg['goodbye_enabled'] and c is not None:
            from community_premium import THEMES
            title,body={'fr':('Au revoir 👋','a quitté le serveur.'),'darija':('Bslama 👋','khrej mn server.'),'en':('Goodbye 👋','left the server.')}[cfg['language']]
            e=embed(title,discord.utils.escape_markdown(member.display_name)+' '+body)
            e.colour=discord.Colour(THEMES[cfg['welcome_theme']][1])
            e.set_thumbnail(url=member.display_avatar.url)
            try: await c.send(embed=e)
            except discord.HTTPException: pass
    async def private_support_setup(self, message):
        """Owner-only DM entry point; never registered as a public command."""
        owner = os.getenv('TOV4R_PRIVATE_SETUP_OWNER_ID', '').strip()
        if not owner or str(message.author.id) != owner:
            return
        args = message.content.split()
        if len(args) == 1:
            owned = [g for g in self.guilds if g.owner_id == message.author.id]
            lines = [f'{discord.utils.escape_markdown(g.name)} — `{g.id}`' for g in owned[:15]]
            await message.channel.send('Private TOV4R setup. Invite the bot to your new server first.\n'
                + ('\n'.join(lines) if lines else 'No owned servers found.')
                + '\nPreview: `.setup-support SERVER_ID`')
            return
        if len(args) not in (2, 3) or not args[1].isascii() or not args[1].isdigit() or len(args[1]) > 20 or (len(args) == 3 and args[2] != 'confirm'):
            await message.channel.send('Usage: `.setup-support SERVER_ID`')
            return
        guild = self.get_guild(int(args[1]))
        if guild is None or guild.owner_id != message.author.id:
            await message.channel.send('Only a server you own and the bot has joined is eligible.')
            return
        if len(args) == 2:
            await message.channel.send(f'Preview for **{discord.utils.escape_markdown(guild.name)}**: '
                '6 categories, 23 channels and 9 optional roles (the bot role already exists). '
                'No deletion, no automatic staff/admin privileges, staff channels private by default. '
                'Existing resources are not edited.\n'
                f'Confirm: `.setup-support {guild.id} confirm`')
            return
        key = '__private_tov4r_support__'
        groups = (
            ('📌・INFORMATION', ('📢・announcements','👋・welcome','📜・rules','🔗・invite-bot','📡・bot-status'), ()),
            ('🤖・TOV4R BOT', ('📚・commands','🧪・test-bot','🆕・changelog','💡・suggestions','🐛・bug-reports'), ()),
            ('🌐・COMMUNITY', ('💬・general','🇲🇦・darija','📸・media','🎮・gaming'), ()),
            ('🎫・SUPPORT', ('🎫・create-ticket','❓・faq'), ()),
            ('🔊・VOICE', (), ('🔊・General','➕・Create Voice','🤖・AI Voice')),
            ('🔒・STAFF', ('💬・staff-chat','📋・mod-logs','⚙️・bot-logs','💻・development'), ()),
        )
        rolespec = (
            ('👑 Founder',0xed4245),('💻 Developer',0x5865f2),('⚡ Administrator',0xe67e22),
            ('🛡️ Moderator',0x3498db),('🎧 Support Team',0x1abc9c),('🧪 Beta Tester',0x9b59b6),
            ('💎 Premium',0xeb459e),('🚀 Booster',0xf47fff),('👤 Member',0x949ba4),
        )
        readonly = {'📢・announcements','👋・welcome','📜・rules','🔗・invite-bot','📡・bot-status','📚・commands','🆕・changelog','❓・faq'}
        def tracked(item):
            row = self.store.one('SELECT discord_id FROM template_items WHERE guild=? AND template=? AND item_key=?',(guild.id,key,item))
            return int(row['discord_id']) if row else None
        def remember(item,obj,kind):
            self.store.run('INSERT OR REPLACE INTO template_items(guild,template,item_key,discord_id,kind) VALUES(?,?,?,?,?)',(guild.id,key,item,obj.id,kind))
        async with self.locks[('setup',guild.id)]:
            if guild.owner_id != message.author.id:
                return
            me = guild.me
            if me is None or not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
                await message.channel.send('Bot needs Manage Channels and Manage Roles.')
                return
            # Refuse name collisions with untracked resources, without modifying them.
            for name,_ in rolespec:
                if any(x.name == name and x.id != tracked('role:'+name) for x in guild.roles):
                    await message.channel.send('Existing role name conflicts with the template. Use a fresh server or rename the conflicting role: '+name)
                    return
            for category, texts, voices in groups:
                if any(x.name == category and x.id != tracked('category:'+category) for x in guild.categories):
                    await message.channel.send('Existing category name conflicts with the template: '+category)
                    return
                for name in texts+voices:
                    if any(x.name == name and x.id != tracked('channel:'+category+':'+name) for x in guild.channels):
                        await message.channel.send('Existing channel name conflicts with the template: '+name)
                        return
            created = 0
            roles = {}
            try:
                for name, color in rolespec:
                    item = 'role:'+name
                    role = guild.get_role(tracked(item))
                    if role is None:
                        role = await guild.create_role(name=name, colour=discord.Colour(color),
                            permissions=discord.Permissions.none(), mentionable=False, reason='Private TOV4R support template')
                        remember(item,role,'role')
                        created += 1
                    roles[name] = role
                for category,texts,voices in groups:
                    private = category == '🔒・STAFF'
                    overwrites = {
                        guild.default_role:discord.PermissionOverwrite(view_channel=False),
                        me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
                    } if private else {}
                    if private:
                        # Owner bypasses overwrites; trusted roles can be assigned manually.
                        for name in ('👑 Founder','💻 Developer','⚡ Administrator','🛡️ Moderator'):
                            role = roles[name]
                            if role.permissions.value == 0:
                                overwrites[role] = discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
                    item = 'category:'+category
                    cat = guild.get_channel(tracked(item))
                    if not isinstance(cat,discord.CategoryChannel):
                        cat = await guild.create_category(category,overwrites=overwrites,reason='Private TOV4R support template')
                        remember(item,cat,'category')
                        created += 1
                    for name in texts+voices:
                        item = 'channel:'+category+':'+name
                        if guild.get_channel(tracked(item)) is not None:
                            continue
                        perms = dict(overwrites)
                        if name in readonly:
                            perms[guild.default_role] = discord.PermissionOverwrite(send_messages=False)
                            perms[me] = discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
                        if name in voices:
                            channel = await guild.create_voice_channel(name,category=cat,overwrites=perms,reason='Private TOV4R support template')
                        else:
                            channel = await guild.create_text_channel(name,category=cat,overwrites=perms,reason='Private TOV4R support template')
                        remember(item,channel,'channel')
                        created += 1
            except discord.HTTPException:
                log.exception('Private support template failed for guild %s',guild.id)
                await message.channel.send('Setup incomplete. Check bot permissions and retry the same confirmation; recorded resources will be reused.')
                return
            await message.channel.send(f'✅ Private template applied to **{discord.utils.escape_markdown(guild.name)}**. {created} new resources. Existing resources were not edited. Ticket/voice automation and status messages still need separate configuration.')

    async def on_message(self,message):
        if message.author.bot: return
        if not message.guild:
            if message.content.split(maxsplit=1)[:1] == ['.setup-support']:
                await self.private_support_setup(message)
            return
        if not isinstance(message.author,discord.Member): return
        cfg=self.store.config(message.guild.id)
        if await self.automod.handle(message): return
        if message.content.startswith('.'):
            await self.process_commands(message)
            return
        if cfg['levels']:
            # Do not include private support conversations in activity counters.
            if not self.store.one('SELECT channel FROM tickets WHERE guild=? AND channel=?',(message.guild.id,message.channel.id)):
                self.store.count_message(message.guild.id,message.author.id)
            up=self.store.award(message.guild.id,message.author.id,cfg['xp_cooldown'])
            await grant_rewards(self,message.author)
            if up is not None:
                try: await message.channel.send(embed=embed(tr(self,message.guild.id,'level_up'),tr(self,message.guild.id,'level_up_body',member=message.author.mention,level=up)))
                except discord.HTTPException: pass
    async def on_message_delete(self,message):
        # Do not copy deleted private messages into general moderation logs.
        if message.guild and not message.author.bot:
            await self.audit(message.guild,'Message deleted',f'User {message.author.id} • Channel {message.channel.id}')
    async def on_message_edit(self,before,after):
        if after.guild and isinstance(after.author,discord.Member) and not after.author.bot and before.content != after.content:
            await self.automod.handle(after,edited=True)
    async def on_guild_channel_create(self,channel):
        if gate_active(self,channel.guild.id):
            async with self.locks[('setup',channel.guild.id)]:
                try: await gate_channel(self,channel)
                except discord.HTTPException: log.warning('Verification gate could not apply to new channel %s',channel.id)
    async def on_guild_channel_delete(self,channel):
        self.store.run('DELETE FROM rooms WHERE channel=?',(channel.id,))
        self.store.run('UPDATE tickets SET closed=1 WHERE channel=?',(channel.id,))
        self.store.run('DELETE FROM ticket_deletions WHERE channel=? AND guild=?',(channel.id,channel.guild.id))
        self.store.run('DELETE FROM ticket_guests WHERE channel=? AND guild=?',(channel.id,channel.guild.id))
    async def on_voice_state_update(self,member,before,after):
        if member.bot or not admitted(self,member): return
        # An interruption or change during a sample invalidates that interval.
        if before.channel!=after.channel or before.self_deaf!=after.self_deaf or before.deaf!=after.deaf or before.self_mute!=after.self_mute or before.mute!=after.mute:
            channels={c.id for c in (before.channel,after.channel) if c}
            self.voice_previous={k for k in self.voice_previous if not (k[0]==member.guild.id and k[2] in channels)}
        if before.channel == after.channel: return
        cfg=self.store.config(member.guild.id)
        if after.channel and after.channel.id==cfg['voice_creator']:
            async with self.locks[('voice',member.id)]:
                if not member.voice or member.voice.channel.id!=cfg['voice_creator']: return
                existing=self.store.one('SELECT channel FROM rooms WHERE guild=? AND owner=?',(member.guild.id,member.id))
                old=member.guild.get_channel(existing['channel']) if existing else None
                if old:
                    try: await member.move_to(old)
                    except discord.HTTPException: pass
                else:
                    cat=member.guild.get_channel(cfg['voice_category'])
                    if not isinstance(cat,discord.CategoryChannel): return
                    overwrites={member.guild.default_role:discord.PermissionOverwrite(view_channel=None if gate_active(self,member.guild.id) else True,connect=True),
                                member.guild.me:discord.PermissionOverwrite(view_channel=True,connect=True,manage_channels=True,move_members=True),
                                member:discord.PermissionOverwrite(view_channel=True,connect=True)}
                    channel=None
                    try:
                        prefs=self.store.voice_preferences(member.guild.id,member.id)
                        name=prefs['name'] if prefs else f'🔊 {member.display_name}'[:100]
                        limit=prefs['user_limit'] if prefs else 0
                        channel=await member.guild.create_voice_channel(name,category=cat,user_limit=limit,overwrites=overwrites,reason='TOV4R Join to Create')
                        self.store.run('INSERT INTO rooms VALUES(?,?,?)',(channel.id,member.guild.id,member.id))
                        await member.move_to(channel)
                        await self.audit(member.guild,'Voice created',f'{channel.name} • Owner {member.id}')
                    except discord.HTTPException:
                        log.exception('Voice creation failed')
                        if channel and not channel.members:
                            try: await channel.delete()
                            except discord.HTTPException: pass
        if before.channel:
            await self.clean_room(before.channel)
    async def clean_room(self,channel):
        async with self.locks[('room',channel.id)]:
            r=self.store.one('SELECT * FROM rooms WHERE channel=?',(channel.id,))
            if r and not channel.members:
                try: await channel.delete(reason='TOV4R empty temporary room')
                except discord.NotFound: pass
                except discord.HTTPException: return
                self.store.run('DELETE FROM rooms WHERE channel=?',(channel.id,))
    async def delete_closed_ticket(self,guild_id,channel_id):
        async with self.locks[('close',channel_id)]:
            job=self.store.one('SELECT * FROM ticket_deletions WHERE guild=? AND channel=?',(guild_id,channel_id))
            if not job or job['due']>time.time(): return
            g=self.get_guild(guild_id)
            if not g or g.unavailable: return
            row=self.store.one('SELECT * FROM tickets WHERE guild=? AND channel=? AND closed=1',(guild_id,channel_id))
            if not row:
                self.store.run('DELETE FROM ticket_deletions WHERE channel=?',(channel_id,))
                return
            try:
                c=g.get_channel(channel_id) or await g.fetch_channel(channel_id)
                if not isinstance(c,discord.TextChannel) or c.guild.id!=guild_id:
                    return
                body=await ticket_transcript_bytes(c)
                self.store.run('INSERT OR REPLACE INTO ticket_transcripts VALUES(?,?,?,?)',(channel_id,guild_id,body,time.time()+7*86400))
                await c.delete(reason='TOV4R ticket closed: 10-second countdown elapsed')
            except discord.NotFound:
                pass
            except (discord.HTTPException, asyncio.TimeoutError):
                # Keep channel and retry if history cannot be saved or deletion fails.
                log.warning('Ticket deletion deferred guild=%s channel=%s',guild_id,channel_id)
                self.store.run('UPDATE ticket_deletions SET due=? WHERE channel=?',(time.time()+60,channel_id))
                return
            self.store.run('DELETE FROM ticket_deletions WHERE channel=?',(channel_id,))
            await self.audit(g,'Ticket deleted',f'Ticket ID: {channel_id} • staff transcript available for 7 days if saved.')

    @tasks.loop(seconds=60)
    async def voice_activity(self):
        # Discord voice states only: no voice receive, audio, or recording.
        if not self.is_ready():
            self.voice_previous=set()
            self.voice_sample_at=None
            return
        now=time.monotonic()
        current=set()
        for guild in self.guilds:
            if guild.unavailable: continue
            cfg=self.store.config(guild.id)
            if not cfg['levels'] or not cfg['voice_xp']: continue
            for channel in guild.voice_channels:
                if channel.id in (getattr(guild.afk_channel,'id',None),cfg['voice_creator']): continue
                members=[m for m in channel.members if not m.bot and m.voice and not m.voice.self_deaf and not m.voice.deaf and not m.voice.self_mute and not m.voice.mute]
                if len(members)<2: continue
                current.update((guild.id,m.id,channel.id) for m in members)
        if self.voice_sample_at is not None:
            elapsed=min(60,max(0,int(round(now-self.voice_sample_at))))
            for guild_id,user_id,_ in current & self.voice_previous:
                self.store.award_voice(guild_id,user_id,elapsed)
                guild=self.get_guild(guild_id)
                member=guild.get_member(user_id) if guild else None
                if member: await grant_rewards(self,member)
        self.voice_previous=current
        self.voice_sample_at=now

    @voice_activity.before_loop
    async def before_voice_activity(self): await self.wait_until_ready()

    @tasks.loop(seconds=15)
    async def maintenance(self):
        from community_social import social_tick
        try: await social_tick(self)
        except Exception: log.exception('Community maintenance failed')
        for job in self.store.all('SELECT guild,channel FROM ticket_deletions WHERE due<=? LIMIT 20',(time.time(),)):
            try: await self.delete_closed_ticket(job['guild'],job['channel'])
            except Exception:
                log.exception('Ticket cleanup failed')
        self.store.run('DELETE FROM ticket_transcripts WHERE expires<=?',(time.time(),))
        for r in self.store.all('SELECT * FROM rooms'):
            g=self.get_guild(r['guild'])
            if not g or g.unavailable: continue
            c=g.get_channel(r['channel'])
            if c: await self.clean_room(c)
            else: self.store.run('DELETE FROM rooms WHERE channel=?',(r['channel'],))
        for r in self.store.all("SELECT * FROM giveaways WHERE state='drawn' OR (state='open' AND ends<=?)",(time.time(),)):
            try: await self.finish_giveaway(r['message'])
            except discord.HTTPException: log.warning('Giveaway delivery deferred %s',r['message'])
        now=time.monotonic()
        self.security.prune(now)
        self.automod.prune(now)
        self.store.run('DELETE FROM automod_incidents WHERE at<=?',(time.time()-RETENTION,))
        for key in list(self.spam):
            if not self.spam[key] or now-self.spam[key][-1]>60: del self.spam[key]
    @maintenance.before_loop
    async def before_maintenance(self): await self.wait_until_ready()
    def giveaway_rules(self,r):
        return self.store.one('SELECT * FROM giveaway_rules WHERE guild=? AND message=?',(r['guild'],r['message'])) or {'min_level':0,'role_id':0,'cosmetic':''}
    def giveaway_embed(self,r):
        rules=self.giveaway_rules(r)
        count=self.store.one('SELECT COUNT(*) n FROM entries WHERE message=?',(r['message'],))['n']
        text=f"Ends <t:{int(r['ends'])}:R> • Winners: {r['count']}\nParticipants: **{count}**\nMinimum level: {rules['min_level']}"
        if rules['role_id']: text+=f"\nRequired role: <@&{rules['role_id']}>"
        return embed('🎁 '+r['prize'],text+'\nFree participation. Click to join or leave.')
    async def finish_giveaway(self,mid,reroll=False):
        async with self.locks[('giveaway',mid)]:
            r=self.store.one('SELECT * FROM giveaways WHERE message=?',(mid,))
            if not r: raise ValueError('Giveaway not found')
            if r['state']=='done' and not reroll: return
            g=self.get_guild(r['guild']); c=g.get_channel(r['channel']) if g else None
            if not c: return
            if r['state']=='open' or reroll:
                rules=self.giveaway_rules(r)
                previous=set(json.loads(r['winners'])) if reroll else set()
                ids=[e['user'] for e in self.store.all('SELECT user FROM entries WHERE message=?',(mid,)) if e['user'] not in previous and eligible(self.store,g.get_member(e['user']),rules)]
                if reroll and not ids: raise ValueError('No other eligible participants.')
                winners=secrets.SystemRandom().sample(ids,min(len(ids),r['count']))
                with self.store.db:
                    self.store.db.execute("UPDATE giveaways SET state='drawn',winners=? WHERE message=?",(json.dumps(winners),mid))
                    if rules['cosmetic'] in CATALOG:
                        for uid in winners:
                            self.store.db.execute('INSERT OR IGNORE INTO cosmetic_inventory VALUES(?,?,?)',(g.id,uid,rules['cosmetic']))
            else: winners=json.loads(r['winners'])
            text=', '.join(f'<@{u}>' for u in winners) or 'No eligible participants.'
            try:
                message=await c.fetch_message(mid)
                await message.edit(embed=embed('Giveaway ended • '+r['prize'],f'Winners: {text}'),view=None)
            except discord.NotFound:
                self.store.run("UPDATE giveaways SET state='done' WHERE message=?",(mid,)); return
            self.store.run("UPDATE giveaways SET state='done' WHERE message=?",(mid,))
            await self.audit(g,'Giveaway ended',f'{r["prize"]} • {text}')

class ErrorView(discord.ui.View):
    async def on_error(self,i,error,item):
        log.error('Component error',exc_info=error)
        await reply(i,'Ma9dertch nkml. Check permissions / role hierarchy, w 3awed.')

async def owned_room(bot,i):
    c=i.user.voice.channel if isinstance(i.user,discord.Member) and i.user.voice else None
    r=bot.store.one('SELECT * FROM rooms WHERE channel=?',(c.id,)) if c else None
    if not r or r['owner']!=i.user.id: raise ValueError('Khassek tkon f room dyalek.')
    return c,r

class RoomModal(discord.ui.Modal):
    def __init__(self,bot,action):
        super().__init__(title='TOV4R • '+action)
        self.bot=bot; self.action=action
        self.value=discord.ui.TextInput(label='New name' if action=='rename' else 'Member limit (0–99)',max_length=80 if action=='rename' else 2)
        self.add_item(self.value)
    async def on_submit(self,i):
        try:
            c,_=await owned_room(self.bot,i)
            await i.response.defer(ephemeral=True)
            if self.action=='rename': await c.edit(name=str(self.value).strip() or 'TOV4R Room')
            else:
                n=int(str(self.value))
                if not 0<=n<=99: raise ValueError('Limit: 0–99')
                await c.edit(user_limit=n)
            await reply(i,'Tbedlat ✅')
        except ValueError as e: await reply(i,str(e))
        except discord.HTTPException: await reply(i,'Check permissions; Discord y9der ylimit rename temporarily.')

class RoomUserSelect(discord.ui.UserSelect):
    def __init__(self,bot,action):
        super().__init__(placeholder='Choose a member',min_values=1,max_values=1)
        self.bot=bot; self.action=action
    async def callback(self,i):
        try:
            c,r=await owned_room(self.bot,i)
            target=i.guild.get_member(self.values[0].id)
            if not target or target.bot or target.id==i.user.id: raise ValueError('Choose another human member.')
            if not admitted(self.bot,target): raise ValueError('Member must complete verification first.')
            await i.response.defer(ephemeral=True)
            if self.action=='transfer':
                if not target.voice or target.voice.channel!=c: raise ValueError('Member khas ykon f room.')
                duplicate=self.bot.store.one('SELECT channel FROM rooms WHERE guild=? AND owner=?',(i.guild.id,target.id))
                if duplicate: raise ValueError('Had member 3ndo deja room.')
                await c.set_permissions(i.user,overwrite=None)
                await c.set_permissions(target,view_channel=True,connect=True)
                self.bot.store.run('UPDATE rooms SET owner=? WHERE channel=?',(target.id,c.id))
            elif self.action=='permit': await c.set_permissions(target,view_channel=True,connect=True)
            else:
                if target.guild_permissions.administrator: raise ValueError('Ma t9derch tblocki admin.')
                await c.set_permissions(target,connect=False)
                if target.voice and target.voice.channel==c: await target.move_to(None)
            await reply(i,'Tgad ✅')
        except ValueError as e: await reply(i,str(e))

class VoicePanel(ErrorView):
    def __init__(self,bot,guild_id=None):
        super().__init__(timeout=None); self.bot=bot
        for action,label in [('lock','🔒 Lock'),('unlock','🔓 Unlock'),('hide','👻 Hide'),('show','👁 Show'),('rename','✏ Rename'),('limit','👥 Limit'),('permit','✅ Permit'),('kick','⛔ Kick / block'),('transfer','👑 Transfer'),('claim','🙋 Claim'),('info','ℹ️ Room info'),('save','💾 Save room'),('forget','🗑 Reset saved')]:
            style=discord.ButtonStyle.primary if action in ('rename','limit','info','save') else discord.ButtonStyle.danger if action in ('kick','forget') else discord.ButtonStyle.secondary
            b=discord.ui.Button(label=tr(bot,guild_id,'voice_'+action),custom_id='voice:'+action,style=style)
            async def action_handler(i,a=action):
                try:
                    if a=='claim':
                        c=i.user.voice.channel if i.user.voice else None
                        r=bot.store.one('SELECT * FROM rooms WHERE channel=?',(c.id,)) if c else None
                        if not r or any(m.id==r['owner'] for m in c.members): raise ValueError('Owner mazal f room, ola hadi machi temp room.')
                        if bot.store.one('SELECT channel FROM rooms WHERE guild=? AND owner=?',(i.guild.id,i.user.id)): raise ValueError('3ndek deja room.')
                        await i.response.defer(ephemeral=True)
                        old=i.guild.get_member(r['owner'])
                        if old: await c.set_permissions(old,overwrite=None)
                        await c.set_permissions(i.user,view_channel=True,connect=True)
                        bot.store.run('UPDATE rooms SET owner=? WHERE channel=?',(i.user.id,c.id))
                        await reply(i,'Daba nta owner ✅'); return
                    if a=='forget':
                        bot.store.clear_voice_preferences(i.guild_id,i.user.id)
                        return await reply(i,'Saved room preferences cleared. Current room unchanged.')
                    if a=='info':
                        c=i.user.voice.channel if isinstance(i.user,discord.Member) and i.user.voice else None
                        r=bot.store.one('SELECT * FROM rooms WHERE guild=? AND channel=?',(i.guild_id,c.id)) if c else None
                        if not r: raise ValueError('Join a TOV4R temporary voice room first.')
                        ow=c.overwrites_for(i.guild.default_role)
                        e=embed('🔊 TOV4R • Room info',discord.utils.escape_markdown(c.name))
                        e.add_field(name='Owner',value=f'<@{r["owner"]}>')
                        e.add_field(name='Members',value=f'{len(c.members)} / {c.user_limit or "Unlimited"}')
                        e.add_field(name='Access',value='Locked' if ow.connect is False else 'Open')
                        e.add_field(name='Visibility',value='Hidden' if ow.view_channel is False else 'Visible')
                        return await i.response.send_message(embed=e,ephemeral=True)
                    c,_=await owned_room(bot,i)
                    if a=='save':
                        bot.store.save_voice_preferences(i.guild_id,i.user.id,c.name,c.user_limit)
                        return await reply(i,'Room name and member limit saved for your next room on this server. Permissions are not saved.')
                    if a in ('rename','limit'): await i.response.send_modal(RoomModal(bot,a)); return
                    if a in ('permit','kick','transfer'):
                        v=ErrorView(timeout=90); v.add_item(RoomUserSelect(bot,a))
                        await i.response.send_message('Choose member:',view=v,ephemeral=True); return
                    await i.response.defer(ephemeral=True)
                    ow=c.overwrites_for(i.guild.default_role)
                    if a in ('lock','unlock'): ow.connect=a=='unlock'
                    else: ow.view_channel=(None if gate_active(bot,i.guild_id) else True) if a=='show' else False
                    await c.set_permissions(i.guild.default_role,overwrite=ow)
                    await reply(i,'Tgad ✅ Explicitly permitted members can still join locked rooms.')
                except ValueError as e: await reply(i,str(e))
            async def callback(i,handler=action_handler):
                if not i.guild or not isinstance(i.user,discord.Member):
                    return await reply(i,'Use this panel in a server.')
                channel=i.user.voice.channel if i.user.voice else None
                async with bot.locks[('room',channel.id if channel else i.user.id)]:
                    await handler(i)
            b.callback=callback; self.add_item(b)

class TicketPanel(ErrorView):
    def __init__(self,bot,guild_id=None):
        super().__init__(timeout=None); self.bot=bot
        self.open_ticket.label='🎫 '+tr(bot,guild_id,'open_ticket')
        self.select_ticket.placeholder=tr(bot,guild_id,'choose_ticket')
        for option in self.select_ticket.options:
            option.label=tr(bot,guild_id,option.value)
            option.description=tr(bot,guild_id,option.value+'_desc')
    @discord.ui.button(label='🎫 Open ticket',custom_id='ticket:open',style=discord.ButtonStyle.danger)
    async def open_ticket(self,i,button):
        await self.create_ticket(i,'support')

    @discord.ui.select(custom_id='ticket:type:v2',placeholder='Choose a ticket type',min_values=1,max_values=1,options=[
        discord.SelectOption(label='Support',value='support',emoji='🎫',description='Questions and technical help'),
        discord.SelectOption(label='Report',value='report',emoji='🛡️',description='Privately report a community issue'),
        discord.SelectOption(label='Partnership',value='partnership',emoji='🤝',description='Discuss a server partnership')])
    async def select_ticket(self,i,select):
        await self.create_ticket(i,select.values[0])

    async def create_ticket(self,i,kind):
        if not i.guild or not isinstance(i.user,discord.Member) or kind not in ('support','report','partnership'):
            return await reply(i,'Invalid ticket request.')
        if not admitted(self.bot,i.user): return await reply(i,'Complete verification first.')
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('ticket',i.guild_id,i.user.id)]:
            cfg=self.bot.store.config(i.guild_id)
            existing=self.bot.store.one('SELECT * FROM tickets WHERE guild=? AND owner=? AND closed=0',(i.guild_id,i.user.id))
            if existing and i.guild.get_channel(existing['channel']): return await reply(i,tr(self.bot,i.guild_id,'ticket_exists',channel=f'<#{existing["channel"]}>'))
            if existing: self.bot.store.run('UPDATE tickets SET closed=1 WHERE channel=?',(existing['channel'],))
            cat=i.guild.get_channel(cfg['ticket_category']); role=i.guild.get_role(cfg['support_role'])
            if not isinstance(cat,discord.CategoryChannel) or not role or role.is_default() or role.managed: return await reply(i,tr(self.bot,i.guild_id,'ticket_missing'))
            overwrites={i.guild.default_role:discord.PermissionOverwrite(view_channel=False),
                        i.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True),
                        i.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True),
                        role:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
            c=await i.guild.create_text_channel(f'{kind}-{i.user.id}',category=cat,overwrites=overwrites,reason='TOV4R private ticket')
            self.bot.store.run('INSERT INTO tickets(channel,guild,owner) VALUES(?,?,?)',(c.id,i.guild_id,i.user.id))
            self.bot.store.set_ticket_kind(i.guild_id,c.id,kind)
            t=lambda k,**v:tr(self.bot,i.guild_id,k,**v)
            e=embed(f'🎫 {t(kind)} • TOV4R',t('ticket_body',member=i.user.mention))
            e.add_field(name=t('status'),value=t('waiting'))
            e.add_field(name=t('ticket_id'),value=str(c.id))
            e.add_field(name='Staff controls',value='/ticket_control · /ticket_add · /ticket_remove · /ticket_rename · /ticket_priority',inline=False)
            await c.send(embed=e,view=CloseTicket(self.bot,i.guild_id))
            await reply(i,t('ticket_ready',channel=c.mention))
            await self.bot.audit(i.guild,'Ticket opened',f'{c.id} • {kind} • {i.user.id}')

class CloseTicket(ErrorView):
    def __init__(self,bot,guild_id=None):
        super().__init__(timeout=None); self.bot=bot
        self.claim_button.label=tr(bot,guild_id,'claim_ticket')
        self.close_ticket.label='🔒 '+tr(bot,guild_id,'close_ticket')
    @discord.ui.button(label='Claim ticket',emoji='🙋',custom_id='ticket:claim:v2',style=discord.ButtonStyle.primary)
    async def claim_button(self,i,button):
        if not i.guild or not isinstance(i.channel,discord.TextChannel):
            return await reply(i,'Ticket channel required.')
        if not is_staff(i.user,self.bot.store.config(i.guild_id)) or not i.channel.permissions_for(i.user).view_channel:
            return await reply(i,'Staff only.')
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('close',i.channel_id)]:
            previous=self.bot.store.one('SELECT claimed_by FROM ticket_details WHERE guild=? AND channel=?',(i.guild_id,i.channel_id))
            claimant=self.bot.store.claim_ticket(i.guild_id,i.channel_id,i.user.id)
            if claimant is None: return await reply(i,'Ticket already closed or not found.')
            if previous and previous['claimed_by']:
                return await reply(i,f'Ticket already claimed by <@{claimant}>.')
            if claimant!=i.user.id: return await reply(i,f'Ticket already claimed by <@{claimant}>.')
            await i.channel.send(embed=embed('🙋 '+tr(self.bot,i.guild_id,'ticket_claimed'),tr(self.bot,i.guild_id,'claimed_by',id=claimant)))
            await self.bot.audit(i.guild,'Ticket claimed',f'{i.channel_id} • {claimant}')
            await reply(i,tr(self.bot,i.guild_id,'ticket_claimed')+' ✅')

    @discord.ui.button(label='🔒 Close ticket',custom_id='ticket:close',style=discord.ButtonStyle.secondary)
    async def close_ticket(self,i,button):
        if not i.guild or not isinstance(i.channel,discord.TextChannel):
            return await reply(i,'Ticket channel required.')
        async with self.bot.locks[('close',i.channel_id)]:
            r=self.bot.store.one('SELECT * FROM tickets WHERE channel=? AND guild=? AND closed=0',(i.channel_id,i.guild_id))
            if not r: return await reply(i,'Ticket deja msdod.')
            if i.user.id!=r['owner'] and not is_staff(i.user,self.bot.store.config(i.guild_id)): return await reply(i,'Ma3ndekch permission.')
            await i.response.defer(ephemeral=True)
            from ticket_premium import TicketReopen
            self.bot.store.queue_ticket_close(i.guild_id,i.channel_id,time.time()+10)
            await i.channel.send(embed=embed('🔒 '+tr(self.bot,i.guild_id,'ticket_closed'),tr(self.bot,i.guild_id,'closed_body')),view=TicketReopen(self.bot))
            await reply(i,tr(self.bot,i.guild_id,'ticket_closed')+' ✅')
        await asyncio.sleep(10)
        await self.bot.delete_closed_ticket(i.guild_id,i.channel_id)

class GiveawayPanel(ErrorView):
    def __init__(self,bot,mid):
        super().__init__(timeout=None); self.bot=bot; self.mid=mid
        b=discord.ui.Button(label='🎉 Participate / Leave',style=discord.ButtonStyle.danger,custom_id=f'giveaway:{mid}')
        b.callback=self.toggle; self.add_item(b)
    async def toggle(self,i):
        async with self.bot.locks[('giveaway',self.mid)]:
            r=self.bot.store.one('SELECT * FROM giveaways WHERE message=?',(self.mid,))
            if not r or r['guild']!=i.guild_id or r['state']!='open' or r['ends']<=time.time(): return await reply(i,'Giveaway sala.')
            old=self.bot.store.one('SELECT * FROM entries WHERE message=? AND user=?',(self.mid,i.user.id))
            if old: self.bot.store.run('DELETE FROM entries WHERE message=? AND user=?',(self.mid,i.user.id))
            else:
                if not eligible(self.bot.store,i.user,self.bot.giveaway_rules(r)): return await reply(i,'You do not meet the participation requirements.')
                self.bot.store.run('INSERT INTO entries VALUES(?,?)',(self.mid,i.user.id))
            await reply(i,'Khrejti mn giveaway.' if old else 'Dkhelti l giveaway ✅')
            try: await i.message.edit(embed=self.bot.giveaway_embed(r))
            except discord.HTTPException: pass

class RolePanel(ErrorView):
    def __init__(self,bot,roles):
        super().__init__(timeout=None)
        for rid,label in roles:
            b=discord.ui.Button(label=label[:80],custom_id=f'role:{rid}',style=discord.ButtonStyle.secondary)
            async def callback(i,role_id=rid):
                if not admitted(bot,i.user): return await reply(i,'Complete verification first.')
                r=i.guild.get_role(role_id)
                if not r or tracked_reward(bot,i.guild_id,r.id) or reserved_staff_role(bot,i.guild,r) or not safe_role(r,i.guild): return await reply(i,'Had role ma9adrch n3tih. Admin khas ycheck permissions.')
                await i.response.defer(ephemeral=True)
                if r in i.user.roles: await i.user.remove_roles(r,reason='Self role')
                else: await i.user.add_roles(r,reason='Self role')
                await reply(i,'Role updated ✅')
            b.callback=callback; self.add_item(b)

class Features(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def interaction_check(self,i):
        if not i.guild: raise app_commands.NoPrivateMessage()
        return True
    async def cog_app_command_error(self,i,error):
        if isinstance(error,app_commands.CheckFailure): return await reply(i,'Ma3ndekch permission l had command.')
        original=getattr(error,'original',error)
        if isinstance(original,ValueError): return await reply(i,str(original))
        if isinstance(original,discord.Forbidden):
            log.warning('Discord permissions denied for %s in guild %s: %s',
                        getattr(i.command,'name','unknown'),i.guild_id,original)
            return await reply(i,
                '❌ Discord refed had action (Missing Permissions). '
                'Staff khas ychecki TOV4R Bot role: Manage Channels, Manage Roles, '
                'Send Messages, Embed Links, w bot role tkoun fo9 roles li kayghed.')
        if isinstance(original,discord.HTTPException):
            log.warning('Discord API error in guild %s: %s',i.guild_id,original)
            return await reply(i,'❌ Discord API error. Check channel limit, permissions, '
                                    'and retry. Code: '+str(original.status))
        log.error('Command failed',exc_info=error)
        await reply(i,'❌ Bug f bot. Kayn error f logs; 3tina screenshot ola waqt l command.')
    @app_commands.guild_only()
    @app_commands.command(description='Invite Tov4r Bot to another server')
    async def invite(self,i:discord.Interaction):
        await i.response.send_message(embed=embed('TOV4R BOT • Add to Server',
            f'**[➕ Ajouter TOV4R Bot à ton serveur]({self.bot.invite_url()})**\n\n'
            'Khtar server li 3ndek fih Manage Server, w confirmi permissions.\n'
            'Mn b3d, dir **/setup** bach tjhed Voice, Tickets, Welcome w Logs.\n'
            'Dir **/server scan** ila bghiti tchecki Roles, Channels w Bots.\n\n'
            '🔒 Autopilot automatique dyal 1729 ma kaytla9ch f servers lokhrin.\n'
            'Ma kaytlebch Administrator permission.'),ephemeral=True)
    @app_commands.guild_only()
    @app_commands.command(name='help',description='All Tov4r Bot commands')
    async def help_command(self,i:discord.Interaction):
        t=lambda k:tr(self.bot,i.guild_id,k)
        await i.response.send_message(embed=embed('TOV4R • '+t('command_center'),
            f'**{t("members")}**\n/help · /invite · /profile · /rank · /leaderboard · /voice_top · /level_rewards · /shop · /wallet · /inventory · /cosmetic_buy · /cosmetic_equip · /serverinfo · /counters · /suggest · /starboard · /birthday · /community_close\n'
            f'**{t("voice")}**\n{t("voice_help")}\n'
            f'**{t("staff")}**\n/setup · /announce · /security_status · /poll · /event · /custom_embed\n'
            '/mod_history · /mod_dismiss · /warn · /warnings · /timeout · /untimeout · /kick · /ban · /unban · /purge\n'
            '/ticket_control · /ticket_add · /ticket_remove · /ticket_rename · /ticket_priority · /ticket_history · /ticket_stats\n/giveaway · /giveaway_end · /giveaway_reroll · /giveaway_history · /transcript\n/backup · /backup_list · /backup_restore\n'
            f'**{t("server_manager")}**\n/server scan · /server bots · /server fix_everyone\n'
            '/server harden_role · /server remove_role · /server move_channel\n'
            '/server private_channel (admin confirmation before edits)\n'
            f'**Dashboard**\n{t("dashboard_help")}'),ephemeral=True)
    @app_commands.guild_only()
    @app_commands.command(description='Configure all TOV4R modules (server owner or Administrator)')
    @app_commands.choices(language=[app_commands.Choice(name='English',value='en'),app_commands.Choice(name='Français',value='fr'),app_commands.Choice(name='Darija',value='darija')],theme=[app_commands.Choice(name=x.title(),value=x) for x in ('ruby','ocean','violet','emerald')])
    async def setup(self,i:discord.Interaction,language:str|None=None,theme:str|None=None,welcome_message:app_commands.Range[str,1,1500]|None=None,voice_xp:bool|None=None,security:bool|None=None,levels:bool|None=None,antispam:bool|None=None,anti_invites:bool|None=None,goodbye:bool|None=None,anti_links:bool|None=None,blocked_words:app_commands.Range[str,1,1500]|None=None,progressive:bool|None=None):
        if i.user.id!=i.guild.owner_id and not i.permissions.administrator: raise ValueError('Server owner or Administrator only.')
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('setup',i.guild_id)]:
            if i.user.id!=i.guild.owner_id:
                member=await i.guild.fetch_member(i.user.id)
                if not member.guild_permissions.administrator: raise ValueError('Administrator permission required.')
            changes={k:v for k,v in [('language',language),('welcome_theme',theme),('welcome_text',welcome_message),('voice_xp',voice_xp),('levels',levels),('antispam',antispam),('anti_invites',anti_invites),('goodbye_enabled',goodbye),('anti_links',anti_links),('blocked_words', '' if blocked_words=='clear' else blocked_words),('automod_progressive',progressive)] if v is not None}
            if changes: self.bot.store.update(i.guild_id,changes)
            if voice_xp is not None or levels is not None:
                self.bot.voice_previous={k for k in self.bot.voice_previous if k[0]!=i.guild_id}
            result=await provision_all(self.bot,i.guild,self.bot.store.config(i.guild_id)['server_type'],voice_panel_factory=VoicePanel,ticket_panel_factory=TicketPanel,embed_factory=embed,security=security)
            await self.bot.audit(i.guild,'Unified setup',f'By {i.user.id}')
        await reply(i,'✅ Setup complete • Roles: '+str(len(result['roles_new']))+' new • Channels: '+str(len(result['channels_new']))+' new • Voice, tickets, welcome, verification, roles and private logs configured. No existing channels deleted or staff permissions changed. '+(' '.join(result['warnings'])))

    @app_commands.guild_only()
    @app_commands.command(description='Show level reward roles and collect eligible rewards')
    async def level_rewards(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        granted=await grant_rewards(self.bot,i.user)
        t=lambda k:tr(self.bot,i.guild_id,k)
        e=embed('TOV4R • '+t('rewards'),t('rewards_desc'))
        for n,name,_,_ in TIERS:
            role=reward_role(self.bot,i.guild,n)
            e.add_field(name=f'{t("level")} {n}',value=role.mention if role else name+' ('+t('not_installed')+')',inline=True)
        if granted: e.add_field(name=t('received'),value=', '.join(granted),inline=False)
        await i.followup.send(embed=e,ephemeral=True)

    @app_commands.guild_only()
    @app_commands.command(description='Restore channels hidden by focused server setup')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def restore_layout(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('setup',i.guild_id)]:
            status = await restore_server(self.bot, i.guild)
            await reply(i,(
                f"✅ Archived channels restored: **{len(status['restored'])}**\\n"
                f"⏳ Remaining: **{status['remaining']}**\\n"
                f"⚠️ Skipped: **{len(status['skipped'])}**. "
                "No messages or roles were deleted."
            ).replace('\\\\n','\\n'))

    @app_commands.guild_only()
    @app_commands.command(description='Show security configuration and recent alerts privately')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def security_status(self,i:discord.Interaction):
        cfg=self.bot.store.config(i.guild_id)
        channel=i.guild.get_channel(cfg['security_channel'])
        usable=isinstance(channel,discord.TextChannel) and not public_access(self.bot,i.guild,channel)
        e=embed('🛡 TOV4R • Security Center')
        e.add_field(name='Monitoring',value='Enabled' if cfg['security_enabled'] else 'Disabled')
        e.add_field(name='Audit log access',value='Available' if i.guild.me.guild_permissions.view_audit_log else 'Missing View Audit Log')
        e.add_field(name='Alerts',value=channel.mention if usable else 'Private status only')
        e.add_field(name='Auto-role pause',value='Active' if cfg['security_enabled'] and self.bot.security.paused(i.guild_id) else 'Inactive')
        rows=self.bot.store.all("SELECT kind,at FROM events WHERE guild=? AND kind LIKE 'Security %' ORDER BY id DESC LIMIT 5",(i.guild_id,))
        e.add_field(name='Recent alerts',value='\n'.join(f"{r['kind']} • <t:{int(r['at'])}:R>" for r in rows) or 'No alerts yet.',inline=False)
        await i.response.send_message(embed=e,ephemeral=True)

    def profile_embed(self, guild_id, member):
        row=self.bot.store.one('SELECT points FROM xp WHERE guild=? AND user=?',(guild_id,member.id))
        points=row['points'] if row else 0
        lv=level(points)
        start=lv**2*100
        goal=(lv+1)**2*100
        progress=points-start
        filled=min(10,max(0,progress*10//(goal-start)))
        rank=self.bot.store.one('SELECT COUNT(*) n FROM xp WHERE guild=? AND points>?',(guild_id,points))['n']+1
        t=lambda k:tr(self.bot,guild_id,k)
        e=embed('✦ TOV4R • '+t('profile'),discord.utils.escape_markdown(member.display_name))
        cosmetics=self.bot.store.cosmetic_profile(guild_id,member.id)
        theme=CATALOG.get(cosmetics['theme'])
        if theme and theme[1]=='theme': e.colour=discord.Colour(theme[3])
        badge=CATALOG.get(cosmetics['badge'])
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name='🏆 '+t('level'),value=str(lv))
        e.add_field(name='⚡ XP',value=f'{points:,}')
        e.add_field(name='📊 '+t('rank'),value=f'#{rank}' if row else t('unranked'))
        activity=self.bot.store.one('SELECT messages,voice_seconds FROM activity WHERE guild=? AND user=?',(guild_id,member.id)) or {'messages':0,'voice_seconds':0}
        minutes=activity['voice_seconds']//60
        e.add_field(name='💬 '+t('messages'),value=f"{activity['messages']:,}")
        e.add_field(name='🎙 '+t('voice_time'),value=f'{minutes//60}h {minutes%60:02d}m')
        badges=['🌱 '+t('member')]
        if lv>=5: badges.append('⭐ '+t('level')+' 5')
        if lv>=10: badges.append('🏆 '+t('level')+' 10')
        if badge and badge[1]=='badge': badges.append(badge[3])
        e.add_field(name=t('badges'),value=' • '.join(badges))
        e.add_field(name=t('next_level'),value=f'`{"█"*filled+"░"*(10-filled)}`\n{progress:,} / {goal-start:,} XP • {goal-points:,} {t("remaining")}',inline=False)
        e.set_footer(text=t('profile_footer'))
        return e

    @app_commands.guild_only()
    @app_commands.command(description='Browse free virtual-credit profile cosmetics')
    async def shop(self,i:discord.Interaction):
        account=self.bot.store.cosmetic_account(i.guild_id,i.user.id)
        t=lambda k,**v:tr(self.bot,i.guild_id,k,**v)
        e=embed('TOV4R • '+t('shop'),t('shop_desc',credits=account['credits'],starter=STARTER_CREDITS))
        for key,(name,category,price,_) in CATALOG.items():
            e.add_field(name=name+' • '+t(category),value=t('owned') if key in account['items'] else f'{price} credits • /cosmetic_buy',inline=True)
        await i.response.send_message(embed=e,ephemeral=True)

    @app_commands.guild_only()
    @app_commands.command(description='View your private cosmetic credit balance')
    async def wallet(self,i:discord.Interaction):
        account=self.bot.store.cosmetic_account(i.guild_id,i.user.id)
        await reply(i,tr(self.bot,i.guild_id,'wallet',credits=account['credits']))

    @app_commands.guild_only()
    @app_commands.command(description='View your owned profile cosmetics')
    async def inventory(self,i:discord.Interaction):
        account=self.bot.store.cosmetic_account(i.guild_id,i.user.id)
        t=lambda k:tr(self.bot,i.guild_id,k)
        lines=[CATALOG[k][0]+(' • '+t('equipped') if k in (account['theme'],account['badge']) else '') for k in account['items'] if k in CATALOG]
        await i.response.send_message(embed=embed('TOV4R • '+t('inventory'),'\n'.join(lines) or t('empty_inventory')),ephemeral=True)

    @app_commands.guild_only()
    @app_commands.command(description='Purchase a fixed-price cosmetic with free credits')
    @app_commands.choices(item=[app_commands.Choice(name=f'{v[0]} ({v[2]} credits)',value=k) for k,v in CATALOG.items()])
    async def cosmetic_buy(self,i:discord.Interaction,item:str):
        result=self.bot.store.cosmetic_buy(i.guild_id,i.user.id,item)
        await reply(i,tr(self.bot,i.guild_id,result))

    @app_commands.guild_only()
    @app_commands.command(description='Equip an owned profile theme or badge')
    @app_commands.choices(item=[app_commands.Choice(name=v[0],value=k) for k,v in CATALOG.items()]+[app_commands.Choice(name='Default theme',value='reset_theme'),app_commands.Choice(name='Remove cosmetic badge',value='reset_badge')])
    async def cosmetic_equip(self,i:discord.Interaction,item:str):
        if item in ('reset_theme','reset_badge'):
            category=item.split('_',1)[1]
            item=''
        elif item in CATALOG: category=CATALOG[item][1]
        else: return await reply(i,tr(self.bot,i.guild_id,'unknown_cosmetic'))
        if not self.bot.store.cosmetic_equip(i.guild_id,i.user.id,category,item):
            return await reply(i,tr(self.bot,i.guild_id,'unlock_first'))
        await reply(i,tr(self.bot,i.guild_id,'profile_updated'))

    @app_commands.guild_only()
    @app_commands.command(description='View a member profile, level, rank and XP progress')
    async def profile(self,i:discord.Interaction,member:discord.Member|None=None):
        await i.response.send_message(embed=self.profile_embed(i.guild_id,member or i.user))

    @app_commands.guild_only()
    @app_commands.command(description='See your level and XP')
    async def rank(self,i:discord.Interaction,member:discord.Member|None=None):
        await i.response.send_message(embed=self.profile_embed(i.guild_id,member or i.user))
    @app_commands.guild_only()
    @app_commands.command(description='Top 10 community members by XP')
    async def leaderboard(self,i:discord.Interaction):
        rows=self.bot.store.all('SELECT user,points FROM xp WHERE guild=? ORDER BY points DESC LIMIT 10',(i.guild_id,))
        text='\n'.join(f'**{n}.** <@{r["user"]}> — {r["points"]} XP • Lv {level(r["points"])}' for n,r in enumerate(rows,1))
        await i.response.send_message(embed=embed('Community leaderboard',text or 'Mazal ma kayn XP.'))
    @app_commands.guild_only()
    @app_commands.command(description='Top 10 by eligible voice time on this server')
    async def voice_top(self,i:discord.Interaction):
        if not self.bot.store.config(i.guild_id)['voice_xp']:
            return await reply(i,'Voice activity is not enabled on this server.')
        rows=self.bot.store.all('SELECT user,voice_seconds FROM activity WHERE guild=? AND voice_seconds>0 ORDER BY voice_seconds DESC,user LIMIT 10',(i.guild_id,))
        lines=[]
        for n,r in enumerate(rows,1):
            m=r['voice_seconds']//60
            lines.append(f'**{n}.** <@{r["user"]}> — {m//60}h {m%60:02d}m')
        await i.response.send_message(embed=embed('🎙 Voice leaderboard','\n'.join(lines) or 'No eligible voice activity yet.'))

    @app_commands.guild_only()
    @app_commands.command(description='Real server stats')
    async def serverinfo(self,i:discord.Interaction):
        rooms=self.bot.store.one('SELECT COUNT(*) n FROM rooms WHERE guild=?',(i.guild_id,))['n']
        await i.response.send_message(embed=embed(i.guild.name,f'Members: **{i.guild.member_count}**\nTemporary rooms: **{rooms}**\nChannels: **{len(i.guild.channels)}**'))
    @app_commands.guild_only()
    @app_commands.command(description='Send a formatted announcement without mass pings')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def announce(self,i:discord.Interaction,title:app_commands.Range[str,1,200],text:app_commands.Range[str,1,3500],channel:discord.TextChannel|None=None):
        await i.response.defer(ephemeral=True)
        await (channel or i.channel).send(embed=embed(title,text))
        await reply(i,'Announcement sent ✅')
    def target(self,i,member):
        if member.id in (i.user.id,i.guild.owner_id,self.bot.user.id): raise ValueError('Invalid moderation target.')
        if i.user.id!=i.guild.owner_id and member.top_role>=i.user.top_role: raise ValueError('Member role equal / higher than yours.')
        if member.top_role>=i.guild.me.top_role: raise ValueError('Move bot role above member role.')
    @app_commands.guild_only()
    @app_commands.command(description='Record a warning')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self,i:discord.Interaction,member:discord.Member,reason:app_commands.Range[str,1,500]):
        self.target(i,member)
        self.bot.store.run('INSERT INTO warnings(guild,user,moderator,reason,at) VALUES(?,?,?,?,?)',(i.guild_id,member.id,i.user.id,reason,time.time()))
        await i.response.defer(ephemeral=True)
        await self.bot.audit(i.guild,'Warning',f'{member.id} • by {i.user.id}: {reason}')
        await reply(i,'Warning recorded ✅')
    @app_commands.guild_only()
    @app_commands.command(description='Read the last 15 warnings for a member')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self,i:discord.Interaction,member:discord.Member):
        rows=self.bot.store.all('SELECT * FROM warnings WHERE guild=? AND user=? ORDER BY id DESC LIMIT 15',(i.guild_id,member.id))
        await reply(i,'\n'.join(f'#{r["id"]}: {r["reason"][:100]}' for r in rows) or 'No warnings.')
    @app_commands.guild_only()
    @app_commands.command(description='Timeout a member for 1–40320 minutes')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self,i:discord.Interaction,member:discord.Member,minutes:app_commands.Range[int,1,40320],reason:app_commands.Range[str,1,500]='Moderation'):
        self.target(i,member); await i.response.defer(ephemeral=True)
        await member.timeout(timedelta(minutes=minutes),reason=f'{i.user.id}: {reason}')
        await self.bot.audit(i.guild,'Timeout',f'{member.id} • {minutes} min • {reason}')
        await reply(i,'Timeout applied ✅')
    @app_commands.guild_only()
    @app_commands.command(description='Remove a timeout')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self,i:discord.Interaction,member:discord.Member):
        self.target(i,member); await i.response.defer(ephemeral=True)
        await member.timeout(None,reason=f'{i.user.id}: remove timeout'); await reply(i,'Timeout removed ✅')
    @app_commands.guild_only()
    @app_commands.command(description='Kick a member')
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self,i:discord.Interaction,member:discord.Member,reason:app_commands.Range[str,1,500]):
        self.target(i,member); await i.response.defer(ephemeral=True)
        await member.kick(reason=f'{i.user.id}: {reason}')
        await self.bot.audit(i.guild,'Kick',f'{member.id} • {reason}'); await reply(i,'Member kicked.')
    @app_commands.guild_only()
    @app_commands.command(description='Ban a member; preserves message history')
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self,i:discord.Interaction,member:discord.Member,reason:app_commands.Range[str,1,500]):
        self.target(i,member); await i.response.defer(ephemeral=True)
        await member.ban(reason=f'{i.user.id}: {reason}',delete_message_seconds=0)
        await self.bot.audit(i.guild,'Ban',f'{member.id} • {reason}'); await reply(i,'Member banned.')
    @app_commands.guild_only()
    @app_commands.command(description='Unban by Discord user ID')
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self,i:discord.Interaction,user_id:str):
        if not user_id.isdecimal(): raise ValueError('Valid user ID required.')
        await i.response.defer(ephemeral=True)
        await i.guild.unban(discord.Object(id=int(user_id)),reason=f'{i.user.id}: unban')
        await self.bot.audit(i.guild,'Unban',user_id); await reply(i,'Unbanned ✅')
    @app_commands.guild_only()
    @app_commands.command(description='Remove 1–100 recent messages from this channel')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self,i:discord.Interaction,count:app_commands.Range[int,1,100]):
        await i.response.defer(ephemeral=True)
        removed=await i.channel.purge(limit=count)
        await self.bot.audit(i.guild,'Purge',f'{len(removed)} messages • channel {i.channel_id} • by {i.user.id}')
        await reply(i,f'{len(removed)} messages removed.')
    @app_commands.guild_only()
    @app_commands.command(description='Start a timed giveaway with persistent participation buttons')
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(prize=[app_commands.Choice(name=v[0],value=k) for k,v in CATALOG.items()])
    async def giveaway(self,i:discord.Interaction,prize:str,minutes:app_commands.Range[int,1,43200],winners:app_commands.Range[int,1,20]=1,min_level:app_commands.Range[int,0,100]=0,required_role:discord.Role|None=None):
        if prize not in CATALOG: raise ValueError('Choose a free profile cosmetic.')
        await i.response.defer(ephemeral=True); ends=time.time()+minutes*60
        name=CATALOG[prize][0]+' cosmetic'
        m=await i.channel.send(embed=embed('🎁 '+name,'Preparing free event…'))
        with self.bot.store.db:
            self.bot.store.db.execute('INSERT INTO giveaways(message,guild,channel,prize,ends,count) VALUES(?,?,?,?,?,?)',(m.id,i.guild_id,i.channel_id,name,ends,winners))
            self.bot.store.db.execute('INSERT INTO giveaway_rules VALUES(?,?,?,?,?)',(m.id,i.guild_id,min_level,required_role.id if required_role else 0,prize))
        r=self.bot.store.one('SELECT * FROM giveaways WHERE message=?',(m.id,))
        await m.edit(embed=self.bot.giveaway_embed(r),view=GiveawayPanel(self.bot,m.id))
        await reply(i,f'Free cosmetic event started ✅ ID: {m.id}')

    @app_commands.guild_only()
    @app_commands.command(description='Staff: view recent giveaway history on this server')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def giveaway_history(self,i:discord.Interaction):
        rows=self.bot.store.all('SELECT * FROM giveaways WHERE guild=? ORDER BY ends DESC LIMIT 10',(i.guild_id,))
        e=embed('TOV4R • Giveaway history')
        for r in rows:
            n=self.bot.store.one('SELECT COUNT(*) n FROM entries WHERE message=?',(r['message'],))['n']
            e.add_field(name=r['prize'],value=f"{r['state']} • {n} entries • ID: {r['message']}",inline=False)
        if not rows: e.description='No events yet.'
        await i.response.send_message(embed=e,ephemeral=True)

    @app_commands.guild_only()
    @app_commands.command(description='Owner: save and download a private server structure and bot settings backup')
    async def backup(self,i:discord.Interaction):
        if i.user.id!=i.guild.owner_id: raise ValueError('Server owner only.')
        await i.response.defer(ephemeral=True)
        from server_manager import snapshot
        bid=save_backup(self.bot.store,i.guild_id,snapshot(i.guild))
        payload=json.dumps(read_backup(self.bot.store,i.guild_id,bid),ensure_ascii=False,indent=2).encode()
        await i.followup.send(f'Backup #{bid}. Includes roles, channels, permissions and selected bot settings. No messages. Keep private.',file=discord.File(io.BytesIO(payload),filename=f'tov4r-backup-{bid}.json'),ephemeral=True)

    @app_commands.guild_only()
    @app_commands.command(description='Owner: list saved server backup IDs')
    async def backup_list(self,i:discord.Interaction):
        if i.user.id!=i.guild.owner_id: raise ValueError('Server owner only.')
        rows=self.bot.store.all('SELECT id,created FROM community_backups WHERE guild=? ORDER BY id DESC LIMIT 5',(i.guild_id,))
        await reply(i,'\n'.join(f"#{r['id']} • <t:{int(r['created'])}:F>" for r in rows) or 'No backups. Use /backup.')

    @app_commands.guild_only()
    @app_commands.command(description='Owner: preview or restore bot settings only from a server backup')
    async def backup_restore(self,i:discord.Interaction,backup_id:app_commands.Range[int,1,2147483647],confirm:bool=False):
        if i.user.id!=i.guild.owner_id: raise ValueError('Server owner only.')
        data=read_backup(self.bot.store,i.guild_id,backup_id)
        cfg=self.bot.store.config(i.guild_id)
        diff=[k for k,v in data['settings'].items() if cfg.get(k)!=v]
        if not confirm:
            return await reply(i,'Settings to restore: '+(', '.join(diff) or 'No changes')+'. Roles and channels are never modified. Run again with confirm:True to apply.')
        changes=restore_settings(self.bot.store,i.guild_id,backup_id)
        self.bot.voice_previous={k for k in self.bot.voice_previous if k[0]!=i.guild_id}
        self.bot.security.clear(i.guild_id)
        await reply(i,'Bot settings restored. Discord roles, channels and permissions unchanged.')

    async def get_giveaway(self,i,message_id):
        if not message_id.isdecimal(): raise ValueError('Message ID required.')
        r=self.bot.store.one('SELECT * FROM giveaways WHERE message=? AND guild=?',(int(message_id),i.guild_id))
        if not r: raise ValueError('Giveaway not found in this server.')
        return r
    @app_commands.guild_only()
    @app_commands.command(description='End a giveaway now')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def giveaway_end(self,i:discord.Interaction,message_id:str):
        r=await self.get_giveaway(i,message_id); await i.response.defer(ephemeral=True)
        await self.bot.finish_giveaway(r['message']); await reply(i,'Giveaway ended ✅')
    @app_commands.guild_only()
    @app_commands.command(description='Draw new winners for a completed giveaway')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def giveaway_reroll(self,i:discord.Interaction,message_id:str):
        r=await self.get_giveaway(i,message_id)
        if r['state']!='done': raise ValueError('End giveaway first.')
        await i.response.defer(ephemeral=True); await self.bot.finish_giveaway(r['message'],reroll=True)
        await reply(i,'New draw completed ✅')
    @app_commands.guild_only()
    @app_commands.command(description='Staff: export ticket text by ID or from the current ticket')
    async def transcript(self,i:discord.Interaction,ticket_id:str|None=None):
        if not is_staff(i.user,self.bot.store.config(i.guild_id)):
            raise ValueError('Staff only.')
        if ticket_id is not None and (not ticket_id.isdecimal() or len(ticket_id)>19 or int(ticket_id)>9223372036854775807):
            raise ValueError('Valid ticket ID required.')
        channel_id=int(ticket_id) if ticket_id else i.channel_id
        r=self.bot.store.one('SELECT * FROM tickets WHERE guild=? AND channel=?',(i.guild_id,channel_id))
        if not r: raise ValueError('Ticket not found in this server.')
        await i.response.defer(ephemeral=True)
        saved=self.bot.store.one('SELECT body FROM ticket_transcripts WHERE guild=? AND channel=? AND expires>?',(i.guild_id,channel_id,time.time()))
        if saved:
            body=saved['body']
        else:
            c=i.guild.get_channel(channel_id)
            if not isinstance(c,discord.TextChannel):
                return await reply(i,'Transcript unavailable or expired.')
            if not c.permissions_for(i.user).view_channel:
                return await reply(i,'No access to this ticket.')
            body=await ticket_transcript_bytes(c)
        await i.followup.send(file=discord.File(io.BytesIO(body),filename=f'ticket-{channel_id}.txt'),ephemeral=True)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    token=os.getenv('DISCORD_TOKEN','').strip()
    if not token or token=='PASTE_BOT_TOKEN_HERE':
        raise SystemExit('Set DISCORD_TOKEN in .env. Read README_FR.md. Never share your token.')
    # Railway volumes are root-owned on first mount. Initialise ownership, then
    # permanently drop privileges before opening the database or connecting.
    if hasattr(os,'geteuid') and os.geteuid()==0:
        import pwd
        account=pwd.getpwnam('app')
        data_path=Path(os.getenv('DATABASE_PATH','data/community.db')).resolve().parent
        data_path.mkdir(parents=True,exist_ok=True)
        os.chown(data_path,account.pw_uid,account.pw_gid)
        for file in data_path.iterdir():
            if file.is_file() and not file.is_symlink(): os.chown(file,account.pw_uid,account.pw_gid)
        os.setgroups([]); os.setgid(account.pw_gid); os.setuid(account.pw_uid)
    Community().run(token,log_handler=None)
