const out = document.getElementById('out');
const statusEl = document.getElementById('status');

document.getElementById('scan').addEventListener('click', async () => {
  statusEl.textContent = 'Scanning...';
  const r = await fetch('/api/discover?timeout=3');
  const j = await r.json();
  out.textContent = JSON.stringify(j, null, 2);
  statusEl.textContent = `Found ${j.count} printer(s)`;
});

fetch('/api/printers').then(r => r.json()).then(p => {
  statusEl.textContent = p.length ? `Configured: ${p[0].name}` : 'No printer configured';
});
