"""Opt-in welcome customization, acknowledgement verification, safe role menus."""
import json
import re
import discord
from discord import app_commands
from discord.ext import commands
from level_rewards import tracked_reward, reward_permissions
from setup_service import reserved_staff_role
from verification_gate import grant_access, admitted

THEMES={"ruby":("#ef3340",0xef3340),"ocean":("#3498db",0x3498db),"violet":("#9b59b6",0x9b59b6),"emerald":("#2ecc71",0x2ecc71)}
TEXT={
    "en":("WELCOME!","Welcome","Verify","Read the server rules and click Verify to receive the Verified role. This is a rules acknowledgement, not identity verification.","Choose one or more roles to toggle."),
    "fr":("BIENVENUE !","Bienvenue","Vérifier","Lisez le règlement puis cliquez sur Vérifier pour recevoir le rôle Vérifié. Ce bouton confirme le règlement, pas votre identité.","Choisissez un ou plusieurs rôles à ajouter ou retirer."),
    "darija":("MER7BA BIK!","Mer7ba","Verify","9ra 9awanin serveur w click Verify bach takhod rôle Verified. Hada ghir confirmation dyal rules, machi identité.","Khtar rôle ola plusieurs bach tzid ola t7yed.")
}

def language(bot,guild_id):
    return TEXT.get(bot.store.config(guild_id)["language"],TEXT["en"])

def welcome_name(name):
    # Never treat general/chat as a dedicated greeting destination.
    tokens=set(re.findall(r"[a-z0-9]+",name.casefold()))
    return bool(tokens & {"welcome","bienvenue","mer7ba","accueil"}) and not bool(tokens & {"general","chat","général"})

def welcome_destination(bot,guild):
    channel=guild.get_channel(bot.store.config(guild.id)["welcome_channel"])
    if not guild.me or not isinstance(channel,discord.TextChannel) or not welcome_name(channel.name): return None
    p=channel.permissions_for(guild.me)
    return channel if p.view_channel and p.send_messages and p.embed_links else None

def verification_role(bot,guild_id,role_id):
    return bool(bot.store.one("SELECT 1 FROM template_items WHERE guild=? AND template='__verification__' AND discord_id=?",(guild_id,role_id)))

def basic_role(bot,guild,role,*,verification=False):
    if not role or role.is_default() or role.managed or not guild.me or not guild.me.guild_permissions.manage_roles or role>=guild.me.top_role:
        return False
    if tracked_reward(bot,guild.id,role.id): return False
    if verification:
        if not verification_role(bot,guild.id,role.id): return False
        # Ordinary community permissions are safe, moderation privileges are not.
        safe_channel=reward_permissions(50).value
        if role.permissions.value & ~safe_channel: return False
        cfg=bot.store.config(guild.id)
        if role.id==cfg['support_role'] or role.id==cfg['autorole']: return False
        protected={cfg['ticket_category'],cfg['logs_channel'],cfg['security_channel']}
        for ch in guild.channels:
            ow=ch.overwrites_for(role)
            if ow.pair()[0].value & ~safe_channel: return False
            if ow.view_channel is True and (ch.id in protected or getattr(ch,'category_id',None)==cfg['ticket_category']):
                return False
    else:
        if reserved_staff_role(bot,guild,role): return False
        if role.permissions.value & ~reward_permissions(50).value: return False
        # Do not grant roles that unlock private channels or elevate channel permissions.
        for ch in guild.channels:
            ow=ch.overwrites_for(role)
            if ow.pair()[0].value: return False
    return True

async def answer(i,msg):
    if i.response.is_done(): await i.followup.send(msg,ephemeral=True)
    else: await i.response.send_message(msg,ephemeral=True)

def paused(bot,guild_id):
    return bot.store.config(guild_id)["security_enabled"] and bot.security.paused(guild_id)

class VerifyView(discord.ui.View):
    def __init__(self,bot,label="Verify"):
        super().__init__(timeout=None)
        self.bot=bot
        self.verify.label=label
    @discord.ui.button(label="Verify",emoji="✅",custom_id="community:verify:v9",style=discord.ButtonStyle.success)
    async def verify(self,i,button):
        if not i.guild or i.user.bot: return await answer(i,"Unavailable.")
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[("verify",i.guild_id,i.user.id)]:
            row=self.bot.store.one("SELECT discord_id FROM template_items WHERE guild=? AND template='__verification__' AND item_key='role'",(i.guild_id,))
            role=i.guild.get_role(row["discord_id"]) if row else None
            if paused(self.bot,i.guild_id):
                return await answer(i,"Verification paused by security monitoring. Try again after the pause.")
            if role is None: return await answer(i,"Verified role missing. Ask the owner to run /setup.")
            if not basic_role(self.bot,i.guild,role,verification=True):
                return await answer(i,"Verified role unsafe or above bot role. Ask the owner to check permissions and role hierarchy.")
            if role not in i.user.roles:
                try: await i.user.add_roles(role,reason="TOV4R rules acknowledgement")
                except discord.HTTPException: return await answer(i,"Bot cannot assign this role.")
            if not await grant_access(self.bot,i.user):
                return await answer(i,"Verified role assigned, but access unavailable. Ask staff to check TOV4R Access.")
            await answer(i,"✅ Verified / Vérifié • Server access unlocked")

