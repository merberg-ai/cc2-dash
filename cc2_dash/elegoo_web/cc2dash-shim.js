/* cc2-dash browser IPC shim for Elegoo Slicer web resources.
   This lets selected stock Elegoo Web UI pages run inside a normal browser by
   translating their nativeIpc requests into cc2-dash FastAPI calls. */
(function () {
  const logPrefix = '[cc2-dash/elegoo-shim]';
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  async function fetchJson(url, opts) {
    const res = await fetch(url, opts || {});
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const msg = data && (data.detail || data.message) ? (data.detail || data.message) : `${res.status} ${res.statusText}`;
      const err = new Error(msg); err.code = res.status; err.data = data; throw err;
    }
    return data;
  }
  function normStatusToElegoo(n) {
    const s = String((n && n.state) || '').toLowerCase();
    if (s.includes('print')) return 1;
    if (s.includes('pause')) return 5;
    if (s.includes('complete') || s.includes('finish')) return 16;
    if (s.includes('error') || s.includes('emergency')) return 32;
    return 0;
  }
  function mapPrinter(p, cfg) {
    p = p || {}; cfg = cfg || {};
    const n = p.normalized || {};
    const temps = n.temps || {};
    const raw = p.raw_status || {};
    const id = p.id || cfg.id || cfg.serial || cfg.host || 'cc2';
    const host = p.host || cfg.host || '';
    const serial = p.serial || cfg.serial || id;
    return {
      printerId: id,
      printerName: p.name || cfg.name || 'Centauri Carbon 2',
      printerModel: raw.machine_model || raw.model || 'Centauri Carbon 2',
      printerImg: '../img/printer-empty.png',
      host,
      hostType: 'ip',
      serialNumber: serial,
      vendor: 'Elegoo',
      networkType: 0,
      connectStatus: p.connected ? 1 : 0,
      printerStatus: normStatusToElegoo(n),
      webUrl: `/portal?printer=${encodeURIComponent(id)}`,
      authMode: 2,
      accessCode: cfg.access_code || '',
      isAdded: true,
      firmwareVersion: raw.firmware_version || raw.firmware || '',
      firmwareUpdate: false,
      extraInfo: {
        progress: n.progress || 0,
        file: n.file || '',
        nozzleTemp: temps.nozzle || {},
        bedTemp: temps.bed || {},
        chamberTemp: temps.chamber || {},
        layers: n.layers || {},
        time: n.time || {},
        raw
      }
    };
  }
  function mapDiscovery(d) {
    return {
      printerId: d.serial || d.ip,
      printerName: d.host_name || d.machine_model || 'Centauri Carbon 2',
      printerModel: d.machine_model || 'Centauri Carbon 2',
      printerImg: '../img/printer-empty.png',
      host: d.ip,
      serialNumber: d.serial || d.ip,
      vendor: 'Elegoo',
      networkType: 0,
      connectStatus: 0,
      printerStatus: 0,
      authMode: d.token_status === 1 ? 2 : 2,
      accessCode: d.token_status === 1 ? '' : '',
      isAdded: false,
      hostType: 'ip'
    };
  }
  async function printerList() {
    const data = await fetchJson('/api/printers');
    const cfgById = {};
    (data.configured || []).forEach(c => { cfgById[c.id] = c; });
    return { printers: (data.status || []).map(p => mapPrinter(p, cfgById[p.id])) };
  }
  async function handle(method, params) {
    console.debug(logPrefix, method, params || {});
    switch (method) {
      case 'ready': return { ok: true };
      case 'request_user_info':
      case 'getUserInfo':
        return { userId: 'local', nickname: 'cc2-dash local', email: '', phone: '', avatar: null, loginStatus: 1, loginErrorMessage: null };
      case 'checkLoginStatus': return { loginStatus: 1 };
      case 'showLoginDialog': return { ok: true };
      case 'logout': return { ok: true };
      case 'request_printer_model_list':
        return [{ model: 'Centauri Carbon 2', name: 'Centauri Carbon 2', vendor: 'Elegoo' }];
      case 'request_printer_list':
      case 'getPrinterList':
        return printerList();
      case 'request_refresh_wan_printers':
      case 'refresh_printer_status':
        return { ok: true };
      case 'request_discover_printers': {
        const d = await fetchJson('/api/discover?timeout=5');
        return (d.printers || []).map(mapDiscovery);
      }
      case 'request_add_printer':
      case 'request_add_physical_printer': {
        const pr = (params && params.printer) || {};
        const body = {
          id: pr.printerId || undefined,
          name: pr.printerName || 'Centauri Carbon 2',
          host: pr.host,
          serial: pr.serialNumber || pr.printerId || pr.host,
          access_code: pr.accessCode || pr.pinCode || '',
          enabled: true,
          allow_commands: false,
          allow_dangerous_commands: false
        };
        return fetchJson('/api/printers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      }
      case 'request_delete_printer': {
        const id = params && params.printerId;
        return fetchJson(`/api/printers/${encodeURIComponent(id)}`, { method: 'DELETE' });
      }
      case 'request_update_printer_name': {
        const list = await fetchJson('/api/printers');
        const cfg = (list.configured || []).find(c => c.id === params.printerId);
        if (!cfg) return { ok: false };
        cfg.name = params.printerName || cfg.name;
        return fetchJson('/api/printers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
      }
      case 'request_update_printer_host': {
        const list = await fetchJson('/api/printers');
        const cfg = (list.configured || []).find(c => c.id === params.printerId);
        if (!cfg) return { ok: false };
        cfg.host = params.host || cfg.host;
        return fetchJson('/api/printers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
      }
      case 'request_update_physical_printer': {
        const pr = (params && params.printer) || {};
        const list = await fetchJson('/api/printers');
        const cfg = (list.configured || []).find(c => c.id === params.printerId) || {};
        Object.assign(cfg, { id: params.printerId, name: pr.printerName || cfg.name, host: pr.host || cfg.host, serial: pr.serialNumber || cfg.serial, access_code: pr.accessCode || cfg.access_code || '' });
        return fetchJson('/api/printers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
      }
      case 'request_cancel_add_printer': return { ok: true };
      case 'request_printer_detail': {
        const id = params && params.printerId;
        const s = await fetchJson(`/api/printers/${encodeURIComponent(id)}/status`);
        return mapPrinter(s, {});
      }
      case 'get_license_expired_devices': return { devices: [] };
      case 'renew_license': return { ok: true };
      case 'getPrinterFilamentInfo':
      case 'request_mms_info':
        return { mmsSystemName: 'CC2', mmsList: [] };
      case 'get_current_bed_type': return 'btPTE';
      case 'navigateToPage': return { ok: true };
      default:
        console.warn(logPrefix, 'unsupported method', method, params || {});
        return {};
    }
  }
  const shim = {
    request(method, params, timeout) {
      const p = handle(method, params || {});
      if (!timeout) return p;
      return Promise.race([p, sleep(timeout).then(() => { const e = new Error('IPC shim timeout'); e.code = 408; throw e; })]);
    },
    requestWithEvents(method, params, cb, timeout) { return this.request(method, params, timeout); },
    on() {}, off() {}, send() {}, sendEvent() {}
  };
  window.cc2DashElegooShim = shim;
  window.nativeIpc = shim;
  console.info(logPrefix, 'loaded');
})();
