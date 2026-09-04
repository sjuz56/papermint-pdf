const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

let tools = [];
let currentTool = null;

async function loadTools() {
  const r = await fetch('/api/tools');
  tools = await r.json();
  renderTools(tools);
}

function renderTools(list) {
  const grid = $('#toolsGrid');
  if (!grid) return;

  grid.innerHTML = list.map(t => `
    <button class="tool-card" data-id="${t.id}">
      <div class="tool-icon">↗</div>
      <div>
        <h3>${t.name}</h3>
        <p>${t.description}</p>
      </div>
    </button>
  `).join('');

  $$('.tool-card').forEach(card => {
    card.addEventListener('click', () => {
      const tool = tools.find(x => x.id === card.dataset.id);
      if (tool) openTool(tool);
    });
  });
}

function openTool(tool) {
  currentTool = tool;

  const modal = $('#toolModal');
  const title = $('#modalTitle');
  const desc = $('#modalDescription');
  const options = $('#toolOptions');
  const result = $('#result');

  if (title) title.textContent = tool.name;
  if (desc) desc.textContent = tool.description;
  if (result) result.innerHTML = '';

  let html = '';

  if (tool.id === 'merge') {
    html = `
      <div class="field">
        <label>Select PDF files</label>
        <input id="toolFiles" type="file"
               accept=".pdf,application/pdf" multiple>
      </div>
    `;
  } else if (tool.id === 'compare') {
    html = `
      <div class="field">
        <label>Select two PDF files</label>
        <input id="toolFiles" type="file"
               accept=".pdf,application/pdf" multiple>
      </div>
    `;
  } else {
    html = `
      <div class="field">
        <label>Select file</label>
        <input id="toolFiles" type="file">
      </div>
    `;
  }

  if (tool.id === 'pdf-word') {
    html += `
      <div class="field">
        <p>
          Creates an editable Word document while preserving
          the original layout as closely as possible.
        </p>
      </div>
    `;
  }

  if (tool.id === 'split') {
    html += `
      <div class="field">
        <label>Pages / ranges</label>
        <input id="ranges" type="text"
               placeholder="Example: 1-3,5,7-9">
      </div>
    `;
  }

  if (tool.id === 'rotate') {
    html += `
      <div class="field">
        <label>Rotation</label>
        <select id="rotation">
          <option value="90">90°</option>
          <option value="180">180°</option>
          <option value="270">270°</option>
        </select>
      </div>
    `;
  }

  if (tool.id === 'organize') {
    html += `
      <div class="field">
        <label>Page order</label>
        <input id="pages" type="text"
               placeholder="Example: 3,1,2">
      </div>
    `;
  }

  if (tool.id === 'watermark') {
    html += `
      <div class="field">
        <label>Watermark text</label>
        <input id="text" type="text"
               placeholder="CONFIDENTIAL">
      </div>
    `;
  }

  if (tool.id === 'sign') {
    html += `
      <div class="field">
        <label>Signature text</label>
        <input id="text" type="text"
               placeholder="Your name">
      </div>

      <div class="field">
        <label>Page</label>
        <input id="page" type="number"
               min="1" value="1">
      </div>
    `;
  }

  if (tool.id === 'protect') {
    html += `
      <div class="field">
        <label>Password</label>
        <input id="password" type="password"
               placeholder="Password">
      </div>
    `;
  }

  if (tool.id === 'unlock') {
    html += `
      <div class="field">
        <label>Current password</label>
        <input id="password" type="password"
               placeholder="Password">
      </div>
    `;
  }

  if (tool.id === 'redact') {
    html += `
      <div class="field">
        <label>Text to redact</label>
        <input id="text" type="text"
               placeholder="Text to permanently remove">
      </div>
    `;
  }

  if (tool.id === 'crop') {
    html += `
      <div class="field">
        <label>Crop margin (mm)</label>
        <input id="margin" type="number"
               value="10" min="0">
      </div>
    `;
  }

  options.innerHTML = html;
  modal.classList.add('open');
}

