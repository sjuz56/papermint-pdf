let tools = [];

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
  tools = await fetch('/api/tools').then(r => r.json());
  render(tools);
}


function render(list) {
  const g = document.getElementById('grid');

  g.innerHTML = list.map(t => `
    <article
      class="card"
      onclick='openTool(${JSON.stringify(t)})'
    >
      <div class="icon">
        ${labels[t.id] || 'PDF'}
      </div>

      <h3>${t.name}</h3>
      <p>${t.description}</p>
    </article>
  `).join('');
}


document
  .getElementById('search')
  .addEventListener('input', e => {

    const q = e.target.value.toLowerCase();

    render(
      tools.filter(t =>
        (t.name + ' ' + t.description)
          .toLowerCase()
          .includes(q)
      )
    );
  });


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


function openTool(t) {

  document
    .getElementById('modal')
    .classList.remove('hidden');

  document.getElementById('modalTitle').textContent =
    t.name;

  document.getElementById('modalDesc').textContent =
    t.description;

  document.getElementById('toolId').value =
    t.id;

  document.getElementById('modalIcon').textContent =
    labels[t.id] || 'PDF';

  document.getElementById('status').textContent =
    '';

  document.getElementById('fileNames').textContent =
    'No files selected';

  document.getElementById('files').value =
    '';

  let x = '';


  // PDF -> WORD
  if (t.id === 'pdf-word') {

    x = `
      <input
        type="hidden"
        name="mode"
        value="editable"
      >

      <div class="field">
        <small>
          Creates an editable Word document while
          preserving the original layout as closely
          as possible.
        </small>
      </div>
    `;
  }


  // PASSWORD
  if (
    ['protect', 'unlock']
      .includes(t.id)
  ) {

    x += field(
      'Password',
      'password',
      'password',
      'Enter password'
    );
  }


  // TEXT TOOLS
  if (
    ['watermark', 'redact', 'sign']
      .includes(t.id)
  ) {

    x += field(
      t.id === 'redact'
        ? 'Text to permanently redact'
        : t.id === 'sign'
          ? 'Signature text'
          : 'Watermark text',

      'text',
      'text',

      t.id === 'watermark'
        ? 'CONF
