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

load_dotenv()
log = logging.getLogger('tov4r')
ROOT = Path(__file__).parent
COLOR = 0xef3340

def embed(title, description=''):
    e = discord.Embed(title=title, description=description, color=COLOR)
    e.set_footer(text='Tov4r Bot • Made for your people')
    return e

async def reply(i, text):
    if i.response.is_done(): await i.followup.send(text, ephemeral=True)
    else: await i.response.send_message(text, ephemeral=True)

def is_staff(member, cfg):
    return member.guild_permissions.manage_guild or any(r.id == cfg['support_role'] for r in member.roles)

def safe_role(role, guild):
    # Self-service must never grant moderation/administrative permissions.
    dangerous = ('administrator','manage_guild','manage_roles','manage_channels','kick_members',
                 'ban_members','moderate_members','manage_messages','manage_webhooks','mention_everyone',
                 'manage_threads','manage_events','mute_members','deafen_members','move_members','view_audit_log')
    return not role.is_default() and not role.managed and role < guild.me.top_role and not any(getattr(role.permissions,k,False) for k in dangerous)

class Community(commands.Bot):
    def __init__(self, store=None):
        intents=discord.Intents.default(); intents.members=True; intents.message_content=True
        super().__init__(command_prefix=".", case_insensitive=True, intents=intents,
                         allowed_mentions=discord.AllowedMentions.none())
        self.store=store or Store(os.getenv('DATABASE_PATH','data/community.db'))
        self.locks=collections.defaultdict(asyncio.Lock)
        self.spam=collections.defaultdict(collections.deque)
        self.web_runner=None
    async def setup_hook(self):
        expected=os.getenv('DISCORD_APPLICATION_ID','').strip()
        if expected and self.application_id != int(expected):
            raise RuntimeError(f'Wrong bot token: authenticated application {self.application_id}, expected {expected}. Update DISCORD_TOKEN; no commands were changed.')
        log.info('Authenticated application ID: %s',self.application_id)
        await self.add_cog(Features(self))
        from voice_commands import VoiceCommands
        await self.add_cog(VoiceCommands(self))
        self.add_view(VoicePanel(self)); self.add_view(TicketPanel(self)); self.add_view(CloseTicket(self))
        for r in self.store.all("SELECT * FROM giveaways WHERE state IN ('open','drawn')"):
            self.add_view(GiveawayPanel(self,r['message']),message_id=r['message'])
        for r in self.store.all('SELECT * FROM role_panels'):
            self.add_view(RolePanel(self,json.loads(r['roles'])),message_id=r['message'])
        await self.sync_public_commands()
        from dashboard import start_dashboard
        self.web_runner=await start_dashboard(self)
        self.maintenance.start()
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
            attach_files=True,read_message_history=True,manage_channels=True,manage_roles=True,
            manage_messages=True,connect=True,move_members=True,kick_members=True,
            ban_members=True,moderate_members=True)
        return discord.utils.oauth_url(self.application_id,permissions=permissions,
                                       scopes=('bot','applications.commands'))
    async def close(self):
        self.maintenance.cancel()
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
    async def on_member_join(self,member):
        cfg=self.store.config(member.guild.id)
        role=member.guild.get_role(cfg['autorole'])
        if role and safe_role(role,member.guild):
            try: await member.add_roles(role,reason='TOV4R Auto-role')
            except discord.HTTPException: await self.audit(member.guild,'Auto-role error',str(member.id))
        c=member.guild.get_channel(cfg['welcome_channel'])
        if isinstance(c,discord.TextChannel):
            image=Image.new('RGB',(1000,340),'#101117'); d=ImageDraw.Draw(image)
            d.rounded_rectangle((22,22,978,318),radius=28,fill='#1a1b25',outline='#ef3340',width=3)
            def font(size):
                for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','C:/Windows/Fonts/arialbd.ttf'):
                    if Path(p).exists(): return ImageFont.truetype(p,size)
                return ImageFont.load_default(size=size)
            d.text((60,55),'TOV4R / COMMUNITY',fill='#ef3340',font=font(25))
            d.text((60,116),'MER7BA BIK!',fill='white',font=font(52))
            # Avoid font failures on arbitrary display names by leaving them in the embed.
            d.text((60,220),f'MEMBER #{member.guild.member_count}',fill='#aaaabc',font=font(24))
            data=io.BytesIO(); image.save(data,format='PNG'); data.seek(0)
            text=cfg['welcome_text'].replace('{mention}',member.mention).replace('{server}',member.guild.name).replace('{name}',member.display_name)
            e=embed('Mer7ba '+member.display_name,text); e.set_image(url='attachment://welcome.png')
            try: await c.send(embed=e,file=discord.File(data,filename='welcome.png'))
            except discord.HTTPException: log.exception('Welcome failed')
        await self.audit(member.guild,'Member joined',f'{member} • {member.id}')
    async def on_member_remove(self,member):
        await self.audit(member.guild,'Member left',f'{member} • {member.id}')
        c=member.guild.get_channel(self.store.config(member.guild.id)['welcome_channel'])
        if isinstance(c,discord.TextChannel):
            try: await c.send(embed=embed('Bslama 👋',f'{member.display_name} khrej mn server.'))
            except discord.HTTPException: pass
    async def on_message(self,message):
        if not message.guild or message.author.bot or not isinstance(message.author,discord.Member): return
        cfg=self.store.config(message.guild.id)
        if not message.author.guild_permissions.manage_messages:
            reason=None
            words=[w.strip().casefold() for w in cfg['blocked_words'].split(',') if w.strip()]
            if any(w in message.content.casefold() for w in words): reason='Blocked word'
            if cfg['anti_invites'] and re.search(r'(discord\.gg/|discord(?:app)?\.com/invite/)',message.content,re.I): reason='Discord invite'
            if cfg['antispam']:
                key=(message.guild.id,message.author.id); q=self.spam[key]; now=time.monotonic()
                q.append(now)
                while q and now-q[0]>7: q.popleft()
                if len(q)>=6: reason='Spam: 6 messages / 7 seconds'
            if reason:
                try: await message.delete()
                except discord.HTTPException: pass
                if reason.startswith('Spam') and message.author.top_role < message.guild.me.top_role and message.author.id != message.guild.owner_id:
                    try: await message.author.timeout(timedelta(minutes=1),reason=reason)
                    except discord.HTTPException: pass
                await self.audit(message.guild,'AutoMod',f'{message.author.id}: {reason}')
                return
        if message.content.startswith('.'):
            await self.process_commands(message)
            return
        if cfg['levels']:
            up=self.store.award(message.guild.id,message.author.id,cfg['xp_cooldown'])
            if up is not None:
                try: await message.channel.send(embed=embed('Level up!',f'{message.author.mention} wsel level **{up}** 🎉'))
                except discord.HTTPException: pass
    async def on_message_delete(self,message):
        if message.guild and not message.author.bot:
            await self.audit(message.guild,'Message deleted',f'User {message.author.id} • Channel {message.channel.id}\n{message.content[:1500]}')
    async def on_message_edit(self,before,after):
        if after.guild and not after.author.bot and before.content != after.content:
            await self.audit(after.guild,'Message edited',f'User {after.author.id}\nBefore: {before.content[:700]}\nAfter: {after.content[:700]}')
    async def on_guild_channel_delete(self,channel):
        self.store.run('DELETE FROM rooms WHERE channel=?',(channel.id,))
        self.store.run('UPDATE tickets SET closed=1 WHERE channel=?',(channel.id,))
    async def on_voice_state_update(self,member,before,after):
        if member.bot or before.channel == after.channel: return
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
                    overwrites={member.guild.default_role:discord.PermissionOverwrite(view_channel=True,connect=True),
                                member.guild.me:discord.PermissionOverwrite(view_channel=True,connect=True,manage_channels=True,move_members=True),
                                member:discord.PermissionOverwrite(view_channel=True,connect=True)}
                    channel=None
                    try:
                        channel=await member.guild.create_voice_channel(f'🔊 {member.display_name}'[:100],category=cat,overwrites=overwrites,reason='TOV4R Join to Create')
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
    @tasks.loop(seconds=15)
    async def maintenance(self):
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
        for key in list(self.spam):
            if not self.spam[key] or now-self.spam[key][-1]>60: del self.spam[key]
    @maintenance.before_loop
    async def before_maintenance(self): await self.wait_until_ready()
    async def finish_giveaway(self,mid,reroll=False):
        async with self.locks[('giveaway',mid)]:
            r=self.store.one('SELECT * FROM giveaways WHERE message=?',(mid,))
            if not r: raise ValueError('Giveaway not found')
            if r['state']=='done' and not reroll: return
            g=self.get_guild(r['guild']); c=g.get_channel(r['channel']) if g else None
            if not c: return
            if r['state']=='open' or reroll:
                ids=[e['user'] for e in self.store.all('SELECT user FROM entries WHERE message=?',(mid,)) if g.get_member(e['user'])]
                winners=secrets.SystemRandom().sample(ids,min(len(ids),r['count']))
                self.store.run("UPDATE giveaways SET state='drawn',winners=? WHERE message=?",(json.dumps(winners),mid))
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
    def __init__(self,bot):
        super().__init__(timeout=None); self.bot=bot
        for action,label in [('lock','🔒 Lock'),('unlock','🔓 Unlock'),('hide','👻 Hide'),('show','👁 Show'),('rename','✏ Rename'),('limit','👥 Limit'),('permit','✅ Permit'),('kick','⛔ Kick / block'),('transfer','👑 Transfer'),('claim','🙋 Claim')]:
            b=discord.ui.Button(label=label,custom_id='voice:'+action,style=discord.ButtonStyle.secondary)
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
                    c,_=await owned_room(bot,i)
                    if a in ('rename','limit'): await i.response.send_modal(RoomModal(bot,a)); return
                    if a in ('permit','kick','transfer'):
                        v=ErrorView(timeout=90); v.add_item(RoomUserSelect(bot,a))
                        await i.response.send_message('Choose member:',view=v,ephemeral=True); return
                    await i.response.defer(ephemeral=True)
                    ow=c.overwrites_for(i.guild.default_role)
                    if a in ('lock','unlock'): ow.connect=a=='unlock'
                    else: ow.view_channel=a=='show'
                    await c.set_permissions(i.guild.default_role,overwrite=ow)
                    await reply(i,'Tgad ✅ Explicitly permitted members can still join locked rooms.')
                except ValueError as e: await reply(i,str(e))
            async def callback(i,handler=action_handler):
                channel=i.user.voice.channel if isinstance(i.user,discord.Member) and i.user.voice else None
                async with bot.locks[('room',channel.id if channel else i.user.id)]:
                    await handler(i)
            b.callback=callback; self.add_item(b)

