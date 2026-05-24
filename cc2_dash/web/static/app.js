const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const DEFAULT_TABS = [
  { id: 'dashboard', label: 'Dashboard', icon: '⌁', visible: true },
  { id: 'camera', label: 'Camera', icon: '◉', visible: true },
  { id: 'temperatures', label: 'Temps', icon: '♨', visible: true },
  { id: 'fans', label: 'Fans', icon: '✺', visible: true },
  { id: 'controls', label: 'Control', icon: '◈', visible: true },
  { id: 'files', label: 'Files', icon: '▣', visible: true },
  { id: 'timelapse', label: 'Timelapse', icon: '▻', visible: true },
  { id: 'console', label: 'Console', icon: '⌘', visible: true },
  { id: 'settings', label: 'Settings', icon: '⚙', visible: true },
];

const TAB_PERMISSIONS = {
  dashboard: 'view_dashboard',
  camera: 'view_camera',
  temperatures: 'view_temperatures',
  fans: 'set_fans',
  controls: 'control_print',
  files: 'view_files',
  timelapse: 'view_timelapse',
  console: 'developer_console',
  settings: 'edit_settings',
};

const prefs = JSON.parse(localStorage.getItem('cc2_dash_v10_prefs') || '{}');
const DEFAULT_APP_CONFIG = {
  auth: { enabled: true, allow_guest_dashboard: true, session_timeout_minutes: 720, lockout_enabled: true, max_failed_attempts: 8, lockout_minutes: 10 },
  guest_dashboard: { show_camera: true, show_current_job: true, show_temperatures: true, show_progress: true, show_eta: true, show_files: false, show_timelapse: true, mask_file_names: false, show_printer_name: true, show_printer_ip: false, show_serial: false },
  dashboard: { default_tab: 'dashboard', poll_interval_ms: 2500, auto_load_camera: true, developer_mode: false },
  mobile: { force_mobile_layout: false, bottom_nav: true, large_touch_controls: true },
  camera: { prefer_direct: true, proxy_fallback: true, auto_wake: true },
  safety: { confirm_cancel: true, confirm_start_print: true, confirm_delete_file: true, confirm_history_delete: true, confirm_temperature_change: false, max_nozzle_temp: 320, max_bed_temp: 120, max_fan_percent: 100 },
  theme: { preset: 'carbon', glass: true, animations: true, accent: 'blue' },
  layout: { tabs: [] },
  developer: { show_raw_console_payloads: false },
};
let appConfig = structuredClone(DEFAULT_APP_CONFIG);
let authState = { authenticated:false, role:'guest', permissions:{}, setup_required:false };
let clientInfo = null;
let activePrinter = null;
let refreshTimer = null;
let lastStatus = null;
let cameraOn = false;
let lightState = false;
let currentTab = prefs.tab || 'dashboard';
const fileTotalLayersCache = new Map();

function savePrefs(){ localStorage.setItem('cc2_dash_v10_prefs', JSON.stringify(prefs)); }
function mergeDeep(base, patch){
  const out = Array.isArray(base) ? [...base] : { ...(base || {}) };
  for(const [k,v] of Object.entries(patch || {})){
    if(v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) out[k] = mergeDeep(out[k], v);
    else out[k] = v;
  }
  return out;
}
async function loadSettings(){
  try{ appConfig = mergeDeep(DEFAULT_APP_CONFIG, await api('/api/settings')); }
  catch(e){ log(`Settings load failed, using defaults: ${e.message}`, 'WARN'); appConfig = structuredClone(DEFAULT_APP_CONFIG); }
}
async function patchSettings(patch){
  const res = await api('/api/settings', { method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch) });
  appConfig = mergeDeep(DEFAULT_APP_CONFIG, res.settings || res);
  applyPrefs(); renderNav(); renderLayoutEditor(); startRefresh();
  return appConfig;
}
function detectClient(){
  const ua = navigator.userAgent || '';
  const isMobileUA = /Android|iPhone|iPad|iPod|Mobile|Silk|Kindle/i.test(ua);
  const isSmallScreen = window.matchMedia('(max-width: 760px)').matches;
  const isTouch = navigator.maxTouchPoints > 0;
  return { isMobile: !!(isMobileUA || appConfig.mobile?.force_mobile_layout || (isSmallScreen && isTouch)), isSmallScreen, isTouch, userAgent: ua };
}
function applyClientClasses(){
  clientInfo = detectClient();
  document.body.classList.toggle('is-mobile', clientInfo.isMobile);
  document.body.classList.toggle('is-touch', clientInfo.isTouch);
  document.body.classList.toggle('is-desktop', !clientInfo.isMobile);
  document.body.classList.toggle('is-small-screen', clientInfo.isSmallScreen);
  document.body.classList.toggle('large-touch', !!appConfig.mobile?.large_touch_controls);
  document.body.classList.toggle('bottom-nav-disabled', appConfig.mobile?.bottom_nav === false);
}
function esc(v){ return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function num(v, digits = 0){ const n = Number(v); return Number.isFinite(n) ? n.toFixed(digits) : '--'; }
function pct(v){ const n = Number(v); if(!Number.isFinite(n)) return 0; return Math.max(0, Math.min(100, n > 1 ? n : n * 100)); }
function setText(id, val){ const el = $('#' + id); if(el) el.textContent = val; }
function showToast(msg){ const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden'); clearTimeout(t._timer); t._timer = setTimeout(() => t.classList.add('hidden'), 2800); }
function showView(id){ ['loadingView','authSetupView','setupView','dashView'].forEach(x => $('#' + x)?.classList.add('hidden')); $('#' + id)?.classList.remove('hidden'); }
function log(msg, tag = 'UI', obj){
  const line = `[${new Date().toLocaleTimeString()}] [${tag}] ${msg}${obj ? ' ' + JSON.stringify(obj) : ''}`;
  console.log(line);
  const el = $('#consoleLog');
  if(el){ el.textContent += line + '\n'; el.scrollTop = el.scrollHeight; }
}
async function api(path, opts){
  const res = await fetch(path, opts);
  if(!res.ok){ throw new Error(`${res.status} ${await res.text()}`); }
  return res.json();
}
function can(permission){ return !!authState?.permissions?.[permission]; }
function isGuest(){ return !authState?.authenticated; }
async function loadAuth(){
  try{
    const data = await api('/api/auth/me');
    authState = { authenticated:false, role:'guest', permissions:{}, setup_required:false, ...data };
  }catch(e){
    authState = { authenticated:false, role:'guest', permissions:{}, setup_required:false };
    log(`Auth check failed: ${e.message}`, 'AUTH');
  }
  renderAuth();
  return authState;
}
function renderAuth(){
  const pill = $('#authPill');
  if(pill){
    const label = authState.setup_required ? 'Setup required' : authState.authenticated ? `${authState.username || 'user'} · ${authState.role}` : 'Guest mode';
    pill.textContent = label;
    pill.classList.toggle('admin', authState.role === 'admin');
    pill.classList.toggle('guest', !authState.authenticated);
  }
  $('#loginBtn')?.classList.toggle('hidden', !!authState.authenticated || !!authState.setup_required);
  $('#logoutBtn')?.classList.toggle('hidden', !authState.authenticated);
}
function showLogin(){ $('#loginModal')?.classList.remove('hidden'); setTimeout(() => $('#loginForm input[name="username"]')?.focus(), 50); }
function hideLogin(){ $('#loginModal')?.classList.add('hidden'); }
async function loginSubmit(ev){
  ev.preventDefault();
  const f = ev.currentTarget;
  try{
    const res = await api('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ username:f.username.value.trim(), password:f.password.value }) });
    authState = res.me || res;
    f.reset(); hideLogin(); renderAuth(); showToast('Logged in'); boot();
  }catch(e){ showToast('Login failed'); log(`Login failed: ${e.message}`, 'AUTH'); }
}
async function logout(){
  try{ await api('/api/auth/logout', { method:'POST' }); }catch{}
  authState = { authenticated:false, role:'guest', permissions:{}, setup_required:false };
  renderAuth(); showToast('Logged out'); location.reload();
}
async function setupAdminSubmit(ev){
  ev.preventDefault();
  const f = ev.currentTarget;
  if(f.password.value !== f.password2.value){ showToast('Passwords do not match'); return; }
  try{
    const res = await api('/api/auth/setup', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ username:f.username.value.trim(), password:f.password.value, display_name:f.display_name.value.trim() }) });
    authState = res.me || res;
    showToast('Admin created'); boot();
  }catch(e){ showToast('Admin setup failed'); log(`Admin setup failed: ${e.message}`, 'AUTH'); }
}
function normalizedFrom(snapshot){ return snapshot?.normalized || snapshot?.status?.normalized || snapshot?.data?.normalized || {}; }
function configuredFrom(list){ return list?.configured?.[0] || null; }
function tabAllowed(tab){
  const perm = TAB_PERMISSIONS[tab.id];
  if(perm && !can(perm)) return false;
  if(isGuest()){
    const guest = appConfig.guest_dashboard || {};
    if(tab.id === 'camera' && guest.show_camera === false) return false;
    if(tab.id === 'temperatures' && guest.show_temperatures === false) return false;
    if(tab.id === 'files' && guest.show_files !== true) return false;
    if(tab.id === 'timelapse' && guest.show_timelapse === false) return false;
    if(['fans','controls','console','settings'].includes(tab.id)) return false;
  }
  return true;
}
function tabConfig(){
  const saved = Array.isArray(appConfig.layout?.tabs) && appConfig.layout.tabs.length ? appConfig.layout.tabs : (Array.isArray(prefs.tabs) ? prefs.tabs : null);
  let tabs;
  if(!saved) tabs = structuredClone(DEFAULT_TABS);
  else {
    const map = new Map(DEFAULT_TABS.map(t => [t.id, t]));
    tabs = saved.filter(t => map.has(t.id)).map(t => ({ ...map.get(t.id), ...t }));
    for(const t of DEFAULT_TABS){ if(!tabs.find(x => x.id === t.id)) tabs.push({ ...t }); }
  }
  return tabs.map(t => ({ ...t, visible: t.visible !== false && tabAllowed(t) }));
}
async function saveTabs(tabs){ prefs.tabs = tabs; savePrefs(); await patchSettings({ layout: { tabs } }); }

