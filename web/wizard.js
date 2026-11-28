'use strict';

// No localStorage or URL credentials. Discord OAuth uses an HttpOnly same-site
// cookie, and the legacy owner key is kept in this page's JS memory only.
let ownerKey = '';
let csrf = '';
let available = [];
let previewToken = '';
let choice = 'gaming';
let meta = null;
const $ = id => document.getElementById(id);
const icons = {movies:'🎬',gaming:'🎮',music:'🎵',anime:'🌸',
               study:'📚',tech:'💻',sports:'⚽',friends:'👋',community:'🌐'};

function message(text, success=false) {
  const box = $('message');
  box.hidden = !text;
  box.className = success ? 'message success' : 'message';
  box.textContent = text;
  if (text) box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function el(tag, text='', cls='') {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

async function api(path, options={}) {
  const headers = {};
  if (ownerKey) headers.Authorization = 'Bearer ' + ownerKey;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (csrf && options.method && options.method !== 'GET') {
    headers['X-CSRF-Token'] = csrf;
  }
  const response = await fetch(path,{
    ...options,headers,credentials:'same-origin',cache:'no-store',
  });
  if (!response.ok) {
    let error = (await response.text()).slice(0,260);
    if (error.startsWith('<!')) error = 'Request failed (HTTP ' + response.status + ')';
    if (response.status === 401) error = 'Login expired. Please sign in again.';
    if (response.status === 403) error = 'Not authorized for this server, or CSRF check failed.';
    throw Error(error || ('Request failed (HTTP ' + response.status + ')'));
  }
  return await response.json();
}

function guild() {
  return available.find(item => item.id === $('guild').value);
}

function resetPreview() {
  $('preview-panel').hidden = true;
  previewToken = '';
}

function renderThemes() {
  const grid = $('themes');
  grid.replaceChildren();
  for (const type of meta.templates) {
    const button = el('button','', 'theme'+(type.id===choice?' active':''));
    button.type = 'button';
    button.setAttribute('aria-pressed',String(type.id===choice));
    button.append(el('span',icons[type.id]||'✨','emoji'),
                  el('span',type.name.replace(/^\S+\s*/,''),'name'));
    button.onclick=()=>{
      choice=type.id;
      renderThemes();
      resetPreview();
    };
    grid.append(button);
  }
}

function renderGuild() {
  const g = guild();
  if (!g) {
    $('guild-name').textContent='No server available';
    $('stats').replaceChildren();
    $('preview').disabled=true;
    $('save-settings').disabled=true;
    $('save-modules').disabled=true;
    $('restore').disabled=true;
    $('events').replaceChildren();
    resetPreview();
    return;
  }
  $('preview').disabled=!g.can_setup;
  $('save-settings').disabled=false;
  $('save-modules').disabled=false;
  $('restore').disabled=!g.can_setup;
  $('modules-form').elements.namedItem('ai_enabled').disabled=!g.can_manage_ai;
  $('guild-name').textContent=g.name;
  $('guild-theme').textContent='Current template: '+(g.type||'community');
  $('stats').replaceChildren();
  for(const [label,number] of [
    ['Members',g.members ?? '—'],['Channels',g.channels],['Roles',g.roles],['Bots visible',g.bots],['Open TABs',g.open_rooms],['Open tickets',g.open_tickets]
  ]) {
    const card=el('div','','stat');
    card.append(el('small',label),el('strong',String(number)));
    $('stats').append(card);
  }
  for(const [name,value] of Object.entries(g.settings)) {
    const input=$('settings-form').elements.namedItem(name)||$('modules-form').elements.namedItem(name);
    if (!input) continue;
    if (input.type==='checkbox') input.checked=!!value;
    else input.value=String(value ?? '');
  }
  $('events').replaceChildren();
  for(const event of g.activity) {
    const block=el('div');
    block.append(el('b',event.kind),el('p',event.detail),
                 el('small',new Date(event.at*1000).toLocaleString()));
    $('events').append(block);
  }
  if (!g.activity.length) $('events').append(el('p','No activity yet.'));
  resetPreview();
}

async function refresh(preserve=true) {
  const data=await api('/api/v2/bootstrap');
  available=data.guilds;
  csrf=data.csrf||'';
  $('actor').textContent=data.actor;
  $('gate').hidden=true;
  $('workspace').hidden=false;
  $('logout').hidden=false;
  $('bot-status').textContent=data.ready?'Discord connected':'Discord connecting';
  const select=$('guild');
  const previous=preserve?select.value:'';
  select.replaceChildren();
  for(const g of available) {
    const option=el('option',g.name);
    option.value=g.id;
    select.append(option);
  }
  if(available.some(g=>g.id===previous)) select.value=previous;
  if(!available.length) message('No manageable server found. Invite TOV4R Bot and make sure you have Manage Server permission.');
  choice=(guild() && guild().type in icons)?guild().type:'gaming';
  renderThemes();
  renderGuild();
}

function renderPlan(plan) {
  $('preview-panel').hidden=false;
  $('preview-name').textContent=plan.title+' / Preview';
  $('preview-stats').replaceChildren();
  for (const item of [
    ['New categories',plan.new_categories_estimate],
    ['New channels',plan.new_channels_estimate],
    ['New roles',plan.new_roles_estimate],
    ['Archive candidates',plan.off_theme_archive_estimate]
  ]) {
    $('preview-stats').append(el('span',item[0]+': '+item[1]));
  }
  const grid=$('preview-structure');
  grid.replaceChildren();
  for(const group of plan.categories) {
    const section=el('div','','structure-group');
    section.append(el('h3',group.category + (group.exists ? ' · exists':'')));
    const list=el('ul');
    for(const channel of group.channels) {
      const label=(channel.kind==='voice'?'🔊 ':'# ')+channel.name+
                  (channel.exists?' · exists':'');
      list.append(el('li',label,channel.exists?'existing':''));
    }
    section.append(list);
    grid.append(section);
  }
  const roles=el('div','','structure-group');
  roles.append(el('h3','🎭 Roles'));
  const list=el('ul');
  for(const role of plan.roles)list.append(el('li',role));
  roles.append(list);
  grid.append(roles);
  $('preview-warning').textContent=plan.warnings.join(' ');
  previewToken=plan.plan_id;
}

async function preview() {
  const g=guild();
  if(!g) return;
  $('preview').disabled=true;
  message('');
  try {
    const plan=await api('/api/v2/guild/'+encodeURIComponent(g.id)+'/preview',{
      method:'POST',
      body:JSON.stringify({theme:choice,focus:$('focus').checked}),
    });
    if(guild()?.id===g.id) renderPlan(plan);
  }catch(error){
    message('Preview: '+error.message);
  }finally{$('preview').disabled=!guild()?.can_setup;}
}

async function apply() {
  const g=guild();
  if(!g||!previewToken) return;
  const token=previewToken;
  previewToken='';
  $('apply').disabled=true;
  message('Applying the reviewed template to '+g.name+'…');
  try {
    const response=await api('/api/v2/guild/'+encodeURIComponent(g.id)+'/apply',{
      method:'POST',body:JSON.stringify({plan_id:token}),
    });
    const result=response.result||{};
    const focus=result.focus||{};
    message('Setup completed! '+(result.channels_new?.length||0)+
      ' new channels, '+(result.roles_new?.length||0)+
      ' new roles, '+(focus.archived?.length||0)+
      ' channels archived. No existing messages were deleted.',true);
    await refresh();
  }catch(error){
    message('Setup interrupted: '+error.message+' Preview again to retry.');
    resetPreview();
  }finally{$('apply').disabled=false;}
}

async function saveSettings(event) {
  event.preventDefault();
  const g=guild();
  if(!g)return;
  const fields=event.currentTarget.elements;
  const payload={};
  for(const name of ['levels','antispam','anti_invites','voice_xp','security_enabled','welcome_text',
                     'blocked_words','xp_cooldown','language','anti_links','automod_progressive','goodbye_enabled']){
    const field=fields.namedItem(name);
    payload[name]=(field.type==='checkbox')?field.checked:
                  name==='xp_cooldown'?Number(field.value):field.value;
  }
  $('save-settings').disabled=true;
  try{
    await api('/api/v2/guild/'+encodeURIComponent(g.id)+'/settings',{
      method:'PUT',body:JSON.stringify(payload),
    });
    message('Settings saved for '+g.name+'.',true);
    await refresh();
  }catch(error){message(error.message);}
  finally{$('save-settings').disabled=false;}
}

async function saveModules(event) {
  event.preventDefault();
  const g=guild();
  if(!g)return;
  const payload={};
  for(const field of event.currentTarget.elements) {
    if(field.type==='checkbox' && !field.disabled) payload[field.name]=field.checked;
  }
  $('save-modules').disabled=true;
  try{
    await api('/api/v2/guild/'+encodeURIComponent(g.id)+'/settings',{
      method:'PUT',body:JSON.stringify(payload)
    });
    message('Modules saved for '+g.name+'.',true);
    await refresh();
  }catch(error){message(error.message);}
  finally{$('save-modules').disabled=!guild();}
}

async function restore() {
  const g=guild();
  if (!g || !window.confirm('Restore up to 75 previously archived channels for '+g.name+'?'))return;
  $('restore').disabled=true;
  try {
    const response=await api('/api/v2/guild/'+encodeURIComponent(g.id)+'/restore',{
      method:'POST',body:JSON.stringify({}),
    });
    const result=response.result;
    message('Restored '+result.restored.length+' channels. '+result.remaining+' remain queued.',true);
    await refresh();
  }catch(error){message(error.message);}
  finally{$('restore').disabled=false;}
}

async function initialize() {
  try {
    meta=await api('/api/v2/meta');
    $('discord-login').hidden=!meta.oauth_enabled;
    $('oauth-info').textContent=meta.oauth_enabled
      ? 'Sign in with Discord to manage only servers where you have Manage Server.'
      : 'Discord OAuth login requires the app owner to configure the client secret and redirect URL. Owner key access remains available.';
    if(!meta.bot_connected)$('bot-status').textContent='Bot connecting…';
    try {await refresh(false);} catch(error) {
      $('gate').hidden=false;
      $('workspace').hidden=true;
      if(error.message.includes('Login expired'))message('');
      else message(error.message);
    }
  } catch(error) {
    message('Dashboard unavailable: '+error.message);
  }
}

$('key-form').addEventListener('submit',async event=>{
  event.preventDefault();
  ownerKey=$('owner-key').value.trim();
  $('owner-key').value='';
  try{await refresh(false);message('',true);}
  catch(error){ownerKey='';message(error.message);}
});
$('guild').addEventListener('change',()=>{
  choice=(guild()?.type in icons)?guild().type:'gaming';
  renderThemes();
  renderGuild();
  message('');
});
$('focus').addEventListener('change',resetPreview);
$('preview').addEventListener('click',preview);
$('reset-preview').addEventListener('click',()=>{resetPreview();message('');});
$('apply').addEventListener('click',apply);
$('settings-form').addEventListener('submit',saveSettings);
$('modules-form').addEventListener('submit',saveModules);
$('restore').addEventListener('click',restore);
$('logout').addEventListener('click',async()=>{
  try {
    await api('/auth/logout',{method:'POST',body:JSON.stringify({})});
  }catch(error) { /* login may already have expired */ }
  ownerKey='';
  csrf='';
  location.assign('/wizard');
});
initialize();