class TicketPanel(ErrorView):
    def __init__(self,bot): super().__init__(timeout=None); self.bot=bot
    @discord.ui.button(label='🎫 Open ticket',custom_id='ticket:open',style=discord.ButtonStyle.danger)
    async def open_ticket(self,i,button):
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('ticket',i.guild_id,i.user.id)]:
            cfg=self.bot.store.config(i.guild_id)
            existing=self.bot.store.one('SELECT * FROM tickets WHERE guild=? AND owner=? AND closed=0',(i.guild_id,i.user.id))
            if existing and i.guild.get_channel(existing['channel']): return await reply(i,f'Ticket dyalek: <#{existing["channel"]}>')
            if existing: self.bot.store.run('UPDATE tickets SET closed=1 WHERE channel=?',(existing['channel'],))
            cat=i.guild.get_channel(cfg['ticket_category']); role=i.guild.get_role(cfg['support_role'])
            if not isinstance(cat,discord.CategoryChannel) or not role: return await reply(i,'Admin khas ydir /setup lwel.')
            overwrites={i.guild.default_role:discord.PermissionOverwrite(view_channel=False),
                        i.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True),
                        i.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True),
                        role:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
            c=await i.guild.create_text_channel(f'ticket-{i.user.id}',category=cat,overwrites=overwrites)
            self.bot.store.run('INSERT INTO tickets(channel,guild,owner) VALUES(?,?,?)',(c.id,i.guild_id,i.user.id))
            await c.send(embed=embed('Support • TOV4R',f'{i.user.mention}, kteb lmochkil dyalek hna.\nClose kayarchive ticket w kay7yed access dyalek.'),view=CloseTicket(self.bot))
            await reply(i,f'Ticket wajd: {c.mention}')
            await self.bot.audit(i.guild,'Ticket opened',f'{c.id} • {i.user.id}')