function applyPrefs(){
  applyClientClasses();
  document.body.dataset.theme = appConfig.theme?.preset || prefs.theme || 'carbon';
  document.body.classList.toggle('no-glass', appConfig.theme?.glass === false);
  document.body.classList.toggle('no-anim', appConfig.theme?.animations === false);
  document.body.classList.toggle('developer-mode', !!appConfig.dashboard?.developer_mode);
  const theme = $('#themeSelect'); if(theme) theme.value = appConfig.theme?.preset || 'carbon';
  const refresh = $('#refreshSelect'); if(refresh) refresh.value = String(appConfig.dashboard?.poll_interval_ms || 2500);
  const glass = $('#glassToggle'); if(glass) glass.checked = appConfig.theme?.glass !== false;
  const anim = $('#animToggle'); if(anim) anim.checked = appConfig.theme?.animations !== false;
  const autoCam = $('#autoCameraToggle'); if(autoCam) autoCam.checked = appConfig.dashboard?.auto_load_camera !== false;
  const dev = $('#developerModeToggle'); if(dev) dev.checked = !!appConfig.dashboard?.developer_mode;
  const defaultTab = $('#defaultTabSelect'); if(defaultTab) defaultTab.value = appConfig.dashboard?.default_tab || 'dashboard';
  const forceMobile = $('#forceMobileToggle'); if(forceMobile) forceMobile.checked = !!appConfig.mobile?.force_mobile_layout;
  const bottomNav = $('#bottomNavToggle'); if(bottomNav) bottomNav.checked = appConfig.mobile?.bottom_nav !== false;
  const largeTouch = $('#largeTouchToggle'); if(largeTouch) largeTouch.checked = appConfig.mobile?.large_touch_controls !== false;
  const preferDirect = $('#preferDirectCameraToggle'); if(preferDirect) preferDirect.checked = appConfig.camera?.prefer_direct !== false;
  const proxyFallback = $('#proxyFallbackToggle'); if(proxyFallback) proxyFallback.checked = appConfig.camera?.proxy_fallback !== false;
  const autoWake = $('#autoWakeCameraToggle'); if(autoWake) autoWake.checked = appConfig.camera?.auto_wake !== false;
  const confirmCancel = $('#confirmCancelToggle'); if(confirmCancel) confirmCancel.checked = appConfig.safety?.confirm_cancel !== false;
  const confirmStart = $('#confirmStartToggle'); if(confirmStart) confirmStart.checked = appConfig.safety?.confirm_start_print !== false;
  const confirmDelete = $('#confirmDeleteToggle'); if(confirmDelete) confirmDelete.checked = appConfig.safety?.confirm_delete_file !== false;
  const confirmTemp = $('#confirmTempToggle'); if(confirmTemp) confirmTemp.checked = !!appConfig.safety?.confirm_temperature_change;
  const maxNozzle = $('#maxNozzleInput'); if(maxNozzle) maxNozzle.value = appConfig.safety?.max_nozzle_temp ?? 320;
  const maxBed = $('#maxBedInput'); if(maxBed) maxBed.value = appConfig.safety?.max_bed_temp ?? 120;
  const maxFan = $('#maxFanInput'); if(maxFan) maxFan.value = appConfig.safety?.max_fan_percent ?? 100;
  const allowGuest = $('#allowGuestDashboardToggle'); if(allowGuest) allowGuest.checked = appConfig.auth?.allow_guest_dashboard !== false;
  const sessionTimeout = $('#sessionTimeoutInput'); if(sessionTimeout) sessionTimeout.value = appConfig.auth?.session_timeout_minutes ?? 720;
  const lockout = $('#lockoutToggle'); if(lockout) lockout.checked = appConfig.auth?.lockout_enabled !== false;
  const maxFailed = $('#maxFailedAttemptsInput'); if(maxFailed) maxFailed.value = appConfig.auth?.max_failed_attempts ?? 8;
  const lockoutMinutes = $('#lockoutMinutesInput'); if(lockoutMinutes) lockoutMinutes.value = appConfig.auth?.lockout_minutes ?? 10;
  const guest = appConfig.guest_dashboard || {};
  const setGuestToggle = (id, val) => { const el = $('#' + id); if(el) el.checked = !!val; };
  setGuestToggle('guestShowCameraToggle', guest.show_camera !== false);
  setGuestToggle('guestShowJobToggle', guest.show_current_job !== false);
  setGuestToggle('guestShowTempsToggle', guest.show_temperatures !== false);
  setGuestToggle('guestShowProgressToggle', guest.show_progress !== false);
  setGuestToggle('guestShowEtaToggle', guest.show_eta !== false);
  setGuestToggle('guestShowFilesToggle', guest.show_files === true);
  setGuestToggle('guestShowTimelapseToggle', guest.show_timelapse !== false);
  setGuestToggle('guestMaskFilesToggle', guest.mask_file_names === true);
  setGuestToggle('guestShowIpToggle', guest.show_printer_ip === true);
  setGuestToggle('guestShowSerialToggle', guest.show_serial === true);
  document.body.classList.toggle('guest-mode', isGuest());
  document.body.classList.toggle('auth-admin', authState.role === 'admin');
  $$('input[name="nozzle"]').forEach(i => i.max = appConfig.safety?.max_nozzle_temp ?? 320);
  $$('input[name="bed"]').forEach(i => i.max = appConfig.safety?.max_bed_temp ?? 120);
  ['modelFan','auxFan','boxFan'].forEach(id => { const el = $('#' + id); if(el) el.max = appConfig.safety?.max_fan_percent ?? 100; });
}
function renderNav(){
  const tabs = tabConfig();
  const visible = tabs.filter(t => t.visible !== false);
  const makeBtn = t => `<button class="nav-btn ${t.id === currentTab ? 'active' : ''}" data-nav="${esc(t.id)}"><span>${esc(t.icon)}</span><b>${esc(t.label)}</b></button>`;
  const side = $('#sideNav'); if(side) side.innerHTML = visible.map(makeBtn).join('');
  const bottom = $('#bottomNav'); if(bottom) bottom.innerHTML = visible.slice(0, 6).map(makeBtn).join('');
  const mobileTop = $('#mobileTopNav'); if(mobileTop) mobileTop.innerHTML = visible.map(makeBtn).join('');
  $$('[data-nav]').forEach(btn => btn.addEventListener('click', () => setPage(btn.dataset.nav)));
}
function setPage(id){
  const visible = tabConfig().filter(t => t.visible !== false).map(t => t.id);
  if(!visible.includes(id)) id = visible[0] || 'dashboard';
  currentTab = id; prefs.tab = id; savePrefs();
  $$('.page').forEach(p => p.classList.toggle('active', p.id === `page-${id}`));
  $$('[data-nav]').forEach(b => b.classList.toggle('active', b.dataset.nav === id));
  $('#mobileMenu')?.classList.add('hidden');
}
function renderLayoutEditor(){
  const box = $('#layoutEditor'); if(!box) return;
  const tabs = tabConfig();
  box.innerHTML = tabs.map((t, i) => `
    <div class="layout-row" data-layout-id="${esc(t.id)}">
      <label class="layout-check"><input type="checkbox" ${t.visible !== false ? 'checked' : ''} data-layout-visible="${esc(t.id)}"><span>${esc(t.icon)} ${esc(t.label)}</span></label>
      <div class="layout-actions"><button class="btn subtle tiny" data-move-up="${esc(t.id)}" ${i === 0 ? 'disabled' : ''}>↑</button><button class="btn subtle tiny" data-move-down="${esc(t.id)}" ${i === tabs.length - 1 ? 'disabled' : ''}>↓</button></div>
    </div>`).join('');
  $$('[data-layout-visible]', box).forEach(ch => ch.addEventListener('change', () => {
    const next = tabConfig().map(t => t.id === ch.dataset.layoutVisible ? { ...t, visible: ch.checked } : t);
    saveTabs(next);
  }));
  $$('[data-move-up]', box).forEach(btn => btn.addEventListener('click', () => moveTab(btn.dataset.moveUp, -1)));
  $$('[data-move-down]', box).forEach(btn => btn.addEventListener('click', () => moveTab(btn.dataset.moveDown, 1)));
}
function moveTab(id, dir){
  const tabs = tabConfig();
  const idx = tabs.findIndex(t => t.id === id);
  const target = idx + dir;
  if(idx < 0 || target < 0 || target >= tabs.length) return;
  [tabs[idx], tabs[target]] = [tabs[target], tabs[idx]];
  saveTabs(tabs);
}