class MultiRoleView(discord.ui.View):
    def __init__(self,bot,roles):
        super().__init__(timeout=None)
        self.bot=bot
        self.ids={int(rid) for rid,label in roles}
        select=discord.ui.Select(custom_id="community:roles:v9",placeholder="Choose roles / Choisir les rôles",min_values=1,max_values=len(roles),options=[discord.SelectOption(label=label[:100],value=str(rid)) for rid,label in roles])
        select.callback=self.toggle
        self.add_item(select)
    async def toggle(self,i):
        if not i.guild or i.user.bot: return await answer(i,"Unavailable.")
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[("role-menu",i.guild_id,i.user.id)]:
            if not admitted(self.bot,i.user): return await answer(i,"Complete verification first.")
            if paused(self.bot,i.guild_id): return await answer(i,"Automatic roles temporarily paused.")
            row=self.bot.store.one("SELECT roles FROM community_role_menus WHERE guild=? AND message=?",(i.guild_id,i.message.id))
            if not row: return await answer(i,"Menu unavailable.")
            allowed={int(r[0]) for r in json.loads(row["roles"])}
            selected={int(x) for x in i.data.get("values",[])}
            if not selected or not selected<=allowed or not selected<=self.ids: return await answer(i,"Invalid selection.")
            roles=[i.guild.get_role(x) for x in selected]
            if not all(basic_role(self.bot,i.guild,r) for r in roles): return await answer(i,"One of these roles is no longer safe to assign.")
            changed=[]
            for r in roles:
                try:
                    if r in i.user.roles: await i.user.remove_roles(r,reason="TOV4R role menu")
                    else: await i.user.add_roles(r,reason="TOV4R role menu")
                    changed.append(r.name)
                except discord.HTTPException: continue
            await answer(i,"Updated: "+(", ".join(changed) or "none; check role hierarchy."))

class CommunityPremium(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_app_command_error(self,i,error):
        e=getattr(error,"original",error)
        await answer(i,str(e) if isinstance(e,ValueError) else "Action unavailable. Check permissions.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.command(description="Customize your server welcome card and message")
    @app_commands.choices(theme=[app_commands.Choice(name=k.title(),value=k) for k in THEMES])
    async def welcome_setup(self,i:discord.Interaction,channel:discord.TextChannel,theme:str="ruby",message:app_commands.Range[str,1,1500]|None=None):
        if theme not in THEMES: raise ValueError("Invalid theme.")
        p=channel.permissions_for(i.guild.me)
        if not (p.view_channel and p.send_messages and p.embed_links and p.attach_files): raise ValueError("Bot needs View Channel, Send Messages, Embed Links and Attach Files.")
        changes={"welcome_channel":channel.id,"welcome_theme":theme}
        if message is not None: changes["welcome_text"]=message
        self.bot.store.update(i.guild_id,changes)
        await answer(i,"Welcome configured ✅")
    @app_commands.guild_only()
    @app_commands.command(description="Owner: publish a rules acknowledgement verification button")
    async def verify_setup(self,i:discord.Interaction,channel:discord.TextChannel):
        if i.user.id!=i.guild.owner_id: raise ValueError("Server owner only.")
        await i.response.defer(ephemeral=True)
        async with self.bot.locks[("setup",i.guild_id)]:
            if not i.guild.me.guild_permissions.manage_roles: raise ValueError("Bot needs Manage Roles.")
            row=self.bot.store.one("SELECT discord_id FROM template_items WHERE guild=? AND template='__verification__' AND item_key='role'",(i.guild_id,))
            role=i.guild.get_role(row["discord_id"]) if row else None
            if role is None:
                role=await i.guild.create_role(name="TOV4R Verified",permissions=discord.Permissions.none(),colour=discord.Colour(0x2ecc71),mentionable=False,reason="TOV4R acknowledgement")
                self.bot.store.run("INSERT OR REPLACE INTO template_items VALUES(?,?,?,?,?)",(i.guild_id,"__verification__","role",role.id,"role"))
            if not basic_role(self.bot,i.guild,role,verification=True): raise ValueError("Verified role unsafe or above bot hierarchy.")
            t=language(self.bot,i.guild_id)
            e=discord.Embed(title="TOV4R • "+t[2],description=t[3],colour=0x2ecc71)
            await channel.send(embed=e,view=VerifyView(self.bot,t[2]))
        await answer(i,"Verification panel published. Configure channel access yourself; no channel permissions changed.")
    @app_commands.guild_only()
    @app_commands.command(description="Owner: publish a safe multi-role selection menu")
    async def roles_menu(self,i:discord.Interaction,channel:discord.TextChannel,role1:discord.Role,role2:discord.Role|None=None,role3:discord.Role|None=None,role4:discord.Role|None=None,role5:discord.Role|None=None):
        if i.user.id!=i.guild.owner_id: raise ValueError("Server owner only.")
        roles=list(dict.fromkeys(r for r in (role1,role2,role3,role4,role5) if r))
        if not all(basic_role(self.bot,i.guild,r) for r in roles): raise ValueError("Use ordinary cosmetic roles without staff, XP, verification or channel access permissions.")
        await i.response.defer(ephemeral=True)
        data=[(r.id,r.name) for r in roles]
        msg=await channel.send(embed=discord.Embed(title="TOV4R • Roles",description=language(self.bot,i.guild_id)[4],colour=0xef3340),view=MultiRoleView(self.bot,data))
        self.bot.store.run("INSERT INTO community_role_menus VALUES(?,?,?)",(msg.id,i.guild_id,json.dumps(data)))
        await answer(i,"Role menu published ✅")
