"""Safe, bounded server snapshots and free cosmetic event eligibility."""
import json
import time
from store import level, validate_config
from cosmetics import CATALOG

RESTORE_FIELDS = ("levels","voice_xp","antispam","anti_invites","security_enabled","xp_cooldown","welcome_text","blocked_words","language")

def eligible(store, member, requirements):
    if member is None or member.bot: return False
    role=requirements["role_id"]
    if role and not any(r.id==role for r in member.roles): return False
    row=store.one("SELECT points FROM xp WHERE guild=? AND user=?",(member.guild.id,member.id))
    return level(row["points"] if row else 0)>=requirements["min_level"]

def save_backup(store, guild, structure):
    cfg=store.config(guild)
    payload=json.dumps({"structure":structure,"settings":{k:cfg[k] for k in RESTORE_FIELDS}},ensure_ascii=False)
    if len(payload.encode("utf-8"))>4000000: raise ValueError("Backup exceeds 4 MB.")
    with store.db:
        cur=store.db.execute("INSERT INTO community_backups(guild,created,body) VALUES(?,?,?)",(guild,time.time(),payload))
        bid=cur.lastrowid
        store.db.execute("DELETE FROM community_backups WHERE guild=? AND id NOT IN (SELECT id FROM community_backups WHERE guild=? ORDER BY id DESC LIMIT 5)",(guild,guild))
    return bid

def read_backup(store,guild,bid):
    row=store.one("SELECT body FROM community_backups WHERE guild=? AND id=?",(guild,bid))
    if not row: raise ValueError("Backup not found on this server.")
    return json.loads(row["body"])

def restore_settings(store,guild,bid):
    data=read_backup(store,guild,bid)["settings"]
    changes=validate_config({k:data[k] for k in RESTORE_FIELDS if k in data})
    store.update(guild,changes)
    return changes