async function boot(){
  try{ await loadAuth(); }catch{}
  if(authState.setup_required){ showView('authSetupView'); return; }
  try{ await loadSettings(); } catch{}
  currentTab = prefs.tab || appConfig.dashboard?.default_tab || 'dashboard';
  applyPrefs(); renderNav(); renderLayoutEditor();
  if(!can('view_dashboard')){
    showView('setupView');
    $('#scanResults').innerHTML = `<article class="card hero-card"><h2>Login required</h2><p>The guest dashboard is disabled. Login to view cc2-dash.</p><button class="btn primary" onclick="document.getElementById('loginBtn').click()">Login</button></article>`;
    showLogin();
    return;
  }
  if(can('edit_settings')) loadUsers().catch(()=>{});
  try{
    const data = await api('/api/printers');
    activePrinter = configuredFrom(data);
    if(!activePrinter){
      showView('setupView');
      if(!can('edit_settings')){
        $('#scanResults').innerHTML = `<article class="card hero-card"><h2>No printer available</h2><p>Login as an admin to scan, pair, or configure the printer.</p><button class="btn primary" onclick="document.getElementById('loginBtn').click()">Login</button></article>`;
      }
      return;
    }
    showView('dashView');
    renderConfigured(activePrinter);
    setPage(currentTab);
    await refreshStatus();
    startRefresh();
    if(can('view_camera') && appConfig.dashboard?.auto_load_camera !== false && appConfig.guest_dashboard?.show_camera !== false) setTimeout(() => loadCamera('auto', true), 450);
  }catch(e){ log(`Boot failed: ${e.message}`, 'ERR'); showView(can('edit_settings') ? 'setupView' : 'dashView'); }
}
function renderConfigured(p){
  setText('printerName', p.name || 'Centauri Carbon 2');
  setText('printerMeta', `${p.host || '--'} · ${p.serial || 'no serial'} · MQTT ${p.port || 1883}`);
  $('#allowCommandsToggle').checked = !!p.allow_commands;
  $('#allowDangerToggle').checked = !!p.allow_dangerous_commands;
  const f = $('#settingsPrinterForm');
  if(f){
    f.name.value = p.name || '';
    f.host.value = p.host || '';
    f.serial.value = p.serial || '';
    f.access_code.value = '';
    f.access_code.placeholder = p.access_code_set ? 'Stored — enter a new code to replace' : 'Enter printer PIN/access code';
  }
}
async function refreshStatus(){
  if(!activePrinter) return;
  try{
    const snapshot = await api(`/api/printers/${encodeURIComponent(activePrinter.id)}/status`);
    lastStatus = snapshot;
    renderStatus(snapshot);
  }catch(e){
    log(`Status refresh failed: ${e.message}`, 'ERR');
    setText('printerMeta', `${activePrinter.host} · offline or reconnecting`);
  }
}
function startRefresh(){ clearInterval(refreshTimer); refreshTimer = setInterval(refreshStatus, Number(appConfig.dashboard?.poll_interval_ms || 2500)); }
function renderStatus(snapshot){
  const n = normalizedFrom(snapshot);

  // Log raw snapshot for developer console debugging
  if (appConfig.dashboard?.developer_mode || appConfig.developer?.show_raw_console_payloads) {
    log(`Status snapshot update: connected=${snapshot.connected}`, 'DEV', snapshot);
  }

  const connected = snapshot.connected ? 'connected' : 'not connected';
  const registered = snapshot.registered ? 'registered' : 'pairing';
  setText('printerMeta', `${activePrinter.host} · ${connected} · ${registered}`);
  activePrinter.allow_commands = snapshot.allow_commands ?? activePrinter.allow_commands;
  activePrinter.allow_dangerous_commands = snapshot.allow_dangerous_commands ?? activePrinter.allow_dangerous_commands;
  $('#allowCommandsToggle').checked = !!activePrinter.allow_commands;
  $('#allowDangerToggle').checked = !!activePrinter.allow_dangerous_commands;
  const progress = pct(n.progress);
  $('.progress-ring')?.style.setProperty('--p', progress);
  setText('progressText', `${Math.round(progress)}%`);
  const state = n.state || (snapshot.connected ? 'connected' : 'offline');
  const badge = $('#stateBadge');
  if(badge){ badge.textContent = state; badge.className = `state-badge ${/print/i.test(state) ? 'printing' : ''} ${/error|emergency|fail/i.test(state) ? 'error' : ''}`; }
  setText('fileName', n.file || 'No active file');
  setText('subState', n.sub_state || snapshot.last_error || 'Ready');
  setText('remainingTime', n.time?.remaining_human || '--');
  
  // Layer details metadata query fallback
  let totalLayers = n.layers?.total;
  const currentFile = n.file;
  if (currentFile && (!totalLayers || totalLayers === '--' || totalLayers === 0 || totalLayers === '0')) {
    if (fileTotalLayersCache.has(currentFile)) {
      totalLayers = fileTotalLayersCache.get(currentFile);
    } else if (can('view_files')) {
      fileTotalLayersCache.set(currentFile, '--');
      api(`/api/printers/${encodeURIComponent(activePrinter.id)}/files/detail?storage_media=local&filename=${encodeURIComponent(currentFile)}`)
        .then(res => {
          const result = res.result?.result || res.result || res;
          const layerCount = result.layer || result.layer_count || result.metadata?.layer_count || result.total_layers || result.layers;
          if (layerCount) {
            fileTotalLayersCache.set(currentFile, layerCount);
            setText('layerText', `${n.layers?.current ?? '--'}/${layerCount}`);
          }
        })
        .catch(e => {
          log(`Failed to fetch total layers for ${currentFile}: ${e.message}`, 'ERR');
        });
    }
  }
  setText('layerText', `${n.layers?.current ?? '--'}/${totalLayers ?? '--'}`);
  
  setText('speedMode', n.position?.speed_mode_name || '--');
  const nozzleActual = `${num(n.temps?.nozzle?.actual)}°`; const nozzleTarget = `target ${num(n.temps?.nozzle?.target)}°`;
  const bedActual = `${num(n.temps?.bed?.actual)}°`; const bedTarget = `target ${num(n.temps?.bed?.target)}°`;
  const chamberActual = `${num(n.temps?.chamber?.actual)}°`; const chamberTarget = `target ${num(n.temps?.chamber?.target)}°`;
  setText('nozzleTemp', nozzleActual); setText('nozzleTarget', nozzleTarget); setText('nozzleTempLarge', nozzleActual);
  setText('bedTemp', bedActual); setText('bedTarget', bedTarget); setText('bedTempLarge', bedActual);
  setText('chamberTemp', chamberActual); setText('chamberTarget', chamberTarget);
  
  // Resilient filament state coercion
  const detected = n.filament?.detected;
  const isDetected = detected === true || detected === 1 || String(detected).toLowerCase() === 'true' || String(detected).toLowerCase() === 'detected';
  const isMissing = detected === false || detected === 0 || String(detected).toLowerCase() === 'false' || String(detected).toLowerCase() === 'missing';
  setText('filamentState', isDetected ? 'Detected' : isMissing ? 'Missing' : '--');

  const sensorEnabled = n.filament?.sensor_enabled;
  const isSensorOn = sensorEnabled === true || sensorEnabled === 1 || String(sensorEnabled).toLowerCase() === 'true' || String(sensorEnabled).toLowerCase() === 'on' || String(sensorEnabled).toLowerCase() === 'enabled';
  const isSensorOff = sensorEnabled === false || sensorEnabled === 0 || String(sensorEnabled).toLowerCase() === 'false' || String(sensorEnabled).toLowerCase() === 'off' || String(sensorEnabled).toLowerCase() === 'disabled';
  setText('filamentSensor', `sensor ${isSensorOn ? 'on' : isSensorOff ? 'off' : '--'}`);
  
  setText('posX', num(n.position?.x,2)); setText('posY', num(n.position?.y,2)); setText('posZ', num(n.position?.z,2)); setText('posE', num(n.position?.e,2));
  lightState = !!(n.led?.status || n.led?.power);
  renderFans(n.fans || {});
  renderSafetyNotice();
}
function renderSafetyNotice(){
  const el = $('#safetyNotice'); if(!el || !activePrinter) return;
  if(!can('control_print')){ el.classList.remove('hidden'); el.textContent = isGuest() ? 'Guest mode is read-only. Login to control the printer.' : 'Your role does not allow printer controls.'; return; }
  if(!activePrinter.allow_commands){ el.classList.remove('hidden'); el.textContent = 'Printer controls are locked. Enable them in Settings → Safety.'; return; }
  if(!activePrinter.allow_dangerous_commands){ el.classList.remove('hidden'); el.textContent = 'Destructive controls like cancel, delete, and start print are locked in Settings → Safety.'; return; }
  el.classList.add('hidden');
}
function renderFans(fans){
  const names = { fan:'Model fan', aux_fan:'Aux fan', box_fan:'Box/chassis fan', heater_fan:'Heater fan', controller_fan:'Controller fan' };
  const rows = Object.entries(fans).map(([name, f]) => `<div class="info-row"><span>${esc(names[name] || name)}</span><b>${esc(f.percent ?? '--')}%${f.rpm ? ` · ${esc(f.rpm)} rpm` : ''}</b></div>`).join('');
  $('#fanList').innerHTML = rows || `<div class="info-row"><span>Fans</span><b>--</b></div>`;
  if(fans.fan?.percent != null) setRange('modelFan', fans.fan.percent);
  if(fans.aux_fan?.percent != null) setRange('auxFan', fans.aux_fan.percent);
  if(fans.box_fan?.percent != null) setRange('boxFan', fans.box_fan.percent);
}
function setRange(id, value){ const el = $('#' + id); if(!el || document.activeElement === el) return; el.value = Math.round(Number(value) || 0); updateRangeLabel(id); }
function updateRangeLabel(id){ const map = { modelFan:'modelFanVal', auxFan:'auxFanVal', boxFan:'boxFanVal' }; setText(map[id], `${$('#' + id).value}%`); }

