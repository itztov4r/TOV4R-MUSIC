"""One-shot conservative Discord server audit for the user's 1729 community.

This is NOT a selfbot, and does not use a personal Discord token.
It acts only through the installed application and the guild permissions it already has.
No third-party bot invitations, irreversible deletion or speculative channel moves.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from server_manager import snapshot, privileged_permissions, secret

LOG = logging.getLogger("tov4r.autopilot")
TARGET_NAME = "1729"
# Exact role names intended for normal members (not staff or administrator).
BASIC_ROLE_NAMES = frozenset({
    "member", "members", "verified", "unverified", "new member",
    "newbie", "guest", "gamer",
})
VERSION = "2026-11-23-v1"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def report_directory() -> Path:
    base = Path(os.environ.get("DATABASE_PATH", "data/community.db")).resolve().parent
    folder = base / "server-audits"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def private_json(path: Path, payload: dict) -> None:
    """Write backup before any mutations; ensure only service account can read it."""
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, indent=2, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


class ServerAutopilot(commands.GroupCog, group_name="autopilot",
                      group_description="One-shot 1729 audit and reporting"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.started = set()
        self.last_report: dict[int, Path] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            self.start_for(guild)

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild):
        self.start_for(guild)

    def start_for(self, guild: discord.Guild):
        # This feature is explicitly limited to one named target, not every
        # server that might later add the public TOV4R Bot.
        if guild.name.casefold().strip() != TARGET_NAME:
            LOG.info("Autopilot skip other guild id=%s name=%r", guild.id, guild.name)
            return
        if guild.id in self.started:
            return
        self.started.add(guild.id)
        asyncio.create_task(self.run_once(guild), name=f"server-audit-{guild.id}")

    async def run_once(self, guild: discord.Guild) -> None:
        try:
            folder = report_directory()
            marker = folder / f"{VERSION}-{guild.id}-done.json"
            latest = folder / f"latest-{guild.id}.json"
            if marker.exists():
                self.last_report[guild.id] = latest if latest.exists() else marker
                LOG.info("Autopilot already ran for guild=%s; not repeating mutations", guild.id)
                return

            # Chunk users to distinguish unused/duplicated member roles
            # from partial cache data. Failure cannot trigger deletions.
            if not guild.chunked:
                try:
                    await asyncio.wait_for(guild.chunk(cache=True), timeout=25)
                except (asyncio.TimeoutError, discord.HTTPException, discord.ClientException) as exc:
                    LOG.warning("Member cache unavailable: %s", exc)

            before = snapshot(guild)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = folder / f"before-{guild.id}-{timestamp}.json"
            private_json(backup, before)
            LOG.info("Autopilot backup created guild=%s roles=%s channels=%s bots_visible=%s findings=%s",
                     guild.id, before["summary"]["roles"], before["summary"]["channels"],
                     before["summary"]["bots_visible"], before["summary"]["issues_to_review"])

            record = {
                "version": VERSION, "at": utc_now(),
                "guild": {"id": str(guild.id), "name": guild.name},
                "backup_file": backup.name,
                "actions": [],
                "skipped": [],
                "before": before,
            }
            # The only unattended edits are direct, reversible removal of
            # explicitly dangerous guild-level permissions from the public
            # role and from conventional non-staff member roles. Every edit
            # is backed up, verified, and recorded.
            me = guild.me
            if not me or not me.guild_permissions.manage_roles:
                record["skipped"].append("Bot lacks Manage Roles. Read-only audit completed.")
            else:
                roles = [guild.default_role]
                for role in guild.roles:
                    if role.is_default() or role.managed:
                        continue
                    if role.name.casefold().strip() not in BASIC_ROLE_NAMES:
                        continue
                    if role >= me.top_role:
                        record["skipped"].append(f"Role {role.id} above bot hierarchy.")
                        continue
                    # Do not automatically modify anything used by a bot.
                    if not guild.chunked:
                        record["skipped"].append(f"Role {role.id}: incomplete member cache.")
                        continue
                    if any(member.bot for member in role.members):
                        record["skipped"].append(f"Role {role.id} is assigned to a bot.")
                        continue
                    roles.append(role)

                for role in roles:
                    risky = privileged_permissions(role)
                    if not risky:
                        continue
                    original_bits = role.permissions.value
                    proposed = discord.Permissions(original_bits)
                    for key in risky:
                        setattr(proposed, key, False)
                    if role.permissions.value != original_bits:
                        record["skipped"].append(f"Role {role.id} changed during review.")
                        continue
                    try:
                        await role.edit(
                            permissions=proposed,
                            reason=f"TOV4R 1729 owner-authorized baseline security {VERSION}",
                        )
                        record["actions"].append({
                            "kind": "role_permission_hardening",
                            "role_id": str(role.id), "role_name": role.name,
                            "previous_permissions_bits": str(original_bits),
                            "updated_permissions_bits": str(proposed.value),
                            "removed": risky,
                        })
                        LOG.info("Autopilot hardened role id=%s name=%r permissions=%s",
                                 role.id, role.name, ",".join(risky))
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        record["skipped"].append(f"Role {role.id} couldn't be edited: {type(exc).__name__}")
                        LOG.warning("Autopilot role edit failed id=%s: %s", role.id, exc)

            after = snapshot(guild)
            record["after"] = after
            record["finished_at"] = utc_now()
            latest = folder / f"latest-{guild.id}.json"
            private_json(latest, record)
            private_json(marker, {"version": VERSION, "completed": record["finished_at"],
                                  "latest": latest.name, "actions": record["actions"]})
            self.last_report[guild.id] = latest
            severity = {}
            for finding in after["findings"]:
                level = finding["level"]
                severity[level] = severity.get(level, 0) + 1
            LOG.info("AUTOPILOT RESULT guild=%s name=%r actions=%s skipped=%s findings=%s severities=%s",
                     guild.id, guild.name, len(record["actions"]), len(record["skipped"]),
                     after["summary"]["issues_to_review"], severity)
            # Write a short, non-sensitive update into configured log channel
            # when available; full backup stays private on the Railway volume.
            try:
                await self.bot.audit(guild, "TOV4R Server Audit",
                    f"Audit complete. Roles {after['summary']['roles']}, "
                    f"channels {after['summary']['channels']}, "
                    f"visible bots {after['summary']['bots_visible']}. "
                    f"Safe permission changes: {len(record['actions'])}. "
                    f"Items for manual review: {after['summary']['issues_to_review']}. "
                    "No role/channel deletion or bot changes. "
                    "Use /autopilot report for the private report.")
            except (discord.HTTPException, KeyError) as exc:
                LOG.warning("Could not publish audit notice: %s", exc)
        except Exception:
            LOG.exception("Autopilot failed before completion for guild %s", guild.id)
            self.started.discard(guild.id)

    @app_commands.command(
        name="report",
        description="Read the latest one-shot 1729 audit, actions and backup"
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automatic_report(self, interaction: discord.Interaction):
        if interaction.guild.name.casefold().strip() != TARGET_NAME:
            return await secret(interaction, "Autopilot only targets server 1729.")
        path = self.last_report.get(interaction.guild_id)
        if path is None:
            path = report_directory() / f"latest-{interaction.guild_id}.json"
        if not path.is_file():
            return await secret(interaction, "No automated audit report yet.")
        payload = path.read_bytes()
        if len(payload) > 7_000_000:
            return await secret(interaction, "Report is too large. Try /server scan.")
        result = json.loads(payload)
        summary = result.get("after", {}).get("summary", {})
        msg = (
            f"**TOV4R 1729 Auto Audit**\n"
            f"Safe edits: {len(result.get('actions', []))}\n"
            f"Skipped: {len(result.get('skipped', []))}\n"
            f"Roles: {summary.get('roles', '?')}, "
            f"Channels: {summary.get('channels', '?')}, "
            f"Visible bots: {summary.get('bots_visible', '?')}\n"
            "Full before/after snapshot attached privately."
        )
        import io
        await secret(interaction, msg, file=discord.File(
            io.BytesIO(payload), filename=f"tov4r-auto-audit-{interaction.guild_id}.json"))
