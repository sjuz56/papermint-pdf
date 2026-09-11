let tools = [];
let selectedFiles = [];
let activeTool = null;

const i18n = window.PaperMintI18n;
const t = (key, variables) => i18n?.t(key, variables) || key;

const multiFileTools = new Set([
  'merge',
  'jpg-pdf',
  'scan-pdf',
  'compare'
]);

const acceptedFiles = {
  'word-pdf': '.doc,.docx',
  'ppt-pdf': '.ppt,.pptx',
  'excel-pdf': '.xls,.xlsx',
  'jpg-pdf': '.jpg,.jpeg,.png,.webp,.tif,.tiff,image/jpeg,image/png,image/webp,image/tiff',
  'scan-pdf': '.jpg,.jpeg,.png,.webp,.tif,.tiff,image/jpeg,image/png,image/webp,image/tiff',
  'html-pdf': '.html,.htm,text/html'
};

const labels = {
  merge: 'MERGE',
  split: 'SPLIT',
  compress: 'ZIP',
  'pdf-word': 'DOCX',
  'pdf-ppt': 'PPTX',
  'pdf-excel': 'XLSX',
  'pdf-jpg': 'JPG',
  'word-pdf': 'PDF',
  'ppt-pdf': 'PDF',
  'excel-pdf': 'PDF',
  'jpg-pdf': 'PDF',
  sign: 'SIGN',
  watermark: 'MARK',
  rotate: '↻',
  'html-pdf': 'HTML',
  unlock: 'OPEN',
  protect: 'LOCK',
  organize: 'PAGES',
  pdfa: 'PDF/A',
  repair: 'FIX',
  'page-numbers': '123',
  'scan-pdf': 'SCAN',
  ocr: 'OCR',
  compare: 'DIFF',
  redact: 'HIDE',
  crop: 'CROP'
};

async function init() {
  try {
    const r = await fetch('/api/tools', {
      cache: 'no-store'
    });

    if (!r.ok) {
      throw new Error(t('error.loadTools'));
    }

    tools = await r.json();
    render(tools);
  } catch (err) {
    console.error(err);

    const g = document.getElementById('grid');

    if (g) {
      g.innerHTML = `<p>Error loading tools: ${err.message}</p>`;
    }
  }
}

function render(list) {
  const g = document.getElementById('grid');

  if (!g) return;

  const localized = list.map(tool => i18n?.translateTool(tool) || tool);

  g.innerHTML = localized.map((tool, i) => `
    <article
      class="card"
      data-tool-index="${i}"
    >
      <div class="icon">
        ${labels[tool.id] || 'PDF'}
      </div>

      <h3>${tool.name}</h3>
      <p>${tool.description}</p>
    </article>
  `).join('');

  g.querySelectorAll('.card').forEach((card, i) => {
    card.addEventListener('click', () => openTool(list[i]));
  });
}

const search = document.getElementById('search');

if (search) {
  search.addEventListener('input', e => {
    const q = e.target.value.toLowerCase();

    render(
      tools.filter(tool => {
        const localized = i18n?.translateTool(tool) || tool;
        return (localized.name + ' ' + localized.description)
          .toLowerCase()
          .includes(q);
      })
    );
  });
}

function field(
  label,
  name,
  type = 'text',
  placeholder = '',
  value = ''
) {
  return `
    <div class="field">
      <label>${label}</label>

      <input
        name="${name}"
        type="${type}"
        placeholder="${placeholder}"
        value="${value}"
      >
    </div>
  `;
}

function renderSelectedFiles() {
  const names = document.getElementById('fileNames');

  if (!names) return;

  if (!selectedFiles.length) {
    names.textContent = t('upload.empty');
    return;
  }

  names.textContent = selectedFiles
    .map((file, index) => `${index + 1}. ${file.name}`)
    .join('  •  ');
}

function addSelectedFiles(files) {
  const tool = document.getElementById('toolId')?.value;
  const incoming = [...files];

  if (multiFileTools.has(tool)) {
    selectedFiles.push(...incoming);
  } else {
    selectedFiles = incoming.slice(0, 1);
  }

  renderSelectedFiles();
}

