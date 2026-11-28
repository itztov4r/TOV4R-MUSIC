"""Owner-triggered verification gate; preserve existing access and private denies."""
import discord
TEMPLATE="__verification_gate__"

def tracked(bot,guild_id,key):
    row=bot.store.one("SELECT discord_id FROM template_items WHERE guild=? AND template=? AND item_key=?",(guild_id,TEMPLATE,key))
    return row["discord_id"] if row else 0

def remember(bot,guild_id,key,value,kind="state"):
    bot.store.run("INSERT OR REPLACE INTO template_items VALUES(?,?,?,?,?)",(guild_id,TEMPLATE,key,value,kind))

def access_role(bot,guild):
    return guild.get_role(tracked(bot,guild.id,"access"))

def active(bot,guild_id):
    return bool(tracked(bot,guild_id,"active"))

def admitted(bot,member):
    if not active(bot,member.guild.id): return True
    role=access_role(bot,member.guild)
    return member.guild_permissions.administrator or (role is not None and role in member.roles)

def public_access(bot,guild,channel):
    if channel.permissions_for(guild.default_role).view_channel: return True
    role=access_role(bot,guild)
    return bool(active(bot,guild.id) and role and channel.permissions_for(role).view_channel)

def safe_access(bot,guild,role):
    if not role or role.managed or role.is_default() or role>=guild.me.top_role: return False
    if role.permissions.value!=discord.Permissions(view_channel=True).value: return False
    cfg=bot.store.config(guild.id)
    if role.id in (cfg["support_role"],cfg["autorole"]): return False
    return all(ch.overwrites_for(role).is_empty() for ch in guild.channels)

def normalize_overwrite(overwrite):
    if overwrite.view_channel is True:
        overwrite.view_channel=None
        return True
    return False

async def gate_channel(bot,channel):
    guild=channel.guild
    if not active(bot,guild.id) or not safe_access(bot,guild,access_role(bot,guild)): return
    if channel.id==tracked(bot,guild.id,"verification_channel"): return
    ow=channel.overwrites_for(guild.default_role)
    if normalize_overwrite(ow):
        await channel.set_permissions(guild.default_role,overwrite=None if ow.is_empty() else ow,reason="TOV4R verification: inherit public visibility")

async def install_gate(bot,guild,verify_channel):
    if not verify_channel or not guild.me.guild_permissions.manage_roles:
        return False,"Verification gate needs a working verification panel and Manage Roles."
    started=bool(tracked(bot,guild.id,"started"))
    if not started and not guild.default_role.permissions.view_channel:
        return False,"@everyone already has custom visibility; automatic gate skipped to avoid exposing private channels."
    role=access_role(bot,guild)
    if role is None:
        if started: return False,"Gate access role missing; restore permissions before setup."
        role=await guild.create_role(name="TOV4R Access",permissions=discord.Permissions(view_channel=True),mentionable=False,reason="TOV4R verification access")
        remember(bot,guild.id,"access",role.id,"role")
    if not safe_access(bot,guild,role): return False,"TOV4R Access must only have View Channel and no channel overrides."
    if not started:
        if not guild.chunked: await guild.chunk(cache=True)
        if not guild.chunked: return False,"Member list incomplete; gate not applied."
        members=[m for m in guild.members if not m.guild_permissions.administrator and role not in m.roles and not (m.id==guild.me.id and any(r.id!=guild.default_role.id and r.permissions.view_channel for r in m.roles))]
        if len(members)>200: return False,"More than 200 members need migration; gate not applied to prevent partial access changes."
        if any(m.top_role>=guild.me.top_role and m.id!=guild.me.id for m in members):
            return False,"An existing member or bot is above the bot role; gate not applied. Check role hierarchy."
        with bot.store.db:
            bot.store.db.executemany("INSERT OR IGNORE INTO verification_migration VALUES(?,?)",[(guild.id,m.id) for m in members])
            bot.store.db.execute("INSERT OR REPLACE INTO template_items VALUES(?,?,?,?,?)",(guild.id,TEMPLATE,"started",guild.id,"state"))
    # Retry only the original member snapshot, never grandfather new arrivals.
    for r in bot.store.all("SELECT user FROM verification_migration WHERE guild=?",(guild.id,)):
        member=guild.get_member(r["user"])
        if member is None:
            try: member=await guild.fetch_member(r["user"])
            except discord.NotFound: member=None
            except discord.HTTPException: return False,"Cannot confirm existing member migration; gate not applied."
        if member and role not in member.roles and not member.guild_permissions.administrator:
            try: await member.add_roles(role,reason="Preserve access of existing members")
            except discord.HTTPException: return False,"Could not preserve an existing member's access; gate not applied."
        bot.store.run("DELETE FROM verification_migration WHERE guild=? AND user=?",(guild.id,r["user"]))
    targets=[]
    for channel in guild.channels:
        if channel.id==verify_channel.id: continue
        if channel.overwrites_for(guild.default_role).view_channel is True:
            if not channel.permissions_for(guild.me).manage_roles:
                return False,"Cannot edit an explicit public channel permission; gate not applied."
            targets.append(channel)
    if not verify_channel.permissions_for(guild.me).manage_roles:
        return False,"Cannot configure verification channel permissions."
    remember(bot,guild.id,"verification_channel",verify_channel.id,"channel")
    category_ids={ch.id for ch in targets if isinstance(ch,discord.CategoryChannel)}
    targets=[ch for ch in targets if not (getattr(ch,"permissions_synced",False) and getattr(ch,"category_id",None) in category_ids)]
    originals=[]
    try:
        for ch in targets:
            old=ch.overwrites_for(guild.default_role)
            ow=ch.overwrites_for(guild.default_role)
            normalize_overwrite(ow)
            originals.append((ch,old))
            await ch.set_permissions(guild.default_role,overwrite=None if ow.is_empty() else ow,reason="TOV4R verification gate")
        old=verify_channel.overwrites_for(guild.default_role)
        ow=verify_channel.overwrites_for(guild.default_role)
        ow.view_channel=True; ow.read_message_history=True; ow.send_messages=False
        originals.append((verify_channel,old))
        await verify_channel.set_permissions(guild.default_role,overwrite=ow,reason="TOV4R verification entry")
        if guild.default_role.permissions.view_channel:
            perms=discord.Permissions(guild.default_role.permissions.value)
            perms.view_channel=False
            await guild.default_role.edit(permissions=perms,reason="TOV4R verification required before public access")
    except discord.HTTPException:
        for ch,old in reversed(originals):
            try: await ch.set_permissions(guild.default_role,overwrite=None if old.is_empty() else old,reason="TOV4R gate rollback")
            except discord.HTTPException: pass
        return False,"Discord refused permission updates; attempted rollback. Review permissions before retry."
    remember(bot,guild.id,"active",guild.id)
    return True,"Verification gate enabled. Existing members retain access; new members use Verify."

async def grant_access(bot,member):
    if not active(bot,member.guild.id): return True
    role=access_role(bot,member.guild)
    if not safe_access(bot,member.guild,role): return False
    if role not in member.roles:
        try: await member.add_roles(role,reason="TOV4R verification completed")
        except discord.HTTPException: return False
    return True
