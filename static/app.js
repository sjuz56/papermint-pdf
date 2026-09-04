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


async function loadTools() {
  try {
    const response = await fetch("/api/tools");

    if (!response.ok) {
      throw new Error("Could not load tools");
    }

    TOOLS = await response.json();
    renderTools(TOOLS);

  } catch (err) {
    console.error(err);

    if (grid) {
      grid.innerHTML =
        `<p>Could not load PDF tools.</p>`;
    }
  }
}


function renderTools(tools) {
  if (!grid) return;

  grid.innerHTML = tools.map(tool => `
    <button
      type="button"
      class="card"
      onclick="openTool('${tool.id}')"
    >
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

  /*
   *
