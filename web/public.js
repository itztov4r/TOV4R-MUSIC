'use strict';
const statusEl=document.getElementById('status');
const stats=document.getElementById('stats');
async function refresh(){
  document.getElementById('refresh').disabled=true;
  try{
    const r=await fetch('/public/stats',{cache:'no-store'});
    if(!r.ok)throw Error('Status unavailable');
    const d=await r.json();
    statusEl.textContent=(d.online?'Discord connected':'Discord not ready')+' · checked '+new Date().toLocaleTimeString();
    stats.replaceChildren();
    const uptime=Number.isFinite(d.process_uptime_seconds)?Math.floor(d.process_uptime_seconds/60)+' min':'Unavailable';
    for(const [label,val] of [['Servers',d.servers??'Unavailable'],['Gateway latency',d.gateway_latency_ms==null?'Unavailable':d.gateway_latency_ms+' ms'],['Dashboard process uptime',uptime]]){
      const c=document.createElement('div');c.className='stat';
      const a=document.createElement('small');a.textContent=label;
      const b=document.createElement('strong');b.textContent=String(val);
      c.append(a,b);stats.append(c);
    }
  }catch(e){statusEl.textContent='Status unavailable; connection could not be verified.';stats.replaceChildren();}
  finally{document.getElementById('refresh').disabled=false;}
}
document.getElementById('refresh').addEventListener('click',refresh);
refresh();