async function scan(){
  if(!can('edit_settings')){ showToast('Login as admin to scan/pair printers'); showLogin(); return; }
  const btn = $('#scanBtn'), results = $('#scanResults'); btn.disabled = true; btn.textContent = 'Scanning…'; results.innerHTML = '';
  log('Scan started','SCAN');
  try{
    const data = await api('/api/discover?timeout=5');
    log(`Scan found ${data.count} printer(s)`, 'SCAN', data.printers);
    if(!data.printers.length){ results.innerHTML = `<article class="card hero-card"><h2>No printers found</h2><p>Try manual setup, check the LAN, or wake the printer up.</p></article>`; return; }
    results.innerHTML = data.printers.map((p, i) => `<article class="printer-result"><div class="result-icon">3D</div><div class="result-meta"><strong>${esc(p.host_name || p.machine_model || 'Centauri Carbon 2')}</strong><span>${esc(p.ip)}${p.serial ? ' · ' + esc(p.serial) : ''}</span></div><button class="btn primary small" data-pick="${i}">Pick</button></article>`).join('');
    $$('[data-pick]', results).forEach(b => b.addEventListener('click', () => fillForm(data.printers[Number(b.dataset.pick)])));
  }catch(e){ log(`Scan failed: ${e.message}`, 'ERR'); results.innerHTML = `<article class="card hero-card"><h2>Scan failed</h2><p>${esc(e.message)}</p></article>`; }
  finally{ btn.disabled = false; btn.textContent = 'Scan for printer'; }
}
function fillForm(p = {}){
  $('#configCard').classList.remove('hidden');
  const f = $('#printerForm');
  f.name.value = p.host_name || p.machine_model || 'Centauri Carbon 2';
  f.host.value = p.ip || '';
  f.serial.value = p.serial || '';
  f.access_code.focus();
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
}
async function savePrinter(ev){
  ev.preventDefault();
  if(!can('edit_settings')){ showToast('Login as admin to save printer setup'); showLogin(); return; }
  const f = ev.currentTarget;
  const payload = { name:f.name.value.trim() || 'Centauri Carbon 2', host:f.host.value.trim(), serial:f.serial.value.trim(), access_code:f.access_code.value.trim(), port:1883, enabled:true, allow_commands:false, allow_dangerous_commands:false };
  await api('/api/printers', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  showToast('Printer saved'); location.reload();
}
async function patchPrinter(patch){
  if(!can('edit_settings')){ showToast('Settings require admin login'); showLogin(); return; }
  if(!activePrinter) return;
  const data = await api(`/api/printers/${encodeURIComponent(activePrinter.id)}`, { method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch) });
  activePrinter = data.printer;
  renderConfigured(activePrinter);
  showToast('Settings saved');
  return data;
}
async function savePrinterSettings(ev){
  ev.preventDefault();
  const f = ev.currentTarget;
  const patch = { name:f.name.value.trim(), host:f.host.value.trim(), serial:f.serial.value.trim() };
  const code = f.access_code.value.trim();
  if(code) patch.access_code = code;
  await patchPrinter(patch);
  f.access_code.value = '';
  f.access_code.placeholder = 'Stored — enter a new code to replace';
}
async function updateSafety(){
  await patchPrinter({ allow_commands: $('#allowCommandsToggle').checked, allow_dangerous_commands: $('#allowDangerToggle').checked });
  renderSafetyNotice();
}

