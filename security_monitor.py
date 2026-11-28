"""Guild-scoped defensive event monitoring. No automatic bans or permission edits."""
from collections import defaultdict, deque
import time

class SecurityMonitor:
    def __init__(self):
        self.events=defaultdict(deque)
        self.alerted={}
        self.raid_until={}
    def observe(self,guild,kind,now=None):
        now=time.monotonic() if now is None else now
        limit,window={'join':(8,30),'destructive':(5,30),'bot':(1,60),'mentions':(1,60)}[kind]
        key=(guild,kind)
        q=self.events[key]
        q.append(now)
        while q and now-q[0]>window: q.popleft()
        if len(q)<limit: return False
        if kind=='join': self.raid_until[guild]=now+120
        if now-self.alerted.get(key,float('-inf'))<60: return False
        self.alerted[key]=now
        return True
    def paused(self,guild,now=None):
        now=time.monotonic() if now is None else now
        return self.raid_until.get(guild,0)>now
    def clear(self,guild):
        for mapping in (self.events,self.alerted):
            for key in list(mapping):
                if key[0]==guild: del mapping[key]
        self.raid_until.pop(guild,None)
    def prune(self,now=None):
        now=time.monotonic() if now is None else now
        for key,q in list(self.events.items()):
            if not q or now-q[-1]>120: del self.events[key]
        for key,at in list(self.alerted.items()):
            if now-at>120: del self.alerted[key]
        for guild,until in list(self.raid_until.items()):
            if until<=now: del self.raid_until[guild]
