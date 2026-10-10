const file=document.getElementById('file'),pages=document.getElementById('pages'),status=document.getElementById('status'),editor=document.getElementById('editor'),selection=document.getElementById('selection'),replacement=document.getElementById('replacement'),saveBtn=document.getElementById('save'),applyBtn=document.getElementById('apply'),undoBtn=document.getElementById('undo'),cancelBtn=document.getElementById('cancel'),boldBtn=document.getElementById('bold');
let changes=[],history=[],chosen=null,spanElements=new Map(),loadedFile=null,verifiedPdf=null,originalPages=[],requestId=0,busy=false;
const changeKey=c=>[c.page,c.old_text,c.occurrence].join('\u0000');
const copyChanges=value=>value.map(c=>({...c}));
const isBold=span=>Boolean(span.flags&16)||/bold|demi|black/i.test(span.font);
function position(record,bbox){
  const [x0,y0,x1,y1]=bbox,info=record.info,el=record.el;
  el.style.left=(x0/info.width*100)+'%';el.style.top=(y0/info.height*100)+'%';
  el.style.width=(Math.max(4,x1-x0)/info.width*100)+'%';el.style.height=(Math.max(5,y1-y0)/info.height*100)+'%';
}
function refresh(){
  for(const [key,record] of spanElements){
    const change=changes.find(c=>changeKey(c)===key),el=record.el;
    el.dataset.edited=change?'true':'false';el.title=change?change.new_text:record.span.text;
    el.setAttribute('aria-label','Edit '+(change?change.new_text:record.span.text));
    el.classList.toggle('editing',chosen?.record===record);
  }
  saveBtn.disabled=busy||!loadedFile||(!changes.length&&!chosen);
  applyBtn.disabled=busy||!chosen;cancelBtn.disabled=busy;undoBtn.disabled=busy||!history.length;
  replacement.disabled=busy;file.disabled=busy;
  boldBtn.disabled=busy||!chosen;boldBtn.setAttribute('aria-pressed',chosen?.bold?'true':'false');
}
function closeEditor(){
  if(chosen)position(chosen.record,chosen.record.displayBBox);
  editor.append(replacement);editor.hidden=true;chosen=null;refresh();
}
function resizeInput(){
  replacement.style.height='auto';replacement.style.height=Math.max(24,replacement.scrollHeight+2)+'px';
}
function updateInputStyle(){
  if(!chosen)return;
  const {record,bold}=chosen,span=record.span;
  const scale=record.el.closest('.page').clientWidth/record.info.width;
  replacement.style.fontSize=(span.size*scale)+'px';
  replacement.style.fontWeight=bold?'700':'400';resizeInput();
}
function toggleBold(){
  if(busy||!chosen)return;
  chosen.bold=!chosen.bold;updateInputStyle();refresh();replacement.focus();
}
async function responseError(response){
  let message='HTTP '+response.status;
  try{message=(await response.json()).detail||message}catch{}
  return new Error(message);
}
async function inspect(source){
  const form=new FormData();form.append('file',source,source.name||'edited.pdf');
  const response=await fetch('/api/experimental/inspect-pdf',{method:'POST',body:form});
  if(!response.ok)throw await responseError(response);
  return response.json();
}
async function renderChanges(next){
  if(!next.length)return {blob:null,images:originalPages.map(p=>p.image),boxes:[],substitutions:0};
  const form=new FormData();form.append('file',loadedFile);form.append('changes',JSON.stringify(next));
  const response=await fetch('/api/experimental/edit-pdf',{method:'POST',body:form});
  if(!response.ok)throw await responseError(response);
  const boxes=JSON.parse(response.headers.get('X-PDFaspect-Edit-Boxes')||'[]');
  if(boxes.length!==next.length)throw new Error('Edited text positions are unavailable. Reload the editor.');
  const substitutions=Number(response.headers.get('X-PDFaspect-Font-Substitutions')||0);
  const blob=await response.blob(),rendered=await inspect(blob);
  if(rendered.pages.length!==originalPages.length)throw new Error('Preview page count changed unexpectedly.');
  return {blob,images:rendered.pages.map(p=>p.image),boxes,substitutions};
}
async function showResult(next,result){
  const images=pages.querySelectorAll('.page > img');
  images.forEach((img,index)=>{img.src=result.images[index]});
  for(const record of spanElements.values()){
    record.displayBBox=record.span.bbox;position(record,record.displayBBox);
  }
  result.boxes.forEach((box,index)=>{
    const record=spanElements.get(changeKey(next[index]));
    if(record){record.displayBBox=box.bbox;position(record,record.displayBBox)}
  });
  await Promise.all(Array.from(images,img=>img.decode().catch(()=>{})));
  verifiedPdf=result.blob;
}
async function applyCurrent(){
  if(busy||!chosen)return false;
  const value=replacement.value.replace(/\r\n?/g,'\n'),key=changeKey(chosen.change);
  const next=changes.filter(c=>changeKey(c)!==key);
  if(value!==chosen.change.old_text||chosen.bold!==isBold(chosen.record.span)){
    next.push({...chosen.change,new_text:value,bold:chosen.bold,width:chosen.width});
  }
  busy=true;refresh();status.textContent='Applying text and rendering the PDF…';
  const current=requestId;
  try{
    const result=await renderChanges(next);
    if(current!==requestId)return false;
    history.push(copyChanges(changes));changes=next;
    await showResult(next,result);closeEditor();
    status.textContent=changes.length+' change(s) applied. Preview shows the saved PDF.'+(result.substitutions?' A replacement font was used for '+result.substitutions+' block(s).':'');
    return true;
  }catch(err){
    if(current===requestId)status.textContent='Unable to apply text: '+err.message;
    return false;
  }finally{if(current===requestId){busy=false;refresh()}}
}
function buildPages(){
  pages.replaceChildren();spanElements.clear();
  originalPages.forEach((info,index)=>{
    const wrapper=document.createElement('div');wrapper.className='page';
    wrapper.style.setProperty('--page-ratio',info.width+'/'+info.height);
    wrapper.style.width=(info.width*1.4)+'px';
    const image=document.createElement('img');image.src=info.image;image.alt='PDF page '+(index+1);
    wrapper.append(image);pages.append(wrapper);
    for(const span of info.spans){
      const el=document.createElement('div');el.className='span';el.tabIndex=0;el.setAttribute('role','button');
      const change={page:index,old_text:span.text,new_text:span.text,occurrence:span.occurrence};
      const record={el,span,info,displayBBox:span.bbox};position(record,span.bbox);spanElements.set(changeKey(change),record);
      const pick=async()=>{
        if(busy)return;
        if(chosen){
          if(changeKey(chosen.change)===changeKey(change))return;
          if(!await applyCurrent())return;
        }
        const existing=changes.find(c=>changeKey(c)===changeKey(change));
        const editBox=span.edit_bbox??span.bbox;
        chosen={change,record,bold:existing?.bold??isBold(span),width:editBox[2]-editBox[0]};editor.hidden=false;
        position(record,[editBox[0],record.displayBBox[1],editBox[2],record.displayBBox[3]]);
        selection.textContent='Page '+(index+1)+' · '+span.font+' · '+Number(span.size.toFixed(1))+' pt';
        replacement.value=existing?.new_text??span.text;
        const fontName=span.font.toLowerCase();
        replacement.style.fontFamily=/mono|courier|consolas/.test(fontName)?'monospace':/sans|helvetica|arial|calibri|carlito/.test(fontName)?'sans-serif':(span.flags&4)?'serif':'sans-serif';
        replacement.style.fontStyle=(span.flags&2)?'italic':'normal';
        replacement.style.color='#'+span.color.toString(16).padStart(6,'0');
        el.append(replacement);refresh();updateInputStyle();replacement.focus();replacement.select();
      };
      el.addEventListener('click',event=>{if(event.target===el)pick()});
      el.addEventListener('dblclick',event=>{if(event.target===el)pick()});
      el.addEventListener('keydown',event=>{if(event.target===el&&event.key==='Enter'){event.preventDefault();pick()}});
      wrapper.append(el);
    }
  });
  refresh();
}
file.addEventListener('change',async()=>{
  const source=file.files[0];if(!source)return;
  const current=++requestId;closeEditor();loadedFile=null;verifiedPdf=null;changes=[];history=[];originalPages=[];
  pages.replaceChildren();spanElements.clear();busy=true;refresh();
  try{
    if(source.size>10*1024*1024)throw new Error('PDF exceeds the 10 MB editor limit.');
    status.textContent='Checking editable text…';
    const inspected=await inspect(source);if(current!==requestId)return;
    loadedFile=source;originalPages=inspected.pages;buildPages();
    status.textContent='Loaded '+originalPages.length+' pages. Click or double-click text to edit directly.';
    if(!originalPages.some(p=>p.spans.length))status.textContent='No selectable text found. Scanned PDFs need OCR before editing.';
  }catch(err){if(current===requestId)status.textContent='Unable to open PDF: '+err.message}
  finally{if(current===requestId){busy=false;refresh()}}
});
replacement.addEventListener('input',resizeInput);
replacement.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();applyCurrent()}
  if(event.key.toLowerCase()==='b'&&(event.ctrlKey||event.metaKey)){event.preventDefault();toggleBold()}
  if(event.key==='Escape'){event.preventDefault();closeEditor()}
});
boldBtn.addEventListener('click',toggleBold);
window.addEventListener('resize',updateInputStyle);
applyBtn.addEventListener('click',applyCurrent);
cancelBtn.addEventListener('click',closeEditor);
undoBtn.addEventListener('click',async()=>{
  if(busy||!history.length)return;
  busy=true;refresh();status.textContent='Restoring the previous PDF…';const current=requestId;
  try{
    const next=copyChanges(history[history.length-1]),result=await renderChanges(next);
    if(current!==requestId)return;
    history.pop();changes=next;await showResult(next,result);closeEditor();
    status.textContent=changes.length+' change(s) applied. Previous PDF restored.';
  }catch(err){if(current===requestId)status.textContent='Unable to undo: '+err.message}
  finally{if(current===requestId){busy=false;refresh()}}
});
saveBtn.addEventListener('click',async()=>{
  if(busy||!loadedFile)return;
  if(chosen&&!await applyCurrent())return;
  const url=URL.createObjectURL(verifiedPdf||loadedFile),link=document.createElement('a');
  link.href=url;link.download='edited.pdf';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  status.textContent='Edited PDF downloaded.';
});
refresh();
