"""Community features: explicit participation, bounded votes, no mass mentions."""
import datetime as dt
import json
import time
import discord
from discord import app_commands
from discord.ext import commands

NONE=discord.AllowedMentions.none()
COLORS={"ruby":0xef3340,"ocean":0x3498db,"violet":0x9b59b6,"emerald":0x2ecc71}
LABELS={"suggestion":["👍 Support","👎 Oppose"],"event":["Going","Not going"],"star":["⭐ Appreciate"]}

def birthday_date(month,day):
    try: return dt.date(2000,month,day)
    except ValueError: raise ValueError("Invalid day/month.")

def event_time(value):
    try:
        when=dt.datetime.fromisoformat(value.replace("Z","+00:00"))
        if when.tzinfo is None: raise ValueError()
        stamp=when.timestamp()
    except (ValueError,OverflowError): raise ValueError("Use ISO date with timezone, e.g. 2027-01-10T18:00+01:00.")
    if not time.time()<stamp<=time.time()+366*86400: raise ValueError("Choose a future date within one year.")
    return stamp

def cast_vote(store,guild,message,user,choice,now):
    with store.db:
        r=store.db.execute("SELECT * FROM community_posts WHERE guild=? AND message=?",(guild,message)).fetchone()
        if not r or r["closed"] or (r["ends"] and r["ends"]<=now): return False
        if not 0<=choice<len(json.loads(r["choices"])): return False
        old=store.db.execute("SELECT choice FROM community_votes WHERE guild=? AND message=? AND user=?",(guild,message,user)).fetchone()
        if old and old["choice"]==choice:
            store.db.execute("DELETE FROM community_votes WHERE guild=? AND message=? AND user=?",(guild,message,user))
        else:
            store.db.execute("INSERT OR REPLACE INTO community_votes VALUES(?,?,?,?)",(message,guild,user,choice))
        return True

def community_channel(bot,guild):
    c=guild.get_channel(bot.store.config(guild.id)["community_channel"])
    if not isinstance(c,discord.TextChannel): return None
    if not c.permissions_for(guild.default_role).view_channel: return None
    p=c.permissions_for(guild.me)
    return c if p.view_channel and p.send_messages and p.embed_links else None

async def reply(i,text):
    if i.response.is_done(): await i.followup.send(text,ephemeral=True,allowed_mentions=NONE)
    else: await i.response.send_message(text,ephemeral=True,allowed_mentions=NONE)

def post_embed(bot,row):
    labels=json.loads(row["choices"])
    counts={x["choice"]:x["n"] for x in bot.store.all("SELECT choice,COUNT(*) n FROM community_votes WHERE guild=? AND message=? GROUP BY choice",(row["guild"],row["message"]))}
    title=row["title"]
    if row["kind"]=="star" and counts.get(0,0)>=3: title="⭐ Highlight • "+title
    e=discord.Embed(title=title[:256],description=row["body"],colour=0xef3340)
    for idx,label in enumerate(labels): e.add_field(name=label[:100],value=str(counts.get(idx,0)),inline=True)
    if row["ends"]: e.add_field(name="Event starts" if row["kind"]=="event" else "Ends",value=f'<t:{int(row["ends"])}:F>',inline=False)
    e.set_footer(text="Closed" if row["closed"] else "Choose an option; click again to remove your vote.")
    return e

class SocialView(discord.ui.View):
    def __init__(self,bot,labels):
        super().__init__(timeout=None)
        self.bot=bot
        for idx,label in enumerate(labels):
            b=discord.ui.Button(label=label[:80],custom_id=f"social:v9:{idx}",style=discord.ButtonStyle.secondary)
            async def click(i,n=idx):
                if not i.guild or i.user.bot: return await reply(i,"Unavailable.")
                await i.response.defer(ephemeral=True)
                async with bot.locks[("social",i.message.id)]:
                    if not cast_vote(bot.store,i.guild_id,i.message.id,i.user.id,n,time.time()):
                        return await reply(i,"Voting is closed.")
                    row=bot.store.one("SELECT * FROM community_posts WHERE guild=? AND message=?",(i.guild_id,i.message.id))
                    await reply(i,"Vote updated.")
                    try: await i.message.edit(embed=post_embed(bot,row))
                    except discord.HTTPException: pass
            b.callback=click
            self.add_item(b)

