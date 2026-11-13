"""Private owner dashboard. Same-process real data, bearer auth, no token in URLs."""
import hmac
import os
from pathlib import Path
from aiohttp import web
from store import validate_config

async def start_dashboard(bot):
    key=os.getenv('DASHBOARD_KEY','').strip()
    if len(key)<32:
        print('Dashboard disabled: set a random DASHBOARD_KEY of at least 32 characters.')
        return None
    @web.middleware
    async def security(request,handler):
        if request.path.startswith('/api/'):
            supplied=request.headers.get('Authorization','')
            if not hmac.compare_digest(supplied,'Bearer '+key): raise web.HTTPUnauthorized()
            if request.method not in ('GET','HEAD') and request.content_type!='application/json': raise web.HTTPUnsupportedMediaType()
        try: response=await handler(request)
        except ValueError as e: response=web.json_response({'error':str(e)},status=400)
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
             'Referrer-Policy':'no-referrer','Cache-Control':'no-store',
             'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response
    app=web.Application(middlewares=[security],client_max_size=16384)
    async def index(request): return web.FileResponse(Path(__file__).parent/'web/index.html')
    async def asset(request):
        name=request.match_info['name']
        if name not in ('app.js','style.css'): raise web.HTTPNotFound()
        return web.FileResponse(Path(__file__).parent/'web'/name)
    async def state(request):
        guilds=[]
        for g in bot.guilds:
            cfg=bot.store.config(g.id)
            # Send Discord IDs as strings: JS numbers cannot preserve snowflake precision.
            cfg={k:str(v) if k.endswith(('_channel','_category','_role')) or k in ('autorole','voice_creator') else v for k,v in cfg.items()}
            guilds.append(dict(id=str(g.id),name=g.name,members=g.member_count,config=cfg,
              channels=[{'id':str(c.id),'name':c.name,'type':str(c.type)} for c in g.channels],
              roles=[{'id':str(r.id),'name':r.name} for r in g.roles],
              rooms=bot.store.one('SELECT COUNT(*) n FROM rooms WHERE guild=?',(g.id,))['n'],
              tickets=bot.store.one('SELECT COUNT(*) n FROM tickets WHERE guild=? AND closed=0',(g.id,))['n'],
              giveaways=bot.store.one("SELECT COUNT(*) n FROM giveaways WHERE guild=? AND state='open'",(g.id,))['n'],
              leaderboard=[{'user':str(r['user']),'points':r['points']} for r in bot.store.all('SELECT user,points FROM xp WHERE guild=? ORDER BY points DESC LIMIT 10',(g.id,))],
              events=bot.store.all('SELECT kind,detail,at FROM events WHERE guild=? ORDER BY id DESC LIMIT 30',(g.id,))))
        return web.json_response({'online':bot.is_ready(),'name':str(bot.user) if bot.user else 'Starting','guilds':guilds})
    async def update(request):
        from bot import safe_role
        import discord
        gid=int(request.match_info['guild']); g=bot.get_guild(gid)
        if not g: raise web.HTTPNotFound()
        changes=validate_config(await request.json())
        for k,v in changes.items():
            if not v: continue
            if k in ('support_role','autorole'):
                r=g.get_role(v)
                if not r: raise ValueError(k+': role not found in server')
                if k=='autorole' and not safe_role(r,g): raise ValueError('Unsafe auto-role')
            elif k in ('voice_category','ticket_category','welcome_channel','logs_channel','voice_creator'):
                expected=discord.CategoryChannel if k.endswith('category') else discord.VoiceChannel if k=='voice_creator' else discord.TextChannel
                if not isinstance(g.get_channel(v),expected): raise ValueError(k+': invalid channel type')
        bot.store.update(gid,changes)
        await bot.audit(g,'Dashboard configuration','Settings saved by dashboard owner.')
        return web.json_response({'ok':True})
    app.router.add_get('/',index); app.router.add_get('/assets/{name}',asset)
    app.router.add_get('/api/state',state); app.router.add_put('/api/guild/{guild}',update)
    runner=web.AppRunner(app); await runner.setup()
    host=os.getenv('DASHBOARD_HOST','127.0.0.1'); port=int(os.getenv('PORT','8080'))
    await web.TCPSite(runner,host,port).start()
    print(f'Dashboard listening on {host}:{port}; private key required.')
    return runner
