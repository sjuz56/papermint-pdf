let TOOLS = [];
let currentTool = null;

const grid = document.getElementById("grid");
const search = document.getElementById("search");
const modal = document.getElementById("modal");
const modalTitle = document.getElementById("modalTitle");
const modalDesc = document.getElementById("modalDesc");
const modalIcon = document.getElementById("modalIcon");
const toolId = document.getElementById("toolId");
const filesInput = document.getElementById("files");
const fileNames = document.getElementById("fileNames");
const extra = document.getElementById("extra");
const status = document.getElementById("status");
const form = document.getElementById("convertForm");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadTools() {
  try {
    const response = await fetch("/api/tools");
    if (!response.ok) throw new Error("Could not load tools");

    TOOLS = await response.json();
    renderTools(TOOLS);
  } catch (err) {
    console.error(err);
    if (grid) grid.innerHTML = "<p>Could not load PDF tools.</p>";
  }
}

function renderTools(tools) {
  if (!grid) return;

  grid.innerHTML = tools.map(tool => `
    <button type="button" class="card" onclick="openTool('${tool.id}')">
      <div class="tool-icon">PDF</div>
      <div>
        <h3>${escapeHtml(tool.name)}</h3>
        <p>${escapeHtml(tool.description)}</p>
      </div>
    </button>
  `).join("");
}

function openTool(id) {
  const tool = TOOLS.find(t => t.id === id);
  if (!tool) return;

  currentTool = tool;

  modalTitle.textContent = tool.name;
  modalDesc.textContent = tool.description;
  modalIcon.textContent = "PDF";
  toolId.value = tool.id;

  filesInput.value = "";
  fileNames.textContent = "No files selected";
  status.innerHTML = "";
  extra.innerHTML = "";

  if (tool.id === "pdf-word") {
    extra.innerHTML = `
      <p>
        Creates an editable Word document while preserving
        the original layout as closely as possible.
      </p>
    `;
  } else if (tool.id === "split") {
    extra.innerHTML = `
      <label>Pages / ranges</label>
      <input name="ranges" type="text"
             placeholder="Example: 1-3,5,7-9"/>
    `;
  } else if (tool.id === "rotate") {
    extra.innerHTML = `
      <label>Rotation</label>
      <select name="rotation">
        <option value="90">90°</option>
        <option value="180">180°</option>
        <option value="270">270°</option>
      </select>
    `;
  } else if (tool.id === "organize") {
    extra.innerHTML = `
      <label>Page order</label>
      <input name="pages" type="text"
             placeholder="Example: 3,1,2"/>
    `;
  } else if (tool.id === "watermark") {
    extra.innerHTML = `
      <label>Watermark text</label>
      <input name="text" type="text"
             placeholder="CONFIDENTIAL"/>
    `;
  } else if (tool.id === "sign") {
    extra.innerHTML = `
      <label>Signature text</label>
      <input name="text" type="text"
             placeholder="Your name"/>

      <label>Page</label>
      <input name="page" type="number"
             min="1" value="1"/>
    `;
  } else if (
    tool.id === "protect" ||
    tool.id === "unlock"
  ) {
    extra.innerHTML = `
      <label>Password</label>
      <input name="password" type="password"
             placeholder="Password"/>
    `;
  } else if (tool.id === "redact") {
    extra.innerHTML = `
      <label>Text to redact</label>
      <input name="text" type="text"
             placeholder="Text to permanently remove"/>
    `;
  } else if (tool.id === "crop") {
    extra.innerHTML = `
      <label>Crop margin (mm)</label>
      <input name="margin" type="number"
             min="0" value="10"/>
    `;
  }

  modal.classList.remove("hidden");
}

function closeModal() {
  modal.classList.add("hidden");
  currentTool = null;
}

window.openTool = openTool;
window.closeModal = closeModal;

filesInput.addEventListener("change", () => {
  const names = [...filesInput.files].map(file => file.name);

  fileNames.textContent =
    names.length ? names.join(", ") : "No files selected";
});

search.addEventListener("input", () => {
  const q = search.value.toLowerCase().trim();

  renderTools(
    TOOLS.filter(tool =>
      tool.name.toLowerCase().includes(q) ||
      tool.description.toLowerCase().includes(q)
    )
  );
});

modal.addEventListener("click", event => {
  if (event.target === modal) closeModal();
});

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function readError(response) {
  try {
    const data = await response.json();

    return data.detail ||
           data.error ||
           `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");

  a.href = url;
  a.download = filename;

  document.body.appendChild(a);
  a.click();
  a.remove();

  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1500);
}

async function convertPdfToWord(file) {
  status.innerHTML = "<p>Uploading PDF…</p>";

  const upload = new FormData();
  upload.append("file", file);

  const start = await fetch("/api/pdf-word/start", {
    method: "POST",
    body: upload
  });

  if (!start.ok) {
    throw new Error(await readError(start));
  }

  const job = await start.json();

  if (!job.job_id) {
    throw new Error("Conversion could not be started.");
  }

  status.innerHTML = `
    <p>
      Converting PDF to editable Word…
      <br>
      <small>Complex documents may take a minute or two.</small>
    </p>
  `;

  for (let i = 0; i < 240; i++) {
    await sleep(2000);

    const response = await fetch(
      `/api/pdf-word/status/${encodeURIComponent(job.job_id)}`,
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const info = await response.json();

    if (info.status === "error") {
      throw new Error(
        info.error || "PDF to Word conversion failed."
      );
    }

    if (info.status === "done") {
      status.innerHTML = "<p>Preparing Word document…</p>";

      const download = await fetch(
        `/api/pdf-word/download/${encodeURIComponent(job.job_id)}`
      );

      if (!download.ok) {
        throw new Error(await readError(download));
      }

      const blob = await download.blob();

      downloadBlob(blob, "converted.docx");

      status.innerHTML =
        "<p>✓ Word document ready</p>";

      return;
    }
  }

  throw new Error("Conversion is taking too long.");
}

form.addEventListener("submit", async event => {
  event.preventDefault();

  if (!currentTool) return;

  const selectedFiles = [...filesInput.files];

  if (!selectedFiles.length) {
    status.innerHTML = "<p>Please select a file.</p>";
    return;
  }

  const submitButton =
    form.querySelector('button[type="submit"]');

  submitButton.disabled = true;

  try {
    if (currentTool.id === "pdf-word") {
      await convertPdfToWord(selectedFiles[0]);
      return;
    }

    status.innerHTML = "<p>Processing file…</p>";

    const data = new FormData(form);

    const response = await fetch("/api/convert", {
      method: "POST",
      body: data
    });

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const blob = await response.blob();

    let filename = "converted-file";

    const disposition =
      response.headers.get("content-disposition");

    if (disposition) {
      const match =
        disposition.match(/filename="?([^"]+)"?/i);

      if (match && match[1]) {
        filename = match[1];
      }
    }

    downloadBlob(blob, filename);

    status.innerHTML = "<p>✓ File ready</p>";

  } catch (err) {
    console.error(err);

    status.innerHTML = `
      <p>
        Error: ${escapeHtml(err.message || String(err))}
      </p>
    `;
  } finally {
    submitButton.disabled = false;
  }
});

loadTools();