function closeTool() {
  const modal = $('#toolModal');
  if (modal) modal.classList.remove('open');
  currentTool = null;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function getErrorMessage(response) {
  try {
    const data = await response.json();
    return data.detail || data.error || JSON.stringify(data);
  } catch {
    try {
      return await response.text();
    } catch {
      return `HTTP ${response.status}`;
    }
  }
}

/*
 * PDF -> Word is special.
 *
 * The server starts the conversion in the background.
 * This prevents Render / the browser from killing a long request
 * while pdf2docx is rebuilding the document.
 */
async function processPdfWord(file) {
  const result = $('#result');

  result.innerHTML = `
    <div class="processing">
      Uploading PDF…
    </div>
  `;

  const form = new FormData();
  form.append('file', file);

  const startResponse = await fetch('/api/pdf-word/start', {
    method: 'POST',
    body: form
  });

  if (!startResponse.ok) {
    throw new Error(await getErrorMessage(startResponse));
  }

  const started = await startResponse.json();
  const jobId = started.job_id;

  if (!jobId) {
    throw new Error('Server did not return a conversion job ID.');
  }

  let attempts = 0;
  const maxAttempts = 240;

  while (attempts < maxAttempts) {
    attempts++;

    result.innerHTML = `
      <div class="processing">
        Converting PDF to editable Word…
        <br>
        <small>This may take a minute.</small>
      </div>
    `;

    await sleep(2000);

    const statusResponse = await fetch(
      `/api/pdf-word/status/${encodeURIComponent(jobId)}`,
      { cache: 'no-store' }
    );

    if (!statusResponse.ok) {
      throw new Error(await getErrorMessage(statusResponse));
    }

    const status = await statusResponse.json();

    if (status.status === 'error') {
      throw new Error(
        status.error || 'PDF to Word conversion failed.'
      );
    }

    if (status.status === 'done') {
      result.innerHTML = `
        <div class="processing">
          Preparing Word document…
        </div>
      `;

      const downloadResponse = await fetch(
        `/api/pdf-word/download/${encodeURIComponent(jobId)}`
      );

      if (!downloadResponse.ok) {
        throw new Error(await getErrorMessage(downloadResponse));
      }

      const blob = await downloadResponse.blob();

      downloadBlob(blob, 'converted.docx');

      result.innerHTML = `
        <div class="success">
          ✓ Word document ready
        </div>
      `;

      return;
    }
  }

  throw new Error(
    'Conversion is taking too long. Please try again.'
  );
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');

  a.href = url;
  a.download = filename;

  document.body.appendChild(a);
  a.click();
  a.remove();

  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
}

async function processTool() {
  if (!currentTool) return;

  const input = $('#toolFiles');
  const result = $('#result');
  const button = $('#processButton');

  if (!input || !input.files || input.files.length === 0) {
    result.innerHTML = `
      <div class="error">
        Please select a file first.
      </div>
    `;
    return;
  }

  /*
   * New asynchronous PDF -> Word route.
   */
  if (currentTool.id === 'pdf-word') {
    try {
      if (button) button.disabled = true;

      await processPdfWord(input.files[0]);

    } catch (err) {
      console.error(err);

      result.innerHTML = `
        <div class="error">
          Error: ${escapeHtml(err.message || String(err))}
        </div>
      `;
    } finally {
      if (button) button.disabled = false;
    }

    return;
  }

  const form = new FormData();

  [...input.files].forEach(file => {
    form.append('files', file);
  });

  form.append('tool', currentTool.id);

  const optionalFields = [
    'ranges',
    'rotation',
    'pages',
    'text',
    'page',
    'password',
    'margin'
  ];

  optionalFields.forEach(id => {
    const el = $('#' + id);

    if (el && el.value !== '') {
      form.append(id, el.value);
    }
  });

  try {
    if (button) button.disabled = true;

    result.innerHTML = `
      <div class="processing">
        Processing file…
      </div>
    `;

    const response = await fetch('/api/convert', {
      method: 'POST',
      body: form
    });

    if (!response.ok) {
      throw new Error(await getErrorMessage(response));
    }

    const blob = await response.blob();

    let filename = 'converted-file';

    const disposition = response.headers.get(
      'content-disposition'
    );

    if (disposition) {
      const match = disposition.match(
        /filename="?([^"]+)"?/i
      );

      if (match && match[1]) {
        filename = match[1];
      }
    }

    downloadBlob(blob, filename);

    result.innerHTML = `
      <div class="success">
        ✓ File ready
      </div>
    `;

  } catch (err) {
    console.error(err);

    result.innerHTML = `
      <div class="error">
        Error: ${escapeHtml(err.message || String(err))}
      </div>
    `;
  } finally {
    if (button) button.disabled = false;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

document.addEventListener('DOMContentLoaded', () => {
  loadTools();

  const close = $('#closeModal');
  if (close) {
    close.addEventListener('click', closeTool);
  }

  const modal = $('#toolModal');
  if (modal) {
    modal.addEventListener('click', e => {
      if (e.target === modal) closeTool();
    });
  }

  const process = $('#processButton');
  if (process) {
    process.addEventListener('click', processTool);
  }

  const search = $('#searchTools');
  if (search) {
    search.addEventListener('input', e => {
      const q = e.target.value.toLowerCase().trim();

      renderTools(
        tools.filter(t =>
          t.name.toLowerCase().includes(q) ||
          t.description.toLowerCase().includes(q)
        )
      );
    });
  }
});
