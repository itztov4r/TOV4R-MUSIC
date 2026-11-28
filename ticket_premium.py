"""Guild-scoped ticket tools; no automatic access to private report tickets."""
import re
import time
import discord
from discord import app_commands
from discord.ext import commands
from verification_gate import admitted

def staff(member, cfg):
    return member.guild_permissions.manage_guild or any(r.id==cfg['support_role'] and not r.is_default() for r in member.roles)

async def reply(i,text):
    if i.response.is_done(): await i.followup.send(text,ephemeral=True)
    else: await i.response.send_message(text,ephemeral=True)

def check_ticket(bot,i):
    if not i.guild or not isinstance(i.channel,discord.TextChannel) or not staff(i.user,bot.store.config(i.guild_id)) or not i.channel.permissions_for(i.user).view_channel:
        raise ValueError("Authorized ticket staff only.")
    row=bot.store.one("SELECT t.*,COALESCE(d.kind,'support') kind FROM tickets t LEFT JOIN ticket_details d ON t.channel=d.channel AND t.guild=d.guild WHERE t.guild=? AND t.channel=? AND t.closed=0",(i.guild_id,i.channel_id))
    if not row: raise ValueError("Open ticket required.")
    return row

class TicketReopen(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=None)
        self.bot=bot

    @discord.ui.button(label="Reopen / Réouvrir",custom_id="ticket:reopen:v8",style=discord.ButtonStyle.primary)
    async def reopen(self,i,button):
        if not i.guild or not isinstance(i.channel,discord.TextChannel) or not staff(i.user,self.bot.store.config(i.guild_id)) or not i.channel.permissions_for(i.user).view_channel:
            return await reply(i,"Authorized ticket staff only.")
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('close',i.channel_id)]:
            if not self.bot.store.reopen_ticket(i.guild_id,i.channel_id,time.time()):
                return await reply(i,"Reopen unavailable: countdown elapsed or another ticket is open.")
            await reply(i,"Ticket reopened ✅")
            try:
                await i.message.edit(content="Ticket reopened ✅",embed=None,view=None)
            except discord.HTTPException: pass

