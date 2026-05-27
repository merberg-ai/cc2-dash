/* cc2-dash-lite camera relay shim for the embedded Elegoo portal.
   Redirects common direct camera URLs through /api/printers/<id>/camera/stream
   so the stock UI does not open another upstream socket to printer:8080. */
(function () {
  const params = new URLSearchParams(window.location.search || '');
  const printerId = params.get('id') || params.get('printer') || params.get('printerId') || window.cc2DashPrinterId || '';
  const host = params.get('ip') || params.get('print_ip') || params.get('host') || '';
  if (!printerId && !host) return;
  const relay = printerId ? `/api/printers/${encodeURIComponent(printerId)}/camera/stream` : '';
  function isCameraUrl(value) {
    if (!value || typeof value !== 'string') return false;
    const s = value.trim();
    if (!s) return false;
    if (host) {
      try {
        const u = new URL(s, window.location.href);
        if (u.hostname === host && u.port === '8080') return true;
      } catch (_) {}
      if (s.includes(`${host}:8080`)) return true;
    }
    return /(^|["'=(])\/(camera|stream|webcam)(\?|["')]|$)/i.test(s) || /action=stream/i.test(s);
  }
  function rewrite(value) {
    if (!relay || !isCameraUrl(value)) return value;
    return relay;
  }
  window.cc2DashRewriteCameraUrl = rewrite;

  const oldFetch = window.fetch;
  if (oldFetch) {
    window.fetch = function (input, init) {
      if (typeof input === 'string') input = rewrite(input);
      else if (input && input.url && isCameraUrl(input.url)) input = new Request(rewrite(input.url), input);
      return oldFetch.call(this, input, init);
    };
  }

  if (window.XMLHttpRequest) {
    const oldOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      arguments[1] = rewrite(url);
      return oldOpen.apply(this, arguments);
    };
  }

  function patchSrc(proto) {
    if (!proto) return;
    const desc = Object.getOwnPropertyDescriptor(proto, 'src');
    if (!desc || !desc.set || !desc.get || desc.__cc2Patched) return;
    Object.defineProperty(proto, 'src', {
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set: function (value) { return desc.set.call(this, rewrite(value)); }
    });
  }
  patchSrc(window.HTMLImageElement && HTMLImageElement.prototype);
  patchSrc(window.HTMLVideoElement && HTMLVideoElement.prototype);
  patchSrc(window.HTMLSourceElement && HTMLSourceElement.prototype);

  const oldSetAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    if (String(name || '').toLowerCase() === 'src') value = rewrite(value);
    return oldSetAttribute.call(this, name, value);
  };

  console.info('[cc2-dash-lite] camera relay shim active', { printerId, host, relay });
})();