class CommunitySocial(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_app_command_error(self,i,error):
        original=getattr(error,"original",error)
        await reply(i,str(original) if isinstance(original,ValueError) else "Action unavailable. Check permissions and /setup.")
    async def publish(self,i,kind,title,body,labels,ends=0):
        channel=community_channel(self.bot,i.guild)
        if not channel: raise ValueError("Run /setup to configure a public community channel.")
        if not channel.permissions_for(i.user).view_channel: raise ValueError("No access to community channel.")
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[("social-create",i.guild_id,i.user.id)]:
            if kind in ("suggestion","star"):
                recent=self.bot.store.one("SELECT 1 FROM community_posts WHERE guild=? AND author=? AND kind=? AND message>? LIMIT 1",(i.guild_id,i.user.id,discord.utils.time_snowflake(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=10))))
                if recent: raise ValueError("One post per type every ten minutes.")
            m=await channel.send(embed=discord.Embed(title=title,description=body,colour=0xef3340),allowed_mentions=NONE)
            self.bot.store.run("INSERT INTO community_posts VALUES(?,?,?,?,?,?,?,?,?,0)",(m.id,i.guild_id,channel.id,i.user.id,kind,title,body,json.dumps(labels),ends))
            row=self.bot.store.one("SELECT * FROM community_posts WHERE message=?",(m.id,))
            await m.edit(embed=post_embed(self.bot,row),view=SocialView(self.bot,labels))
        await reply(i,"Published: "+m.jump_url)

    @app_commands.guild_only()
    @app_commands.command(description="Share a community suggestion and receive anonymous vote counts")
    async def suggest(self,i:discord.Interaction,idea:app_commands.Range[str,1,1000]):
        await self.publish(i,"suggestion","💡 Community suggestion",idea,LABELS["suggestion"])

    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.command(description="Create a two to four option poll")
    async def poll(self,i:discord.Interaction,question:app_commands.Range[str,1,180],option1:app_commands.Range[str,1,60],option2:app_commands.Range[str,1,60],option3:app_commands.Range[str,1,60]|None=None,option4:app_commands.Range[str,1,60]|None=None):
        labels=[x.strip() for x in (option1,option2,option3,option4) if x and x.strip()]
        if len(labels)<2 or len(set(x.casefold() for x in labels))!=len(labels): raise ValueError("Use at least two distinct options.")
        await self.publish(i,"poll","📊 "+question,"Vote for one option.",labels)

    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.command(description="Publish an optional community event with RSVP")
    async def event(self,i:discord.Interaction,title:app_commands.Range[str,1,150],starts:str,description:app_commands.Range[str,1,1000]="Community event"):
        await self.publish(i,"event","📅 "+title,description,LABELS["event"],event_time(starts))

    @app_commands.guild_only()
    @app_commands.command(description="Opt in your own community-channel message to the starboard")
    async def starboard(self,i:discord.Interaction,message_id:str):
        c=community_channel(self.bot,i.guild)
        if not c or i.channel_id!=c.id: raise ValueError("Use this in the community channel.")
        if not message_id.isdecimal() or len(message_id)>19: raise ValueError("Valid message ID required.")
        if not c.permissions_for(i.user).read_message_history: raise ValueError("No message history access.")
        m=await c.fetch_message(int(message_id))
        if m.author.id!=i.user.id or m.author.bot: raise ValueError("Only your own message can be nominated.")
        await self.publish(i,"star","Community message",m.jump_url,LABELS["star"])

    @app_commands.guild_only()
    @app_commands.command(description="Close your own post or a post you moderate")
    async def community_close(self,i:discord.Interaction,message_id:str):
        if not message_id.isdecimal() or len(message_id)>19 or int(message_id)>9223372036854775807: raise ValueError("Valid message ID required.")
        mid=int(message_id)
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[("social",mid)]:
            r=self.bot.store.one("SELECT * FROM community_posts WHERE guild=? AND message=?",(i.guild_id,mid))
            if not r or (r["author"]!=i.user.id and not i.user.guild_permissions.manage_messages): raise ValueError("Post not found or not yours.")
            c=i.guild.get_channel(r["channel"])
            if not c or not c.permissions_for(i.user).view_channel: raise ValueError("No channel access.")
            self.bot.store.run("UPDATE community_posts SET closed=1 WHERE guild=? AND message=?",(i.guild_id,mid))
            r["closed"]=1
            # Closing your nomination removes the link from the public board.
            try:
                m=await c.fetch_message(mid)
                if r["kind"]=="star": await m.delete()
                else: await m.edit(embed=post_embed(self.bot,r),view=None)
            except discord.NotFound: pass
        await reply(i,"Closed.")

    @app_commands.guild_only()
    @app_commands.command(description="Save or remove your optional birthday (day and month only)")
    async def birthday(self,i:discord.Interaction,day:app_commands.Range[int,1,31]|None=None,month:app_commands.Range[int,1,12]|None=None,share:bool=False,remove:bool=False):
        if remove:
            self.bot.store.run("DELETE FROM community_birthdays WHERE guild=? AND user=?",(i.guild_id,i.user.id))
            return await reply(i,"Birthday removed. Previous announcements are not automatically deleted.")
        if day is None and month is None:
            r=self.bot.store.one("SELECT month,day,shared FROM community_birthdays WHERE guild=? AND user=?",(i.guild_id,i.user.id))
            return await reply(i,f"Saved: {r['day']:02d}/{r['month']:02d} • {'shared' if r['shared'] else 'private'}" if r else "No birthday saved. Day and month only; sharing is optional.")
        if day is None or month is None: raise ValueError("Provide day and month, or use remove:True.")
        birthday_date(month,day)
        if share and not community_channel(self.bot,i.guild): raise ValueError("Community channel unavailable.")
        self.bot.store.run("INSERT INTO community_birthdays(guild,user,month,day,shared) VALUES(?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET month=excluded.month,day=excluded.day,shared=excluded.shared",(i.guild_id,i.user.id,month,day,int(share)))
        await reply(i,"Birthday saved. "+("Your display name may be announced on your birthday in the community channel (UTC), without a ping." if share else "Private; no announcement."))

    @app_commands.guild_only()
    @app_commands.command(description="Show current server member and bot counters")
    async def counters(self,i:discord.Interaction):
        g=i.guild
        bots=sum(1 for m in g.members if m.bot)
        e=discord.Embed(title="TOV4R • Server counters",colour=0xef3340)
        e.add_field(name="Members",value=str(g.member_count if g.member_count is not None else len(g.members)))
        e.add_field(name="Bots" if g.chunked else "Cached bots (partial)",value=str(bots))
        await i.response.send_message(embed=e,ephemeral=True)

    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.choices(color=[app_commands.Choice(name=k.title(),value=k) for k in COLORS])
    @app_commands.command(description="Publish a custom announcement embed without pings")
    async def custom_embed(self,i:discord.Interaction,title:app_commands.Range[str,1,200],description:app_commands.Range[str,1,3500],color:str="ruby"):
        if color not in COLORS: raise ValueError("Invalid color.")
        await i.response.defer(ephemeral=True)
        await i.channel.send(embed=discord.Embed(title=title,description=description,colour=COLORS[color]),allowed_mentions=NONE)
        await reply(i,"Announcement sent.")