function commandBlocked(kind = 'command'){
  const required = kind === 'temp' ? 'set_temperatures' : kind === 'fan' ? 'set_fans' : kind === 'start' ? 'start_print' : kind === 'danger' ? 'dangerous_commands' : 'control_print';
  if(!can(required)){ showToast(isGuest() ? 'Login required for printer controls' : `Permission required: ${required}`); if(isGuest()) showLogin(); return true; }
  if(!activePrinter?.allow_commands){ showToast(`Enable printer controls first`); if(can('edit_settings')) setPage('settings'); return true; }
  if((kind === 'danger' || kind === 'start') && !activePrinter?.allow_dangerous_commands){ showToast(`Enable destructive controls first`); if(can('edit_settings')) setPage('settings'); return true; }
  return false;
}
async function printerCommand(path, opts = {}, label = 'Command'){
  try{ const res = await api(`/api/printers/${encodeURIComponent(activePrinter.id)}${path}`, opts); log(`${label} OK`, 'CMD', res); showToast(`${label} sent`); setTimeout(refreshStatus, 800); return res; }
  catch(e){ log(`${label} failed: ${e.message}`, 'ERR'); showToast(`${label} failed`); throw e; }
}
async function pausePrint(){ if(commandBlocked()) return; await printerCommand('/print/pause', { method:'POST' }, 'Pause'); }
async function resumePrint(){ if(commandBlocked()) return; await printerCommand('/print/resume', { method:'POST' }, 'Resume'); }
async function cancelPrint(){ if(commandBlocked('danger')) return; if(appConfig.safety?.confirm_cancel !== false && !confirm('Cancel the current print? This is the spicy red button.')) return; await printerCommand('/print/cancel', { method:'POST' }, 'Cancel'); }
async function setLight(on){ if(commandBlocked()) return; await printerCommand('/light', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ on }) }, `Light ${on ? 'on' : 'off'}`); lightState = on; }
async function toggleLight(){ await setLight(!lightState); }
async function setTemperature(payload){
  if(commandBlocked('temp')) return;
  if(payload.nozzle != null) payload.nozzle = Math.max(0, Math.min(Number(appConfig.safety?.max_nozzle_temp ?? 320), Number(payload.nozzle)));
  if(payload.bed != null) payload.bed = Math.max(0, Math.min(Number(appConfig.safety?.max_bed_temp ?? 120), Number(payload.bed)));
  if(appConfig.safety?.confirm_temperature_change && !confirm(`Set temperature target? ${JSON.stringify(payload)}`)) return;
  await printerCommand('/temperature', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) }, 'Temperature');
}
async function setFans(){
  if(commandBlocked('fan')) return;
  const max = Number(appConfig.safety?.max_fan_percent ?? 100);
  const clamp = v => Math.max(0, Math.min(max, Number(v) || 0));
  await printerCommand('/fans', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ model:clamp($('#modelFan').value), aux:clamp($('#auxFan').value), box:clamp($('#boxFan').value) }) }, 'Fans');
}
async function setSpeed(mode){ if(commandBlocked()) return; await printerCommand('/speed', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ mode:Number(mode) }) }, 'Speed mode'); }

