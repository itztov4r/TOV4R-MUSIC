"""Limit newly created roles to permissions available to the bot."""
import discord
import logging
log=logging.getLogger("tov4r.setup")
def grantable_permissions(requested, available):
    if available.administrator: return requested
    return discord.Permissions(requested.value & available.value)

async def setup_action(label, operation):
    try:
        return await operation
    except discord.Forbidden as exc:
        log.warning("Setup denied at step %s (Discord code %s)",label,exc.code)
        raise ValueError("Setup blocked: "+label+". Check the bot's permissions and role hierarchy, then rerun /setup.") from exc