class CloseTicket(ErrorView):
    def __init__(self,bot): super().__init__(timeout=None); self.bot=bot
    @discord.ui.button(label='🔒 Close & archive',custom_id='ticket:close',style=discord.ButtonStyle.secondary)
    async def close_ticket(self,i,button):
        async with self.bot.locks[('close',i.channel_id)]:
            r=self.bot.store.one('SELECT * FROM tickets WHERE channel=? AND closed=0',(i.channel_id,))
            if not r: return await reply(i,'Ticket deja msdod.')
            if i.user.id!=r['owner'] and not is_staff(i.user,self.bot.store.config(i.guild_id)): return await reply(i,'Ma3ndekch permission.')
            await i.response.defer(ephemeral=True)
            # Archive permissions first; never delete user messages.
            owner=i.guild.get_member(r['owner'])
            overwrites={target:ow for target,ow in i.channel.overwrites.items() if target.id!=r['owner']}
            if owner: overwrites[owner]=discord.PermissionOverwrite(view_channel=False,send_messages=False)
            await i.channel.edit(name=f'closed-{r["owner"]}',overwrites=overwrites)
            self.bot.store.run('UPDATE tickets SET closed=1 WHERE channel=?',(i.channel_id,))
            await i.channel.send(embed=embed('Ticket archived',f'Closed by {i.user.id}. Staff can use /transcript.'))
            await self.bot.audit(i.guild,'Ticket closed',f'{i.channel_id} • by {i.user.id}')
            await reply(i,'Ticket tsed ✅')