function withCacheBust(url){ const sep = url.includes('?') ? '&' : '?'; return `${url}${sep}cc2dash=${Date.now()}`; }
function proxyCameraUrl(){ return `/api/printers/${encodeURIComponent(activePrinter.id)}/camera/stream?t=${Date.now()}`; }
function directCameraUrl(action = false){ const base = `http://${activePrinter.host}:8080/`; return action ? `${base}?action=stream` : base; }
function setCameraSrc(src){
  const a = $('#cameraImg'), b = $('#cameraImgBig');
  for(const img of [a,b]) if(img){ img.src = src; img.classList.add('live'); }
}
async function loadCamera(mode = 'auto', quiet = false){
  if(!activePrinter) return;
  cameraOn = true;
  const imgs = [$('#cameraImg'), $('#cameraImgBig')].filter(Boolean);
  imgs.forEach(img => { img.removeAttribute('src'); img.dataset.fallbackStep = '0'; });
  const primary = imgs[0];
  primary.onerror = () => {
    if(!cameraOn) return;
    const step = Number(primary.dataset.fallbackStep || '0');
    if(step === 0){ primary.dataset.fallbackStep = '1'; setCameraSrc(withCacheBust(directCameraUrl(true))); log('Camera root failed; trying ?action=stream', 'CAM'); }
    else if(step === 1 && appConfig.camera?.proxy_fallback !== false){ primary.dataset.fallbackStep = '2'; setCameraSrc(proxyCameraUrl()); log('Direct camera failed; trying proxy', 'CAM'); }
    else log('Camera stream failed after all fallbacks', 'ERR');
  };
  try{
    const data = await api(`/api/printers/${encodeURIComponent(activePrinter.id)}/camera/url`);
    const first = appConfig.camera?.prefer_direct === false ? (data.proxy_url || proxyCameraUrl()) : (data.direct_url || data.url || directCameraUrl(false));
    setCameraSrc(withCacheBust(first));
    if(!quiet) showToast('Camera loading');
    log('Camera stream requested', 'CAM');
  }catch(e){ setCameraSrc(withCacheBust(directCameraUrl(false))); log(`Camera URL lookup failed, trying direct: ${e.message}`, 'ERR'); }
}
function stopCamera(){ cameraOn = false; [$('#cameraImg'), $('#cameraImgBig')].filter(Boolean).forEach(img => { img.onerror = null; img.removeAttribute('src'); img.classList.remove('live'); }); log('Camera stopped','CAM'); }
async function wakeCamera(){ if(!can('control_print')){ showToast('Login/control permission required to wake camera'); if(isGuest()) showLogin(); return; } try{ await api(`/api/printers/${activePrinter.id}/camera/enable`, { method:'POST' }); showToast('Camera wake sent'); }catch(e){ log(`Camera wake failed: ${e.message}`, 'ERR'); } }

function flattenFiles(payload){
  const result = payload?.result?.result || payload?.result || payload;
  const candidates = [result?.file_list, result?.files, result?.list, result?.data, result?.items];
  for(const c of candidates){ if(Array.isArray(c)) return c; }
  if(Array.isArray(result)) return result;
  return [];
}
function fileNameOf(f){ return f.filename || f.name || f.path || f.file_name || f.Url || 'unnamed'; }
function filePathOf(f){ return f.file_path || f.path || f.filename || f.name || ''; }
function fileSize(v){ const n = Number(v); if(!Number.isFinite(n)) return v ? String(v) : ''; if(n > 1024*1024) return `${(n/1024/1024).toFixed(1)} MB`; if(n > 1024) return `${(n/1024).toFixed(1)} KB`; return `${n} B`; }
async function loadFiles(){
  if(!activePrinter) return;
  const box = $('#fileList'); box.className = 'file-list empty'; box.textContent = 'Loading files…';
  const storage = $('#fileStorage').value; const path = $('#filePath').value || '/';
  try{
    const data = await api(`/api/printers/${encodeURIComponent(activePrinter.id)}/files?storage_media=${encodeURIComponent(storage)}&path=${encodeURIComponent(path)}&page_size=80`);
    const files = flattenFiles(data);
    if(!files.length){ box.textContent = 'No files returned.'; log('Files raw response', 'FILES', data); return; }
    box.className = 'file-list';
    box.innerHTML = files.map((f, i) => {
      const name = fileNameOf(f); const pathVal = filePathOf(f) || name;
      const type = f.type || f.file_type || (f.is_dir ? 'folder' : 'gcode');
      return `<div class="file-item" data-file-index="${i}"><div class="file-main"><strong>${esc(name)}</strong><span>${esc(type)} ${f.size || f.file_size ? '· ' + esc(fileSize(f.size || f.file_size)) : ''}</span></div><div class="file-actions"><button class="btn subtle tiny" data-file-detail="${i}">Info</button><button class="btn primary tiny" data-file-print="${i}">Print</button><button class="btn danger tiny" data-file-delete="${i}">Delete</button></div></div>`;
    }).join('');
    $$('[data-file-detail]', box).forEach(b => b.addEventListener('click', () => fileDetail(files[Number(b.dataset.fileDetail)], storage)));
    $$('[data-file-print]', box).forEach(b => b.addEventListener('click', () => startFile(files[Number(b.dataset.filePrint)], storage)));
    $$('[data-file-delete]', box).forEach(b => b.addEventListener('click', () => deleteFile(files[Number(b.dataset.fileDelete)], storage)));
    log(`Loaded ${files.length} files`, 'FILES');
  }catch(e){ box.className = 'file-list empty'; box.textContent = `File load failed: ${e.message}`; log(`Files failed: ${e.message}`, 'ERR'); }
}
async function fileDetail(f, storage){
  const name = fileNameOf(f);
  try{ const data = await api(`/api/printers/${activePrinter.id}/files/detail?storage_media=${encodeURIComponent(storage)}&filename=${encodeURIComponent(name)}`); log('File detail', 'FILES', data); showToast('File details logged to console'); }
  catch(e){ log(`File detail failed: ${e.message}`, 'ERR'); }
}
async function startFile(f, storage){
  if(commandBlocked('start')) return;
  const name = fileNameOf(f);
  if(appConfig.safety?.confirm_start_print !== false && !confirm(`Start printing ${name}?`)) return;
  await printerCommand('/files/start', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ filename:name, storage_media:storage, start_layer:0, calibration:false, timelapse:false }) }, 'Start print');
}
async function deleteFile(f, storage){
  if(commandBlocked('danger')) return;
  const pathVal = filePathOf(f) || fileNameOf(f);
  if(appConfig.safety?.confirm_delete_file !== false && !confirm(`Delete ${pathVal}?`)) return;
  await printerCommand('/files/delete', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ file_path:pathVal, storage_media:storage }) }, 'Delete file');
  loadFiles();
}

