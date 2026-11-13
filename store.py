"""SQLite persistence; all calls run on the bot's single event loop."""
import json
import math
import sqlite3
import time
from pathlib import Path

DEFAULTS = dict(voice_creator=0, voice_category=0, welcome_channel=0, logs_channel=0,
                ticket_category=0, support_role=0, autorole=0, levels=True,
                antispam=True, anti_invites=False, welcome_text='Mer7ba {mention} f {server}! 🎉',
                blocked_words='', xp_cooldown=60)

def level(xp):
    return math.isqrt(max(0, xp) // 100)

def validate_config(changes):
    if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
        raise ValueError('Unknown configuration field')
    out = {}
    for k, v in changes.items():
        if type(DEFAULTS[k]) is bool:
            if type(v) is not bool: raise ValueError(k + ': boolean required')
        elif type(DEFAULTS[k]) is int:
            if isinstance(v, bool): raise ValueError(k + ': integer required')
            try: v = int(v)
            except (ValueError, TypeError): raise ValueError(k + ': integer required')
            if not 0 <= v <= 9223372036854775807: raise ValueError(k + ': invalid integer range')
            if k == 'xp_cooldown' and not 15 <= v <= 3600: raise ValueError('Cooldown: 15–3600 seconds')
        else:
            if not isinstance(v, str) or len(v) > 1500: raise ValueError(k + ': invalid text')
        out[k] = v
    return out

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS config(guild INTEGER PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rooms(channel INTEGER PRIMARY KEY,guild INTEGER,owner INTEGER);
        CREATE TABLE IF NOT EXISTS tickets(channel INTEGER PRIMARY KEY,guild INTEGER,owner INTEGER,closed INTEGER DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS one_ticket ON tickets(guild,owner) WHERE closed=0;
        CREATE TABLE IF NOT EXISTS xp(guild INTEGER,user INTEGER,points INTEGER DEFAULT 0,last REAL DEFAULT 0,PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS warnings(id INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,moderator INTEGER,reason TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,guild INTEGER,kind TEXT,detail TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS giveaways(message INTEGER PRIMARY KEY,guild INTEGER,channel INTEGER,prize TEXT,ends REAL,count INTEGER,state TEXT DEFAULT 'open',winners TEXT DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS entries(message INTEGER,user INTEGER,PRIMARY KEY(message,user));
        CREATE TABLE IF NOT EXISTS role_panels(message INTEGER PRIMARY KEY,guild INTEGER,roles TEXT);
        ''')
        self.db.commit()
    def run(self, sql, params=()):
        with self.db: return self.db.execute(sql, params)
    def all(self, sql, params=()): return [dict(r) for r in self.db.execute(sql, params)]
    def one(self, sql, params=()):
        r = self.db.execute(sql, params).fetchone()
        return dict(r) if r else None
    def config(self, guild):
        r = self.one('SELECT value FROM config WHERE guild=?', (guild,))
        return DEFAULTS | (json.loads(r['value']) if r else {})
    def update(self, guild, changes):
        cfg = self.config(guild) | validate_config(changes)
        self.run('INSERT OR REPLACE INTO config VALUES(?,?)', (guild,json.dumps(cfg)))
        return cfg
    def award(self, guild, user, cooldown, now=None):
        now = time.time() if now is None else now
        r = self.one('SELECT * FROM xp WHERE guild=? AND user=?',(guild,user))
        if r and now - r['last'] < cooldown: return None
        before = r['points'] if r else 0
        self.run('INSERT OR REPLACE INTO xp VALUES(?,?,?,?)',(guild,user,before+20,now))
        return level(before+20) if level(before+20)>level(before) else None
    def event(self, guild, kind, detail):
        self.run('INSERT INTO events(guild,kind,detail,at) VALUES(?,?,?,?)',(guild,kind,detail[:2000],time.time()))
        self.run('DELETE FROM events WHERE guild=? AND id NOT IN (SELECT id FROM events WHERE guild=? ORDER BY id DESC LIMIT 1000)',(guild,guild))
