"""One owner-authorized, repeatable setup. No edits to existing channels or staff permissions."""
import json
import discord
from setup_service import provision_server, reserved_staff_role
from community_premium import basic_role, VerifyView, MultiRoleView, language, welcome_name
from localization import tr
from level_rewards import tracked_reward
from verification_gate import public_access, install_gate
from setup_permissions import setup_action

TEMPLATE="__unified_setup__"
MENU_ROLES=(("gaming","TOV4R • Gaming",0x5865f2),("music","TOV4R • Music",0x2ecc71),("movies","TOV4R • Movies",0x9b59b6))
PANEL_NAMES=("voice-panel","tickets","verification","roles")

def tracked(bot,guild,key,template=TEMPLATE):
    r=bot.store.one("SELECT discord_id FROM template_items WHERE guild=? AND template=? AND item_key=?",(guild.id,template,key))
    return r["discord_id"] if r else 0

def remember(bot,guild,key,id,kind,template=TEMPLATE):
    bot.store.run("INSERT OR REPLACE INTO template_items VALUES(?,?,?,?,?)",(guild.id,template,key,id,kind))

def public_panel(channel,guild,bot=None):
    if not isinstance(channel,discord.TextChannel): return False
    p=channel.permissions_for(guild.me)
    return p.view_channel and p.send_messages and p.embed_links and (public_access(bot,guild,channel) if bot else channel.permissions_for(guild.default_role).view_channel)

def private_log(channel,guild,bot=None):
    if not isinstance(channel,discord.TextChannel): return False
    p=channel.permissions_for(guild.me)
    if not(p.view_channel and p.send_messages and p.embed_links) or (public_access(bot,guild,channel) if bot else channel.permissions_for(guild.default_role).view_channel): return False
    # Do not reuse a log channel with explicit grants to unrelated members or roles.
    for target,ow in channel.overwrites.items():
        if ow.view_channel is True and target.id!=guild.me.id:
            if not isinstance(target,discord.Role) or not target.permissions.administrator: return False
    return True