class TicketPremium(commands.Cog):
    def __init__(self,bot): self.bot=bot

    async def cog_app_command_error(self,i,error):
        original=getattr(error,'original',error)
        if isinstance(original,ValueError): await reply(i,str(original))
        elif isinstance(original,app_commands.CheckFailure): await reply(i,"Permission required.")
        elif isinstance(original,discord.HTTPException): await reply(i,"Discord rejected the action; check bot permissions.")
        else: await reply(i,"Ticket action failed; check bot logs.")

    @app_commands.guild_only()
    @app_commands.command(description="Staff: add a guest to a support or partnership ticket")
    async def ticket_add(self,i:discord.Interaction,member:discord.Member):
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('close',i.channel_id)]:
            r=check_ticket(self.bot,i)
            if not admitted(self.bot,member): raise ValueError('Member must complete verification first.')
            if r['kind']=='report': raise ValueError("Report tickets cannot invite additional members.")
            if member.bot or member.id==r['owner'] or i.channel.permissions_for(member).view_channel or member in i.channel.overwrites:
                raise ValueError("Choose a human member without existing access or overrides.")
            ow=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
            await i.channel.set_permissions(member,overwrite=ow,reason="TOV4R staff ticket guest")
            self.bot.store.run('INSERT OR IGNORE INTO ticket_guests VALUES(?,?,?)',(i.channel_id,i.guild_id,member.id))
            await i.channel.send(f"Ticket staff added <@{member.id}> to this ticket.",allowed_mentions=discord.AllowedMentions.none())
        await reply(i,"Guest added ✅")

    @app_commands.guild_only()
    @app_commands.command(description="Staff: remove a guest invited through the ticket system")
    async def ticket_remove(self,i:discord.Interaction,member:discord.Member):
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('close',i.channel_id)]:
            r=check_ticket(self.bot,i)
            if member.id==r['owner'] or staff(member,self.bot.store.config(i.guild_id)):
                raise ValueError("Cannot remove ticket owner or staff.")
            if not self.bot.store.one('SELECT 1 FROM ticket_guests WHERE guild=? AND channel=? AND user=?',(i.guild_id,i.channel_id,member.id)):
                raise ValueError("Not a tracked ticket guest.")
            expected=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
            if i.channel.overwrites_for(member)!=expected: raise ValueError("Permissions changed manually; review in Discord.")
            await i.channel.set_permissions(member,overwrite=None,reason="TOV4R ticket guest removed")
            self.bot.store.run('DELETE FROM ticket_guests WHERE guild=? AND channel=? AND user=?',(i.guild_id,i.channel_id,member.id))
            await i.channel.send(f"Staff removed the guest override for <@{member.id}>.",allowed_mentions=discord.AllowedMentions.none())
        await reply(i,"Guest override removed ✅. Other permissions may still grant access.")

    @app_commands.guild_only()
    @app_commands.command(description="Staff: rename the current open ticket")
    async def ticket_rename(self,i:discord.Interaction,name:app_commands.Range[str,1,60]):
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('close',i.channel_id)]:
            check_ticket(self.bot,i)
            clean=re.sub(r'[^a-z0-9-]+','-',name.lower()).strip('-')
            if not clean: raise ValueError("Use letters or numbers.")
            await i.channel.edit(name='ticket-'+clean,reason="TOV4R ticket rename")
        await reply(i,"Ticket renamed ✅")

    @app_commands.guild_only()
    @app_commands.command(description="Staff: set the priority of the current ticket")
    @app_commands.choices(priority=[app_commands.Choice(name=x.title(),value=x) for x in ('low','normal','high')])
    async def ticket_priority(self,i:discord.Interaction,priority:str):
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[('close',i.channel_id)]:
            check_ticket(self.bot,i)
            if not self.bot.store.set_ticket_priority(i.guild_id,i.channel_id,priority): raise ValueError("Open ticket required.")
            await i.channel.send("Ticket priority: "+priority.title())
        await reply(i,"Priority saved ✅")

    @app_commands.guild_only()
    @app_commands.command(description="Staff: view ticket controls and priority privately")
    async def ticket_control(self,i:discord.Interaction):
        check_ticket(self.bot,i)
        p=self.bot.store.one('SELECT priority FROM ticket_priority WHERE guild=? AND channel=?',(i.guild_id,i.channel_id))
        await reply(i,"Priority: "+(p['priority'] if p else 'normal')+" • /ticket_add · /ticket_remove · /ticket_rename · /ticket_priority")

    @app_commands.guild_only()
    @app_commands.command(description="Manager: view recent ticket metadata privately")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_history(self,i:discord.Interaction):
        rows=self.bot.store.all("SELECT t.channel,t.closed,COALESCE(d.kind,'support') kind,COALESCE(p.priority,'normal') priority FROM tickets t LEFT JOIN ticket_details d ON t.channel=d.channel AND t.guild=d.guild LEFT JOIN ticket_priority p ON t.channel=p.channel AND t.guild=p.guild WHERE t.guild=? ORDER BY t.channel DESC LIMIT 10",(i.guild_id,))
        lines=[f"{r['channel']} • {r['kind']} • {r['priority']} • "+('Closed' if r['closed'] else 'Open') for r in rows]
        await reply(i,"Recent tickets:\n"+('\n'.join(lines) or 'None'))

    @app_commands.guild_only()
    @app_commands.command(description="Manager: view ticket claim counts privately")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_stats(self,i:discord.Interaction):
        rows=self.bot.store.all("SELECT d.claimed_by,COUNT(*) n,SUM(t.closed) closed FROM ticket_details d JOIN tickets t ON t.channel=d.channel AND t.guild=d.guild WHERE d.guild=? AND d.claimed_by<>0 GROUP BY d.claimed_by ORDER BY n DESC LIMIT 10",(i.guild_id,))
        lines=[f"<@{r['claimed_by']}> • {r['n']} claimed • {r['closed']} closed" for r in rows]
        await reply(i,"Staff claim statistics:\n"+('\n'.join(lines) or 'None'))
