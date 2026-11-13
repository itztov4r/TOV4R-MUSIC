"""Run locally. Never prints the Discord token."""
from getpass import getpass
from pathlib import Path
import secrets
import discord

def main():
    path=Path(__file__).parent/'.env'
    if path.exists():
        print('.env already exists. Edit it locally to change settings; it will not be overwritten.')
        return
    token=getpass('Discord BOT token (hidden): ').strip()
    if not token or any(c in token for c in '\n\r\"\' '): raise SystemExit('Invalid token format.')
    app=input('Application ID (Developer Portal): ').strip()
    if not app.isdecimal(): raise SystemExit('IDs must be numbers.')
    key=secrets.token_urlsafe(36)
    path.write_text(f'DISCORD_TOKEN={token}\nDISCORD_GUILD_ID=\nDASHBOARD_KEY={key}\nDASHBOARD_HOST=127.0.0.1\nPORT=8080\nDATABASE_PATH=data/community.db\n',encoding='utf-8')
    try: path.chmod(0o600)
    except OSError: pass
    permissions=discord.Permissions(view_channel=True,send_messages=True,embed_links=True,attach_files=True,
        read_message_history=True,manage_channels=True,manage_roles=True,manage_messages=True,
        connect=True,move_members=True,kick_members=True,ban_members=True,moderate_members=True)
    print('\nInvite URL (open in your browser):\n'+discord.utils.oauth_url(int(app),permissions=permissions,scopes=('bot','applications.commands')))
    print('\n.env created. Dashboard: http://127.0.0.1:8080\nCopy DASHBOARD_KEY from .env to sign in. Do not share .env.')
if __name__=='__main__': main()