async def provision_all(bot,guild,theme,*,voice_panel_factory,ticket_panel_factory,role_panel_factory=None,safe_role_fn=None,embed_factory,focus=False,cleanup=False,security=None):
    if focus or cleanup: raise ValueError("Channel cleanup is disabled.")
    required=("view_channel","manage_channels","manage_roles","send_messages","embed_links","read_message_history","attach_files","connect")
    if not guild.me: raise ValueError("Bot membership unavailable. Retry after Discord reconnects.")
    missing=[k.replace("_"," ").title() for k in required if not getattr(guild.me.guild_permissions,k)]
    if missing: raise ValueError("Bot missing permissions: "+", ".join(missing)+". Enable them on the bot role, then rerun /setup.")
    summary=await provision_server(bot,guild,theme)
    cfg=bot.store.config(guild.id)
    def update(**v):
        cfg.update(v); bot.store.update(guild.id,v)
    async def category(key,name,private=False):
        ch=guild.get_channel(cfg[key])
        if not isinstance(ch,discord.CategoryChannel):
            ow={guild.default_role:discord.PermissionOverwrite(view_channel=False),guild.me:discord.PermissionOverwrite(view_channel=True,manage_channels=True)} if private else {}
            ch=await setup_action("create category "+name,guild.create_category(name,overwrites=ow,reason="TOV4R unified setup"))
            update(**{key:ch.id})
            summary["categories_new"].append(ch.name)
        return ch
    voice=await category("voice_category","TOV4R • VOICE")
    await category("ticket_category","TOV4R • SUPPORT",True)
    creator=guild.get_channel(cfg["voice_creator"])
    if not isinstance(creator,discord.VoiceChannel):
        creator=await setup_action("create voice room in "+voice.name,guild.create_voice_channel("➕ Create your room",category=voice,reason="TOV4R unified setup"))
        update(voice_creator=creator.id); summary["channels_new"].append(creator.name)
    pub={guild.default_role:discord.PermissionOverwrite(send_messages=False),guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True,attach_files=True)}
    private={guild.default_role:discord.PermissionOverwrite(view_channel=False),guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)}
    def usable_welcome(ch):
        return public_panel(ch,guild,bot) and welcome_name(ch.name) and ch.permissions_for(guild.me).attach_files
    welcome=guild.get_channel(cfg["welcome_channel"])
    if not usable_welcome(welcome):
        welcome=next((ch for ch in guild.text_channels if usable_welcome(ch)),None)
        if welcome is None:
            welcome=await setup_action("create welcome channel",guild.create_text_channel("welcome",overwrites={
                **pub,guild.default_role:discord.PermissionOverwrite(view_channel=True,send_messages=False)
            },reason="TOV4R dedicated welcome"))
            summary["channels_new"].append(welcome.name)
        update(welcome_channel=welcome.id)
    # Discord's own arrival messages are independent from the bot.
    # Preserve every other system flag and leave system channel unchanged.
    flags=guild.system_channel_flags
    if flags.join_notifications:
        if guild.me.guild_permissions.manage_guild:
            try:
                flags.join_notifications=False
                await guild.edit(system_channel_flags=flags,reason="TOV4R welcome uses dedicated channel")
            except discord.HTTPException: summary["warnings"].append("Could not disable native Discord arrival messages; check Manage Server.")
        else: summary["warnings"].append("Disable native arrival messages in Server Settings > Overview, or grant Manage Server.")
    logs=guild.get_channel(cfg["logs_channel"])
    if not private_log(logs,guild,bot):
        logs=await setup_action("create private logs",guild.create_text_channel("tov4r-private-logs",overwrites=private,reason="TOV4R unified setup"))
        update(logs_channel=logs.id); summary["channels_new"].append(logs.name)
    community=guild.get_channel(cfg["community_channel"])
    if not isinstance(community,discord.TextChannel):
        community=await setup_action("create community channel",guild.create_text_channel("tov4r-community",overwrites={
            guild.default_role:discord.PermissionOverwrite(send_messages=True),
            guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)
        },reason="TOV4R community modules"))
        update(community_channel=community.id); summary["channels_new"].append(community.name)
    if not public_panel(community,guild,bot): summary["warnings"].append("Community channel not public or missing bot permissions; configure access manually.")
    if not private_log(guild.get_channel(cfg["security_channel"]),guild,bot): update(security_channel=logs.id)
    first=not tracked(bot,guild,"initialized")
    if security is not None or first:
        enabled=security if security is not None else True
        if cfg["security_enabled"]!=enabled:
            update(security_enabled=enabled)
            bot.security.clear(guild.id)
    if first: remember(bot,guild,"initialized",guild.id,"state")
    async def role(key,name,color,template=TEMPLATE):
        r=guild.get_role(tracked(bot,guild,key,template))
        if not r:
            r=await setup_action("create role "+name,guild.create_role(name=name,permissions=discord.Permissions.none(),colour=discord.Colour(color),mentionable=False,reason="TOV4R unified setup"))
            remember(bot,guild,key,r.id,"role",template); summary["roles_new"].append(r.name)
        return r
    verified=await role("role","TOV4R Verified",0x2ecc71,"__verification__")
    roles=[await role(k,n,c) for k,n,c in MENU_ROLES]
    t=language(bot,guild.id)
    if not basic_role(bot,guild,verified,verification=True):
        summary["warnings"].append("Verification role unsafe or above bot: put TOV4R Bot above TOV4R Verified, remove elevated permissions, then rerun /setup.")
    safe=[r for r in roles if basic_role(bot,guild,r)]
    if len(safe)!=len(roles): summary["warnings"].append("Some cosmetic roles were unsafe and omitted.")
    definitions=[
      ("voice-panel",embed_factory("🔊 TOV4R • "+tr(bot,guild.id,"voice_title"),tr(bot,guild.id,"voice_desc")+"\n"+tr(bot,guild.id,"voice_manage")+"\n"+tr(bot,guild.id,"voice_prefs")),voice_panel_factory(bot,guild.id),None,"voice:"),
      ("tickets",embed_factory("🎫 TOV4R • "+tr(bot,guild.id,"tickets"),tr(bot,guild.id,"ticket_intro")+"\n"+tr(bot,guild.id,"ticket_private")),ticket_panel_factory(bot,guild.id),None,"ticket:type:v2"),
    ]
    if basic_role(bot,guild,verified,verification=True):
        definitions.append(("verification",discord.Embed(title="TOV4R • "+t[2],description=t[3],colour=0x2ecc71),VerifyView(bot,t[2]),None,"community:verify:v9"))
    if safe:
        data=[(r.id,r.name) for r in safe]
        definitions.append(("roles",discord.Embed(title="TOV4R • Roles",description=t[4],colour=0xef3340),MultiRoleView(bot,data),data,"community:roles:v9"))
    verify_channel=None
    for name,e,view,data,prefix in definitions:
        channel=guild.get_channel(tracked(bot,guild,"channel:"+name))
        if not public_panel(channel,guild,bot):
            channel=next((ch for ch in guild.text_channels if ch.name==name and public_panel(ch,guild,bot)),None)
        if channel is None:
            overwrites=pub
            if name=="verification":
                overwrites={**pub,guild.default_role:discord.PermissionOverwrite(view_channel=True,read_message_history=True,send_messages=False)}
            channel=await setup_action("create #"+name,guild.create_text_channel(name,overwrites=overwrites,reason="TOV4R unified setup"))
            summary["channels_new"].append(channel.name)
        remember(bot,guild,"channel:"+name,channel.id,"channel")
        msg=None
        mid=tracked(bot,guild,"panel:"+name)
        if mid:
            try: msg=await channel.fetch_message(mid)
            except (discord.NotFound,discord.Forbidden): pass
        if msg is None and channel.permissions_for(guild.me).read_message_history:
            async for candidate in channel.history(limit=50):
                if candidate.author.id==bot.user.id and any(str(getattr(child,"custom_id","")).startswith(prefix) for row in candidate.components for child in row.children):
                    msg=candidate; break
        if msg and msg.author.id==bot.user.id:
            await setup_action("refresh panel #"+name,msg.edit(embed=e,view=view))
        else: msg=await setup_action("send panel #"+name,channel.send(embed=e,view=view))
        remember(bot,guild,"panel:"+name,msg.id,"message")
        if name=="verification":
            verify_channel=channel
            bot.add_view(view,message_id=msg.id)
        if data is not None:
            bot.store.run("INSERT OR REPLACE INTO community_role_menus VALUES(?,?,?)",(msg.id,guild.id,json.dumps(data)))
            bot.add_view(view,message_id=msg.id)
    try:
        ok,detail=await install_gate(bot,guild,verify_channel)
    except (discord.HTTPException,TimeoutError):
        ok,detail=False,"Verification gate could not finish; check permissions and rerun setup."
    summary["verification_gate"]=ok
    summary["warnings"].append(detail)
    summary["roles_panel"]="configured" if safe else "unavailable"
    if not guild.me.guild_permissions.view_audit_log: summary["warnings"].append("Grant View Audit Log for destructive-activity alerts.")
    return summary