function flattenHistory(payload){
  const r = payload?.result?.result || payload?.result || payload;
  const candidates = [r?.history_task_list, r?.task_list, r?.tasks, r?.data, r?.items];
  for(const c of candidates){ if(Array.isArray(c)) return c; }
  return Array.isArray(r) ? r : [];
}
function fmtTime(ts){ if(!ts) return '--'; const n = Number(ts); const d = new Date(n > 1e12 ? n : n * 1000); return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString(); }
async function loadHistory(){
  const box = $('#historyList'); box.className = 'file-list empty'; box.textContent = 'Loading history…';
  try{
    const data = await api(`/api/printers/${encodeURIComponent(activePrinter.id)}/history`);
    const items = flattenHistory(data);
    if(!items.length){ box.textContent = 'No history/timelapse records returned.'; log('History raw response', 'HIST', data); return; }
    box.className = 'file-list';
    box.innerHTML = items.map((t, i) => {
      const name = t.task_name || t.TaskName || t.filename || `Task ${t.task_id || t.TaskId || i + 1}`;
      const id = t.task_id ?? t.TaskId ?? t.id ?? '';
      const url = t.time_lapse_video_url || t.TimeLapseVideoUrl || t.url || '';
      const status = t.time_lapse_video_status ?? t.TimeLapseVideoStatus ?? t.task_status ?? '';
      return `<div class="file-item"><div class="file-main"><strong>${esc(name)}</strong><span>${esc(fmtTime(t.begin_time || t.BeginTime))} · status ${esc(status)}</span></div><div class="file-actions"><button class="btn primary tiny" data-hist-download="${i}">Download</button><button class="btn subtle tiny" data-hist-export="${i}">Export</button><button class="btn danger tiny" data-hist-delete="${i}">Delete</button></div></div>`;
    }).join('');
    $$('[data-hist-download]', box).forEach(b => b.addEventListener('click', () => downloadTimelapse(items[Number(b.dataset.histDownload)])));
    $$('[data-hist-export]', box).forEach(b => b.addEventListener('click', () => exportTimelapse(items[Number(b.dataset.histExport)])));
    $$('[data-hist-delete]', box).forEach(b => b.addEventListener('click', () => deleteHistory(items[Number(b.dataset.histDelete)])));
    log(`Loaded ${items.length} history items`, 'HIST');
  }catch(e){ box.className = 'file-list empty'; box.textContent = `History load failed: ${e.message}`; log(`History failed: ${e.message}`, 'ERR'); }
}
function taskId(t){ return t.task_id ?? t.TaskId ?? t.id ?? t.Id; }
function timelapseUrl(t){ return t.time_lapse_video_url || t.TimeLapseVideoUrl || t.url || t.Url || ''; }
function downloadTimelapse(t){
  const url = timelapseUrl(t);
  if(url){ window.open(url, '_blank'); return; }
  showToast('No timelapse URL yet. Try Export first.');
}
async function exportTimelapse(t){
  const url = timelapseUrl(t) || t.task_name || t.TaskName || String(taskId(t) || '');
  if(!url){ showToast('No URL/task info available'); return; }
  const res = await printerCommand('/timelapse/export', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ url }) }, 'Timelapse export');
  const returned = res?.result?.url || res?.result?.result?.url;
  if(returned) window.open(returned, '_blank');
}
async function deleteHistory(t){
  if(commandBlocked('danger')) return;
  const id = taskId(t); if(id == null){ showToast('No task id found'); return; }
  if(appConfig.safety?.confirm_history_delete !== false && !confirm(`Delete history/timelapse record ${id}?`)) return;
  await printerCommand('/history/delete', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ task_ids:[id] }) }, 'Delete history');
  loadHistory();
}

async function forgetPrinter(){
  if(!can('edit_settings')){ showToast('Admin login required'); showLogin(); return; }
  if(!activePrinter || !confirm('Forget this printer config?')) return;
  await api(`/api/printers/${encodeURIComponent(activePrinter.id)}`, { method:'DELETE' });
  location.reload();
}
function openPortal(){ if(!can('stock_portal')){ showToast('Admin login required for stock portal bridge'); showLogin(); return; } window.open('/portal-fullscreen', '_blank'); }

async function loadUsers(){
  const box = $('#usersList');
  if(!box || !can('edit_settings')) return;
  box.className = 'file-list empty'; box.textContent = 'Loading users…';
  try{
    const data = await api('/api/auth/users');
    const users = data.users || [];
    if(!users.length){ box.textContent = 'No users found.'; return; }
    box.className = 'file-list';
    box.innerHTML = users.map(u => `<div class="file-item user-item"><div class="file-main"><strong>${esc(u.display_name || u.username)}</strong><span>${esc(u.username)} · ${esc(u.role)} · ${u.enabled ? 'enabled' : 'disabled'}${u.last_login_at ? ' · last login ' + esc(new Date(u.last_login_at * 1000).toLocaleString()) : ''}</span></div><div class="file-actions"><select data-user-role="${esc(u.username)}"><option value="viewer" ${u.role==='viewer'?'selected':''}>Viewer</option><option value="operator" ${u.role==='operator'?'selected':''}>Operator</option><option value="admin" ${u.role==='admin'?'selected':''}>Admin</option></select><button class="btn subtle tiny" data-user-toggle="${esc(u.username)}">${u.enabled ? 'Disable' : 'Enable'}</button><button class="btn danger tiny" data-user-delete="${esc(u.username)}">Delete</button></div></div>`).join('');
    $$('[data-user-role]', box).forEach(sel => sel.addEventListener('change', () => updateUser(sel.dataset.userRole, { role: sel.value })));
    $$('[data-user-toggle]', box).forEach(btn => btn.addEventListener('click', () => updateUser(btn.dataset.userToggle, { enabled: btn.textContent.trim() === 'Enable' })));
    $$('[data-user-delete]', box).forEach(btn => btn.addEventListener('click', () => deleteUser(btn.dataset.userDelete)));
  }catch(e){ box.className = 'file-list empty'; box.textContent = `User load failed: ${e.message}`; }
}
async function createUser(ev){
  ev.preventDefault();
  const f = ev.currentTarget;
  try{
    await api('/api/auth/users', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ username:f.username.value.trim(), display_name:f.display_name.value.trim(), role:f.role.value, password:f.password.value }) });
    f.reset(); showToast('User created'); loadUsers();
  }catch(e){ showToast('Create user failed'); log(`Create user failed: ${e.message}`, 'AUTH'); }
}
async function updateUser(username, patch){
  try{ await api(`/api/auth/users/${encodeURIComponent(username)}`, { method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch) }); showToast('User updated'); loadUsers(); }
  catch(e){ showToast('User update failed'); log(`User update failed: ${e.message}`, 'AUTH'); }
}
async function deleteUser(username){
  if(!confirm(`Delete local user ${username}?`)) return;
  try{ await api(`/api/auth/users/${encodeURIComponent(username)}`, { method:'DELETE' }); showToast('User deleted'); loadUsers(); }
  catch(e){ showToast('User delete failed'); log(`User delete failed: ${e.message}`, 'AUTH'); }
}
async function saveAuthSettings(){
  await patchSettings({ auth:{ allow_guest_dashboard:$('#allowGuestDashboardToggle').checked, session_timeout_minutes:Number($('#sessionTimeoutInput').value), lockout_enabled:$('#lockoutToggle').checked, max_failed_attempts:Number($('#maxFailedAttemptsInput').value), lockout_minutes:Number($('#lockoutMinutesInput').value) } });
  showToast('Auth settings saved');
}
async function saveGuestSettings(){
  await patchSettings({ guest_dashboard:{ show_camera:$('#guestShowCameraToggle').checked, show_current_job:$('#guestShowJobToggle').checked, show_temperatures:$('#guestShowTempsToggle').checked, show_progress:$('#guestShowProgressToggle').checked, show_eta:$('#guestShowEtaToggle').checked, show_files:$('#guestShowFilesToggle').checked, show_timelapse:$('#guestShowTimelapseToggle').checked, mask_file_names:$('#guestMaskFilesToggle').checked, show_printer_ip:$('#guestShowIpToggle').checked, show_serial:$('#guestShowSerialToggle').checked } });
  showToast('Guest dashboard saved');
}

