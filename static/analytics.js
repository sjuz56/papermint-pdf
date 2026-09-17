(() => {
  const endpoint = '/api/analytics/event';

  function track(event, details = {}) {
    const payload = JSON.stringify({
      event,
      path: window.location.pathname,
      referrer: document.referrer || '',
      tool: details.tool || ''
    });

    if (navigator.sendBeacon) {
      navigator.sendBeacon(endpoint, new Blob([payload], {type: 'application/json'}));
      return;
    }
    fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: payload,
      keepalive: true,
      credentials: 'same-origin'
    }).catch(() => {});
  }

  window.PDFaspectAnalytics = {track};
  track('page_view');
})();