function openTool(tool) {
  activeTool = tool;
  const localizedTool = i18n?.translateTool(tool) || tool;
  document
    .getElementById('modal')
    .classList.remove('hidden');

  document
    .getElementById('modalTitle')
    .textContent = localizedTool.name;

  document
    .getElementById('modalDesc')
    .textContent = localizedTool.description;

  document
    .getElementById('toolId')
    .value = tool.id;

  document
    .getElementById('modalIcon')
    .textContent = labels[tool.id] || 'PDF';

  document
    .getElementById('status')
    .textContent = '';

  selectedFiles = [];

  const fileInput = document.getElementById('files');

  fileInput.value = '';
  fileInput.multiple = multiFileTools.has(tool.id);
  fileInput.accept = acceptedFiles[tool.id] || '.pdf,application/pdf';

  renderSelectedFiles();

  let x = '';

  if (tool.id === 'pdf-word') {
    x = `
      <input
        type="hidden"
        name="mode"
        value="editable"
      >

      <div class="field">
        <small>
          ${t('hint.pdfWord')}
        </small>
      </div>
    `;
  }

  if (tool.id === 'pdf-ppt') {
    x += `
      <div class="field">
        <small>
          ${t('hint.pdfPpt')}
        </small>
      </div>
    `;
  }

  if (tool.id === 'pdf-jpg') {
    x += `<div class="field"><small>${t('hint.pdfJpg')}</small></div>`;
  }

  if (tool.id === 'ocr') {
    x += `
      <div class="field">
        <label>${t('form.documentLanguage')}</label>
        <select name="ocr_language">
          <option value="eng">English</option>
          <option value="ces">Čeština</option>
          <option value="slk">Slovenčina</option>
          <option value="deu">Deutsch</option>
          <option value="spa">Español</option>
          <option value="fra">Français</option>
          <option value="ita">Italiano</option>
          <option value="por">Português</option>
          <option value="pol">Polski</option>
          <option value="ron">Română</option>
          <option value="rus">Русский</option>
          <option value="ukr">Українська</option>
          <option value="chi_sim">简体中文</option>
          <option value="chi_tra">繁體中文</option>
          <option value="hin">हिन्दी</option>
          <option value="jpn">日本語</option>
          <option value="kor">한국어</option>
          <option value="ara">العربية</option>
        </select>
      </div>
      <div class="field"><small>${t('hint.ocr')}</small></div>
    `;
  }

  if (tool.id === 'html-pdf') {
    x += `<div class="field"><small>${t('hint.html')}</small></div>`;
  }

  if (tool.id === 'pdfa') {
    x += `<div class="field"><small>${t('hint.pdfa')}</small></div>`;
  }

  if (tool.id === 'protect') {
    x += `
      <div class="field">
        <label>${t('form.password')}</label>
        <input
          name="password"
          type="password"
          placeholder="${t('form.passwordHint')}"
          minlength="6"
          maxlength="128"
          autocomplete="new-password"
          required
        >
      </div>

      <div class="field">
        <label>${t('form.confirmPassword')}</label>
        <input
          name="password_confirm"
          type="password"
          placeholder="${t('form.confirmPasswordHint')}"
          minlength="6"
          maxlength="128"
          autocomplete="new-password"
          required
        >
      </div>

      <div class="field">
        <small>
          ${t('hint.protect')}
        </small>
      </div>
    `;
  }

  if (tool.id === 'unlock') {
    x += `
      <div class="field">
        <label>${t('form.pdfPassword')}</label>
        <input
          name="password"
          type="password"
          placeholder="${t('form.pdfPasswordHint')}"
          maxlength="128"
          autocomplete="current-password"
          required
        >
      </div>

      <div class="field">
        <small>
          ${t('hint.unlock')}
        </small>
      </div>
    `;
  }

  if (['watermark', 'redact', 'sign'].includes(tool.id)) {
    x += field(
      tool.id === 'redact'
        ? t('form.redactText')
        : tool.id === 'sign'
          ? t('form.signatureText')
          : t('form.watermarkText'),
      'text',
      'text',
      tool.id === 'watermark'
        ? t('form.confidential')
        : ''
    );
  }

  if (tool.id === 'sign') {
    x += `
      <div class="field">
        <small>
          ${t('hint.sign')}
        </small>
      </div>
    `;
  }

  if (tool.id === 'watermark') {
    x += `
      <div class="field">
        <small>
          ${t('hint.watermark')}
        </small>
      </div>
    `;
  }

  if (['split', 'organize'].includes(tool.id)) {
    x += field(
      tool.id === 'organize'
        ? t('form.pageOrder')
        : t('form.pagesRanges'),
      'pages',
      'text',
      tool.id === 'organize'
        ? '3,1,2 or 5-3'
        : '1-3,5'
    );
  }

  if (tool.id === 'rotate') {
    x += `
      <div class="field">
        <label>${t('form.rotation')}</label>

        <select name="rotation">
          <option value="90">${t('form.right')}</option>
          <option value="270">${t('form.left')}</option>
          <option value="180">180°</option>
        </select>
      </div>
    `;
  }

  if (tool.id === 'crop') {
    x += field(
      t('form.cropMargin'),
      'margin',
      'number',
      '10',
      '10'
    );
  }

  if (tool.id === 'page-numbers') {
    x += `
      <div class="field">
        <label>${t('form.startNumber')}</label>
        <input name="page_number_start" type="number" value="1" min="0" max="1000000" required>
      </div>

      <div class="field">
        <label>${t('form.format')}</label>
        <select name="page_number_format">
          <option value="number">1</option>
          <option value="page">${t('form.pageOne')}</option>
          <option value="page-total">${t('form.pageTotal')}</option>
        </select>
      </div>

      <div class="field">
        <label>${t('form.position')}</label>
        <select name="page_number_position">
          <option value="bottom-center">${t('form.bottomCenter')}</option>
          <option value="bottom-left">${t('form.bottomLeft')}</option>
          <option value="bottom-right">${t('form.bottomRight')}</option>
          <option value="top-center">${t('form.topCenter')}</option>
          <option value="top-left">${t('form.topLeft')}</option>
          <option value="top-right">${t('form.topRight')}</option>
        </select>
      </div>

      <div class="field">
        <label>${t('form.coverPage')}</label>
        <label style="display:flex;align-items:center;gap:8px;font-weight:400">
          <input name="page_number_skip_first" type="checkbox" value="true" style="width:auto;padding:0;margin:0">
          <span>${t('form.skipFirst')}</span>
        </label>
      </div>
    `;
  }

  if (tool.id === 'sign') {
    x += field(
      t('form.page'),
      'signature_page',
      'number',
      '1',
      '1'
    );

    x += field(
      t('form.xPosition'),
      'signature_x',
      'number',
      '30',
      '30'
    );

    x += field(
      t('form.yPosition'),
      'signature_y',
      'number',
      '30',
      '30'
    );
  }

  document
    .getElementById('extra')
    .innerHTML = x;
}

