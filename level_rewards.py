"""Guild-scoped, non-staff level reward roles. No changes to existing role permissions."""
import discord
from store import level

TIERS = (
    (1, "TOV4R • Rookie", 0x95a5a6, ()),
    (5, "TOV4R • Active", 0x2ecc71, ("attach_files",)),
    (10, "TOV4R • Regular", 0x3498db, ("embed_links",)),
    (20, "TOV4R • Elite", 0x9b59b6, ("use_external_emojis",)),
    (30, "TOV4R • Veteran", 0xe67e22, ("stream",)),
    (50, "TOV4R • Legend", 0xf1c40f, ("use_external_stickers",)),
)
BASE = ("view_channel","read_message_history","send_messages","add_reactions","connect","speak")
TEMPLATE = "__level_rewards__"

def reward_permissions(threshold):
    names = set(BASE)
    for n,_,_,extra in TIERS:
        if n <= threshold:
            names.update(extra)
    return discord.Permissions(**{name:True for name in names})

def tracked_reward(bot,guild_id,role_id):
    return bool(bot.store.one("SELECT 1 FROM template_items WHERE guild=? AND template=? AND discord_id=?",
                              (guild_id,TEMPLATE,role_id)))

def reward_role(bot,guild,threshold):
    row = bot.store.one("SELECT discord_id FROM template_items WHERE guild=? AND template=? AND item_key=?",
                        (guild.id,TEMPLATE,str(threshold)))
    role = guild.get_role(row["discord_id"]) if row else None
    if role and not role.managed and role.id != guild.default_role.id:
        return role
    return None

async def provision_rewards(bot,guild):
    new=[]
    for n,name,color,_ in TIERS:
        if reward_role(bot,guild,n):
            continue
        # Never adopt an unrelated existing role by its name.
        role=await guild.create_role(name=name,permissions=reward_permissions(n),
            colour=discord.Colour(color),mentionable=False,reason="TOV4R automatic level rewards")
        bot.store.run("INSERT OR REPLACE INTO template_items VALUES(?,?,?,?,?)",
                      (guild.id,TEMPLATE,str(n),role.id,"role"))
        new.append(role.name)
    return new

async def grant_rewards(bot,member):
    from setup_service import reserved_staff_role
    guild=member.guild
    cfg=bot.store.config(guild.id)
    if member.bot or not cfg["levels"] or not guild.me or not guild.me.guild_permissions.manage_roles:
        return []
    if cfg["security_enabled"] and bot.security.paused(guild.id):
        return []
    row=bot.store.one("SELECT points FROM xp WHERE guild=? AND user=?",(guild.id,member.id))
    lv=level(row["points"]) if row else 0
    granted=[]
    async with bot.locks[("reward",guild.id,member.id)]:
        for n,_,_,_ in TIERS:
            if n>lv: break
            role=reward_role(bot,guild,n)
            if not role or role in member.roles or reserved_staff_role(bot,guild,role):
                continue
            # Strict allowlist: skip roles that have been edited to gain extra privileges.
            if role >= guild.me.top_role or role.permissions.value & ~reward_permissions(n).value:
                continue
            try:
                await member.add_roles(role,reason=f"TOV4R level {n} reward")
            except discord.HTTPException:
                continue
            granted.append(role.name)
    return granted
