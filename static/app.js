let tools = [];
let selectedFiles = [];
let activeTool = null;
let askOctoSession = null;

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
  crop: 'CROP',
  'ask-octo': 'AI'
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
      class="card${tool.pro ? ' pro-card' : ''}"
      data-tool-index="${i}"
    >
      ${tool.pro ? `<span class="pro-badge">${t('ai.pro')}</span>` : ''}
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

  askOctoSession = null;

  selectedFiles = [];

  const fileInput = document.getElementById('files');

  fileInput.value = '';
  fileInput.multiple = multiFileTools.has(tool.id);
  fileInput.accept = acceptedFiles[tool.id] || '.pdf,application/pdf';

  const dropZone = document.getElementById('dropZone');
  const processButton = document.getElementById('processButton');
  document.getElementById('extra')?.classList.remove('hidden');
  dropZone?.classList.remove('hidden');
  processButton?.classList.remove('hidden');
  if (processButton) processButton.textContent = t('upload.process');

  renderSelectedFiles();

  let x = '';

  if (tool.id === 'ask-octo') {
    const account = window.PaperMintAccount;
    if (account?.plan !== 'pro') {
      dropZone?.classList.add('hidden');
      processButton?.classList.add('hidden');
      x = `
        <div class="ai-upgrade">
          <img src="/static/assets/octopus-mascot-transparent.png" alt="" aria-hidden="true">
          <strong>${t('ai.proRequired')}</strong>
          <p>${t('ai.proDescription')}</p>
          <button class="primary full" id="askOctoUpgrade" type="button">${t('ai.choosePlan')}</button>
        </div>
      `;
    } else {
      if (processButton) processButton.textContent = t('ai.summarize');
      x = `
        <div class="ai-settings">
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
          <div class="field">
            <label>${t('ai.answerLanguage')}</label>
            <select name="response_language">
              <option value="en">English</option>
              <option value="cs">Čeština</option>
              <option value="de">Deutsch</option>
              <option value="es">Español</option>
              <option value="fr">Français</option>
              <option value="zh">中文</option>
              <option value="hi">हिन्दी</option>
              <option value="ja">日本語</option>
            </select>
          </div>
        </div>
        <div class="ai-privacy">${t('ai.privacy')}</div>
      `;
    }
  }

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

  document.getElementById('askOctoUpgrade')?.addEventListener('click', () => {
    closeModal();
    if (!window.PaperMintAccount?.authenticated && window.openPaperMintAuth) {
      window.openPaperMintAuth();
      return;
    }
    document.getElementById('pricing')?.scrollIntoView({behavior: 'smooth'});
  });
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

function appendAiMessage(container, role, text, sourcePages = []) {
  const message = document.createElement('div');
  message.className = `ai-message ${role}`;

  const label = document.createElement('strong');
  label.textContent = role === 'user' ? t('ai.you') : 'Ask Octo';
  message.appendChild(label);

  const copy = document.createElement('div');
  copy.className = 'ai-message-copy';
  copy.textContent = text;
  message.appendChild(copy);

  if (sourcePages.length) {
    const sources = document.createElement('small');
    sources.textContent = t('ai.sourcePages', {pages: sourcePages.join(', ')});
    message.appendChild(sources);
  }
  container.appendChild(message);
  container.scrollTop = container.scrollHeight;
}

function renderAskOctoResult(data, responseLanguage) {
  askOctoSession = {
    id: data.session_id,
    remaining: data.questions_remaining,
    responseLanguage
  };

  const status = document.getElementById('status');
  status.textContent = '';

  const workspace = document.createElement('section');
  workspace.className = 'ai-workspace';

  const heading = document.createElement('div');
  heading.className = 'ai-result-heading';
  const title = document.createElement('strong');
  title.textContent = t('ai.summary');
  const usage = document.createElement('small');
  usage.textContent = t('ai.documentUsage', {
    used: data.usage.documents_used,
    limit: data.usage.documents_limit
  });
  heading.append(title, usage);
  workspace.appendChild(heading);

  const conversation = document.createElement('div');
  conversation.className = 'ai-conversation';
  appendAiMessage(conversation, 'octo', data.summary);
  workspace.appendChild(conversation);

  const questionForm = document.createElement('div');
  questionForm.className = 'ai-question-form';
  const question = document.createElement('textarea');
  question.name = 'question';
  question.rows = 2;
  question.maxLength = 600;
  question.required = true;
  question.placeholder = t('ai.questionPlaceholder');
  question.setAttribute('aria-label', t('ai.questionPlaceholder'));
  const askButton = document.createElement('button');
  askButton.type = 'button';
  askButton.className = 'primary';
  askButton.textContent = t('ai.ask');
  const remaining = document.createElement('small');
  remaining.className = 'ai-remaining';
  remaining.textContent = t('ai.questionsRemaining', {count: askOctoSession.remaining});
  questionForm.append(question, askButton, remaining);
  workspace.appendChild(questionForm);
  status.appendChild(workspace);

  document.getElementById('dropZone')?.classList.add('hidden');
  document.getElementById('extra')?.classList.add('hidden');
  document.getElementById('processButton')?.classList.add('hidden');

  const submitQuestion = async event => {
    event?.preventDefault();
    event?.stopPropagation();
    const prompt = question.value.trim();
    if (!prompt || !askOctoSession?.id) return;

    appendAiMessage(conversation, 'user', prompt);
    question.value = '';
    askButton.disabled = true;
    askButton.textContent = t('ai.thinking');
    try {
      const response = await fetch('/api/ai/question', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          session_id: askOctoSession.id,
          question: prompt,
          response_language: askOctoSession.responseLanguage
        })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || t('ai.failed'));
      askOctoSession.remaining = result.questions_remaining;
      appendAiMessage(conversation, 'octo', result.answer, result.source_pages || []);
      remaining.textContent = t('ai.questionsRemaining', {count: askOctoSession.remaining});
      if (askOctoSession.remaining <= 0) {
        question.disabled = true;
        askButton.disabled = true;
      }
    } catch (error) {
      appendAiMessage(conversation, 'octo', `${t('error.prefix')}: ${error.message}`);
    } finally {
      if (askOctoSession?.remaining > 0) askButton.disabled = false;
      askButton.textContent = t('ai.ask');
    }
  };
  askButton.addEventListener('click', submitQuestion);
  question.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) submitQuestion(event);
  });
}

async function processAskOcto(formData) {
  const responseLanguage = formData.get('response_language') || i18n?.language || 'en';
  const upload = new FormData();
  upload.append('file', selectedFiles[0]);
  upload.append('ocr_language', formData.get('ocr_language') || 'eng');
  upload.append('response_language', responseLanguage);

  const response = await fetch('/api/ai/document', {method: 'POST', body: upload});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || t('ai.failed'));
  renderAskOctoResult(data, responseLanguage);
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

      if (tool === 'ask-octo') {
        s.textContent = t('ai.reading');
        await processAskOcto(fd);
        return;
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
