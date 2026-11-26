"""SQLite persistence; all calls run on the bot's single event loop."""
import json
import math
import sqlite3
import time
from pathlib import Path
from cosmetics import CATALOG, STARTER_CREDITS

DEFAULTS = dict(voice_creator=0, voice_category=0, welcome_channel=0, logs_channel=0,
                ticket_category=0, support_role=0, autorole=0, server_type='community', levels=True,
                antispam=True, anti_invites=False, welcome_text='Mer7ba {mention} f {server}! 🎉',
                blocked_words='', xp_cooldown=60, voice_xp=False, security_enabled=False, security_channel=0, language='en', welcome_theme='ruby')

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
        if k == 'welcome_theme' and v not in ('ruby','ocean','violet','emerald'): raise ValueError('Unsupported welcome theme')
        if k == 'language' and v not in ('en','fr','darija'): raise ValueError('Unsupported language')
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
        CREATE TABLE IF NOT EXISTS voice_preferences(guild INTEGER NOT NULL,user INTEGER NOT NULL,name TEXT NOT NULL,user_limit INTEGER NOT NULL,PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS tickets(channel INTEGER PRIMARY KEY,guild INTEGER,owner INTEGER,closed INTEGER DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS one_ticket ON tickets(guild,owner) WHERE closed=0;
        CREATE TABLE IF NOT EXISTS ticket_details(channel INTEGER PRIMARY KEY,guild INTEGER NOT NULL,kind TEXT NOT NULL DEFAULT 'support',claimed_by INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ticket_priority(channel INTEGER PRIMARY KEY,guild INTEGER NOT NULL,priority TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ticket_guests(channel INTEGER NOT NULL,guild INTEGER NOT NULL,user INTEGER NOT NULL,PRIMARY KEY(channel,user));
        CREATE TABLE IF NOT EXISTS ticket_deletions(channel INTEGER PRIMARY KEY,guild INTEGER NOT NULL,due REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ticket_transcripts(channel INTEGER PRIMARY KEY,guild INTEGER NOT NULL,body BLOB NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS xp(guild INTEGER,user INTEGER,points INTEGER DEFAULT 0,last REAL DEFAULT 0,PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS cosmetic_wallet(guild INTEGER NOT NULL,user INTEGER NOT NULL,credits INTEGER NOT NULL CHECK(credits>=0),theme TEXT NOT NULL DEFAULT '',badge TEXT NOT NULL DEFAULT '',PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS cosmetic_inventory(guild INTEGER NOT NULL,user INTEGER NOT NULL,item TEXT NOT NULL,PRIMARY KEY(guild,user,item));
        CREATE TABLE IF NOT EXISTS activity(guild INTEGER NOT NULL,user INTEGER NOT NULL,messages INTEGER NOT NULL DEFAULT 0,voice_seconds INTEGER NOT NULL DEFAULT 0,voice_day INTEGER NOT NULL DEFAULT -1,daily_voice_seconds INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS warnings(id INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,moderator INTEGER,reason TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,guild INTEGER,kind TEXT,detail TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS giveaways(message INTEGER PRIMARY KEY,guild INTEGER,channel INTEGER,prize TEXT,ends REAL,count INTEGER,state TEXT DEFAULT 'open',winners TEXT DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS entries(message INTEGER,user INTEGER,PRIMARY KEY(message,user));
        CREATE TABLE IF NOT EXISTS giveaway_rules(message INTEGER PRIMARY KEY,guild INTEGER NOT NULL,min_level INTEGER NOT NULL,role_id INTEGER NOT NULL,cosmetic TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS community_backups(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER NOT NULL,created REAL NOT NULL,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS community_role_menus(message INTEGER PRIMARY KEY,guild INTEGER NOT NULL,roles TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS role_panels(message INTEGER PRIMARY KEY,guild INTEGER,roles TEXT);
        CREATE TABLE IF NOT EXISTS template_items(
            guild INTEGER NOT NULL, template TEXT NOT NULL,
            item_key TEXT NOT NULL, discord_id INTEGER NOT NULL, kind TEXT NOT NULL,
            PRIMARY KEY(guild, template, item_key)
        );
        CREATE TABLE IF NOT EXISTS template_created_items(
            guild INTEGER NOT NULL, template TEXT NOT NULL,
            item_key TEXT NOT NULL, discord_id INTEGER NOT NULL, kind TEXT NOT NULL,
            PRIMARY KEY(guild, template, item_key)
        );
        CREATE TABLE IF NOT EXISTS focused_archives(
            guild INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            previous_category_id INTEGER NOT NULL DEFAULT 0,
            previous_position INTEGER NOT NULL DEFAULT 0,
            previous_overwrites TEXT NOT NULL,
            archive_category_id INTEGER NOT NULL,
            archived_at REAL NOT NULL,
            PRIMARY KEY(guild,channel_id)
        );
        CREATE TABLE IF NOT EXISTS focused_archive_bins(
            guild INTEGER NOT NULL, idx INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            PRIMARY KEY(guild,idx)
        );
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
    def cosmetic_profile(self,guild,user):
        return self.one('SELECT theme,badge FROM cosmetic_wallet WHERE guild=? AND user=?',(guild,user)) or {'theme':'','badge':''}
    def cosmetic_account(self,guild,user):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO cosmetic_wallet(guild,user,credits) VALUES(?,?,?)',(guild,user,STARTER_CREDITS))
        row=self.one('SELECT credits,theme,badge FROM cosmetic_wallet WHERE guild=? AND user=?',(guild,user))
        row['items']=[r['item'] for r in self.all('SELECT item FROM cosmetic_inventory WHERE guild=? AND user=? ORDER BY item',(guild,user))]
        return row
    def cosmetic_buy(self,guild,user,item):
        if item not in CATALOG: raise ValueError('Unknown cosmetic.')
        price=CATALOG[item][2]
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO cosmetic_wallet(guild,user,credits) VALUES(?,?,?)',(guild,user,STARTER_CREDITS))
            if self.db.execute('SELECT 1 FROM cosmetic_inventory WHERE guild=? AND user=? AND item=?',(guild,user,item)).fetchone():
                return 'already_owned'
            if not self.db.execute('UPDATE cosmetic_wallet SET credits=credits-? WHERE guild=? AND user=? AND credits>=?',(price,guild,user,price)).rowcount:
                return 'insufficient'
            self.db.execute('INSERT INTO cosmetic_inventory VALUES(?,?,?)',(guild,user,item))
        return 'purchased'
    def cosmetic_equip(self,guild,user,category,item):
        if category not in ('theme','badge'): raise ValueError('Invalid category.')
        if item and (item not in CATALOG or CATALOG[item][1]!=category):
            raise ValueError('Invalid cosmetic.')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO cosmetic_wallet(guild,user,credits) VALUES(?,?,?)',(guild,user,STARTER_CREDITS))
            if item and not self.db.execute('SELECT 1 FROM cosmetic_inventory WHERE guild=? AND user=? AND item=?',(guild,user,item)).fetchone():
                return False
            self.db.execute(f'UPDATE cosmetic_wallet SET {category}=? WHERE guild=? AND user=?',(item,guild,user))
        return True
    def voice_preferences(self,guild,user):
        return self.one('SELECT name,user_limit FROM voice_preferences WHERE guild=? AND user=?',(guild,user))
    def save_voice_preferences(self,guild,user,name,user_limit):
        if not isinstance(name,str) or not name.strip() or len(name)>100:
            raise ValueError('Room name must have 1–100 characters.')
        if type(user_limit) is not int or not 0<=user_limit<=99:
            raise ValueError('Member limit must be 0–99.')
        self.run('INSERT INTO voice_preferences VALUES(?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET name=excluded.name,user_limit=excluded.user_limit',(guild,user,name.strip(),user_limit))
    def clear_voice_preferences(self,guild,user):
        self.run('DELETE FROM voice_preferences WHERE guild=? AND user=?',(guild,user))
    def count_message(self,guild,user):
        self.run('INSERT INTO activity(guild,user,messages) VALUES(?,?,1) ON CONFLICT(guild,user) DO UPDATE SET messages=messages+1',(guild,user))
    def award_voice(self,guild,user,seconds,now=None):
        seconds=max(0,min(60,int(seconds)))
        if not seconds: return 0
        day=int((time.time() if now is None else now)//86400)
        with self.db:
            r=self.db.execute('SELECT voice_day,daily_voice_seconds FROM activity WHERE guild=? AND user=?',(guild,user)).fetchone()
            before=r['daily_voice_seconds'] if r and r['voice_day']==day else 0
            after=min(3600,before+seconds)
            points=5*(after//60-before//60)
            self.db.execute('INSERT INTO activity(guild,user,voice_seconds,voice_day,daily_voice_seconds) VALUES(?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET voice_seconds=voice_seconds+excluded.voice_seconds,voice_day=excluded.voice_day,daily_voice_seconds=excluded.daily_voice_seconds',(guild,user,seconds,day,after))
            if points:
                self.db.execute('INSERT INTO xp(guild,user,points,last) VALUES(?,?,?,0) ON CONFLICT(guild,user) DO UPDATE SET points=points+excluded.points',(guild,user,points))
        return points
    def set_ticket_kind(self,guild,channel,kind):
        if kind not in ('support','report','partnership'): raise ValueError('Invalid ticket type')
        self.run('INSERT OR IGNORE INTO ticket_details(channel,guild,kind) SELECT channel,guild,? FROM tickets WHERE guild=? AND channel=? AND closed=0',(kind,guild,channel))
    def claim_ticket(self,guild,channel,user):
        with self.db:
            if not self.db.execute('SELECT 1 FROM tickets WHERE guild=? AND channel=? AND closed=0',(guild,channel)).fetchone():
                return None
            self.db.execute('INSERT OR IGNORE INTO ticket_details(channel,guild) VALUES(?,?)',(channel,guild))
            self.db.execute('UPDATE ticket_details SET claimed_by=? WHERE guild=? AND channel=? AND claimed_by=0',(user,guild,channel))
            row=self.db.execute('SELECT claimed_by FROM ticket_details WHERE guild=? AND channel=?',(guild,channel)).fetchone()
            return row['claimed_by'] if row else None
    def set_ticket_priority(self,guild,channel,priority):
        if priority not in ('low','normal','high'): raise ValueError('Invalid priority.')
        with self.db:
            if not self.db.execute('SELECT 1 FROM tickets WHERE guild=? AND channel=? AND closed=0',(guild,channel)).fetchone(): return False
            self.db.execute('INSERT OR REPLACE INTO ticket_priority VALUES(?,?,?)',(channel,guild,priority))
        return True
    def reopen_ticket(self,guild,channel,now):
        with self.db:
            row=self.db.execute('SELECT owner FROM tickets WHERE guild=? AND channel=? AND closed=1',(guild,channel)).fetchone()
            job=self.db.execute('SELECT due FROM ticket_deletions WHERE guild=? AND channel=?',(guild,channel)).fetchone()
            if not row or not job or job['due']<=now: return False
            if self.db.execute('SELECT 1 FROM tickets WHERE guild=? AND owner=? AND closed=0',(guild,row['owner'])).fetchone(): return False
            self.db.execute('UPDATE tickets SET closed=0 WHERE guild=? AND channel=?',(guild,channel))
            self.db.execute('DELETE FROM ticket_deletions WHERE guild=? AND channel=?',(guild,channel))
        return True
    def queue_ticket_close(self,guild,channel,due):
        with self.db:
            changed=self.db.execute('UPDATE tickets SET closed=1 WHERE guild=? AND channel=? AND closed=0',(guild,channel)).rowcount
            if changed:
                self.db.execute('INSERT INTO ticket_deletions(channel,guild,due) VALUES(?,?,?)',(channel,guild,due))
            return bool(changed)
    def event(self, guild, kind, detail):
        self.run('INSERT INTO events(guild,kind,detail,at) VALUES(?,?,?,?)',(guild,kind,detail[:2000],time.time()))
        self.run('DELETE FROM events WHERE guild=? AND id NOT IN (SELECT id FROM events WHERE guild=? ORDER BY id DESC LIMIT 1000)',(guild,guild))
