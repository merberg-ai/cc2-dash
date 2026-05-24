const $ = (sel) => document.querySelector(sel);
const logEl = $('#consoleLog');
function log(msg, obj){
  const line = `[${new Date().toLocaleTimeString()}] ${msg}${obj ? ' ' + JSON.stringify(obj) : ''}`;
  console.log(line);
  if(logEl){ logEl.textContent += line + '\n'; logEl.scrollTop = logEl.scrollHeight; }
}
function isDesktop(){
  return !/Android|iPhone|iPad|iPod|Mobile|Silk|Kindle/i.test(navigator.userAgent) && window.innerWidth > 740;
}
function show(id){ ['loading','setup','desktopNotice'].forEach(x => $('#'+x)?.classList.add('hidden')); $('#'+id)?.classList.remove('hidden'); }
async function api(path, opts){
  const res = await fetch(path, opts);
  if(!res.ok){ throw new Error(`${res.status} ${await res.text()}`); }
  return res.json();
}
async function boot(){
  if(isDesktop() && !localStorage.getItem('cc2_mobile_force_desktop')){ show('desktopNotice'); return; }
  try{
    const data = await api('/api/printers');
    log('config check', {configured:data.configured?.length || 0});
    if(data.configured && data.configured.length){
      location.replace('/portal-fullscreen');
      return;
    }
  }catch(e){ log('config check failed', {error:String(e)}); }
  show('setup');
}
function fillForm(p){
  $('#configCard').classList.remove('hidden');
  const form = $('#printerForm');
  form.name.value = p.host_name || p.machine_model || 'Centauri Carbon 2';
  form.host.value = p.ip || '';
  form.serial.value = p.serial || '';
  form.access_code.focus();
  window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'});
}
async function scan(){
  const btn = $('#scanBtn');
  const results = $('#scanResults');
  btn.disabled = true; btn.textContent = 'Scanning…'; results.innerHTML = '';
  log('scan started');
  try{
    const data = await api('/api/discover?timeout=5');
    log('scan complete', {count:data.count});
    if(!data.printers.length){
      results.innerHTML = `<section class="card"><h2>No printers found</h2><p>Try manual setup, or make sure LAN mode/cloud mode allows local discovery.</p></section>`;
      return;
    }
    results.innerHTML = data.printers.map((p,i)=>`
      <article class="printer-result">
        <div class="icon">3D</div>
        <div class="meta"><strong>${p.host_name || p.machine_model || 'Centauri Carbon 2'}</strong><span>${p.ip}${p.serial ? ' · ' + p.serial : ''}</span></div>
        <button class="primary" data-pick="${i}">Pick</button>
      </article>`).join('');
    results.querySelectorAll('[data-pick]').forEach(btn => btn.addEventListener('click', () => fillForm(data.printers[Number(btn.dataset.pick)])));
  }catch(e){
    log('scan failed', {error:String(e)});
    results.innerHTML = `<section class="card"><h2>Scan failed</h2><p>${String(e)}</p></section>`;
  }finally{
    btn.disabled = false; btn.textContent = 'Scan for printer';
  }
}
async function save(ev){
  ev.preventDefault();
  const form = ev.currentTarget;
  const payload = {
    name: form.name.value.trim() || 'Centauri Carbon 2',
    host: form.host.value.trim(),
    serial: form.serial.value.trim(),
    access_code: form.access_code.value.trim(),
    port: 1883,
    enabled: true,
    allow_commands: false,
    allow_dangerous_commands: true
  };
  log('saving printer', {host:payload.host, serial:payload.serial});
  await api('/api/printers', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  location.replace('/portal-fullscreen');
}
$('#scanBtn')?.addEventListener('click', scan);
$('#manualBtn')?.addEventListener('click', () => { $('#configCard').classList.remove('hidden'); $('#printerForm').host.focus(); });
$('#printerForm')?.addEventListener('submit', save);
$('#forceDesktop')?.addEventListener('click', () => { localStorage.setItem('cc2_mobile_force_desktop','1'); boot(); });
boot();
