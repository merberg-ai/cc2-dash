const $ = (id) => document.getElementById(id);
let printer = null;
let refreshHandle = null;

function log(msg) {
  const ts = new Date().toISOString();
  $('console').textContent = `[${ts}] ${msg}\n` + $('console').textContent;
}

async function loadPrinters() {
  const r = await fetch('/api/printers');
  const list = await r.json();
  if (list.length) {
    printer = list[0];
    $('conn').textContent = `Configured: ${printer.name}`;
    startRefresh();
  } else {
    $('conn').textContent = 'No printer configured';
  }
}

async function refreshStatus() {
  if (!printer) return;
  const r = await fetch(`/api/printers/${printer.id}/status`);
  const j = await r.json();
  $('status-json').textContent = JSON.stringify(j.normalized || j, null, 2);
}

function startRefresh() {
  if (refreshHandle) clearInterval(refreshHandle);
  refreshStatus();
  refreshHandle = setInterval(refreshStatus, 2500);
}

$('scan').onclick = async () => {
  log('scan started');
  const r = await fetch('/api/discover?timeout=3');
  const j = await r.json();
  $('scan-results').textContent = JSON.stringify(j.printers, null, 2);
  if (j.printers?.length) {
    const p = j.printers[0];
    $('name').value = p.host_name || 'Centauri Carbon 2';
    $('host').value = p.ip || '';
    $('serial').value = p.serial || '';
  }
};

$('save').onclick = async () => {
  const payload = {
    id: ($('name').value || 'centauri-carbon-2').toLowerCase().replace(/[^a-z0-9]+/g, '-'),
    name: $('name').value,
    host: $('host').value,
    serial: $('serial').value,
    access_code: $('pin').value,
    port: 1883,
    enabled: true,
    allow_commands: false,
    allow_dangerous_commands: false,
  };
  const r = await fetch('/api/printers', { method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(payload)});
  printer = await r.json();
  log('saved printer');
  $('conn').textContent = `Configured: ${printer.name}`;
  startRefresh();
};

async function post(path, body={}) {
  if (!printer) return log('No printer configured');
  const r = await fetch(`/api/printers/${printer.id}${path}`, { method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(body)});
  const j = await r.json();
  log(`${path} -> ${JSON.stringify(j)}`);
}

$('pause').onclick = () => post('/print/pause');
$('resume').onclick = () => post('/print/resume');
$('cancel').onclick = () => confirm('Cancel current print?') && post('/print/cancel');
$('set-temp').onclick = () => post('/temperature', { nozzle: Number($('nozzle').value)||null, bed: Number($('bed').value)||null });
$('set-fan').onclick = () => post('/fans', { fan: Number($('fan').value)||0 });

$('cam-start').onclick = async () => {
  if (!printer) return;
  const r = await fetch(`/api/printers/${printer.id}/camera/url`);
  const j = await r.json();
  $('camera').src = j.direct_url;
  $('camera').onerror = () => { $('camera').src = j.alt_direct_url; };
  log('camera started');
};
$('cam-stop').onclick = () => { $('camera').src = ''; log('camera stopped'); };

loadPrinters();