// Event wiring
$('#adminSetupForm')?.addEventListener('submit', setupAdminSubmit);
$('#loginForm')?.addEventListener('submit', loginSubmit);
$('#loginBtn')?.addEventListener('click', showLogin);
$('#closeLoginBtn')?.addEventListener('click', hideLogin);
$('#logoutBtn')?.addEventListener('click', logout);
$('#loginModal')?.addEventListener('click', e => { if(e.target.id === 'loginModal') hideLogin(); });
$('#scanBtn')?.addEventListener('click', scan);
$('#manualBtn')?.addEventListener('click', () => fillForm({}));
$('#printerForm')?.addEventListener('submit', savePrinter);
$('#settingsPrinterForm')?.addEventListener('submit', savePrinterSettings);
$('#refreshBtn')?.addEventListener('click', refreshStatus);
$('#menuBtn')?.addEventListener('click', () => $('#mobileMenu').classList.toggle('hidden'));
$('#portalBtnSide')?.addEventListener('click', openPortal); $('#openPortalBtn')?.addEventListener('click', openPortal);
$('#pauseBtn')?.addEventListener('click', pausePrint); $('#pauseBtn2')?.addEventListener('click', pausePrint);
$('#resumeBtn')?.addEventListener('click', resumePrint); $('#resumeBtn2')?.addEventListener('click', resumePrint);
$('#cancelBtn')?.addEventListener('click', cancelPrint); $('#cancelBtn2')?.addEventListener('click', cancelPrint);
$('#lightToggleBtn')?.addEventListener('click', toggleLight);
$('#loadCamBtn')?.addEventListener('click', () => loadCamera()); $('#reloadCamMiniBtn')?.addEventListener('click', () => loadCamera()); $('#stopCamBtn')?.addEventListener('click', stopCamera); $('#enableCamBtn')?.addEventListener('click', wakeCamera);
$('#nozzleTempForm')?.addEventListener('submit', e => { e.preventDefault(); const v = e.currentTarget.nozzle.value; if(v !== '') setTemperature({ nozzle:Number(v) }); });
$('#bedTempForm')?.addEventListener('submit', e => { e.preventDefault(); const v = e.currentTarget.bed.value; if(v !== '') setTemperature({ bed:Number(v) }); });
$$('[data-temp-target] button').forEach(btn => btn.addEventListener('click', () => setTemperature({ [btn.parentElement.dataset.tempTarget]: Number(btn.dataset.temp) })));
['modelFan','auxFan','boxFan'].forEach(id => $('#' + id)?.addEventListener('input', () => updateRangeLabel(id)));
$('#applyFansBtn')?.addEventListener('click', setFans);
$$('[data-speed]').forEach(b => b.addEventListener('click', () => setSpeed(b.dataset.speed)));
$('#loadFilesBtn')?.addEventListener('click', loadFiles);
$('#loadHistoryBtn')?.addEventListener('click', loadHistory);
$('#clearConsoleBtn')?.addEventListener('click', () => { $('#consoleLog').textContent = ''; });
$('#themeSelect')?.addEventListener('change', e => patchSettings({ theme:{ preset:e.target.value } }));
$('#refreshSelect')?.addEventListener('change', e => patchSettings({ dashboard:{ poll_interval_ms:Number(e.target.value) } }).then(() => showToast('Refresh updated')));
$('#glassToggle')?.addEventListener('change', e => patchSettings({ theme:{ glass:e.target.checked } }));
$('#animToggle')?.addEventListener('change', e => patchSettings({ theme:{ animations:e.target.checked } }));
$('#autoCameraToggle')?.addEventListener('change', e => patchSettings({ dashboard:{ auto_load_camera:e.target.checked } }));
$('#developerModeToggle')?.addEventListener('change', e => patchSettings({ dashboard:{ developer_mode:e.target.checked } }));
$('#defaultTabSelect')?.addEventListener('change', e => patchSettings({ dashboard:{ default_tab:e.target.value } }));
$('#forceMobileToggle')?.addEventListener('change', e => patchSettings({ mobile:{ force_mobile_layout:e.target.checked } }));
$('#bottomNavToggle')?.addEventListener('change', e => patchSettings({ mobile:{ bottom_nav:e.target.checked } }));
$('#largeTouchToggle')?.addEventListener('change', e => patchSettings({ mobile:{ large_touch_controls:e.target.checked } }));
$('#preferDirectCameraToggle')?.addEventListener('change', e => patchSettings({ camera:{ prefer_direct:e.target.checked } }));
$('#proxyFallbackToggle')?.addEventListener('change', e => patchSettings({ camera:{ proxy_fallback:e.target.checked } }));
$('#autoWakeCameraToggle')?.addEventListener('change', e => patchSettings({ camera:{ auto_wake:e.target.checked } }));
$('#confirmCancelToggle')?.addEventListener('change', e => patchSettings({ safety:{ confirm_cancel:e.target.checked } }));
$('#confirmStartToggle')?.addEventListener('change', e => patchSettings({ safety:{ confirm_start_print:e.target.checked } }));
$('#confirmDeleteToggle')?.addEventListener('change', e => patchSettings({ safety:{ confirm_delete_file:e.target.checked } }));
$('#confirmTempToggle')?.addEventListener('change', e => patchSettings({ safety:{ confirm_temperature_change:e.target.checked } }));
$('#saveLimitsBtn')?.addEventListener('click', () => patchSettings({ safety:{ max_nozzle_temp:Number($('#maxNozzleInput').value), max_bed_temp:Number($('#maxBedInput').value), max_fan_percent:Number($('#maxFanInput').value) } }).then(() => showToast('Safety limits saved')));
$('#resetAppSettingsBtn')?.addEventListener('click', async () => { if(!confirm('Reset dashboard app settings? Printer pairing will stay.')) return; await api('/api/settings/reset', { method:'POST' }); await loadSettings(); applyPrefs(); renderNav(); renderLayoutEditor(); showToast('App settings reset'); });
$('#saveAuthSettingsBtn')?.addEventListener('click', saveAuthSettings);
$('#saveGuestSettingsBtn')?.addEventListener('click', saveGuestSettings);
$('#refreshUsersBtn')?.addEventListener('click', loadUsers);
$('#createUserForm')?.addEventListener('submit', createUser);
$('#exportSettingsBtn')?.addEventListener('click', () => window.open('/api/settings/export', '_blank'));
$('#allowCommandsToggle')?.addEventListener('change', updateSafety);
$('#allowDangerToggle')?.addEventListener('change', updateSafety);
$('#resetConfigBtn')?.addEventListener('click', forgetPrinter);
$('#resetLayoutBtn')?.addEventListener('click', async () => { await saveTabs(structuredClone(DEFAULT_TABS)); setPage('dashboard'); });

function initSettingsSubnav() {
  const subnav = $('#settingsSubNav');
  if (!subnav) return;
  const buttons = $$('.settings-subnav-btn', subnav);
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.settingsTab;
      buttons.forEach(b => b.classList.toggle('active', b === btn));
      $$('.settings-section').forEach(sect => {
        const isActive = sect.id === `settings-sect-${tab}`;
        sect.classList.toggle('active', isActive);
      });
    });
  });
}

initSettingsSubnav();
boot();

