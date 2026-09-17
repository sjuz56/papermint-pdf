const number = value => new Intl.NumberFormat().format(value || 0);
const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[character]);

function list(id, rows, empty = 'No data yet') {
  const target = document.getElementById(id);
  target.innerHTML = rows.length
    ? rows.map(row => `<li><span>${escapeHtml(row.name)}</span><strong>${number(row.count)}</strong></li>`).join('')
    : `<li><span>${escapeHtml(empty)}</span><strong>—</strong></li>`;
}

async function loadAnalytics() {
  const status = document.getElementById('analyticsStatus');
  try {
    const response = await fetch('/api/analytics/summary?days=30', {cache: 'no-store'});
    if (response.status === 401) {
      status.innerHTML = 'Sign in with the PDFaspect administrator account on the <a href="/">homepage</a>, then return here.';
      return;
    }
    if (response.status === 403) {
      status.textContent = 'This account does not have access to analytics.';
      return;
    }
    if (!response.ok) throw new Error('Analytics could not be loaded.');
    const data = await response.json();
    status.textContent = 'Anonymous first-party statistics for the last 30 days (UTC).';
    Object.entries(data.totals).forEach(([key, value]) => {
      const element = document.querySelector(`[data-metric="${key}"]`);
      if (element) element.textContent = number(value);
    });
    list('analyticsSources', data.sources);
    list('analyticsTools', data.top_tools);
    list('analyticsPages', data.top_pages);
    const daily = document.getElementById('analyticsDaily');
    daily.innerHTML = data.daily.slice(-14).reverse().map(row => `
      <tr><td>${escapeHtml(row.day)}</td><td>${number(row.visitors)}</td><td>${number(row.pageviews)}</td></tr>
    `).join('');
    document.getElementById('analyticsContent').classList.remove('hidden');
  } catch (error) {
    status.textContent = error.message;
  }
}

loadAnalytics();