function closeModal() {
  document
    .getElementById('modal')
    .classList.add('hidden');
}

window.openTool = openTool;
window.closeModal = closeModal;

const dz = document.getElementById('dropZone');
const fi = document.getElementById('files');

if (fi) {
  fi.addEventListener('change', () => {
    addSelectedFiles(fi.files);

    // Umožní znovu otevřít výběr a přidat další soubor.
    fi.value = '';
  });
}

if (dz && fi) {
  ['dragenter', 'dragover'].forEach(ev =>
    dz.addEventListener(ev, e => {
      e.preventDefault();
      dz.classList.add('drag');
    })
  );

  ['dragleave', 'drop'].forEach(ev =>
    dz.addEventListener(ev, e => {
      e.preventDefault();
      dz.classList.remove('drag');
    })
  );

  dz.addEventListener('drop', e => {
    addSelectedFiles(e.dataTransfer.files);
  });
}

async function downloadBlobResponse(response, filename) {
  const blob = await response.blob();

  if (!blob || blob.size === 0) {
    throw new Error(t('error.emptyDownload'));
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');

  a.href = url;
  a.download = filename;

  document.body.appendChild(a);

  a.click();
  a.remove();

  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 15000);
}

const form = document.getElementById('convertForm');

if (form) {
  form.addEventListener('submit', async e => {
    e.preventDefault();

    const s = document.getElementById('status');
    const tool = document.getElementById('toolId').value;
    const fd = new FormData(e.target);

    // Nativní políčko se po každém výběru vymaže.
    // Zde do formuláře vrátíme všechny postupně vybrané soubory.
    fd.delete('files');

    selectedFiles.forEach(file => {
      fd.append('files', file);
    });

    s.textContent = t('status.processing');

    try {
      if (tool === 'protect') {
        const password = fd.get('password') || '';
        const confirmation = fd.get('password_confirm') || '';

        if (password.length < 6) {
          throw new Error(
            t('error.passwordLength')
          );
        }

        if (password !== confirmation) {
          throw new Error(t('error.passwordMatch'));
        }
      }

      if (tool === 'unlock' && !fd.get('password')) {
        throw new Error(t('error.pdfPassword'));
      }

      if (tool === 'sign' && !(fd.get('text') || '').trim()) {
        throw new Error(t('error.signature'));
      }

      if (tool === 'watermark' && !(fd.get('text') || '').trim()) {
        throw new Error(t('error.watermark'));
      }

      if (tool === 'redact' && !(fd.get('text') || '').trim()) {
        throw new Error(t('error.redact'));
      }

      if (!selectedFiles.length) {
        throw new Error(t('error.noFiles'));
      }

      // =========================================
      // PDF -> WORD
      // =========================================

      if (tool === 'pdf-word') {
        if (!selectedFiles.length) {
          throw new Error(t('error.noPdf'));
        }

        const upload = new FormData();

        upload.append('file', selectedFiles[0]);

        const startResponse = await fetch(
          '/api/pdf-word/start',
          {
            method: 'POST',
            body: upload
          }
        );

        if (!startResponse.ok) {
          let message = t('error.conversion');

          try {
            const data = await startResponse.json();
            message = data?.detail || message;
          } catch (_) {}

          throw new Error(message);
        }

        const startData = await startResponse.json();
        const jobId = startData.job_id;

        if (!jobId) {
          throw new Error(t('error.missingJob'));
        }

        s.textContent = t('status.convertingWord');

        while (true) {
          await new Promise(resolve =>
            setTimeout(resolve, 2000)
          );

          const statusResponse = await fetch(
            `/api/pdf-word/status/${jobId}`,
            {
              cache: 'no-store'
            }
          );

          if (!statusResponse.ok) {
            throw new Error(
              t('error.status')
            );
          }

          const statusData = await statusResponse.json();

          if (statusData.status === 'error') {
            throw new Error(
              statusData.error || t('error.conversion')
            );
          }

          if (statusData.status === 'done') {
            break;
          }
        }

        s.textContent = t('status.preparingWord');

        const downloadResponse = await fetch(
          `/api/pdf-word/download/${jobId}`,
          {
            method: 'GET',
            cache: 'no-store'
          }
        );

        if (!downloadResponse.ok) {
          let message = t('error.download');

          try {
            const data = await downloadResponse.json();
            message = data?.detail || message;
          } catch (_) {}

          throw new Error(message);
        }

        const contentType =
          downloadResponse.headers.get('content-type') || '';

        if (contentType.includes('application/json')) {
          const data = await downloadResponse.json();

          throw new Error(
            data?.detail ||
            t('error.serverJson')
          );
        }

        await downloadBlobResponse(
          downloadResponse,
          'converted.docx'
        );

        s.textContent = t('status.doneWord');

        return;
      }

      // =========================================
      // ALL OTHER TOOLS
      // =========================================

      const startResponse = await fetch('/api/convert', {
        method: 'POST',
        body: fd
      });

      if (!startResponse.ok) {
        let message = t('error.processing');

        try {
          const data = await startResponse.json();
          message = data?.detail || message;
        } catch (_) {}

        throw new Error(message);
      }

      const startData = await startResponse.json();
      const jobId = startData.job_id;

      if (!jobId) {
        throw new Error(t('error.missingJob'));
      }

      while (true) {
        const position = startData.queue_position;

        s.textContent = position
          ? t('status.waitingPosition', {position})
          : t('status.waiting');

        await new Promise(resolve =>
          setTimeout(resolve, 1500)
        );

        const statusResponse = await fetch(
          `/api/jobs/status/${jobId}`,
          {
            cache: 'no-store'
          }
        );

        if (!statusResponse.ok) {
          let message = t('error.status');

          try {
            const data = await statusResponse.json();
            message = data?.detail || message;
          } catch (_) {}

          throw new Error(message);
        }

        const statusData = await statusResponse.json();

        if (statusData.status === 'error') {
          throw new Error(
            statusData.error || t('error.processing')
          );
        }

        if (statusData.status === 'done') {
          break;
        }

        if (statusData.status === 'processing') {
          s.textContent = t('status.processing');
        } else if (statusData.queue_position) {
          startData.queue_position =
            statusData.queue_position;
        }
      }

      s.textContent = t('status.downloading');

      const downloadResponse = await fetch(
        `/api/jobs/download/${jobId}`,
        {
          method: 'GET',
          cache: 'no-store'
        }
      );

      if (!downloadResponse.ok) {
        let message = t('error.download');

        try {
          const data = await downloadResponse.json();
          message = data?.detail || message;
        } catch (_) {}

        throw new Error(message);
      }

      let name = 'result';
      const cd =
        downloadResponse.headers.get(
          'content-disposition'
        ) || '';
      const m = cd.match(
        /filename="?([^";]+)"?/
      );

      if (m) {
        name = m[1];
      }

      await downloadBlobResponse(
        downloadResponse,
        name
      );

      s.textContent = t('status.done');
    } catch (err) {
      console.error(err);

      s.textContent =
        `${t('error.prefix')}: ${err.message}`;
    }
  });
}

window.addEventListener('papermint:languagechange', () => {
  render(tools);
  renderSelectedFiles();

  if (activeTool && !document.getElementById('modal')?.classList.contains('hidden')) {
    const preservedFiles = [...selectedFiles];
    openTool(activeTool);
    selectedFiles = preservedFiles;
    renderSelectedFiles();
  }
});

init();
