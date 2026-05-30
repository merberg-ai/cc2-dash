(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const page = document.body.dataset.page;
  const cfgEl = $('#bootConfig');
  let cfg = cfgEl ? JSON.parse(cfgEl.textContent) : {};
  let dashboardThumbnailUrl = '';
  let dashboardThumbnailFile = '';

  function toast(message, type = 'info', timeout = 4200) {
    const host = $('#toastHost');
    if (!host) return;
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      setTimeout(() => el.remove(), 200);
    }, timeout);
  }

  async function api(path, options = {}) {
    const resp = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const text = await resp.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!resp.ok) {
      const msg = data.detail || data.error || data.message || `HTTP ${resp.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  function setButtonBusy(button, busy, label) {
    if (!button) return;
    const labelEl = $('.button-label', button);
    if (busy) {
      button.dataset.originalLabel = labelEl ? labelEl.textContent : button.textContent;
      button.disabled = true;
      if (labelEl) labelEl.innerHTML = `<span class="spinner"></span> ${label || 'Working...'}`;
    } else {
      button.disabled = false;
      if (labelEl) labelEl.textContent = button.dataset.originalLabel || labelEl.textContent;
    }
  }

  function tempLine(current, target) {
    const c = current === null || current === undefined ? '-' : Number(current).toFixed(1);
    const t = target === null || target === undefined || Number(target) <= 0 ? 'off' : Number(target).toFixed(1);
    return `${c} / ${t}`;
  }

  function setText(id, value) {
    const el = $('#' + id);
    if (el) el.textContent = value ?? '-';
  }

  function summarizeAIHeaderStatus(ai, vision) {
    ai = ai || {};
    vision = vision || ai.vision || ai.vision_ai || {};
    const level = String(ai.level || 'low').toLowerCase();
    const risk = Math.max(0, Math.min(100, Number(ai.risk || 0)));
    const vState = String(vision.visual_state || '').toLowerCase();
    const badVision = ['failure_likely', 'camera_bad', 'failed', 'error'].includes(vState);
    const fishyVision = ['possible_failure', 'uncertain'].includes(vState) && !(vision.benign_uncertainty || vision.normalized_from === 'uncertain');
    const highLevel = ['high', 'critical', 'bad', 'error', 'failure'].includes(level);
    const fishyLevel = ['watch', 'medium', 'warn', 'warning', 'elevated'].includes(level);
    if (badVision || highLevel || risk >= 60) {
      return { tone: 'bad', label: 'Possible failure detected' };
    }
    if (fishyVision || fishyLevel || risk >= 25) {
      return { tone: 'warn', label: 'Something looks fishy' };
    }
    return { tone: 'good', label: 'Looks Good' };
  }

  function renderPortalAI(ai) {
    ai = ai || {};
    const summary = ai.summary || 'Standing By';
    const level = ai.level || 'low';
    const risk = Math.max(0, Math.min(100, Number(ai.risk || 0)));
    setText('assistantText', summary);
    const reason = (ai.reasons || [])[0] || 'No warning rules are currently triggered.';
    setText('assistantReason', reason);
    const aiLevelText = `${level.toUpperCase()} · ${risk}%`;
    const aiSource = ai.source === 'background' || ai.served_from_cache ? 'watchdog' : 'checked';
    const aiCheckText = ai.last_check ? `${aiSource} ${ai.last_check}` : 'checking...';
    setText('aiLevel', aiLevelText);
    setText('aiLevelBrief', aiLevelText);
    setText('aiLastCheck', aiCheckText);
    setText('aiLastCheckBrief', aiCheckText);
    const bar = $('#aiRiskBar');
    if (bar) {
      bar.style.width = `${risk}%`;
      bar.className = `ai-risk-bar ${level}`;
    }
    const pill = $('#aiLevel');
    if (pill) pill.className = `ai-pill ${level}`;
    const panel = $('#aiPanel');
    if (panel) panel.className = `ai-panel ${level}`;
    const reasons = $('#aiReasons');
    if (reasons) {
      const rows = (ai.reasons && ai.reasons.length ? ai.reasons : ['No warning rules are currently triggered.']).slice(0, 5);
      reasons.innerHTML = rows.map(r => `<li>${esc(r)}</li>`).join('');
    }
    const vision = ai.vision || ai.vision_ai || {};
    const headerState = summarizeAIHeaderStatus(ai, vision);
    const headerPill = $('#aiSummaryPill');
    if (headerPill) {
      headerPill.className = `summary-ai-status ${headerState.tone}`;
      headerPill.textContent = headerState.label;
      headerPill.title = `Portal AI: ${headerState.label} · ${level.toUpperCase()} · ${risk}%`;
      headerPill.setAttribute('aria-label', `AI status: ${headerState.label}`);
    }
    const visionBox = $('#aiVisionBox');
    if (visionBox) {
      if (!cfg?.portal_ai?.vision_ai_enabled) {
        visionBox.classList.add('hidden');
        return;
      }
      visionBox.classList.remove('hidden');
      const vState = vision.visual_state || 'pending';
      const vSummary = vision.summary || 'Waiting for a vision check.';
      const vLabel = vision.benign_uncertainty || vision.normalized_from === 'uncertain'
        ? 'looks OK · low confidence'
        : String(vState).replace(/_/g, ' ');
      const heur = vision.heuristics || {};
      const heurWarnings = Array.isArray(heur.warnings) && heur.warnings.length ? `flags ${heur.warnings.join(', ')}` : '';
      const heurMetrics = Number.isFinite(Number(heur.mean_luma)) ? `luma ${Number(heur.mean_luma).toFixed(0)} · contrast ${Number(heur.contrast || 0).toFixed(0)} · edge ${Number(heur.edge_density || 0).toFixed(3)}` : '';
      const learning = vision.learning_thresholds || {};
      const applied = learning.applied || {};
      const appliedBits = Object.entries(applied)
        .filter(([, value]) => Math.abs(Number(value || 0)) > 0)
        .map(([key, value]) => `${key.replace(/_modifier$/, '').replace(/_/g, ' ')} ${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(key.includes('edge') ? 3 : 0)}`);
      const learningMeta = learning.mode
        ? (vision.learning_applied || learning.active
          ? `learning auto · ${appliedBits.join(', ') || 'bounded modifiers active'}`
          : `learning ${String(learning.mode).replace(/_/g, ' ')}`)
        : '';
      const vMeta = [
        vision.model ? `model ${vision.model}` : '',
        vision.last_check ? `checked ${vision.last_check}` : '',
        Number.isFinite(Number(vision.confidence)) ? `conf ${Number(vision.confidence)}%` : '',
        Number.isFinite(Number(vision.severity)) ? `severity ${Number(vision.severity)}%` : '',
        vision.consecutive_bad ? `${vision.consecutive_bad}/${vision.required_bad_checks || '?'} bad` : '',
        heurWarnings,
        heurMetrics,
        learningMeta
      ].filter(Boolean).join(' · ');
      const img = vision.frame?.latest_url ? `<img class="vision-thumb" src="${esc(vision.frame.latest_url)}" alt="Latest vision frame" loading="lazy">` : '';
      const vClass = [String(vState), vision.benign_uncertainty || vision.normalized_from === 'uncertain' ? 'benign_uncertain' : ''].filter(Boolean).join(' ');
      visionBox.className = `ai-vision-box ${esc(vClass)}`;
      visionBox.innerHTML = `${img}<div><strong>Vision: ${esc(vLabel)}</strong><span>${esc(vSummary)}</span>${vMeta ? `<small>${esc(vMeta)}</small>` : ''}</div>`;
    }
  }


  function renderCameraRelay(relay) {
    relay = relay || {};
    const dot = $('#cameraRelayDot');
    const text = $('#cameraRelayText');
    const detail = $('#cameraRelayStatus');
    const ok = !!relay.ok;
    const running = !!relay.running;
    const connected = !!relay.upstream_connected;
    const stale = !!relay.stale;
    const age = Number(relay.last_frame_age_seconds);
    const clients = Number(relay.client_count || 0);
    let cls = ok ? 'good' : (running || connected ? 'warn' : 'bad');
    let label = ok ? 'RELAY LIVE' : (running ? 'RELAY WARMING' : 'RELAY DOWN');
    if (stale && running) label = 'RELAY STALE';
    if (dot) dot.className = `dot ${cls}`;
    if (text) text.textContent = label;
    if (detail) {
      const bits = [];
      bits.push(connected ? 'one upstream camera connection' : 'no upstream camera connection yet');
      if (Number.isFinite(age)) bits.push(`last frame ${age.toFixed(age < 10 ? 1 : 0)}s ago`);
      bits.push(`${clients} viewer${clients === 1 ? '' : 's'}`);
      if (relay.reconnects) bits.push(`${relay.reconnects} reconnects`);
      if (relay.last_error && !ok) bits.push(String(relay.last_error).slice(0, 90));
      detail.textContent = bits.join(' · ');
    }
  }

  window.cc2CameraFailed = function () {
    const ph = $('#cameraPlaceholder');
    const cam = $('#cameraStream');
    if (ph) {
      ph.classList.remove('hidden');
      ph.innerHTML = '<span>Camera relay unavailable. Check relay status or restart camera.</span>';
    }
    if (cam) cam.classList.add('hidden');
    setText('cameraState', 'Unavailable');
  };

  function setKioskCameraPlaceholder(message, mode = 'warming') {
    const ph = $('#kioskCameraPlaceholder');
    if (!ph) return;
    ph.classList.remove('hidden');
    ph.classList.toggle('camera-placeholder-warn', mode === 'warn');
    ph.classList.toggle('camera-placeholder-bad', mode === 'bad');
    ph.innerHTML = `<span class="spinner"></span><span>${message}</span>`;
  }

  function hideKioskCameraPlaceholder() {
    const ph = $('#kioskCameraPlaceholder');
    if (ph) ph.classList.add('hidden');
  }

  window.cc2KioskCameraLoaded = function () {
    hideKioskCameraPlaceholder();
    const cam = $('#kioskCameraStream');
    if (cam) cam.classList.remove('hidden');
  };

  window.cc2KioskCameraFailed = function () {
    setKioskCameraPlaceholder('Camera relay reconnecting...', 'warn');
    const cam = $('#kioskCameraStream');
    if (cam) {
      cam.classList.add('hidden');
      window.clearTimeout(window.__cc2KioskRetryTimer);
      window.__cc2KioskRetryTimer = window.setTimeout(() => {
        const src = cam.dataset.streamSrc || cam.getAttribute('src') || '';
        if (!src) return;
        const clean = src.replace(/[?&]kiosk_reload=\d+$/, '');
        cam.src = `${clean}${clean.includes('?') ? '&' : '?'}kiosk_reload=${Date.now()}`;
      }, 4500);
    }
  };

  function primeKioskCamera() {
    const cam = $('#kioskCameraStream');
    if (!cam) return;
    const src = cam.dataset.streamSrc || cam.getAttribute('src');
    if (src && !cam.getAttribute('src')) {
      cam.src = `${src}${src.includes('?') ? '&' : '?'}kiosk=1&t=${Date.now()}`;
    }
    setKioskCameraPlaceholder('Starting camera relay...', 'warming');
    // MJPEG load events can be weird across browsers. Do not leave a giant
    // spinner pinned over the stream forever; after a short grace period, let
    // the overlay badges explain whether the relay is live/warming/stale.
    window.setTimeout(() => {
      const ph = $('#kioskCameraPlaceholder');
      const relayText = ($('#kioskRelayText')?.textContent || '').toLowerCase();
      if (ph && !ph.classList.contains('hidden') && !/down|unavailable|error/.test(relayText)) {
        hideKioskCameraPlaceholder();
        cam.classList.remove('hidden');
      }
    }, 2200);
  }


  function hideGcodeThumbnail() {
    const card = $('#gcodeThumbnailCard');
    const img = $('#gcodeThumbnailImg');
    if (card) card.classList.add('hidden');
    if (img) {
      img.removeAttribute('src');
      img.alt = 'G-code thumbnail';
    }
    dashboardThumbnailUrl = '';
    dashboardThumbnailFile = '';
  }

  function renderGcodeThumbnail(st) {
    const card = $('#gcodeThumbnailCard');
    const img = $('#gcodeThumbnailImg');
    if (!card || !img) return;
    const url = st?.gcode_thumbnail_url || '';
    const file = st?.file && st.file !== '-' ? st.file : '';
    if (!url || st?.show_gcode_thumbnail === false || !file) {
      hideGcodeThumbnail();
      return;
    }
    dashboardThumbnailFile = file;
    if (dashboardThumbnailUrl === url && img.getAttribute('src')) {
      card.classList.remove('hidden');
      return;
    }
    dashboardThumbnailUrl = url;
    card.classList.add('hidden');
    img.onload = () => {
      card.classList.remove('hidden');
      img.alt = `G-code thumbnail for ${file}`;
    };
    img.onerror = () => {
      hideGcodeThumbnail();
    };
    img.src = url;
  }

  function openGcodeThumbnailModal() {
    const modal = $('#gcodeThumbnailModal');
    const source = $('#gcodeThumbnailImg');
    const img = $('#gcodeThumbnailModalImg');
    const file = $('#gcodeThumbnailModalFile');
    if (!modal || !source || !img || !source.getAttribute('src')) return;
    img.src = source.getAttribute('src');
    img.alt = source.alt || 'G-code thumbnail preview';
    if (file) file.textContent = dashboardThumbnailFile || 'Current print preview';
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeGcodeThumbnailModal() {
    const modal = $('#gcodeThumbnailModal');
    const img = $('#gcodeThumbnailModalImg');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    if (img) img.removeAttribute('src');
  }

  function initGcodeThumbnailModal() {
    const button = $('#gcodeThumbnailButton');
    const close = $('#gcodeThumbnailModalClose');
    const modal = $('#gcodeThumbnailModal');
    if (button) button.addEventListener('click', openGcodeThumbnailModal);
    if (close) close.addEventListener('click', closeGcodeThumbnailModal);
    if (modal) {
      modal.addEventListener('click', event => {
        if (event.target === modal) closeGcodeThumbnailModal();
      });
    }
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeGcodeThumbnailModal();
    });
  }

  async function refreshDashboard() {
    try {
      const st = await api('/api/status');
      const progress = Math.max(0, Math.min(100, Number(st.progress || 0)));
      const progressBar = $('#progressBar');
      const progressText = $('#progressText');
      const summaryProgressBar = $('#summaryProgressBar');
      const summaryProgressText = $('#summaryProgressText');
      if (progressBar) progressBar.style.width = `${progress}%`;
      if (progressText) progressText.textContent = `${progress.toFixed(1)}%`;
      if (summaryProgressBar) summaryProgressBar.style.width = `${progress}%`;
      if (summaryProgressText) summaryProgressText.textContent = `${progress.toFixed(1)}%`;
      const activePrint = typeof st.active_print === 'boolean'
        ? st.active_print
        : (/print|printing|running|pause|paused|filament operating/i.test(`${st.status_text || ''} ${st.state || ''}`) && !/idle|ready|standby|complete|finished/i.test(`${st.status_text || ''} ${st.state || ''}`));
      const printSummary = $('#printStatusSummary');
      const printStatePill = $('#summaryPrintState');
      if (printSummary) {
        printSummary.classList.toggle('printing', !!activePrint);
        printSummary.classList.toggle('idle', !activePrint);
      }
      if (printStatePill) {
        printStatePill.className = `summary-print-state ${activePrint ? 'printing' : 'idle'}`;
        printStatePill.textContent = activePrint ? 'PRINTING' : 'IDLE';
        printStatePill.title = activePrint
          ? `Active print · ${progress.toFixed(1)}% complete`
          : 'Printer idle';
      }

      setText('statusText', st.status_text || st.state || 'Unknown');
      renderPortalAI(st.portal_ai || { summary: st.reachable ? 'Standing By' : 'Connection Lost', level: st.reachable ? 'low' : 'watch', risk: st.reachable ? 0 : 35, reasons: [st.message || 'Waiting for printer telemetry.'] });
      setText('printTime', st.print_time || '-');
      setText('timeLeft', st.time_left || '-');
      setText('completion', st.completion || `${progress.toFixed(1)}%`);
      const speedText = st.speed_setting || st.speed_mode_name || (st.speed_percent ? `${st.speed_percent}%` : '-') || '-';
      setText('currentSpeed', speedText);
      setText('currentSpeedBrief', speedText);
      setText('layerProgress', formatDashboardLayer(st));
      renderGcodeThumbnail(st);
      setText('hotendTemp', tempLine(st.hotend_current, st.hotend_target));
      setText('bedTemp', tempLine(st.bed_current, st.bed_target));
      setText('fileName', st.file || '-');
      setText('printerHost', st.host || '-');
      setText('lastUpdate', new Date().toLocaleTimeString());
      if (st.portal_url) setText('portalState', st.portal_url);
      if (st.camera_url) setText('cameraState', st.camera_url);
      renderCameraRelay(st.camera_relay || st.cameraRelay || {});

      const portalButton = $('#portalButton');
      if (portalButton && st.portal_url) portalButton.href = st.portal_url;

      const statusEl = $('#statusText');
      if (statusEl) {
        statusEl.classList.toggle('bad-text', !st.reachable || /error|fail|offline/i.test(st.status_text || st.state || ''));
        statusEl.classList.toggle('good-text', st.reachable && /print|ready|standby|idle/i.test(st.status_text || st.state || ''));
      }
      const ph = $('#cameraPlaceholder');
      const cam = $('#cameraStream');
      if (ph && cam && !cam.classList.contains('hidden')) ph.classList.add('hidden');
    } catch (err) {
      renderPortalAI({ summary: 'Connection Trouble', level: 'high', risk: 75, reasons: [err.message || 'Dashboard could not load printer status.'] });
      const statusEl = $('#statusText');
      if (statusEl) {
        statusEl.textContent = 'Printer Error';
        statusEl.classList.add('bad-text');
      }
      console.warn(err);
    }
  }


  function formatDashboardLayer(status) {
    const toLayerInt = value => {
      if (value === null || value === undefined || value === '') return null;
      const number = Number(value);
      if (!Number.isFinite(number) || number < 0) return null;
      return Math.floor(number);
    };
    const current = toLayerInt(status?.layer_current ?? status?.current_layer ?? status?.currentLayer);
    const total = toLayerInt(status?.layer_total ?? status?.total_layer ?? status?.totalLayers ?? status?.total_layer_count);
    if (current !== null && total !== null && total > 0) return `${current}/${total}`;
    if (current !== null && current > 0) return String(current);
    if (total !== null && total > 0) return `-/${total}`;
    return status?.layer_progress || '-';
  }

  function dashboardAccordionStorageKey() {
    const printerId = document.body.dataset.printerId || 'default';
    return `cc2dash.dashboard.accordions.${printerId}`;
  }

  function initDashboardAccordions() {
    const panels = $$('.dashboard-accordion[data-card]');
    if (!panels.length) return;
    const key = dashboardAccordionStorageKey();
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(key) || '{}') || {};
    } catch {
      saved = {};
    }
    panels.forEach(panel => {
      const id = panel.dataset.card;
      if (Object.prototype.hasOwnProperty.call(saved, id)) {
        panel.open = !!saved[id];
      }
    });
    const persist = () => {
      const state = {};
      panels.forEach(panel => {
        if (panel.dataset.card) state[panel.dataset.card] = !!panel.open;
      });
      try { localStorage.setItem(key, JSON.stringify(state)); } catch {}
    };
    panels.forEach(panel => panel.addEventListener('toggle', persist));
  }

  function initDashboard() {
    initDashboardAccordions();
    initGcodeThumbnailModal();
    refreshDashboard();
    const interval = Number(cfg?.dashboard?.refresh_interval_seconds || 3) * 1000;
    setInterval(refreshDashboard, Math.max(1500, interval));
    const feedbackBox = $('#aiFeedbackButtons');
    if (feedbackBox && cfg?.portal_ai?.feedback_enabled === false) feedbackBox.classList.add('hidden');
    $$('.ai-feedback-button').forEach(btn => {
      btn.addEventListener('click', async () => {
        const label = btn.dataset.aiFeedback || 'unknown';
        const printerId = document.body.dataset.printerId;
        if (!printerId) return toast('No printer configured for AI feedback.', 'warn');
        setButtonBusy(btn, true, 'Saving...');
        try {
          const data = await api(`/api/printers/${encodeURIComponent(printerId)}/ai/feedback`, {
            method:'POST',
            body: JSON.stringify({
              label,
              context: {
                page,
                camera_visible: !!$('#cameraStream') && !$('#cameraStream').classList.contains('hidden'),
                saved_from: 'dashboard_feedback_button',
                user_agent: navigator.userAgent || ''
              }
            })
          });
          const frameMsg = data?.frame?.captured ? (data.frame.fresh ? ' + fresh frame captured' : ' + cached frame saved') : ' (no frame yet)';
          const outcome = data?.interpretation?.outcome ? ` · ${String(data.interpretation.outcome).replace(/_/g, ' ')}` : '';
          const supMsg = data?.suppression ? ' · similar warnings muted for this print' : '';
          toast(`Portal AI feedback saved${frameMsg}${outcome}${supMsg}`, data?.frame?.captured ? 'success' : 'warn', data?.suppression ? 6500 : 4500);
        } catch (err) {
          toast(err.message, 'error', 7000);
        } finally {
          setButtonBusy(btn, false);
        }
      });
    });

    $$('.action-button').forEach(btn => {
      btn.addEventListener('click', async () => {
        const action = btn.dataset.action;
        const requires = btn.dataset.requiresConfirm === 'true';
        if (requires && !confirm(btn.dataset.confirm || 'Are you sure?')) return;
        setButtonBusy(btn, true, btn.dataset.spinnerText || 'Sending...');
        try {
          const body = {};
          if (action === 'set_speed_preset') {
            const select = btn.closest('.speed-action-row')?.querySelector('.speed-preset-select');
            body.params = { mode: Number(select?.value ?? 1) };
          }
          const data = await api(`/api/action/${action}`, { method: 'POST', body: JSON.stringify(body) });
          toast(data.message || 'Command sent', data.ok ? 'success' : 'warn');
          await refreshDashboard();
        } catch (err) {
          toast(err.message, 'error', 7000);
        } finally {
          setButtonBusy(btn, false);
        }
      });
    });
  }


  function renderKioskCameraRelay(relay) {
    relay = relay || {};
    const dot = $('#kioskRelayDot');
    const text = $('#kioskRelayText');
    if (!dot && !text) return;
    const ok = !!relay.ok;
    const running = !!relay.running;
    const stale = !!relay.stale;
    let cls = ok ? 'good' : (running ? 'warn' : 'bad');
    let label = ok ? 'Relay Live' : (running ? 'Relay Warming' : 'Relay Down');
    if (stale && running) label = 'Relay Stale';
    if (dot) dot.className = `dot ${cls}`;
    if (text) text.textContent = label;
  }

  async function refreshKiosk() {
    const printerId = document.body.dataset.printerId;
    const statusUrl = printerId ? `/api/kiosk/status/${encodeURIComponent(printerId)}` : '/api/kiosk/status';
    try {
      const st = await api(statusUrl);
      const progress = Math.max(0, Math.min(100, Number(st.progress || 0)));
      const bar = $('#kioskProgressBar');
      const text = $('#kioskProgressText');
      if (bar) bar.style.width = `${progress}%`;
      if (text) text.textContent = `${progress.toFixed(1)}%`;

      const ai = st.portal_ai || { level: st.reachable ? 'low' : 'watch', risk: st.reachable ? 0 : 35 };
      const aiState = summarizeAIHeaderStatus(ai, ai.vision || ai.vision_ai || st.vision_ai || {});
      const aiBadge = $('#kioskAiBadge');
      if (aiBadge) {
        aiBadge.className = `kiosk-overlay-pill ai ${aiState.tone}`;
        aiBadge.textContent = aiState.label;
        aiBadge.title = `Portal AI: ${aiState.label}`;
      }

      const statusBadge = $('#kioskStatusBadge');
      if (statusBadge) {
        const status = st.status_text || st.state || 'Unknown';
        statusBadge.textContent = `Status: ${status}`;
        statusBadge.classList.toggle('bad', !st.reachable || /error|fail|offline/i.test(status));
        statusBadge.classList.toggle('good', st.reachable && /print|ready|standby|idle/i.test(status));
      }

      setText('kioskTimeLeftBadge', `Left: ${st.time_left || '-'}`);
      setText('kioskPrinterName', st.name || cfg?.app?.name || 'cc2-dash');
      setText('kioskPrinterMeta', st.host || 'connected printer');
      setText('kioskFileName', st.file && st.file !== '-' ? st.file : (st.status_text || st.state || '-'));
      renderKioskCameraRelay(st.camera_relay || st.cameraRelay || {});

      const ph = $('#kioskCameraPlaceholder');
      const cam = $('#kioskCameraStream');
      const relay = st.camera_relay || st.cameraRelay || {};
      if (cam && relay.running !== false && relay.enabled !== false) cam.classList.remove('hidden');
      if (ph && cam && (relay.running || relay.ok || relay.upstream_connected || relay.frames_received > 0)) ph.classList.add('hidden');
      else if (ph && relay.enabled === false) setKioskCameraPlaceholder('Camera relay disabled in settings.', 'bad');
      else if (ph && relay.running === false) setKioskCameraPlaceholder('Camera relay is not running yet.', 'warn');
    } catch (err) {
      const aiBadge = $('#kioskAiBadge');
      if (aiBadge) {
        aiBadge.className = 'kiosk-overlay-pill ai bad';
        aiBadge.textContent = 'Possible failure detected';
        aiBadge.title = err.message || 'Kiosk status refresh failed';
      }
      setText('kioskStatusBadge', 'Status: Error');
      console.warn(err);
    }
  }

  function initKiosk() {
    primeKioskCamera();
    refreshKiosk();
    const interval = Number(cfg?.kiosk?.refresh_interval_seconds || cfg?.dashboard?.refresh_interval_seconds || 3) * 1000;
    setInterval(refreshKiosk, Math.max(1000, interval));
  }

  function refreshConfigEditor() {
    const editor = $('#configEditor');
    if (editor && cfg) editor.value = JSON.stringify(cfg, null, 2);
  }

  async function savePrinter(host, name, portalUrl, cameraUrl, serial, accessCode, options = {}) {
    const redirect = options.redirect !== false;
    const data = await api('/api/printers', {
      method: 'POST',
      body: JSON.stringify({
        host,
        name,
        serial: serial || host,
        access_code: accessCode || '',
        portal_url: portalUrl,
        camera_url: cameraUrl,
        set_default: options.setDefault !== false,
        enabled: true,
        allow_commands: true,
        allow_dangerous_commands: false
      })
    });
    cfg = data.config;
    refreshConfigEditor();
    if (redirect) {
      toast('Printer saved. Opening dashboard...', 'success');
      setTimeout(() => location.href = '/', 600);
    } else {
      toast('Printer saved.', 'success');
      renderSettings();
    }
    return data;
  }

  function renderScanResults(candidates, targetId = 'scanResults', options = {}) {
    const box = $('#' + targetId);
    if (!box) return;
    const redirect = options.redirect !== false;
    box.innerHTML = '';
    if (!candidates.length) {
      box.innerHTML = '<div class="result-item"><strong>No verified printers found</strong><span>Routers and generic web devices are hidden now. Try manual add if broadcast discovery is blocked.</span></div>';
      return;
    }
    candidates.forEach((c, idx) => {
      const item = document.createElement('div');
      item.className = 'result-item verified-printer-result';
      const serial = c.serial || '';
      const model = c.machine_model || c.http_title || 'Centauri Carbon 2';
      const proof = (c.verification_proof || []).filter(Boolean).join(' • ');
      const serialId = `${targetId}Serial${idx}`;
      const pinId = `${targetId}Pin${idx}`;
      item.innerHTML = `
        <strong>Verified Centauri: ${esc(c.host)}</strong>
        <span>Ports: ${esc((c.open_ports || []).join(', ') || 'verified')} • ${esc(model)}</span>
        ${proof ? `<span>Proof: ${esc(proof)}</span>` : `<span>Proof: Centauri discovery response</span>`}
        ${serial ? `<span>Serial: ${esc(serial)}</span>` : `<label class="field-label" for="${serialId}">Serial number</label><input id="${serialId}" class="input scan-serial" placeholder="Printer serial / SN" />`}
        <label class="field-label" for="${pinId}">Printer PIN / access code</label>
        <input id="${pinId}" class="input scan-pin" type="password" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="Printer PIN / access code" />
        <button class="button primary full" style="margin-top:.65rem"><span class="button-label">Pair / Save This Printer</span></button>
      `;
      $('button', item).addEventListener('click', async e => {
        const pin = $('.scan-pin', item)?.value?.trim() || '';
        if (!pin) return toast('Enter the printer PIN / access code first.', 'warn');
        const serialValue = serial || $('.scan-serial', item)?.value?.trim() || c.host;
        setButtonBusy(e.currentTarget, true, 'Pairing...');
        try {
          await savePrinter(c.host, c.host_name || c.machine_model || 'Centauri Carbon 2', c.portal_url, c.camera_url, serialValue, pin, { redirect });
          if (typeof options.afterSave === 'function') options.afterSave(c);
          setButtonBusy(e.currentTarget, false);
        }
        catch (err) { toast(err.message, 'error'); setButtonBusy(e.currentTarget, false); }
      });
      box.appendChild(item);
    });
  }


  function initThemePreviewCards(root = document) {
    $$('.theme-preview-grid', root).forEach(grid => {
      const selectId = grid.dataset.themeTarget;
      const select = selectId ? $('#' + selectId) : null;
      const cards = $$('[data-theme-choice]', grid);
      const sync = () => {
        const value = select?.value || '';
        cards.forEach(card => card.classList.toggle('active', card.dataset.themeChoice === value));
      };
      cards.forEach(card => card.addEventListener('click', () => {
        if (select) {
          select.value = card.dataset.themeChoice || select.value;
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        sync();
      }));
      if (select) select.addEventListener('change', sync);
      sync();
    });
  }

  function initSetup() {
    initThemePreviewCards();
    let setupIndex = 0;
    const setupCards = $$('[data-setup-card]');
    const stepLabel = $('#setupStepLabel');
    const stepPill = $('#setupStepPill');
    const progressBar = $('#setupProgressBar');

    function setupPrinterCount() {
      return Object.keys(cfg?.printers || {}).length;
    }

    function setupSummaryHtml() {
      const printers = Object.entries(cfg?.printers || {});
      const themeId = cfg?.app?.theme || 'default';
      const ai = cfg?.portal_ai || {};
      const access = [
        ...((cfg?.network?.allowed_subnets || []).map(x => `subnet ${x}`)),
        ...((cfg?.network?.allowed_hosts || []).map(x => `host ${x}`)),
      ];
      const printerRows = printers.length
        ? printers.map(([id, p]) => `<div class="result-item"><strong>${esc(p.name || id)}</strong><span>${esc(p.host || '')} · SN ${esc(p.serial || 'not set')} · ${id === cfg?.app?.default_printer ? 'default' : esc(id)}</span></div>`).join('')
        : '<div class="result-item"><strong>No printer saved yet</strong><span>Go back to Scan or Manual Add before finishing.</span></div>';
      return `
        <div class="setup-summary-grid">
          <div><strong>Printer(s)</strong><span>${printers.length}</span></div>
          <div><strong>Theme</strong><span>${esc(themeId)}</span></div>
          <div><strong>Portal AI</strong><span>${ai.enabled ? 'enabled' : 'disabled'}</span></div>
          <div><strong>Vision</strong><span>${ai.vision_ai_enabled ? 'Ollama enabled' : 'telemetry/local only'}</span></div>
        </div>
        <div class="mini-note">Access: ${esc(access.join(' · ') || 'localhost only')}</div>
        <div class="result-list">${printerRows}</div>
      `;
    }

    function setupGo(index) {
      if (!setupCards.length) return;
      setupIndex = Math.max(0, Math.min(setupCards.length - 1, index));
      setupCards.forEach((card, i) => card.classList.toggle('active', i === setupIndex));
      const title = setupCards[setupIndex]?.dataset.stepTitle || '';
      if (stepLabel) stepLabel.textContent = title;
      if (stepPill) stepPill.textContent = `Step ${setupIndex + 1} / ${setupCards.length}`;
      if (progressBar) progressBar.style.width = `${((setupIndex + 1) / setupCards.length) * 100}%`;
      const summary = $('#setupSummary');
      if (summary) summary.innerHTML = setupSummaryHtml();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    async function saveSetupConfig(button, label = 'Saving...') {
      setButtonBusy(button, true, label);
      try {
        const data = await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) });
        cfg = data.config || cfg;
        refreshConfigEditor();
        return data;
      } finally {
        setButtonBusy(button, false);
      }
    }

    function applySetupAppearanceFromInputs() {
      cfg.app = cfg.app || {};
      cfg.appearance = cfg.appearance || {};
      cfg.appearance.fonts = cfg.appearance.fonts || {};
      cfg.app.theme = $('#setupTheme')?.value || cfg.app.theme;
      $$('.setup-card .font-select').forEach(sel => cfg.appearance.fonts[sel.dataset.fontRole] = sel.value);
    }

    function applySetupAccessFromInputs() {
      cfg.network = cfg.network || {};
      cfg.network.allowed_subnets = ($('#setupAllowedSubnets')?.value || '').split('\n').map(x => x.trim()).filter(Boolean);
      cfg.network.allowed_hosts = ($('#setupAllowedHosts')?.value || '').split('\n').map(x => x.trim()).filter(Boolean);
    }

    function applySetupAiFromInputs() {
      cfg.portal_ai = cfg.portal_ai || {};
      cfg.portal_ai.enabled = !!$('#setupPortalAIEnabled')?.checked;
      cfg.portal_ai.background_monitor_enabled = !!$('#setupAIBackgroundEnabled')?.checked;
      cfg.portal_ai.telemetry_rules_enabled = !!$('#setupAITelemetryRules')?.checked;
      cfg.portal_ai.vision_heuristics_enabled = !!$('#setupAIHeuristics')?.checked;
      cfg.portal_ai.vision_ai_enabled = !!$('#setupAIVisionEnabled')?.checked;
      cfg.portal_ai.ollama_base_url = $('#aiOllamaBaseUrl')?.value?.trim() || cfg.portal_ai.ollama_base_url || 'http://localhost:11434';
      cfg.portal_ai.ollama_vision_model = selectedOllamaModel();
      cfg.portal_ai.vision_check_interval_seconds = Number($('#setupVisionInterval')?.value || cfg.portal_ai.vision_check_interval_seconds || 120);
      cfg.portal_ai.vision_required_bad_checks = Number($('#setupVisionBadChecks')?.value || cfg.portal_ai.vision_required_bad_checks || 2);
    }

    loadFreshConfig().then(data => {
      populateFontSelects(data.font_stacks || []);
      populateOllamaModelSelect([], cfg?.portal_ai?.ollama_vision_model || 'llava');
      refreshConfigEditor();
      setupGo(0);
    }).catch(err => toast(err.message, 'error'));

    $$('.setup-next').forEach(btn => btn.addEventListener('click', () => setupGo(setupIndex + 1)));
    $$('.setup-back').forEach(btn => btn.addEventListener('click', () => setupGo(setupIndex - 1)));

    const scanButton = $('#scanButton');
    if (scanButton) scanButton.addEventListener('click', async () => {
      const subnet = $('#scanSubnet').value.trim();
      const scanStatus = $('#scanStatus');
      if (scanStatus) scanStatus.classList.remove('hidden');
      setButtonBusy(scanButton, true, 'Scanning...');
      try {
        const data = await api('/api/scan', { method: 'POST', body: JSON.stringify({ subnet }) });
        renderScanResults(data.candidates || [], 'scanResults', { redirect:false, afterSave: () => setupGo(1) });
        const hidden = Number(data.hidden_count || 0);
        toast(`Scan complete: ${(data.candidates || []).length} verified printer(s)${hidden ? `, ${hidden} non-printer device(s) hidden` : ''}`, 'success');
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        if (scanStatus) scanStatus.classList.add('hidden');
        setButtonBusy(scanButton, false);
      }
    });

    const manual = $('#manualAddButton');
    if (manual) manual.addEventListener('click', async () => {
      const host = $('#manualHost').value.trim();
      const name = $('#manualName').value.trim() || 'Centauri Carbon 2';
      const serial = $('#manualSerial').value.trim() || host;
      const pin = $('#manualPin').value.trim();
      if (!host) return toast('Enter a printer IP first.', 'warn');
      if (!pin) return toast('Enter the printer PIN / access code first.', 'warn');
      setButtonBusy(manual, true, 'Pairing...');
      try {
        await savePrinter(host, name, `http://${host}/`, `http://${host}:8080/`, serial, pin, { redirect:false });
        toast('Manual printer saved.', 'success');
        setupGo(2);
      }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(manual, false); }
    });

    const saveUi = $('#saveSetupUiButton');
    if (saveUi) saveUi.addEventListener('click', async () => {
      applySetupAppearanceFromInputs();
      try { await saveSetupConfig(saveUi, 'Saving UI...'); toast('UI settings saved.', 'success'); setupGo(3); }
      catch (err) { toast(err.message, 'error'); }
    });

    const saveAccess = $('#saveSetupAccessButton');
    if (saveAccess) saveAccess.addEventListener('click', async () => {
      applySetupAccessFromInputs();
      try { await saveSetupConfig(saveAccess, 'Saving access...'); toast('Access settings saved.', 'success'); setupGo(4); }
      catch (err) { toast(err.message, 'error'); }
    });

    const refreshOllamaModels = $('#refreshOllamaModelsButton');
    if (refreshOllamaModels) refreshOllamaModels.addEventListener('click', async () => {
      try {
        const data = await loadOllamaModels(refreshOllamaModels);
        const models = (data.models || []).slice(0, 8).join(', ') || 'No models returned';
        toast(`Ollama models loaded: ${models}`, 'success', 8000);
      } catch (err) { toast(err.message, 'error', 9000); }
    });

    const testOllama = $('#testOllamaButton');
    if (testOllama) testOllama.addEventListener('click', async () => {
      setButtonBusy(testOllama, true, 'Testing...');
      try {
        const data = await loadOllamaModels();
        const model = selectedOllamaModel();
        const present = (data.models || []).includes(model);
        setInlineStatus('ollamaModelStatus', present ? `Ready: ${model} is installed on ${data.base_url}.` : `Ollama is reachable, but ${model} is not in the installed list.`, present ? 'good' : 'warn');
        toast(present ? `Ollama reachable and ${model} is installed.` : `Ollama reachable, but selected model was not listed.`, present ? 'success' : 'warn', 8000);
      } catch (err) { toast(err.message, 'error', 9000); }
      finally { setButtonBusy(testOllama, false); }
    });

    const pullOllamaModel = $('#pullOllamaModelButton');
    if (pullOllamaModel) pullOllamaModel.addEventListener('click', async () => {
      const input = $('#aiOllamaPullModel');
      const model = input?.value?.trim() || selectedOllamaModel();
      if (!model) return toast('Enter a model name to pull.', 'warn');
      if (!confirm(`Pull Ollama model "${model}"? Large models can take a while.`)) return;
      setButtonBusy(pullOllamaModel, true, 'Pulling...');
      setInlineStatus('ollamaModelStatus', `Pulling ${model}...`, '');
      try {
        const data = await api('/api/vision/pull', { method:'POST', body:JSON.stringify({ model, base_url: ollamaBaseUrlFromSettings() }) });
        populateOllamaModelSelect(data.models || [model], model);
        if (input) input.value = '';
        setInlineStatus('ollamaModelStatus', `Pulled ${model}.`, 'good');
        toast(`Pulled ${model}`, 'success', 9000);
      } catch (err) {
        setInlineStatus('ollamaModelStatus', err.message, 'bad');
        toast(err.message, 'error', 12000);
      } finally { setButtonBusy(pullOllamaModel, false); }
    });

    const saveAi = $('#saveSetupAiButton');
    if (saveAi) saveAi.addEventListener('click', async () => {
      applySetupAiFromInputs();
      try { await saveSetupConfig(saveAi, 'Saving AI...'); toast('AI monitoring settings saved.', 'success'); setupGo(5); }
      catch (err) { toast(err.message, 'error'); }
    });

    const finish = $('#finishSetupButton');
    if (finish) finish.addEventListener('click', async () => {
      try {
        await loadFreshConfig();
        if (!setupPrinterCount()) {
          toast('Add or scan at least one printer before finishing setup.', 'warn', 8000);
          setupGo(0);
          return;
        }
        setButtonBusy(finish, true, 'Launching...');
        await api('/api/setup/finish', { method:'POST' });
        toast('Setup complete. Opening dashboard...', 'success');
        setTimeout(() => location.href = '/', 450);
      } catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(finish, false); }
    });
  }

  async function loadFreshConfig() {
    const data = await api('/api/config');
    cfg = data.config;
    return data;
  }

  function populateFontSelects(fonts) {
    $$('.font-select').forEach(sel => {
      const role = sel.dataset.fontRole;
      sel.innerHTML = fonts.map(f => `<option value="${f}">${f}</option>`).join('');
      sel.value = cfg?.appearance?.fonts?.[role] || cfg?.appearance?.font_pack || 'Terminal Modern';
    });
  }

  function renderSettings() {
    const cardBox = $('#cardSettings');
    if (cardBox) {
      cardBox.innerHTML = (cfg.dashboard.cards || []).map(c => `
        <div class="setting-row" data-card-id="${c.id}">
          <div><strong>${c.label || c.id}</strong><small>${c.id}</small></div>
          <div class="setting-controls">
            <label><input class="toggle card-enabled" type="checkbox" ${c.enabled ? 'checked' : ''}> show</label>
            <input class="input card-order" type="number" value="${c.order ?? 99}">
          </div>
        </div>
      `).join('');
    }

    const actionBox = $('#actionSettings');
    if (actionBox) {
      actionBox.innerHTML = Object.entries(cfg.actions || {}).sort((a,b)=>(a[1].order||99)-(b[1].order||99)).map(([id,a]) => `
        <div class="setting-row" data-action-id="${id}">
          <div><strong>${esc(a.label || id)}</strong><small>${esc(id)}${id === 'set_speed_preset' ? ' · preset is chosen from the dashboard button' : ''}</small></div>
          <div class="setting-controls action-controls">
            <input class="input action-label" type="text" value="${esc(a.label || id)}" title="Button label">
            <label><input class="toggle action-visible" type="checkbox" ${a.visible ? 'checked' : ''}> visible</label>
            <label><input class="toggle action-confirm" type="checkbox" ${a.requires_confirm ? 'checked' : ''}> confirm</label>
            <input class="input action-order" type="number" value="${a.order ?? 99}" title="Order">
          </div>
        </div>`).join('');
    }

    const printerBox = $('#printerSettings');
    if (printerBox) {
      const entries = Object.entries(cfg.printers || {});
      printerBox.innerHTML = entries.length ? entries.map(([id,p]) => `
        <div class="printer-config-card" data-printer-id="${esc(id)}">
          <div class="printer-config-head">
            <div><strong>${esc(p.name || id)}</strong><small>${esc(p.host || '')} • SN: ${esc(p.serial || 'unknown')}</small></div>
            <span class="pill">${cfg.app.default_printer === id ? 'Default' : esc(id)}</span>
          </div>
          <div class="grid-2 gap printer-edit-grid">
            <label class="inline-field"><span class="field-label">Display name</span><input class="input printer-name" value="${esc(p.name || '')}" /></label>
            <label class="inline-field"><span class="field-label">Host / IP</span><input class="input printer-host" value="${esc(p.host || '')}" /></label>
            <label class="inline-field"><span class="field-label">Serial / SN</span><input class="input printer-serial" value="${esc(p.serial || '')}" /></label>
            <label class="inline-field"><span class="field-label">PIN / access code</span><input class="input printer-pin" type="password" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="leave blank to keep saved" /></label>
            <label class="inline-field"><span class="field-label">MQTT port</span><input class="input printer-port" type="number" min="1" max="65535" value="${esc(p.port || 1883)}" /></label>
          </div>
          <div class="printer-toggle-row">
            <label><input class="toggle printer-enabled" type="checkbox" ${p.enabled !== false ? 'checked' : ''}> enabled</label>
            <label><input class="toggle printer-commands" type="checkbox" ${p.allow_commands !== false ? 'checked' : ''}> commands</label>
            <label><input class="toggle printer-danger" type="checkbox" ${p.allow_dangerous_commands ? 'checked' : ''}> dangerous</label>
          </div>
          <div class="printer-action-row">
            <button class="button primary tiny printer-save"><span class="button-label">Save</span></button>
            <button class="button secondary tiny printer-default" ${cfg.app.default_printer === id ? 'disabled' : ''}><span class="button-label">Make Default</span></button>
            <button class="button danger tiny printer-delete"><span class="button-label">Remove</span></button>
          </div>
        </div>
      `).join('') : '<div class="result-item"><strong>No printers configured</strong><span>Scan or manually add one above.</span></div>';
      bindPrinterManagerRows();
    }
  }

  function bindPrinterManagerRows() {
    $$('#printerSettings [data-printer-id]').forEach(row => {
      const id = row.dataset.printerId;
      $('.printer-save', row)?.addEventListener('click', async e => {
        const body = {
          name: $('.printer-name', row)?.value?.trim() || 'Centauri Carbon 2',
          host: $('.printer-host', row)?.value?.trim() || '',
          serial: $('.printer-serial', row)?.value?.trim() || '',
          port: Number($('.printer-port', row)?.value || 1883),
          enabled: !!$('.printer-enabled', row)?.checked,
          allow_commands: !!$('.printer-commands', row)?.checked,
          allow_dangerous_commands: !!$('.printer-danger', row)?.checked,
        };
        const pin = $('.printer-pin', row)?.value?.trim();
        if (pin) body.access_code = pin;
        if (!body.host) return toast('Printer host/IP is required.', 'warn');
        setButtonBusy(e.currentTarget, true, 'Saving...');
        try {
          const data = await api(`/api/printers/${encodeURIComponent(id)}`, { method:'PATCH', body:JSON.stringify(body) });
          cfg = data.config || cfg;
          refreshConfigEditor();
          renderSettings();
          toast('Printer saved.', 'success');
        } catch (err) { toast(err.message, 'error'); }
        finally { setButtonBusy(e.currentTarget, false); }
      });
      $('.printer-default', row)?.addEventListener('click', async e => {
        setButtonBusy(e.currentTarget, true, 'Saving...');
        try {
          const data = await api(`/api/printers/${encodeURIComponent(id)}/default`, { method:'POST' });
          cfg = data.config || cfg;
          refreshConfigEditor();
          renderSettings();
          toast('Default printer updated.', 'success');
        } catch (err) { toast(err.message, 'error'); }
        finally { setButtonBusy(e.currentTarget, false); }
      });
      $('.printer-delete', row)?.addEventListener('click', async e => {
        const name = $('.printer-name', row)?.value?.trim() || id;
        if (!confirm(`Remove printer "${name}"?`)) return;
        setButtonBusy(e.currentTarget, true, 'Removing...');
        try {
          const data = await api(`/api/printers/${encodeURIComponent(id)}`, { method:'DELETE' });
          cfg = data.config || cfg;
          refreshConfigEditor();
          renderSettings();
          toast('Printer removed.', 'success');
        } catch (err) { toast(err.message, 'error'); }
        finally { setButtonBusy(e.currentTarget, false); }
      });
    });
  }



  function setInlineStatus(id, message, tone = '') {
    const el = $('#' + id);
    if (!el) return;
    el.textContent = message;
    el.className = `inline-status ${tone || ''}`.trim();
  }

  function fmtLearningNumber(value, digits = 3) {
    const n = Number(value);
    if (!Number.isFinite(n)) return value === 0 ? '0' : '-';
    if (Number.isInteger(n)) return String(n);
    return n.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '');
  }

  function signedLearningNumber(value, digits = 3) {
    const n = Number(value);
    if (!Number.isFinite(n) || n === 0) return '0';
    return `${n > 0 ? '+' : ''}${fmtLearningNumber(n, digits)}`;
  }

  function collectAiLearningSettings() {
    cfg.portal_ai = cfg.portal_ai || {};
    const mode = $('#aiLearningMode')?.value || cfg.portal_ai.ai_feedback_learning_mode || 'suggest_only';
    cfg.portal_ai.ai_feedback_learning_enabled = !!$('#aiLearningEnabled')?.checked && mode !== 'off';
    cfg.portal_ai.ai_feedback_learning_mode = mode;
    cfg.portal_ai.ai_learning_min_samples = Number($('#aiLearningMinSamples')?.value || 8);
    cfg.portal_ai.ai_learning_min_false_positives = Number($('#aiLearningMinFalsePositives')?.value || 4);
    cfg.portal_ai.ai_learning_min_false_negatives = Number($('#aiLearningMinFalseNegatives')?.value || 2);
    cfg.portal_ai.ai_learning_max_dark_luma_adjustment = Number($('#aiLearningMaxDarkLuma')?.value || 8);
    cfg.portal_ai.ai_learning_max_edge_density_adjustment = Number($('#aiLearningMaxEdgeDensity')?.value || 0.05);
    cfg.portal_ai.ai_learning_max_required_bad_checks_adjustment = Number($('#aiLearningMaxBadChecks')?.value || 1);
    cfg.portal_ai.ai_learning_apply_dark_luma = !!$('#aiLearningApplyDarkLuma')?.checked;
    cfg.portal_ai.ai_learning_apply_edge_density = !!$('#aiLearningApplyEdgeDensity')?.checked;
    cfg.portal_ai.ai_learning_apply_required_bad_checks = !!$('#aiLearningApplyBadChecks')?.checked;
    cfg.portal_ai.ai_learning_rebuild_on_feedback = !!$('#aiLearningRebuildOnFeedback')?.checked;
    cfg.portal_ai.ai_learning_keep_jsonl_audit_log = true;
  }

  function thresholdCell(label, value, digits = 3) {
    return `<div class="ai-learning-threshold"><span>${esc(label)}</span><strong>${esc(fmtLearningNumber(value, digits))}</strong></div>`;
  }

  function renderAiLearningProfile(profile) {
    profile = profile || {};
    const thresholds = profile.thresholds || {};
    const manual = thresholds.manual || {};
    const suggested = thresholds.suggested || {};
    const applied = thresholds.applied || {};
    const effective = thresholds.effective || {};
    const outcomes = profile.outcomes || {};
    const baselines = profile.normal_baselines || {};
    const luma = baselines.luma || {};
    const contrast = baselines.contrast || {};
    const edge = baselines.edge_density || {};
    const confidence = String(profile.confidence || 'none').toLowerCase();
    const reasons = (profile.reasons || []).slice(0, 4);

    const metricRows = [
      ['Dark luma', manual.dark_luma, signedLearningNumber(suggested.dark_luma_modifier, 2), signedLearningNumber(applied.dark_luma_modifier, 2), effective.dark_luma, 2],
      ['Fine-edge density', manual.edge_density, signedLearningNumber(suggested.edge_density_modifier, 3), signedLearningNumber(applied.edge_density_modifier, 3), effective.edge_density, 3],
      ['Bad checks', manual.required_bad_checks, signedLearningNumber(suggested.required_bad_checks_modifier, 0), signedLearningNumber(applied.required_bad_checks_modifier, 0), effective.required_bad_checks, 0],
    ].map(row => `
      <div class="ai-learning-metric-row">
        <div class="ai-learning-metric-name">${esc(row[0])}</div>
        ${thresholdCell('manual', row[1], row[5])}
        ${thresholdCell('suggested', row[2], row[5])}
        ${thresholdCell('applied', row[3], row[5])}
        ${thresholdCell('effective', row[4], row[5])}
      </div>
    `).join('');

    return `
      <article class="ai-learning-card" data-printer-id="${esc(profile.printer_id || '')}">
        <div class="ai-learning-card-header">
          <div>
            <strong>${esc(profile.printer_id || 'unknown printer')}</strong>
            <small>${esc(profile.message || 'Learning profile loaded.')}</small>
          </div>
          <span class="ai-learning-badge ${esc(confidence)}">${esc((profile.mode || 'suggest_only').replaceAll('_', ' '))} · ${esc(confidence)}</span>
        </div>
        <div class="ai-learning-outcomes">
          ${thresholdCell('samples', profile.sample_count, 0)}
          ${thresholdCell('true +', outcomes.true_positive, 0)}
          ${thresholdCell('false +', outcomes.false_positive, 0)}
          ${thresholdCell('false -', outcomes.false_negative, 0)}
          ${thresholdCell('true -', outcomes.true_negative, 0)}
        </div>
        <div class="ai-learning-thresholds">
          ${metricRows}
        </div>
        <div class="ai-learning-baselines">
          ${thresholdCell('normal luma median', luma.median, 2)}
          ${thresholdCell('normal luma p10/p90', `${fmtLearningNumber(luma.p10, 2)} / ${fmtLearningNumber(luma.p90, 2)}`, 2)}
          ${thresholdCell('normal contrast median', contrast.median, 2)}
          ${thresholdCell('edge p90/p95', `${fmtLearningNumber(edge.p90, 3)} / ${fmtLearningNumber(edge.p95, 3)}`, 3)}
        </div>
        ${reasons.length ? `<ul class="ai-learning-reasons">${reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : '<div class="ai-learning-message">No learning reason text yet.</div>'}
      </article>
    `;
  }

  function renderAiLearningStatus(data) {
    const panel = $('#aiLearningPanel');
    const summary = $('#aiLearningStatusSummary');
    if (!panel) return;
    const learning = data?.learning || {};
    const db = data?.database || {};
    const profiles = learning.profiles || [];
    if (summary) {
      summary.textContent = `${learning.enabled ? 'Enabled' : 'Off'} · ${String(learning.mode || 'suggest_only').replaceAll('_', ' ')} · ${profiles.length} profile(s) · ${db.feedback_samples || 0} sample(s)`;
    }
    if (!profiles.length) {
      panel.innerHTML = '<div class="ai-learning-empty">No printer learning profiles yet. Click feedback during prints, then rebuild profiles here.</div>';
      return;
    }
    panel.innerHTML = profiles.map(renderAiLearningProfile).join('');
  }

  async function refreshAiLearningStatus(button = null) {
    setButtonBusy(button, true, 'Refreshing...');
    try {
      const data = await api('/api/ai/learning/status');
      renderAiLearningStatus(data);
      return data;
    } catch (err) {
      setInlineStatus('aiLearningStatusSummary', err.message, 'bad');
      throw err;
    } finally {
      setButtonBusy(button, false);
    }
  }

  function ollamaBaseUrlFromSettings() {
    return $('#aiOllamaBaseUrl')?.value?.trim() || cfg?.portal_ai?.ollama_base_url || 'http://localhost:11434';
  }

  function selectedOllamaModel() {
    return $('#aiOllamaVisionModel')?.value?.trim() || cfg?.portal_ai?.ollama_vision_model || 'llava';
  }

  function populateOllamaModelSelect(models, preferred) {
    const select = $('#aiOllamaVisionModel');
    if (!select) return;
    const current = preferred || select.value || select.dataset.currentModel || cfg?.portal_ai?.ollama_vision_model || 'llava';
    const unique = Array.from(new Set([current, ...(models || [])].filter(Boolean)));
    select.innerHTML = unique.map(m => `<option value="${esc(m)}">${esc(m)}${m === current && !(models || []).includes(m) ? ' (saved)' : ''}</option>`).join('');
    select.value = current;
  }

  async function loadOllamaModels(button = null) {
    const base = ollamaBaseUrlFromSettings();
    const current = selectedOllamaModel();
    setButtonBusy(button, true, 'Loading...');
    setInlineStatus('ollamaModelStatus', 'Contacting Ollama...', '');
    try {
      const data = await api(`/api/vision/models?base_url=${encodeURIComponent(base)}`);
      const models = data.models || [];
      populateOllamaModelSelect(models, current);
      setInlineStatus('ollamaModelStatus', models.length ? `Loaded ${models.length} model(s) from ${data.base_url || base}.` : `Ollama responded, but no models were returned.`, models.length ? 'good' : 'warn');
      return data;
    } catch (err) {
      setInlineStatus('ollamaModelStatus', err.message, 'bad');
      throw err;
    } finally {
      setButtonBusy(button, false);
    }
  }

  function initSettings() {
    initThemePreviewCards();
    loadFreshConfig().then(data => {
      populateFontSelects(data.font_stacks || []);
      renderSettings();
      populateOllamaModelSelect([], cfg?.portal_ai?.ollama_vision_model || 'llava');
      refreshConfigEditor();
    }).catch(err => toast(err.message, 'error'));

    const managerScan = $('#managerScanButton');
    if (managerScan) managerScan.addEventListener('click', async () => {
      const subnet = $('#managerScanSubnet')?.value?.trim() || cfg?.network?.allowed_subnets?.[0] || '192.168.1.0/24';
      const scanStatus = $('#managerScanStatus');
      if (scanStatus) scanStatus.classList.remove('hidden');
      setButtonBusy(managerScan, true, 'Scanning...');
      try {
        const data = await api('/api/scan', { method:'POST', body:JSON.stringify({ subnet }) });
        renderScanResults(data.candidates || [], 'managerScanResults', { redirect:false });
        const hidden = Number(data.hidden_count || 0);
        toast(`Scan complete: ${(data.candidates || []).length} verified printer(s)${hidden ? `, ${hidden} non-printer device(s) hidden` : ''}`, 'success');
      } catch (err) { toast(err.message, 'error'); }
      finally {
        if (scanStatus) scanStatus.classList.add('hidden');
        setButtonBusy(managerScan, false);
      }
    });

    const managerManual = $('#managerManualAddButton');
    if (managerManual) managerManual.addEventListener('click', async () => {
      const host = $('#managerManualHost')?.value?.trim() || '';
      const name = $('#managerManualName')?.value?.trim() || 'Centauri Carbon 2';
      const serial = $('#managerManualSerial')?.value?.trim() || host;
      const pin = $('#managerManualPin')?.value?.trim() || '';
      if (!host) return toast('Enter a printer IP/host first.', 'warn');
      if (!pin) return toast('Enter the printer PIN / access code first.', 'warn');
      setButtonBusy(managerManual, true, 'Saving...');
      try {
        await savePrinter(host, name, `http://${host}/`, `http://${host}:8080/`, serial, pin, { redirect:false });
        $('#managerManualHost').value = '';
        $('#managerManualSerial').value = '';
      } catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(managerManual, false); }
    });

    const saveTheme = $('#saveThemeButton');
    if (saveTheme) saveTheme.addEventListener('click', async () => {
      cfg.app.theme = $('#themeSelect').value;
      cfg.appearance.fonts = cfg.appearance.fonts || {};
      $$('.font-select').forEach(sel => cfg.appearance.fonts[sel.dataset.fontRole] = sel.value);
      setButtonBusy(saveTheme, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Appearance saved. Reloading...', 'success'); setTimeout(()=>location.reload(), 500); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveTheme, false); }
    });

    const saveLayout = $('#saveLayoutButton');
    if (saveLayout) saveLayout.addEventListener('click', async () => {
      $$('#cardSettings [data-card-id]').forEach(row => {
        const id = row.dataset.cardId;
        const c = cfg.dashboard.cards.find(x => x.id === id);
        if (c) { c.enabled = $('.card-enabled', row).checked; c.order = Number($('.card-order', row).value || 99); }
      });
      setButtonBusy(saveLayout, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Layout saved', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveLayout, false); }
    });

    const saveMenu = $('#saveMenuButton');
    if (saveMenu) saveMenu.addEventListener('click', async () => {
      cfg.features = cfg.features || {};
      cfg.features.file_manager_enabled = !!$('#fileManagerEnabled')?.checked;
      cfg.features.filament_manager_enabled = !!$('#filamentManagerEnabled')?.checked;
      cfg.features.kiosk_enabled = !!$('#kioskMenuEnabled')?.checked;
      setButtonBusy(saveMenu, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Menu settings saved. Reloading...', 'success'); setTimeout(()=>location.reload(), 500); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveMenu, false); }
    });

    async function refreshCameraProxyStatus(button = null) {
      setButtonBusy(button, true, 'Checking...');
      try {
        const data = await api('/api/camera/status');
        const relays = data.relays || {};
        const rows = Object.entries(relays);
        if (!rows.length) {
          setInlineStatus('cameraProxyStatus', 'No camera relays have been created yet. Save/start the relay or open the dashboard.', 'warn');
          return data;
        }
        const summary = rows.map(([id, r]) => {
          const age = Number(r.last_frame_age_seconds);
          const ageText = Number.isFinite(age) ? `${age.toFixed(age < 10 ? 1 : 0)}s` : 'no frame';
          return `${id}: ${r.ok ? 'OK' : (r.running ? 'warming/stale' : 'down')} · ${r.client_count || 0} clients · ${ageText} · ${r.reconnects || 0} reconnects`;
        }).join(' | ');
        setInlineStatus('cameraProxyStatus', summary, rows.every(([, r]) => r.ok) ? 'good' : 'warn');
        return data;
      } catch (err) {
        setInlineStatus('cameraProxyStatus', err.message, 'bad');
        throw err;
      } finally {
        setButtonBusy(button, false);
      }
    }

    const refreshCameraProxy = $('#refreshCameraProxyStatusButton');
    if (refreshCameraProxy) refreshCameraProxy.addEventListener('click', async () => {
      try { await refreshCameraProxyStatus(refreshCameraProxy); }
      catch (err) { toast(err.message, 'error'); }
    });

    const saveCameraProxy = $('#saveCameraProxyButton');
    if (saveCameraProxy) saveCameraProxy.addEventListener('click', async () => {
      cfg.camera_proxy = cfg.camera_proxy || {};
      cfg.camera_proxy.enabled = !!$('#cameraProxyEnabled')?.checked;
      cfg.camera_proxy.start_on_boot = !!$('#cameraProxyStartOnBoot')?.checked;
      cfg.camera_proxy.max_client_fps = Number($('#cameraProxyMaxFps')?.value || 8);
      cfg.camera_proxy.stale_frame_seconds = Number($('#cameraProxyStaleSeconds')?.value || 10);
      cfg.camera_proxy.upstream_connect_timeout_seconds = Number($('#cameraProxyConnectTimeout')?.value || 5);
      cfg.camera_proxy.upstream_read_timeout_seconds = Number($('#cameraProxyReadTimeout')?.value || 20);
      cfg.camera_proxy.rewrite_portal_camera_urls = !!$('#cameraProxyRewritePortal')?.checked;
      cfg.camera_proxy.fallback_to_direct = !!$('#cameraProxyFallbackDirect')?.checked;
      setButtonBusy(saveCameraProxy, true, 'Saving...');
      try {
        await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) });
        toast('Camera relay settings saved.', 'success');
        await refreshCameraProxyStatus();
      }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveCameraProxy, false); }
    });

    refreshCameraProxyStatus().catch(() => {});

    function applySettingsFormToConfig() {
      cfg.app = cfg.app || {};
      cfg.appearance = cfg.appearance || {};
      cfg.dashboard = cfg.dashboard || {};
      cfg.features = cfg.features || {};
      cfg.camera_proxy = cfg.camera_proxy || {};
      cfg.kiosk = cfg.kiosk || {};
      cfg.portal_ai = cfg.portal_ai || {};
      cfg.actions = cfg.actions || {};
      cfg.network = cfg.network || {};

      const themeSelect = $('#themeSelect');
      if (themeSelect) cfg.app.theme = themeSelect.value;
      cfg.appearance.fonts = cfg.appearance.fonts || {};
      $$('.font-select').forEach(sel => cfg.appearance.fonts[sel.dataset.fontRole] = sel.value);

      $$('#cardSettings [data-card-id]').forEach(row => {
        const id = row.dataset.cardId;
        const c = (cfg.dashboard.cards || []).find(x => x.id === id);
        if (c) {
          c.enabled = !!$('.card-enabled', row)?.checked;
          c.order = Number($('.card-order', row)?.value || 99);
        }
      });

      const showThumb = $('#dashboardShowGcodeThumbnail');
      if (showThumb) cfg.dashboard.show_gcode_thumbnail = !!showThumb.checked;

      cfg.features.file_manager_enabled = !!$('#fileManagerEnabled')?.checked;
      cfg.features.filament_manager_enabled = !!$('#filamentManagerEnabled')?.checked;
      cfg.features.kiosk_enabled = !!$('#kioskMenuEnabled')?.checked;

      cfg.kiosk.refresh_interval_seconds = Number($('#kioskRefreshInterval')?.value || 3);
      cfg.kiosk.camera_fit = $('#kioskCameraFit')?.value || 'contain';
      cfg.kiosk.show_top_nav = !!$('#kioskShowTopNav')?.checked;
      cfg.kiosk.show_printer_name = !!$('#kioskShowPrinterName')?.checked;
      cfg.kiosk.show_camera_badge = !!$('#kioskShowCameraBadge')?.checked;
      cfg.kiosk.show_progress = !!$('#kioskShowProgress')?.checked;
      cfg.kiosk.show_ai_status = !!$('#kioskShowAiStatus')?.checked;
      cfg.kiosk.show_time_left = !!$('#kioskShowTimeLeft')?.checked;
      cfg.kiosk.show_print_status = !!$('#kioskShowPrintStatus')?.checked;

      cfg.camera_proxy.enabled = !!$('#cameraProxyEnabled')?.checked;
      cfg.camera_proxy.start_on_boot = !!$('#cameraProxyStartOnBoot')?.checked;
      cfg.camera_proxy.max_client_fps = Number($('#cameraProxyMaxFps')?.value || 8);
      cfg.camera_proxy.stale_frame_seconds = Number($('#cameraProxyStaleSeconds')?.value || 10);
      cfg.camera_proxy.upstream_connect_timeout_seconds = Number($('#cameraProxyConnectTimeout')?.value || 5);
      cfg.camera_proxy.upstream_read_timeout_seconds = Number($('#cameraProxyReadTimeout')?.value || 20);
      cfg.camera_proxy.rewrite_portal_camera_urls = !!$('#cameraProxyRewritePortal')?.checked;
      cfg.camera_proxy.fallback_to_direct = !!$('#cameraProxyFallbackDirect')?.checked;

      cfg.portal_ai.enabled = !!$('#portalAIEnabled')?.checked;
      cfg.portal_ai.background_monitor_enabled = !!$('#aiBackgroundMonitorEnabled')?.checked;
      cfg.portal_ai.check_interval_seconds = Number($('#aiCheckIntervalSeconds')?.value || 30);
      cfg.portal_ai.background_log_changes = !!$('#aiBackgroundLogChanges')?.checked;
      cfg.portal_ai.background_min_log_level = $('#aiBackgroundMinLogLevel')?.value || 'watch';
      cfg.portal_ai.telemetry_rules_enabled = !!$('#aiTelemetryRules')?.checked;
      cfg.portal_ai.camera_rules_enabled = !!$('#aiCameraRules')?.checked;
      cfg.portal_ai.vision_ai_enabled = !!$('#aiVisionEnabled')?.checked;
      cfg.portal_ai.ollama_base_url = $('#aiOllamaBaseUrl')?.value?.trim() || 'http://localhost:11434';
      cfg.portal_ai.ollama_vision_model = $('#aiOllamaVisionModel')?.value?.trim() || 'llava';
      cfg.portal_ai.vision_check_interval_seconds = Number($('#aiVisionCheckInterval')?.value || 120);
      cfg.portal_ai.vision_require_active_print = !!$('#aiVisionRequireActivePrint')?.checked;
      cfg.portal_ai.vision_heuristics_enabled = !!$('#aiVisionHeuristicsEnabled')?.checked;
      cfg.portal_ai.vision_treat_benign_uncertain_as_ok = !!$('#aiVisionBenignUncertainOk')?.checked;
      cfg.portal_ai.vision_dark_mean_threshold = Number($('#aiVisionDarkMeanThreshold')?.value || 42);
      const darkDrop = $('#aiVisionDarkDropThreshold');
      if (darkDrop) cfg.portal_ai.vision_dark_relative_drop_threshold = Number(darkDrop.value || 18);
      cfg.portal_ai.vision_stringing_edge_density_threshold = Number($('#aiVisionStringingEdgeThreshold')?.value || 0.125);
      cfg.portal_ai.vision_confidence_threshold = Number($('#aiVisionConfidenceThreshold')?.value || 70);
      cfg.portal_ai.vision_severity_threshold = Number($('#aiVisionSeverityThreshold')?.value || 60);
      cfg.portal_ai.vision_required_bad_checks = Number($('#aiVisionRequiredBadChecks')?.value || 2);
      cfg.portal_ai.vision_prompt = $('#aiVisionPrompt')?.value || cfg.portal_ai.vision_prompt || '';
      cfg.portal_ai.progress_stuck_minutes = Number($('#aiProgressStuckMinutes')?.value || 8);
      cfg.portal_ai.multi_color_mode = $('#aiMultiColorMode')?.value || 'auto';
      cfg.portal_ai.multi_color_progress_stuck_minutes = Number($('#aiMultiColorStuckMinutes')?.value || 30);
      cfg.portal_ai.stale_status_seconds = Number($('#aiStaleStatusSeconds')?.value || 75);
      cfg.portal_ai.feedback_enabled = !!$('#aiFeedbackEnabled')?.checked;
      cfg.portal_ai.feedback_suppression_enabled = !!$('#aiFeedbackSuppressionEnabled')?.checked;
      cfg.portal_ai.feedback_suppression_ttl_hours = Number($('#aiFeedbackSuppressionTtlHours')?.value || 18);
      cfg.portal_ai.feedback_suppression_max_severity = Number($('#aiFeedbackSuppressionMaxSeverity')?.value || 65);
      collectAiLearningSettings();
      cfg.portal_ai.auto_pause_enabled = !!$('#aiAutoPauseEnabled')?.checked;
      cfg.portal_ai.auto_pause_threshold = Number($('#aiAutoPauseThreshold')?.value || 90);

      $$('#actionSettings [data-action-id]').forEach(row => {
        const id = row.dataset.actionId;
        const a = cfg.actions[id];
        if (a) {
          const oldLabel = String(a.label || id);
          a.label = $('.action-label', row)?.value?.trim() || oldLabel;
          a.visible = !!$('.action-visible', row)?.checked;
          a.requires_confirm = !!$('.action-confirm', row)?.checked;
          a.order = Number($('.action-order', row)?.value || 99);
          if (id === 'set_speed_preset') {
            delete a.preset_mode;
            delete a.preset_name;
          }
        }
      });

      const allowedSubnets = $('#allowedSubnets');
      if (allowedSubnets) cfg.network.allowed_subnets = allowedSubnets.value.split('\n').map(x => x.trim()).filter(Boolean);
      const allowedHosts = $('#allowedHosts');
      if (allowedHosts) cfg.network.allowed_hosts = allowedHosts.value.split('\n').map(x => x.trim()).filter(Boolean);
      return cfg;
    }

    async function saveAllSettings(button = null) {
      const rawOverride = !!$('#useRawJsonOnSave')?.checked;
      setButtonBusy(button, true, 'Saving...');
      try {
        if (rawOverride) {
          try { cfg = JSON.parse($('#configEditor')?.value || '{}'); }
          catch (err) { throw new Error('Invalid raw JSON: ' + err.message); }
        } else {
          applySettingsFormToConfig();
          refreshConfigEditor();
        }
        await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) });
        toast('All settings saved. Reloading...', 'success');
        setTimeout(() => location.reload(), 550);
      } catch (err) {
        toast(err.message, 'error', 9000);
      } finally {
        setButtonBusy(button, false);
      }
    }

    ['saveAllSettingsButton', 'saveAllSettingsButtonBottom'].forEach(id => {
      const btn = $('#' + id);
      if (btn) btn.addEventListener('click', () => saveAllSettings(btn));
    });
    ['cancelSettingsButton', 'cancelSettingsButtonBottom'].forEach(id => {
      const btn = $('#' + id);
      if (btn) btn.addEventListener('click', () => {
        if (confirm('Discard unsaved settings and reload the saved config?')) location.reload();
      });
    });

    const refreshOllamaModels = $('#refreshOllamaModelsButton');
    if (refreshOllamaModels) refreshOllamaModels.addEventListener('click', async () => {
      try {
        const data = await loadOllamaModels(refreshOllamaModels);
        const models = (data.models || []).slice(0, 8).join(', ') || 'No models returned';
        toast(`Ollama models loaded: ${models}`, 'success', 8000);
      } catch (err) {
        toast(err.message, 'error', 9000);
      }
    });

    const testOllama = $('#testOllamaButton');
    if (testOllama) testOllama.addEventListener('click', async () => {
      setButtonBusy(testOllama, true, 'Testing...');
      try {
        const data = await loadOllamaModels();
        const model = selectedOllamaModel();
        const present = (data.models || []).includes(model);
        setInlineStatus('ollamaModelStatus', present ? `Ready: ${model} is installed on ${data.base_url}.` : `Ollama is reachable, but ${model} is not in the installed list.`, present ? 'good' : 'warn');
        toast(present ? `Ollama reachable and ${model} is installed.` : `Ollama reachable, but selected model was not listed.`, present ? 'success' : 'warn', 8000);
      } catch (err) {
        toast(err.message, 'error', 9000);
      } finally {
        setButtonBusy(testOllama, false);
      }
    });

    const pullOllamaModel = $('#pullOllamaModelButton');
    if (pullOllamaModel) pullOllamaModel.addEventListener('click', async () => {
      const input = $('#aiOllamaPullModel');
      const model = input?.value?.trim() || selectedOllamaModel();
      if (!model) return toast('Enter a model name to pull.', 'warn');
      if (!confirm(`Pull Ollama model "${model}"? Large models can take a while.`)) return;
      setButtonBusy(pullOllamaModel, true, 'Pulling...');
      setInlineStatus('ollamaModelStatus', `Pulling ${model}...`, '');
      try {
        const data = await api('/api/vision/pull', { method:'POST', body:JSON.stringify({ model, base_url: ollamaBaseUrlFromSettings() }) });
        populateOllamaModelSelect(data.models || [model], model);
        if (input) input.value = '';
        setInlineStatus('ollamaModelStatus', `Pulled ${model}.`, 'good');
        toast(`Pulled ${model}`, 'success', 9000);
      } catch (err) {
        setInlineStatus('ollamaModelStatus', err.message, 'bad');
        toast(err.message, 'error', 12000);
      } finally {
        setButtonBusy(pullOllamaModel, false);
      }
    });

    const refreshAiLearning = $('#refreshAiLearningButton');
    if (refreshAiLearning) refreshAiLearning.addEventListener('click', async () => {
      try { await refreshAiLearningStatus(refreshAiLearning); }
      catch (err) { toast(err.message, 'error', 9000); }
    });

    const rebuildAiLearning = $('#rebuildAiLearningButton');
    if (rebuildAiLearning) rebuildAiLearning.addEventListener('click', async () => {
      setButtonBusy(rebuildAiLearning, true, 'Rebuilding...');
      try {
        const data = await api('/api/ai/learning/rebuild', { method:'POST', body:JSON.stringify({}) });
        renderAiLearningStatus({ ok:true, database:data.database || {}, learning:{ profiles:data.profiles || [], mode:cfg?.portal_ai?.ai_feedback_learning_mode || 'suggest_only', enabled:!!cfg?.portal_ai?.ai_feedback_learning_enabled } });
        await refreshAiLearningStatus();
        toast(`Rebuilt ${data.count || 0} AI learning profile(s).`, 'success');
      } catch (err) {
        toast(err.message, 'error', 9000);
      } finally {
        setButtonBusy(rebuildAiLearning, false);
      }
    });

    const resetAiLearning = $('#resetAiLearningButton');
    if (resetAiLearning) resetAiLearning.addEventListener('click', async () => {
      if (!confirm('Reset learned tuning profiles? Feedback samples and JSONL audit logs will be kept.')) return;
      setButtonBusy(resetAiLearning, true, 'Resetting...');
      try {
        const data = await api('/api/ai/learning/reset', { method:'POST', body:JSON.stringify({ delete_samples:false }) });
        renderAiLearningStatus(data.status || data);
        toast('Learned tuning reset. Feedback samples were kept.', 'success');
      } catch (err) {
        toast(err.message, 'error', 9000);
      } finally {
        setButtonBusy(resetAiLearning, false);
      }
    });

    refreshAiLearningStatus().catch(() => {});

    const saveAI = $('#saveAIButton');
    if (saveAI) saveAI.addEventListener('click', async () => {
      cfg.portal_ai = cfg.portal_ai || {};
      cfg.portal_ai.enabled = !!$('#portalAIEnabled')?.checked;
      cfg.portal_ai.background_monitor_enabled = !!$('#aiBackgroundMonitorEnabled')?.checked;
      cfg.portal_ai.check_interval_seconds = Number($('#aiCheckIntervalSeconds')?.value || 30);
      cfg.portal_ai.background_log_changes = !!$('#aiBackgroundLogChanges')?.checked;
      cfg.portal_ai.background_min_log_level = $('#aiBackgroundMinLogLevel')?.value || 'watch';
      cfg.portal_ai.telemetry_rules_enabled = !!$('#aiTelemetryRules')?.checked;
      cfg.portal_ai.camera_rules_enabled = !!$('#aiCameraRules')?.checked;
      cfg.portal_ai.vision_ai_enabled = !!$('#aiVisionEnabled')?.checked;
      cfg.portal_ai.ollama_base_url = $('#aiOllamaBaseUrl')?.value?.trim() || 'http://localhost:11434';
      cfg.portal_ai.ollama_vision_model = $('#aiOllamaVisionModel')?.value?.trim() || 'llava';
      cfg.portal_ai.vision_check_interval_seconds = Number($('#aiVisionCheckInterval')?.value || 120);
      cfg.portal_ai.vision_require_active_print = !!$('#aiVisionRequireActivePrint')?.checked;
      cfg.portal_ai.vision_heuristics_enabled = !!$('#aiVisionHeuristicsEnabled')?.checked;
      cfg.portal_ai.vision_treat_benign_uncertain_as_ok = !!$('#aiVisionBenignUncertainOk')?.checked;
      cfg.portal_ai.vision_dark_mean_threshold = Number($('#aiVisionDarkMeanThreshold')?.value || 58);
      cfg.portal_ai.vision_dark_relative_drop_threshold = Number($('#aiVisionDarkDropThreshold')?.value || 18);
      cfg.portal_ai.vision_stringing_edge_density_threshold = Number($('#aiVisionStringingEdgeThreshold')?.value || 0.125);
      cfg.portal_ai.vision_confidence_threshold = Number($('#aiVisionConfidenceThreshold')?.value || 70);
      cfg.portal_ai.vision_severity_threshold = Number($('#aiVisionSeverityThreshold')?.value || 60);
      cfg.portal_ai.vision_required_bad_checks = Number($('#aiVisionRequiredBadChecks')?.value || 2);
      cfg.portal_ai.vision_prompt = $('#aiVisionPrompt')?.value || cfg.portal_ai.vision_prompt || '';
      cfg.portal_ai.progress_stuck_minutes = Number($('#aiProgressStuckMinutes')?.value || 8);
      cfg.portal_ai.multi_color_mode = $('#aiMultiColorMode')?.value || 'auto';
      cfg.portal_ai.multi_color_progress_stuck_minutes = Number($('#aiMultiColorStuckMinutes')?.value || 30);
      cfg.portal_ai.stale_status_seconds = Number($('#aiStaleStatusSeconds')?.value || 75);
      cfg.portal_ai.feedback_enabled = !!$('#aiFeedbackEnabled')?.checked;
      cfg.portal_ai.feedback_suppression_enabled = !!$('#aiFeedbackSuppressionEnabled')?.checked;
      cfg.portal_ai.feedback_suppression_ttl_hours = Number($('#aiFeedbackSuppressionTtlHours')?.value || 18);
      cfg.portal_ai.feedback_suppression_max_severity = Number($('#aiFeedbackSuppressionMaxSeverity')?.value || 65);
      collectAiLearningSettings();
      cfg.portal_ai.auto_pause_enabled = !!$('#aiAutoPauseEnabled')?.checked;
      cfg.portal_ai.auto_pause_threshold = Number($('#aiAutoPauseThreshold')?.value || 90);
      setButtonBusy(saveAI, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Portal AI settings saved. Reloading...', 'success'); setTimeout(()=>location.reload(), 500); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveAI, false); }
    });

    const saveActions = $('#saveActionsButton');
    if (saveActions) saveActions.addEventListener('click', async () => {
      $$('#actionSettings [data-action-id]').forEach(row => {
        const id = row.dataset.actionId;
        const a = cfg.actions[id];
        if (a) {
          const oldLabel = String(a.label || id);
          const labelInput = $('.action-label', row)?.value?.trim() || oldLabel;
          a.visible = $('.action-visible', row).checked;
          a.requires_confirm = $('.action-confirm', row).checked;
          a.order = Number($('.action-order', row).value || 99);
          a.label = labelInput;
          if (id === 'set_speed_preset') {
            delete a.preset_mode;
            delete a.preset_name;
          }
        }
      });
      setButtonBusy(saveActions, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Buttons saved', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveActions, false); }
    });

    const saveNetwork = $('#saveNetworkButton');
    if (saveNetwork) saveNetwork.addEventListener('click', async () => {
      cfg.network.allowed_subnets = $('#allowedSubnets').value.split('\n').map(x=>x.trim()).filter(Boolean);
      cfg.network.allowed_hosts = $('#allowedHosts').value.split('\n').map(x=>x.trim()).filter(Boolean);
      setButtonBusy(saveNetwork, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Network settings saved', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveNetwork, false); }
    });

    const saveJson = $('#saveJsonButton');
    if (saveJson) saveJson.addEventListener('click', async () => {
      if (!confirm('Save the full raw JSON config? Bad JSON can make the app grumpy.')) return;
      try { cfg = JSON.parse($('#configEditor').value); }
      catch (err) { return toast('Invalid JSON: ' + err.message, 'error'); }
      setButtonBusy(saveJson, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Full config saved. Reloading...', 'success'); setTimeout(()=>location.reload(), 500); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveJson, false); }
    });
  }

  function logParams() {
    const params = new URLSearchParams();
    params.set('limit', $('#logLimit')?.value || '180');
    const source = $('#logSource')?.value || 'all';
    const level = $('#logLevel')?.value || 'all';
    const q = $('#logSearch')?.value?.trim() || '';
    if (source !== 'all') params.set('source', source);
    if (level !== 'all') params.set('level', level);
    if (q) params.set('q', q);
    return params.toString();
  }

  function renderLogLine(l) {
    const extra = l.extra && Object.keys(l.extra).length ? `<span class="log-extra">${esc(JSON.stringify(l.extra).slice(0, 500))}</span>` : '';
    const sourceClass = String(l.source || 'app').toUpperCase().replace(/[^A-Z0-9_-]/g, '_');
    return `<div class="log-line"><span>${esc(l.ts || l.iso || '')}</span> <strong class="${esc(l.level || 'INFO')}">[${esc(l.level || 'INFO')}]</strong> <strong class="${sourceClass}">${esc(l.source || 'app')}</strong> — ${esc(l.message || '')}${extra}</div>`;
  }

  async function refreshLogs() {
    const out = $('#logOutput');
    if (!out) return;
    try {
      const data = await api(`/api/logs?${logParams()}`);
      const src = $('#logSource');
      if (src && !src.dataset.loadedSources) {
        const current = src.value || 'all';
        const sources = ['all', ...(data.sources || [])];
        src.innerHTML = sources.map(s => `<option value="${esc(s)}">${esc(s === 'all' ? 'all sources' : s)}</option>`).join('');
        src.value = sources.includes(current) ? current : 'all';
        src.dataset.loadedSources = '1';
      }
      out.innerHTML = (data.logs || []).map(renderLogLine).join('') || '<div class="log-line">No logs match the current filter.</div>';
    } catch (err) {
      out.innerHTML = `<div class="log-line"><strong class="ERROR">ERROR</strong> ${esc(err.message)}</div>`;
    }
  }

  function initLogs() {
    refreshLogs();
    setInterval(refreshLogs, 3000);
    const btn = $('#refreshLogs');
    if (btn) btn.addEventListener('click', refreshLogs);
    ['logSource', 'logLevel', 'logLimit'].forEach(id => $('#' + id)?.addEventListener('change', refreshLogs));
    $('#logSearch')?.addEventListener('input', () => {
      clearTimeout(window.__cc2LogSearchTimer);
      window.__cc2LogSearchTimer = setTimeout(refreshLogs, 250);
    });
  }


  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  }

  function bytesHuman(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return value ? String(value) : '';
    if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
    if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${n} B`;
  }

  function unwrapCommand(payload) {
    // Firmware replies can be shaped like {result:{result:{...}}}, {result:{...}}, or raw arrays.
    return payload?.result?.result ?? payload?.result ?? payload;
  }

  function printerResultError(payload) {
    const root = unwrapCommand(payload);
    const code = root?.error_code ?? root?.ErrorCode;
    if (code === undefined || code === null || Number(code) === 0) return '';
    const msg = root?.error_msg || root?.ErrorMsg || root?.message || root?.Message || '';
    return `Printer returned error_code ${code}${msg ? ': ' + msg : ''}`;
  }

  function arrayFromAny(payload, candidateKeys) {
    const root = unwrapCommand(payload);
    for (const key of candidateKeys) {
      const value = root?.[key];
      if (Array.isArray(value)) return value;
    }
    if (Array.isArray(root)) return root;
    return [];
  }

  function fileNameOf(file) {
    return file?.filename || file?.name || file?.file_name || file?.Name || file?.FileName || file?.path || file?.file_path || 'unnamed';
  }

  function filePathOf(file) {
    return file?.file_path || file?.path || file?.Path || file?.filename || file?.name || file?.FileName || '';
  }

  function fileTypeOf(file) {
    return file?.type || file?.file_type || file?.FileType || (file?.is_dir || file?.IsDir ? 'folder' : 'gcode');
  }

  function fileSizeOf(file) {
    return file?.size ?? file?.file_size ?? file?.FileSize ?? file?.Size ?? '';
  }

  function fileTimeOf(file) {
    const raw = file?.mtime || file?.modified_time || file?.ModifyTime || file?.create_time || file?.CreateTime || file?.time;
    if (!raw) return '';
    const n = Number(raw);
    if (Number.isFinite(n)) {
      const d = new Date(n > 1e12 ? n : n * 1000);
      if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    }
    return String(raw);
  }

  function fmtDate(value) {
    if (!value) return '--';
    const n = Number(value);
    if (Number.isFinite(n)) {
      const d = new Date(n > 1e12 ? n : n * 1000);
      if (!Number.isNaN(d.getTime())) return d.toLocaleString();
    }
    return String(value);
  }

  function activePrinterId() {
    return document.body.dataset.printerId || cfg?.app?.default_printer || Object.keys(cfg?.printers || {})[0] || '';
  }

  async function printerApi(path, options = {}) {
    const id = activePrinterId();
    if (!id) throw new Error('No printer configured. Run setup first.');
    return api(`/api/printers/${encodeURIComponent(id)}${path}`, options);
  }

  function setBoxLoading(box, loadingEl, loading, loadingText) {
    if (loadingEl) {
      loadingEl.classList.toggle('hidden', !loading);
      const label = loadingEl.querySelector('span:last-child');
      if (label && loadingText) label.textContent = loadingText;
    }
    if (box && loading) {
      box.className = 'file-list empty';
      box.innerHTML = `<span class="spinner"></span><span>${esc(loadingText || 'Loading...')}</span>`;
    }
  }

  function renderEmpty(box, message, detail) {
    if (!box) return;
    box.className = 'file-list empty';
    box.innerHTML = `<strong>${esc(message)}</strong>${detail ? `<span>${esc(detail)}</span>` : ''}`;
  }

  const filamentState = {
    lastData: null,
    selectedTray: null,
    printerIdle: false,
    activePrint: false,
  };

  const FILAMENT_PRESETS = {
    PLA: { type: 'PLA', name: 'PLA', min: 190, max: 230, code: '0x0000' },
    'PLA+': { type: 'PLA', name: 'PLA+', min: 190, max: 230, code: '0x0001' },
    'PLA Silk': { type: 'PLA', name: 'PLA Silk', min: 190, max: 230, code: '0x0003' },
    'PLA-CF': { type: 'PLA', name: 'PLA-CF', min: 210, max: 240, code: '0x0004' },
    PETG: { type: 'PETG', name: 'PETG', min: 220, max: 250, code: '0x0100' },
    ABS: { type: 'ABS', name: 'ABS', min: 240, max: 270, code: '0x0200' },
    ASA: { type: 'ASA', name: 'ASA', min: 240, max: 270, code: '0x0201' },
    TPU: { type: 'TPU', name: 'TPU', min: 210, max: 240, code: '0x0300' },
    PC: { type: 'PC', name: 'PC', min: 260, max: 300, code: '0x0400' },
    PA: { type: 'PA', name: 'PA', min: 250, max: 290, code: '0x0500' },
  };

  function filamentMetaLine(tray) {
    const bits = [];
    if (tray.brand || tray.vendor) bits.push(tray.brand || tray.vendor);
    if (tray.filament_name && tray.filament_name !== tray.filament_type) bits.push(tray.filament_name);
    if (tray.diameter) bits.push(`${tray.diameter}mm`);
    if (tray.weight_g !== null && tray.weight_g !== undefined && tray.weight_g !== '') bits.push(`${tray.weight_g}g`);
    const nozzle = [tray.min_nozzle_temp, tray.max_nozzle_temp].filter(v => v !== null && v !== undefined && v !== '').join('–');
    const bed = [tray.min_bed_temp, tray.max_bed_temp].filter(v => v !== null && v !== undefined && v !== '').join('–');
    if (nozzle) bits.push(`nozzle ${nozzle}°C`);
    if (bed) bits.push(`bed ${bed}°C`);
    if (tray.serial_number) bits.push(`SN ${tray.serial_number}`);
    return bits.filter(Boolean).join(' · ');
  }

  function filamentStatusClass(tray) {
    const label = String(tray?.status_label || '').toLowerCase();
    if (tray?.active || label.includes('loaded') || label.includes('ready')) return 'active';
    if (label.includes('empty')) return 'empty';
    if (label.includes('busy') || label.includes('rfid')) return 'busy';
    return 'unknown';
  }

  function filamentContrastColor(hexColor) {
    let color = String(hexColor || '#8b8f9a').replace('#', '').trim();
    if (color.length === 3) color = color.split('').map(x => x + x).join('');
    if (color.length !== 6) return '#fff';
    const r = parseInt(color.slice(0, 2), 16);
    const g = parseInt(color.slice(2, 4), 16);
    const b = parseInt(color.slice(4, 6), 16);
    const brightness = (r * 299 + g * 587 + b * 114) / 1000;
    return brightness > 168 ? '#161719' : '#fff';
  }

  function normalizeHexColor(color) {
    let value = String(color || '#8b8f9a').trim();
    if (!value.startsWith('#') && (value.length === 3 || value.length === 6)) value = `#${value}`;
    if (!/^#[0-9a-fA-F]{6}$/.test(value) && /^#[0-9a-fA-F]{3}$/.test(value)) {
      value = '#' + value.slice(1).split('').map(x => x + x).join('');
    }
    return /^#[0-9a-fA-F]{6}$/.test(value) ? value.toUpperCase() : '#8B8F9A';
  }

  function filamentDisplayOrder(tray, fallbackIndex = 99) {
    const slot = Number(tray?.slot_number ?? (Number(tray?.tray_id) >= 0 ? Number(tray.tray_id) + 1 : fallbackIndex + 1));
    const order = { 1: 0, 4: 1, 2: 2, 3: 3 };
    return Object.prototype.hasOwnProperty.call(order, slot) ? order[slot] : 50 + fallbackIndex;
  }

  function sortedFilamentTrays(trays) {
    return [...(trays || [])].sort((a, b) => {
      const av = filamentDisplayOrder(a, 0);
      const bv = filamentDisplayOrder(b, 0);
      if (av !== bv) return av - bv;
      return Number(a?.slot_number || a?.tray_id || 0) - Number(b?.slot_number || b?.tray_id || 0);
    });
  }

  function filamentControlsAllowed() {
    return filamentState.printerIdle === true && filamentState.activePrint !== true;
  }

  function setFilamentIdleGuard(data) {
    const guard = $('#filamentIdleGuard');
    if (!guard) return;
    const idle = data?.printer_idle === true;
    const state = [data?.printer_state, data?.printer_sub_state].filter(Boolean).join(' / ') || 'unknown';
    guard.classList.toggle('hidden', idle);
    guard.textContent = idle
      ? ''
      : `Filament load/unload/edit controls are locked until the printer is idle. Current state: ${state}.`;
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  async function refreshFilamentsAfterCommand(delayMs = 900) {
    await sleep(delayMs);
    return loadFilaments(true, null, { notify: false });
  }


  function updateFilamentSelection(tray) {
    filamentState.selectedTray = tray || null;
    $$('.filament-tray').forEach(card => {
      card.classList.toggle('selected', tray && card.dataset.canvasId === String(tray.canvas_id ?? '0') && card.dataset.trayId === String(tray.tray_id ?? '0'));
    });
    const selected = $('#selectedFilamentSlot');
    if (selected) selected.textContent = tray ? `${tray.tray_name || `Slot ${tray.slot_number || tray.tray_id}`}` : 'none';
    const canUse = !!tray && filamentControlsAllowed();
    ['loadFilamentButton', 'unloadFilamentButton', 'editFilamentButton'].forEach(id => {
      const btn = $('#' + id);
      if (btn) {
        btn.disabled = !canUse;
        btn.title = canUse ? '' : (tray ? 'Filament controls are available only while the printer is idle.' : 'Select a filament slot first.');
      }
    });
  }

  function renderFilaments(data) {
    filamentState.lastData = data || null;
    filamentState.selectedTray = null;
    filamentState.printerIdle = data?.printer_idle === true;
    filamentState.activePrint = data?.active_print === true;
    const list = $('#filamentList');
    const trays = data?.trays || [];
    setFilamentIdleGuard(data);
    setText('filamentSystemName', data?.system_name || 'CANVAS');
    setText('filamentConnected', data?.connected ? 'connected' : (data?.raw_available ? 'reported' : 'unknown'));
    setText('filamentActiveSlots', `${data?.active_count ?? 0} / ${data?.tray_count ?? 0}`);
    const sensor = data?.sensor || {};
    const sensorEnabled = sensor.enabled === true || sensor.enabled === 1 || sensor.enabled === '1';
    const sensorDisabled = sensor.enabled === false || sensor.enabled === 0 || sensor.enabled === '0';
    const sensorDetected = sensor.detected === true || sensor.detected === 1 || sensor.detected === '1';
    const sensorEmpty = sensor.detected === false || sensor.detected === 0 || sensor.detected === '0';
    const sensorText = sensorDisabled ? 'disabled' : (sensorDetected ? 'filament present' : (sensorEmpty ? 'no filament' : (sensorEnabled ? 'enabled / unknown' : 'unknown')));
    setText('filamentSensor', sensorText);
    const refill = data?.auto_refill;
    const refillEl = $('#autoRefillState');
    if (refillEl) {
      refillEl.textContent = refill === true ? 'enabled' : (refill === false ? 'disabled' : 'unknown');
      refillEl.className = `pill auto-refill ${refill === true ? 'on' : (refill === false ? 'off' : 'unknown')}`;
    }
    updateFilamentSelection(null);
    if (!list) return;
    if (!trays.length) {
      list.className = 'filament-list empty';
      list.innerHTML = `<strong>No filament data available from the printer.</strong><span>Make sure the CANVAS/Combo system is connected, wait for telemetry, then tap Refresh. Source: ${esc(data?.source || 'none')}.</span>`;
      return;
    }
    const groups = data?.mms_list?.length ? data.mms_list : [{ mms_id: '0', mms_name: data?.system_name || 'CANVAS', trays }];
    list.className = 'filament-list';
    list.innerHTML = groups.map(group => {
      const groupTrays = sortedFilamentTrays(group.trays || []);
      return `<section class="mms-card">
        <div class="mms-head">
          <div><strong>${esc(group.mms_name || group.mms_id || 'CANVAS')}</strong><span>${esc(group.active_count ?? groupTrays.filter(t => t.active).length)} active · ${esc(group.tray_count ?? groupTrays.length)} slot(s)</span></div>
          <span class="pill">${group.connected === false ? 'not connected' : 'connected'}</span>
        </div>
        <div class="tray-grid">
          ${groupTrays.map((tray, index) => {
            const cls = filamentStatusClass(tray);
            const label = tray.filament_type || tray.filament_name || (cls === 'empty' ? 'Empty' : 'Unknown');
            const meta = filamentMetaLine(tray);
            const color = normalizeHexColor(tray.filament_color || '#8b8f9a');
            const contrast = filamentContrastColor(color);
            const slot = tray.slot_number || (Number(tray.tray_id) >= 0 ? Number(tray.tray_id) + 1 : index + 1);
            return `<article class="filament-tray ${cls}" tabindex="0" role="button" aria-label="Select ${esc(tray.tray_name || `Slot ${slot}`)}" data-canvas-id="${esc(tray.canvas_id ?? group.mms_id ?? '0')}" data-tray-id="${esc(tray.tray_id ?? index)}" data-tray-index="${index}">
              <div class="tray-color" style="--tray-color:${esc(color)}; --tray-text:${esc(contrast)}"><span>${esc(label || '?')}</span></div>
              <div class="tray-main">
                <div class="tray-title-row"><strong>${esc(tray.tray_name || `Slot ${slot}`)}</strong><span>${esc(tray.status_label || 'unknown')}</span></div>
                <div class="tray-material">${esc(label)}</div>
                ${meta ? `<small>${esc(meta)}</small>` : '<small>No extra spool metadata reported.</small>'}
              </div>
            </article>`;
          }).join('')}
        </div>
      </section>`;
    }).join('');
    $$('.filament-tray', list).forEach(card => {
      const onPick = () => {
        const tray = (filamentState.lastData?.trays || []).find(t => String(t.canvas_id ?? '0') === card.dataset.canvasId && String(t.tray_id ?? '0') === card.dataset.trayId)
          || (filamentState.lastData?.trays || [])[Number(card.dataset.trayIndex)]
          || null;
        updateFilamentSelection(tray);
      };
      card.addEventListener('click', onPick);
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(); }
      });
    });
  }

  async function loadFilaments(refresh = false, button = null, options = {}) {
    const list = $('#filamentList');
    const loading = $('#filamentLoadStatus');
    setBoxLoading(list, loading, true, 'Loading filament data...');
    setButtonBusy(button, true, 'Loading...');
    try {
      const data = await printerApi(refresh ? '/filaments/refresh' : '/filaments', { method: refresh ? 'POST' : 'GET' });
      renderFilaments(data);
      const count = data?.tray_count ?? 0;
      if (options.notify !== false) toast(count ? `Loaded ${count} filament tray slot(s).` : 'No CANVAS filament trays reported yet.', count ? 'success' : 'warn');
      return data;
    } catch (err) {
      renderEmpty(list, 'Filament load failed.', err.message);
      toast(err.message, 'error', 8000);
    } finally {
      setBoxLoading(null, loading, false);
      setButtonBusy(button, false);
    }
  }

  async function setAutoRefill(enabled, button) {
    if (!confirm(`${enabled ? 'Enable' : 'Disable'} Auto Filament Refill?`)) return;
    setButtonBusy(button, true, enabled ? 'Enabling...' : 'Disabling...');
    try {
      const data = await printerApi('/filaments/auto-refill', { method: 'POST', body: JSON.stringify({ enabled }) });
      renderFilaments(data);
      toast(`Auto Filament Refill ${enabled ? 'enabled' : 'disabled'}. Refreshing printer report...`, 'success');
      refreshFilamentsAfterCommand(1200).catch(err => toast(`Auto-refill refresh failed: ${err.message}`, 'warn', 8000));
    } catch (err) {
      toast(err.message, 'error', 9000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  function filamentCommandPayload(tray) {
    return {
      canvas_id: tray?.canvas_id ?? 0,
      tray_id: tray?.tray_id ?? 0,
    };
  }

  async function runFilamentMotion(action, button) {
    const tray = filamentState.selectedTray;
    if (!tray) return toast('Select a filament slot first.', 'warn');
    if (!filamentControlsAllowed()) return toast('Filament load/unload is locked until the printer is idle.', 'warn', 8000);
    const label = tray.tray_name || `Slot ${tray.slot_number || tray.tray_id}`;
    if (!confirm(`${action === 'load' ? 'Load/feed' : 'Unload'} filament for ${label}?\n\nThis uses the same CANVAS command shape as the stock portal and requires printer commands to be enabled.`)) return;
    setButtonBusy(button, true, action === 'load' ? 'Loading...' : 'Unloading...');
    try {
      const data = await printerApi(`/filaments/${action}`, { method: 'POST', body: JSON.stringify(filamentCommandPayload(tray)) });
      renderFilaments(data);
      toast(`${action === 'load' ? 'Load/feed' : 'Unload'} command sent for ${label}. Refreshing printer report...`, 'success', 6500);
      refreshFilamentsAfterCommand(1500).catch(err => toast(`Filament refresh failed: ${err.message}`, 'warn', 8000));
    } catch (err) {
      toast(err.message, 'error', 10000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  function applyFilamentPreset(name) {
    const preset = FILAMENT_PRESETS[name] || FILAMENT_PRESETS.PLA;
    const typeEl = $('#filamentEditType');
    const nameEl = $('#filamentEditName');
    const codeEl = $('#filamentEditCode');
    const minEl = $('#filamentEditMinTemp');
    const maxEl = $('#filamentEditMaxTemp');
    if (typeEl) typeEl.value = preset.type;
    if (nameEl) nameEl.value = preset.name;
    if (codeEl) codeEl.value = preset.code;
    if (minEl) minEl.value = preset.min;
    if (maxEl) maxEl.value = preset.max;
  }

  function openFilamentEditModal() {
    const tray = filamentState.selectedTray;
    if (!tray) return toast('Select a filament slot first.', 'warn');
    if (!filamentControlsAllowed()) return toast('Filament editing is locked until the printer is idle.', 'warn', 8000);
    const modal = $('#filamentEditModal');
    if (!modal) return;
    const label = tray.tray_name || `Slot ${tray.slot_number || tray.tray_id}`;
    setText('filamentEditSlotLabel', label);
    const name = tray.filament_name || tray.filament_type || 'PLA';
    const presetKey = Object.keys(FILAMENT_PRESETS).find(k => k.toLowerCase() === String(name).toLowerCase()) || (FILAMENT_PRESETS[tray.filament_type] ? tray.filament_type : 'PLA');
    const presetEl = $('#filamentEditPreset');
    if (presetEl) presetEl.value = presetKey;
    const brandEl = $('#filamentEditBrand');
    const typeEl = $('#filamentEditType');
    const nameEl = $('#filamentEditName');
    const codeEl = $('#filamentEditCode');
    const colorEl = $('#filamentEditColor');
    const minEl = $('#filamentEditMinTemp');
    const maxEl = $('#filamentEditMaxTemp');
    if (brandEl) brandEl.value = tray.brand || tray.vendor || 'ELEGOO';
    if (typeEl) typeEl.value = tray.filament_type || FILAMENT_PRESETS[presetKey]?.type || 'PLA';
    if (nameEl) nameEl.value = name;
    if (codeEl) codeEl.value = tray.filament_code || tray.setting_id || FILAMENT_PRESETS[presetKey]?.code || '';
    if (colorEl) colorEl.value = normalizeHexColor(tray.filament_color || '#8b8f9a');
    if (minEl) minEl.value = tray.min_nozzle_temp || FILAMENT_PRESETS[presetKey]?.min || 190;
    if (maxEl) maxEl.value = tray.max_nozzle_temp || FILAMENT_PRESETS[presetKey]?.max || 230;
    modal.classList.remove('hidden');
  }

  function closeFilamentEditModal() {
    $('#filamentEditModal')?.classList.add('hidden');
  }

  async function saveFilamentEdit(button) {
    const tray = filamentState.selectedTray;
    if (!tray) return toast('Select a filament slot first.', 'warn');
    if (!filamentControlsAllowed()) return toast('Filament editing is locked until the printer is idle.', 'warn', 8000);
    const body = {
      canvas_id: tray.canvas_id ?? 0,
      tray_id: tray.tray_id ?? 0,
      brand: $('#filamentEditBrand')?.value || 'ELEGOO',
      filament_type: $('#filamentEditType')?.value || 'PLA',
      filament_name: $('#filamentEditName')?.value || 'PLA',
      filament_code: $('#filamentEditCode')?.value || '',
      filament_color: normalizeHexColor($('#filamentEditColor')?.value || '#8b8f9a'),
      filament_min_temp: Number($('#filamentEditMinTemp')?.value || 190),
      filament_max_temp: Number($('#filamentEditMaxTemp')?.value || 230),
    };
    setButtonBusy(button, true, 'Saving...');
    try {
      const data = await printerApi('/filaments/edit', { method: 'POST', body: JSON.stringify(body) });
      closeFilamentEditModal();
      renderFilaments(data);
      toast(`Updated ${tray.tray_name || 'slot'} to ${body.filament_name}. Refreshing printer report...`, 'success');
      refreshFilamentsAfterCommand(1200).catch(err => toast(`Filament refresh failed: ${err.message}`, 'warn', 8000));
    } catch (err) {
      toast(err.message, 'error', 10000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  const fileManagerState = {
    usbPath: '/',
    loadedTabs: new Set(),
  };

  function fileIsFolder(file) {
    const type = String(fileTypeOf(file) || '').toLowerCase();
    return type === 'folder' || type === 'dir' || type === 'directory' || file?.is_dir === true || file?.IsDir === true;
  }

  function normalizeDirPath(path) {
    let value = String(path || '/').trim() || '/';
    if (!value.startsWith('/')) value = '/' + value;
    value = value.replace(/\/+/g, '/');
    if (value !== '/' && !value.endsWith('/')) value += '/';
    return value;
  }

  function basename(value) {
    const text = String(value || '').replace(/\/+/g, '/').replace(/\/$/, '');
    return text.split('/').filter(Boolean).pop() || text || '';
  }

  function joinUsbPath(dir, name) {
    const base = normalizeDirPath(dir || '/');
    const clean = String(name || '').replace(/^\/+/, '');
    return normalizeDirPath(base === '/' ? `/${clean}` : `${base}${clean}`);
  }

  function fullFilePath(file, storage, directory = '/') {
    const raw = filePathOf(file) || fileNameOf(file);
    if (storage !== 'u-disk') return raw;
    if (String(raw || '').startsWith('/')) return raw;
    const joined = joinUsbPath(directory, raw);
    return fileIsFolder(file) ? joined : joined.replace(/\/$/, '');
  }

  function fileMetaLine(file, storage, directory = '/') {
    const type = fileIsFolder(file) ? 'folder' : (file?.is_gcode ? 'gcode' : fileTypeOf(file));
    const size = fileIsFolder(file) ? '' : bytesHuman(fileSizeOf(file));
    const time = fileTimeOf(file);
    const pathVal = fullFilePath(file, storage, directory);
    return [type, size, time, pathVal && pathVal !== fileNameOf(file) ? pathVal : ''].filter(Boolean).join(' · ');
  }

  function historyNameOf(item) {
    return item?.task_name || item?.TaskName || item?.filename || item?.FileName || item?.name || item?.Name || 'History task';
  }

  function historyIdOf(item) {
    return item?.task_id ?? item?.TaskId ?? item?.taskId ?? item?.id ?? item?.Id;
  }

  function renderFileRows(box, files, storage, directory = '/') {
    if (!box) return;
    if (!files.length) {
      const label = storage === 'u-disk' ? 'No USB files returned.' : 'No printer files returned.';
      const hint = storage === 'u-disk' ? 'Make sure the USB drive is inserted and mounted, then tap Refresh.' : 'The printer returned an empty local file list.';
      renderEmpty(box, label, hint);
      return;
    }
    box.className = 'file-list';
    box.innerHTML = files.map((file, i) => {
      const name = fileNameOf(file);
      const folder = fileIsFolder(file);
      const meta = fileMetaLine(file, storage, directory);
      const icon = folder ? '📁 ' : '';
      return `<div class="file-item" data-file-index="${i}">
        <div class="file-main"><strong>${icon}${esc(name)}</strong><span>${esc(meta || 'file')}</span></div>
        <div class="file-actions">
          ${folder && storage === 'u-disk' ? `<button class="button primary tiny" type="button" data-file-open="${i}">Open</button>` : ''}
          ${!folder ? `<button class="button secondary tiny" type="button" data-file-info="${i}">Info</button>` : ''}
          ${!folder ? `<button class="button primary tiny" type="button" data-file-print="${i}">Print</button>` : ''}
          <button class="button danger tiny" type="button" data-file-delete="${i}">Delete</button>
        </div>
      </div>`;
    }).join('');
    $$('[data-file-open]', box).forEach(el => el.addEventListener('click', () => openUsbFolder(files[Number(el.dataset.fileOpen)])));
    $$('[data-file-info]', box).forEach(el => el.addEventListener('click', () => showFileDetail(files[Number(el.dataset.fileInfo)], storage, directory)));
    $$('[data-file-print]', box).forEach(el => el.addEventListener('click', () => startFile(files[Number(el.dataset.filePrint)], storage, directory)));
    $$('[data-file-delete]', box).forEach(el => el.addEventListener('click', () => deleteFile(files[Number(el.dataset.fileDelete)], storage, directory)));
  }

  async function loadFilesFor(storage, directory = '/', boxId, loadingId, buttonId) {
    const box = $(boxId);
    const loading = $(loadingId);
    const btn = $(buttonId);
    const label = storage === 'u-disk' ? 'Loading USB files...' : 'Loading printer files...';
    setBoxLoading(box, loading, true, label);
    setButtonBusy(btn, true, 'Loading...');
    try {
      const data = await printerApi(`/files?storage_media=${encodeURIComponent(storage)}&path=${encodeURIComponent(directory)}&page_size=150`);
      const printerErr = printerResultError(data);
      if (printerErr) {
        const hint = storage === 'u-disk' ? 'USB storage may be empty, missing, or not mounted.' : 'The printer rejected the local file-list request.';
        renderEmpty(box, 'File load returned a printer error.', `${printerErr}. ${hint}`);
        toast(printerErr, 'warn', 7000);
        return [];
      }
      let files = data?.files || arrayFromAny(data, ['file_list', 'files', 'list', 'data', 'items', 'FileList']);
      renderFileRows(box, files, storage, directory);
      toast(`Loaded ${files.length} ${storage === 'u-disk' ? 'USB' : 'printer'} file item(s)`, 'success');
      return files;
    } catch (err) {
      renderEmpty(box, 'File load failed.', err.message);
      toast(err.message, 'error', 7000);
      return [];
    } finally {
      setBoxLoading(null, loading, false);
      setButtonBusy(btn, false);
    }
  }

  async function loadPrinterFiles() {
    fileManagerState.loadedTabs.add('printer');
    return loadFilesFor('local', '/', '#printerFileList', '#printerFileLoadStatus', '#refreshPrinterFilesButton');
  }

  async function loadUsbFiles() {
    fileManagerState.usbPath = normalizeDirPath(fileManagerState.usbPath || '/');
    const label = $('#usbPathLabel');
    if (label) label.textContent = fileManagerState.usbPath;
    fileManagerState.loadedTabs.add('usb');
    return loadFilesFor('u-disk', fileManagerState.usbPath, '#usbFileList', '#usbFileLoadStatus', '#refreshUsbFilesButton');
  }

  function openUsbFolder(file) {
    const name = basename(filePathOf(file) || fileNameOf(file));
    fileManagerState.usbPath = joinUsbPath(fileManagerState.usbPath, name);
    loadUsbFiles();
  }

  function usbBack() {
    const path = normalizeDirPath(fileManagerState.usbPath || '/');
    if (path === '/') return loadUsbFiles();
    const parts = path.split('/').filter(Boolean);
    parts.pop();
    fileManagerState.usbPath = parts.length ? `/${parts.join('/')}/` : '/';
    loadUsbFiles();
  }

  async function showFileDetail(file, storage, directory = '/') {
    const name = storage === 'u-disk' ? fullFilePath(file, storage, directory) : fileNameOf(file);
    try {
      const url = `/files/detail?storage_media=${encodeURIComponent(storage)}&filename=${encodeURIComponent(name)}&directory=${encodeURIComponent(directory)}`;
      const data = await printerApi(url);
      const pretty = JSON.stringify(unwrapCommand(data), null, 2);
      toast('File info loaded. Details printed to browser console.', 'success');
      console.log('[cc2-dash] file detail', name, pretty);
      alert(`File details for ${name}:\n\n${pretty.slice(0, 1800)}${pretty.length > 1800 ? '\n\n…truncated; see browser console for full detail.' : ''}`);
    } catch (err) {
      toast('File detail failed: ' + err.message, 'error');
    }
  }

  async function startFile(file, storage, directory = '/') {
    if (fileIsFolder(file)) return;
    const name = storage === 'u-disk' ? fullFilePath(file, storage, directory) : fileNameOf(file);
    if (!confirm(`Start printing ${name}?`)) return;
    const button = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null;
    setButtonBusy(button, true, 'Starting...');
    try {
      await printerApi('/files/start', { method: 'POST', body: JSON.stringify({ filename: name, storage_media: storage, start_layer: 0, calibration: false, timelapse: false }) });
      toast('Start print command sent', 'success');
    } catch (err) {
      toast(err.message, 'error', 7000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async function deleteFile(file, storage, directory = '/') {
    const pathVal = storage === 'u-disk' ? fullFilePath(file, storage, directory) : (filePathOf(file) || fileNameOf(file));
    if (!confirm(`Delete ${pathVal}? This cannot be undone.`)) return;
    const button = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null;
    setButtonBusy(button, true, 'Deleting...');
    try {
      await printerApi('/files/delete', { method: 'POST', body: JSON.stringify({ file_path: pathVal, storage_media: storage }) });
      toast('File delete command sent', 'success');
      if (storage === 'u-disk') await loadUsbFiles();
      else await loadPrinterFiles();
    } catch (err) {
      toast(err.message, 'error', 7000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async function loadHistoryList() {
    const box = $('#historyList');
    const loading = $('#historyLoadStatus');
    const btn = $('#refreshHistoryButton');
    setBoxLoading(box, loading, true, 'Loading print history...');
    setButtonBusy(btn, true, 'Loading...');
    try {
      const data = await printerApi('/history/list?page_size=150');
      const printerErr = printerResultError(data);
      if (printerErr) {
        renderEmpty(box, 'Print history returned a printer error.', printerErr);
        toast(printerErr, 'warn', 7000);
        return;
      }
      const rows = data?.history || arrayFromAny(data, ['history_task_list', 'HistoryTaskList', 'task_list', 'items', 'list']);
      if (!rows.length) {
        renderEmpty(box, 'No print history returned.', 'The printer did not return any saved history rows. The stock portal uses method 1036 for this section.');
        return;
      }
      box.className = 'file-list';
      box.innerHTML = rows.map((item, i) => {
        const name = historyNameOf(item);
        const id = historyIdOf(item);
        const start = item?.begin_time || item?.BeginTime || item?.create_time || item?.CreateTime;
        const end = item?.end_time || item?.EndTime;
        const size = bytesHuman(item?.file_size ?? item?.FileSize ?? item?.size ?? item?.Size);
        const status = item?.task_status ?? item?.TaskStatus ?? item?.status ?? item?.Status ?? '';
        const video = item?.has_timelapse || item?.time_lapse_video_status ? 'timelapse' : '';
        const meta = [size, fmtDate(start), end ? `ended ${fmtDate(end)}` : '', status ? `status ${status}` : '', video, `ID ${id ?? '-'}`].filter(Boolean).join(' · ');
        return `<div class="file-item" data-history-index="${i}">
          <div class="file-main"><strong>${esc(name)}</strong><span>${esc(meta)}</span></div>
          <div class="file-actions">
            <button class="button secondary tiny" type="button" data-history-info="${i}">Info</button>
            <button class="button primary tiny" type="button" data-history-reprint="${i}">Reprint</button>
            <button class="button danger tiny" type="button" data-history-delete="${i}">Delete</button>
          </div>
        </div>`;
      }).join('');
      $$('[data-history-info]', box).forEach(el => el.addEventListener('click', () => showHistoryInfo(rows[Number(el.dataset.historyInfo)])));
      $$('[data-history-reprint]', box).forEach(el => el.addEventListener('click', () => reprintHistory(rows[Number(el.dataset.historyReprint)])));
      $$('[data-history-delete]', box).forEach(el => el.addEventListener('click', () => deleteHistory(rows[Number(el.dataset.historyDelete)])));
      toast(`Loaded ${rows.length} history row(s)`, 'success');
    } catch (err) {
      renderEmpty(box, 'Print history load failed.', err.message);
      toast(err.message, 'error', 7000);
    } finally {
      setBoxLoading(null, loading, false);
      setButtonBusy(btn, false);
    }
  }

  function showHistoryInfo(item) {
    const name = historyNameOf(item);
    const pretty = JSON.stringify(item?.raw || item, null, 2);
    console.log('[cc2-dash] history detail', name, pretty);
    alert(`Print history details for ${name}:\n\n${pretty.slice(0, 1800)}${pretty.length > 1800 ? '\n\n…truncated; see browser console for full detail.' : ''}`);
  }

  async function reprintHistory(item) {
    const name = historyNameOf(item);
    if (!name || !String(name).toLowerCase().includes('.g')) return toast('This history row does not include a reusable G-code filename.', 'warn');
    if (!confirm(`Try to reprint ${name}? This only works if the source file still exists on local printer storage.`)) return;
    const button = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null;
    setButtonBusy(button, true, 'Starting...');
    try {
      await printerApi('/files/start', { method: 'POST', body: JSON.stringify({ filename: name, storage_media: 'local', start_layer: 0, calibration: false, timelapse: false }) });
      toast('Reprint command sent', 'success');
    } catch (err) {
      toast(err.message, 'error', 9000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async function deleteHistory(item) {
    const id = historyIdOf(item);
    if (id === undefined || id === null || id === '') return toast('No task ID found for delete.', 'warn');
    if (!confirm(`Delete history record ${id}?`)) return;
    const button = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null;
    setButtonBusy(button, true, 'Deleting...');
    try {
      await printerApi('/history/delete', { method: 'POST', body: JSON.stringify({ task_ids: [id] }) });
      toast('History delete command sent', 'success');
      await loadHistoryList();
    } catch (err) {
      toast(err.message, 'error', 9000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  function timelapseNameOf(item, i) {
    return item?.task_name || item?.TaskName || item?.filename || item?.FileName || item?.name || `Timelapse ${i + 1}`;
  }

  function timelapseIdOf(item) {
    return item?.task_id ?? item?.TaskId ?? item?.id ?? item?.Id ?? item?.taskId;
  }

  function timelapseUrlOf(item) {
    return item?.download_url || item?.DownloadUrl || item?.time_lapse_video_url || item?.TimeLapseVideoUrl || item?.video_url || item?.VideoUrl || item?.url || item?.Url || '';
  }

  function timelapseRawUrlOf(item) {
    return item?.time_lapse_video_url || item?.TimeLapseVideoUrl || item?.video_url || item?.VideoUrl || item?.url || item?.Url || '';
  }

  function timelapseStatusOf(item) {
    return Number(item?.time_lapse_video_status ?? item?.TimeLapseVideoStatus ?? item?.video_status ?? item?.VideoStatus ?? 0);
  }

  function timelapseSizeOf(item) {
    return item?.time_lapse_video_size ?? item?.TimeLapseVideoSize ?? item?.video_size ?? item?.VideoSize ?? item?.file_size ?? item?.FileSize ?? item?.size ?? item?.Size ?? '';
  }

  function timelapseDurationOf(item) {
    return item?.time_lapse_video_duration ?? item?.TimeLapseVideoDuration ?? item?.video_duration ?? item?.VideoDuration ?? item?.duration ?? item?.Duration ?? '';
  }

  function timelapseStatusText(status) {
    if (status === 2) return 'generated';
    if (status === 1) return 'needs export';
    if (status === 3) return 'failed';
    return status ? `status ${status}` : '';
  }

  async function loadTimelapseList() {
    const box = $('#timelapseList');
    const loading = $('#timelapseLoadStatus');
    const btn = $('#refreshTimelapseButton');
    setBoxLoading(box, loading, true, 'Loading timelapse records...');
    setButtonBusy(btn, true, 'Loading...');
    try {
      const data = await printerApi('/timelapse');
      const printerErr = printerResultError(data);
      if (printerErr) {
        renderEmpty(box, 'Timelapse/history load returned a printer error.', printerErr);
        toast(printerErr, 'warn', 7000);
        return;
      }
      let items = arrayFromAny(data, ['videos', 'time_lapse_video_list', 'TimeLapseVideoList', 'items', 'list']);
      if (!items.length && Array.isArray(data?.videos)) items = data.videos;
      if (!items.length) {
        const root = unwrapCommand(data);
        const rawCount = root?.raw_history_total ?? data?.result?.raw_history_total ?? 0;
        renderEmpty(box, 'No timelapse videos returned.', rawCount ? `History loaded (${rawCount} task(s)), but none were marked as timelapse video rows by the printer.` : 'The printer did not return any video records. The stock portal only shows history rows with timelapse status 1 or 2.');
        return;
      }
      box.className = 'file-list';
      box.innerHTML = items.map((item, i) => {
        const name = timelapseNameOf(item, i);
        const id = timelapseIdOf(item);
        const url = timelapseUrlOf(item);
        const rawUrl = timelapseRawUrlOf(item);
        const status = timelapseStatusOf(item);
        const statusLabel = timelapseStatusText(status);
        const start = item?.begin_time || item?.BeginTime || item?.create_time || item?.CreateTime || item?.start_time || item?.StartTime;
        const size = bytesHuman(timelapseSizeOf(item));
        const duration = timelapseDurationOf(item);
        const meta = [size, fmtDate(start), duration !== '' && duration !== undefined && duration !== null ? `${duration}s` : '', statusLabel, url || rawUrl ? 'download ready' : 'export needed', `ID ${id ?? '-'}`].filter(Boolean).join(' · ');
        return `<div class="file-item" data-timelapse-index="${i}">
          <div class="file-main"><strong>${esc(name)}</strong><span>${esc(meta)}</span></div>
          <div class="file-actions">
            <button class="button primary tiny" type="button" data-tl-download="${i}">${url ? 'Download' : 'Open'}</button>
            <button class="button secondary tiny" type="button" data-tl-export="${i}">Export</button>
            <button class="button danger tiny" type="button" data-tl-delete="${i}">Delete</button>
          </div>
        </div>`;
      }).join('');
      $$('[data-tl-download]', box).forEach(el => el.addEventListener('click', () => downloadTimelapse(items[Number(el.dataset.tlDownload)])));
      $$('[data-tl-export]', box).forEach(el => el.addEventListener('click', () => exportTimelapse(items[Number(el.dataset.tlExport)])));
      $$('[data-tl-delete]', box).forEach(el => el.addEventListener('click', () => deleteTimelapse(items[Number(el.dataset.tlDelete)])));
      toast(`Loaded ${items.length} timelapse/history item(s)`, 'success');
    } catch (err) {
      renderEmpty(box, 'Timelapse load failed.', err.message);
      toast(err.message, 'error', 7000);
    } finally {
      setBoxLoading(null, loading, false);
      setButtonBusy(btn, false);
    }
  }

  function downloadTimelapse(item) {
    const url = timelapseUrlOf(item);
    if (!url) {
      toast('No video URL returned yet. Try Export first.', 'warn');
      return;
    }
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  async function exportTimelapse(item) {
    const token = timelapseRawUrlOf(item) || timelapseUrlOf(item) || item?.task_name || item?.TaskName || String(timelapseIdOf(item) ?? '');
    if (!token) return toast('No task/video identifier found for export.', 'warn');
    const button = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null;
    setButtonBusy(button, true, 'Exporting...');
    try {
      const data = await printerApi('/timelapse/export', { method: 'POST', body: JSON.stringify({ url: token }) });
      const result = unwrapCommand(data);
      const url = result?.download_url || result?.DownloadUrl || result?.url || result?.Url || result?.time_lapse_video_url || result?.TimeLapseVideoUrl;
      toast('Timelapse export command sent', 'success');
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
      else await loadTimelapseList();
    } catch (err) {
      toast(err.message, 'error', 9000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async function deleteTimelapse(item) {
    const id = timelapseIdOf(item);
    if (id === undefined || id === null || id === '') return toast('No task ID found for delete.', 'warn');
    if (!confirm(`Delete timelapse/history record ${id}?`)) return;
    const button = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null;
    setButtonBusy(button, true, 'Deleting...');
    try {
      await printerApi('/history/delete', { method: 'POST', body: JSON.stringify({ task_ids: [id] }) });
      toast('Timelapse/history delete command sent', 'success');
      await loadTimelapseList();
    } catch (err) {
      toast(err.message, 'error', 9000);
    } finally {
      setButtonBusy(button, false);
    }
  }

  function activateFileTab(tab) {
    $$('[data-file-tab]').forEach(b => b.classList.toggle('active', b.dataset.fileTab === tab));
    $$('.file-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === tab));
    if (tab === 'printer' && !fileManagerState.loadedTabs.has('printer')) loadPrinterFiles();
    if (tab === 'usb' && !fileManagerState.loadedTabs.has('usb')) loadUsbFiles();
    if (tab === 'history' && !fileManagerState.loadedTabs.has('history')) {
      fileManagerState.loadedTabs.add('history');
      loadHistoryList();
    }
    if (tab === 'videos' && !fileManagerState.loadedTabs.has('videos')) {
      fileManagerState.loadedTabs.add('videos');
      loadTimelapseList();
    }
  }

  function initFiles() {
    $$('[data-file-tab]').forEach(btn => btn.addEventListener('click', () => activateFileTab(btn.dataset.fileTab)));
    $('#refreshPrinterFilesButton')?.addEventListener('click', loadPrinterFiles);
    $('#refreshUsbFilesButton')?.addEventListener('click', loadUsbFiles);
    $('#usbBackButton')?.addEventListener('click', usbBack);
    $('#refreshHistoryButton')?.addEventListener('click', () => {
      fileManagerState.loadedTabs.add('history');
      loadHistoryList();
    });
    $('#refreshTimelapseButton')?.addEventListener('click', () => {
      fileManagerState.loadedTabs.add('videos');
      loadTimelapseList();
    });
    activateFileTab('printer');
  }

  function initFilaments() {
    $('#refreshFilamentsButton')?.addEventListener('click', e => loadFilaments(true, e.currentTarget));
    $('#enableAutoRefillButton')?.addEventListener('click', e => setAutoRefill(true, e.currentTarget));
    $('#disableAutoRefillButton')?.addEventListener('click', e => setAutoRefill(false, e.currentTarget));
    $('#loadFilamentButton')?.addEventListener('click', e => runFilamentMotion('load', e.currentTarget));
    $('#unloadFilamentButton')?.addEventListener('click', e => runFilamentMotion('unload', e.currentTarget));
    $('#editFilamentButton')?.addEventListener('click', openFilamentEditModal);
    $('#cancelFilamentEditButton')?.addEventListener('click', closeFilamentEditModal);
    $('#filamentEditModalClose')?.addEventListener('click', closeFilamentEditModal);
    $('#saveFilamentEditButton')?.addEventListener('click', e => saveFilamentEdit(e.currentTarget));
    $('#filamentEditPreset')?.addEventListener('change', e => applyFilamentPreset(e.currentTarget.value));
    $('#filamentEditModal')?.addEventListener('click', e => { if (e.target?.id === 'filamentEditModal') closeFilamentEditModal(); });
    loadFilaments(false);
  }

  if (page === 'dashboard') initDashboard();
  if (page === 'kiosk') initKiosk();
  if (page === 'setup') initSetup();
  if (page === 'settings') initSettings();
  if (page === 'logs') initLogs();
  if (page === 'files') initFiles();
  if (page === 'filaments') initFilaments();
})();