async def social_tick(bot):
    now=dt.datetime.now(dt.timezone.utc)
    # Only explicit sharing, no year, no ping and no disclosure outside the server.
    for r in bot.store.all("SELECT * FROM community_birthdays WHERE shared=1 AND month=? AND day=? AND last_notice<>? LIMIT 20",(now.month,now.day,now.date().isoformat())):
        g=bot.get_guild(r["guild"])
        if not g or g.unavailable: continue
        c=community_channel(bot,g)
        m=g.get_member(r["user"])
        if not c or not m or not c.permissions_for(m).view_channel: continue
        bot.store.run("UPDATE community_birthdays SET last_notice=? WHERE guild=? AND user=? AND shared=1",(now.date().isoformat(),g.id,m.id))
        try: await c.send("🎂 Happy birthday, "+discord.utils.escape_markdown(m.display_name)+"!",allowed_mentions=NONE)
        except discord.HTTPException: pass
    for r in bot.store.all("SELECT * FROM community_posts WHERE closed=0 AND ends>0 AND ends<=? LIMIT 20",(now.timestamp(),)):
        async with bot.locks[("social",r["message"])]:
            row=bot.store.one("SELECT * FROM community_posts WHERE message=? AND guild=?",(r["message"],r["guild"]))
            if not row or row["closed"]: continue
            g=bot.get_guild(row["guild"]); c=g.get_channel(row["channel"]) if g else None
            if not c: continue
            try:
                row["closed"]=1
                m=await c.fetch_message(row["message"])
                await m.edit(embed=post_embed(bot,row),view=None)
            except discord.NotFound: pass
            except discord.HTTPException: continue
            bot.store.run("UPDATE community_posts SET closed=1 WHERE guild=? AND message=?",(row["guild"],row["message"]))