class GiveawayPanel(ErrorView):
    def __init__(self,bot,mid):
        super().__init__(timeout=None); self.bot=bot; self.mid=mid
        b=discord.ui.Button(label='🎉 Participate / Leave',style=discord.ButtonStyle.danger,custom_id=f'giveaway:{mid}')
        b.callback=self.toggle; self.add_item(b)
    async def toggle(self,i):
        async with self.bot.locks[('giveaway',self.mid)]:
            r=self.bot.store.one('SELECT * FROM giveaways WHERE message=?',(self.mid,))
            if not r or r['state']!='open' or r['ends']<=time.time(): return await reply(i,'Giveaway sala.')
            old=self.bot.store.one('SELECT * FROM entries WHERE message=? AND user=?',(self.mid,i.user.id))
            if old: self.bot.store.run('DELETE FROM entries WHERE message=? AND user=?',(self.mid,i.user.id))
            else: self.bot.store.run('INSERT INTO entries VALUES(?,?)',(self.mid,i.user.id))
            await reply(i,'Khrejti mn giveaway.' if old else 'Dkhelti l giveaway ✅ Good luck!')

class RolePanel(ErrorView):
    def __init__(self,bot,roles):
        super().__init__(timeout=None)
        for rid,label in roles:
            b=discord.ui.Button(label=label[:80],custom_id=f'role:{rid}',style=discord.ButtonStyle.secondary)
            async def callback(i,role_id=rid):
                r=i.guild.get_role(role_id)
                if not r or not safe_role(r,i.guild): return await reply(i,'Had role ma9adrch n3tih. Admin khas ycheck permissions.')
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
        log.error('Command failed',exc_info=error)
        await reply(i,'Command ma kmelch. Check bot permissions / role hierarchy, w logs dyal application.')
    @app_commands.guild_only()
    @app_commands.command(description='Invite Tov4r Bot to another server')
    async def invite(self,i:discord.Interaction):
        await i.response.send_message(embed=embed('Invite Tov4r Bot',
            f'[Add to your server]({self.bot.invite_url()})\nAn admin runs /setup after inviting the bot.'),ephemeral=True)
    @app_commands.guild_only()
    @app_commands.command(name='help',description='All Tov4r Bot commands')
    async def help_command(self,i:discord.Interaction):
        await i.response.send_message(embed=embed('TOV4R • Command center',
            '**Members**\n/help · /invite · /rank · /leaderboard · /serverinfo\n'
            '**Voice**\nدخل للروم ديالك وكتب .v help باش تشوف أوامر التحكم بالدارجة.\n'
            '**Staff**\n/setup · /configure · /panels · /rolepanel · /announce\n'
            '/warn · /warnings · /timeout · /untimeout · /kick · /ban · /unban · /purge\n'
            '/giveaway · /giveaway_end · /giveaway_reroll · /transcript\n'
            '**Dashboard**\nPrivate local admin dashboard; see README.'),ephemeral=True)
    @app_commands.guild_only()
    @app_commands.command(description='Create or repair TOV4R community channels and panels')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('setup',i.guild_id)]:
            g=i.guild; cfg=self.bot.store.config(g.id)
            async def category(key,name,private=False):
                c=g.get_channel(cfg[key])
                if not isinstance(c,discord.CategoryChannel):
                    ow={g.default_role:discord.PermissionOverwrite(view_channel=False),g.me:discord.PermissionOverwrite(view_channel=True,manage_channels=True)} if private else {}
                    c=await g.create_category(name,overwrites=ow)
                    cfg[key]=c.id; self.bot.store.update(g.id,{key:c.id})
                return c
            voice=await category('voice_category','TOV4R • VOICE')
            await category('ticket_category','TOV4R • SUPPORT',True)
            role=g.get_role(cfg['support_role'])
            if not role:
                role=await g.create_role(name='TOV4R Support',colour=discord.Colour(COLOR),reason='TOV4R setup')
                cfg['support_role']=role.id; self.bot.store.update(g.id,{'support_role':role.id})
            if not g.get_channel(cfg['voice_creator']):
                c=await g.create_voice_channel('➕ Create your room',category=voice)
                cfg['voice_creator']=c.id; self.bot.store.update(g.id,{'voice_creator':c.id})
            for key,name in [('welcome_channel','welcome'),('logs_channel','tov4r-logs')]:
                if not g.get_channel(cfg[key]):
                    ow={g.default_role:discord.PermissionOverwrite(view_channel=False),g.me:discord.PermissionOverwrite(view_channel=True,send_messages=True),role:discord.PermissionOverwrite(view_channel=True)} if key=='logs_channel' else {g.default_role:discord.PermissionOverwrite(send_messages=False),g.me:discord.PermissionOverwrite(send_messages=True)}
                    c=await g.create_text_channel(name,overwrites=ow)
                    cfg[key]=c.id; self.bot.store.update(g.id,{key:c.id})
            for name,view,description in [('voice-panel',VoicePanel(self.bot),'Dkhel l **➕ Create your room**, men ba3d control room dyalek hna.'),('tickets',TicketPanel(self.bot),'Support / report / request. Click Open ticket.')]:
                c=discord.utils.get(g.text_channels,name=name)
                if not c:
                    c=await g.create_text_channel(name,overwrites={g.default_role:discord.PermissionOverwrite(send_messages=False),g.me:discord.PermissionOverwrite(send_messages=True)})
                    await c.send(embed=embed('TOV4R • '+name,description),view=view)
            await self.bot.audit(g,'Setup','Community configured by '+str(i.user.id))
            await reply(i,'Setup wajd ✅ 3ti role TOV4R Support l staff. /configure bach tbedel settings, /panels bach t3awed tsift panel ila tmsa7.')
    @app_commands.guild_only()
    @app_commands.command(description='Set channels, support role and optional safe auto-role')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def configure(self,i:discord.Interaction,welcome:discord.TextChannel|None=None,logs:discord.TextChannel|None=None,support:discord.Role|None=None,autorole:discord.Role|None=None,levels:bool|None=None,antispam:bool|None=None,anti_invites:bool|None=None):
        changes={}
        for k,v in [('welcome_channel',welcome),('logs_channel',logs),('support_role',support),('autorole',autorole)]:
            if v: changes[k]=v.id
        if autorole and not safe_role(autorole,i.guild): raise ValueError('Auto-role khas ykon bla dangerous permissions, w ta7t bot role.')
        for k,v in [('levels',levels),('antispam',antispam),('anti_invites',anti_invites)]:
            if v is not None: changes[k]=v
        self.bot.store.update(i.guild_id,changes)
        await reply(i,'Settings saved ✅ Full settings f dashboard.')
    @app_commands.guild_only()
    @app_commands.command(description='Send a voice or ticket control panel here')
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(kind=[app_commands.Choice(name='Voice',value='voice'),app_commands.Choice(name='Tickets',value='tickets')])
    async def panels(self,i:discord.Interaction,kind:str):
        await i.response.defer(ephemeral=True)
        await i.channel.send(embed=embed('TOV4R • '+kind,'Use the buttons below.'),view=VoicePanel(self.bot) if kind=='voice' else TicketPanel(self.bot))
        await reply(i,'Panel sent ✅')
    @app_commands.guild_only()
    @app_commands.command(description='Publish a safe self-service role button')
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rolepanel(self,i:discord.Interaction,role:discord.Role):
        if not safe_role(role,i.guild) or (i.user.id!=i.guild.owner_id and role>=i.user.top_role): raise ValueError('Role unsafe or too high in hierarchy.')
        await i.response.defer(ephemeral=True)
        roles=[(role.id,role.name)]
        m=await i.channel.send(embed=embed('Choose your role',f'Click to add / remove **{role.name}**.'),view=RolePanel(self.bot,roles))
        self.bot.store.run('INSERT INTO role_panels VALUES(?,?,?)',(m.id,i.guild_id,json.dumps(roles)))
        await reply(i,'Role panel sent ✅')
    @app_commands.guild_only()
    @app_commands.command(description='See your level and XP')
    async def rank(self,i:discord.Interaction,member:discord.Member|None=None):
        m=member or i.user; r=self.bot.store.one('SELECT points FROM xp WHERE guild=? AND user=?',(i.guild_id,m.id))
        points=r['points'] if r else 0; lv=level(points)
        await i.response.send_message(embed=embed(m.display_name,f'Level **{lv}** • **{points} XP**\nNext level: {(lv+1)**2*100} XP'))
    @app_commands.guild_only()
    @app_commands.command(description='Top 10 community members by XP')
    async def leaderboard(self,i:discord.Interaction):
        rows=self.bot.store.all('SELECT user,points FROM xp WHERE guild=? ORDER BY points DESC LIMIT 10',(i.guild_id,))
        text='\n'.join(f'**{n}.** <@{r["user"]}> — {r["points"]} XP • Lv {level(r["points"])}' for n,r in enumerate(rows,1))
        await i.response.send_message(embed=embed('Community leaderboard',text or 'Mazal ma kayn XP.'))
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
    async def giveaway(self,i:discord.Interaction,prize:app_commands.Range[str,1,200],minutes:app_commands.Range[int,1,43200],winners:app_commands.Range[int,1,20]=1):
        await i.response.defer(ephemeral=True); ends=time.time()+minutes*60
        m=await i.channel.send(embed=embed('🎉 '+prize,f'Ends <t:{int(ends)}:R> • Winners: {winners}\nClick below to participate.'))
        self.bot.store.run('INSERT INTO giveaways(message,guild,channel,prize,ends,count) VALUES(?,?,?,?,?,?)',(m.id,i.guild_id,i.channel_id,prize,ends,winners))
        await m.edit(view=GiveawayPanel(self.bot,m.id)); await reply(i,f'Giveaway started ✅ ID: {m.id}')
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
    @app_commands.command(description='Export up to 5000 ticket messages as a text transcript')
    async def transcript(self,i:discord.Interaction):
        r=self.bot.store.one('SELECT * FROM tickets WHERE channel=?',(i.channel_id,))
        if not r or not is_staff(i.user,self.bot.store.config(i.guild_id)): raise ValueError('Staff only, inside a ticket.')
        await i.response.defer(ephemeral=True)
        lines=['TOV4R ticket transcript — last 5000 messages; attachment URLs only.']
        async for m in i.channel.history(limit=5000,oldest_first=False):
            lines.append(f'{m.created_at.isoformat()} | {m.author} ({m.author.id})\n{m.content}\n'+'\n'.join(a.url for a in m.attachments))
        data=io.BytesIO('\n\n'.join(lines).encode())
        await i.followup.send(file=discord.File(data,filename=f'ticket-{i.channel_id}.txt'),ephemeral=True)

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
