let tools=[];
const labels={merge:'MERGE',split:'SPLIT',compress:'ZIP','pdf-word':'DOCX','pdf-ppt':'PPTX','pdf-excel':'XLSX','pdf-jpg':'JPG','word-pdf':'PDF','ppt-pdf':'PDF','excel-pdf':'PDF','jpg-pdf':'PDF',sign:'SIGN',watermark:'MARK',rotate:'↻','html-pdf':'HTML',unlock:'OPEN',protect:'LOCK',organize:'PAGES',pdfa:'PDF/A',repair:'FIX','page-numbers':'123','scan-pdf':'SCAN',ocr:'OCR',compare:'DIFF',redact:'HIDE',crop:'CROP'};
async function init(){tools=await fetch('/api/tools').then(r=>r.json());render(tools)}
function render(list){const g=document.getElementById('grid');g.innerHTML=list.map(t=>`<article class="card" onclick='openTool(${JSON.stringify(t)})'><div class="icon">${labels[t.id]||'PDF'}</div><h3>${t.name}</h3><p>${t.description}</p></article>`).join('')}
document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();render(tools.filter(t=>(t.name+' '+t.description).toLowerCase().includes(q)))})
function field(label,name,type='text',placeholder='',value=''){return `<div class="field"><label>${label}</label><input name="${name}" type="${type}" placeholder="${placeholder}" value="${value}"></div>`}
function openTool(t){document.getElementById('modal').classList.remove('hidden');document.getElementById('modalTitle').textContent=t.name;document.getElementById('modalDesc').textContent=t.description;document.getElementById('toolId').value=t.id;document.getElementById('modalIcon').textContent=labels[t.id]||'PDF';document.getElementById('status').textContent='';document.getElementById('fileNames').textContent='No files selected';document.getElementById('files').value='';let x='';
if(t.id==='pdf-word')x=`<div class="field"><label>Conversion mode</label><div class="mode-row"><label class="mode"><input type="radio" name="mode" value="visual" checked><b>Visual 1:1</b><br><small>Best visual match</small></label><label class="mode"><input type="radio" name="mode" value="editable"><b>Editable</b><br><small>More editable text</small></label></div></div>`;
if(['protect','unlock'].includes(t.id))x+=field('Password','password','password','Enter password');
if(['watermark','redact','sign'].includes(t.id))x+=field(t.id==='redact'?'Text to permanently redact':t.id==='sign'?'Signature text':'Watermark text','text','text',t.id==='watermark'?'CONFIDENTIAL':'');
if(['split','organize'].includes(t.id))x+=field(t.id==='organize'?'Page order':'Pages / ranges','pages','text',t.id==='organize'?'3,1,2':'1-3,5');
if(t.id==='rotate')x+=`<div class="field"><label>Rotation</label><select name="rotation"><option>90</option><option>180</option><option>270</option></select></div>`;
if(t.id==='crop')x+=field('Crop margin (mm)','margin','number','10','10');
if(t.id==='page-numbers')x+=field('Start number','page_number_start','number','1','1');
if(t.id==='sign'){x+=field('Page','signature_page','number','1','1')+field('X position (mm)','signature_x','number','30','30')+field('Y position (mm)','signature_y','number','30','30')}
document.getElementById('extra').innerHTML=x;
}
function closeModal(){document.getElementById('modal').classList.add('hidden')}
const dz=document.getElementById('dropZone'), fi=document.getElementById('files');
fi.addEventListener('change',()=>document.getElementById('fileNames').textContent=[...fi.files].map(f=>f.name).join(', ')||'No files selected');
['dragenter','dragover'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('drag')}));
['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove('drag')}));
dz.addEventListener('drop',e=>{fi.files=e.dataTransfer.files;document.getElementById('fileNames').textContent=[...fi.files].map(f=>f.name).join(', ')});
document.getElementById('convertForm').addEventListener('submit',async e=>{e.preventDefault();const s=document.getElementById('status');s.textContent='Processing…';const fd=new FormData(e.target);try{const r=await fetch('/api/convert',{method:'POST',body:fd});if(!r.ok){const j=await r.json();throw new Error(j.detail||'Conversion failed')}const blob=await r.blob();let name='result';const cd=r.headers.get('content-disposition')||'';const m=cd.match(/filename="?([^";]+)"?/);if(m)name=m[1];const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);s.textContent='Done. Your file has been prepared.'}catch(err){s.textContent='Error: '+err.message}})
init();
