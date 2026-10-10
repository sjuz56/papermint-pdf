const byId=id=>document.getElementById(id);
const file=byId('file'),pages=byId('pages'),status=byId('status'),editor=byId('editor'),selection=byId('selection'),replacement=byId('replacement');
const saveBtn=byId('save'),applyBtn=byId('apply'),undoBtn=byId('undo'),redoBtn=byId('redo'),cancelBtn=byId('cancel');
const boldBtn=byId('bold'),italicBtn=byId('italic'),fontPicker=byId('font-family'),sizePicker=byId('font-size'),colorPicker=byId('font-color'),zoomPicker=byId('zoom'),widthHandle=byId('width-handle');
let changes=[],history=[],future=[],chosen=null,spanElements=new Map(),loadedFile=null,verifiedPdf=null,originalPages=[],requestId=0,busy=false,fontLoading=false,fontError=false,fontToken=0;
const fontLoads=new Map();
const changeKey=c=>[c.page,c.old_text,c.occurrence].join('\u0000');
const copyChanges=value=>value.map(c=>({...c}));
const isBold=span=>Boolean(span.flags&16)||/bold|demi|black/i.test(span.font);
const isItalic=span=>Boolean(span.flags&2)||/italic|oblique/i.test(span.font);
const hexColor=value=>'#'+value.toString(16).padStart(6,'0');
function position(record,bbox){
  const [x0,y0,x1,y1]=bbox,info=record.info,el=record.el;
  el.style.left=(x0/info.width*100)+'%';el.style.top=(y0/info.height*100)+'%';
  el.style.width=(Math.max(4,x1-x0)/info.width*100)+'%';el.style.height=(Math.max(5,y1-y0)/info.height*100)+'%';
}
function draftChanges(){
  if(!chosen)return changes;
  const value=replacement.value.replace(/\r\n?/g,'\n'),key=changeKey(chosen.change),span=chosen.record.span;
  const next=copyChanges(changes),index=next.findIndex(c=>changeKey(c)===key);
  const changed=value!==span.text||chosen.bold!==isBold(span)||chosen.italic!==isItalic(span)||
    chosen.family!=='original'||Math.abs(chosen.size-span.size)>0.01||chosen.color!==span.color||
    Math.abs(chosen.width-chosen.maxWidth)>0.01;
  if(changed){
    const change={...chosen.change,new_text:value,bold:chosen.bold,italic:chosen.italic,
      width:chosen.width,font_size:chosen.size,color:chosen.color};
    if(chosen.family!=='original')change.font_family=chosen.family;
    if(index<0)next.push(change);else next[index]=change;
  }else if(index>=0)next.splice(index,1);
  return next;
}
function draftDirty(){return chosen&&JSON.stringify(draftChanges())!==JSON.stringify(changes)}
function refresh(){
  for(const [key,record] of spanElements){
    const change=changes.find(c=>changeKey(c)===key),el=record.el;
    el.dataset.edited=change?'true':'false';el.title=change?change.new_text:record.span.text;
    el.setAttribute('aria-label','Edit '+(change?change.new_text:record.span.text));
    el.classList.toggle('editing',chosen?.record===record);
  }
  const ready=!busy&&!fontLoading&&!fontError&&(!chosen||sizePicker.validity.valid);
  saveBtn.disabled=!ready||!loadedFile||(!changes.length&&!chosen);
  applyBtn.disabled=!ready||!chosen;cancelBtn.disabled=busy;
  undoBtn.disabled=busy||(!history.length&&!draftDirty());redoBtn.disabled=busy||!future.length;
  replacement.disabled=busy;file.disabled=busy;zoomPicker.disabled=busy||!loadedFile;
  for(const control of [boldBtn,italicBtn,fontPicker,sizePicker,colorPicker])control.disabled=busy||!chosen;
  boldBtn.setAttribute('aria-pressed',chosen?.bold?'true':'false');
  italicBtn.setAttribute('aria-pressed',chosen?.italic?'true':'false');
}
function closeEditor(){
  if(chosen)position(chosen.record,chosen.record.displayBBox);
  ++fontToken;fontLoading=false;fontError=false;
  editor.append(replacement,widthHandle);editor.hidden=true;chosen=null;refresh();
}
function resizeInput(){
  replacement.style.height='auto';replacement.style.height=Math.max(24,replacement.scrollHeight+2)+'px';
  widthHandle.style.top=(replacement.offsetHeight-6)+'px';
}
function fallbackFamily(span){
  const name=span.font.toLowerCase();
  return /mono|courier|consolas/.test(name)?'monospace':
    /sans|helvetica|arial|calibri|carlito/.test(name)?'sans-serif':
    /serif|times|cambria|caladea|georgia/.test(name)||(span.flags&4)?'serif':'sans-serif';
}
async function loadFont(family,bold,italic){
  const variant=bold&&italic?'bold-italic':bold?'bold':italic?'italic':'regular',key=family+'/'+variant;
  if(!fontLoads.has(key)){
    const face=new FontFace('PDFaspect-'+family,'url("/api/experimental/edit-pdf-fonts/'+key+'")',
      {weight:bold?'700':'400',style:italic?'italic':'normal'});
    const loading=face.load().then(font=>{document.fonts.add(font);return font}).catch(err=>{fontLoads.delete(key);throw err});
    fontLoads.set(key,loading);
  }
  await fontLoads.get(key);
}
async function updateInputStyle(){
  if(!chosen)return;
  const current=chosen,token=++fontToken,span=current.record.span;
  const scale=current.record.el.closest('.page').clientWidth/current.record.info.width;
  replacement.style.fontSize=(current.size*scale)+'px';
  replacement.style.fontWeight=current.bold?'700':'400';replacement.style.fontStyle=current.italic?'italic':'normal';
  replacement.style.color=hexColor(current.color);
  replacement.style.fontFamily=current.family==='original'?fallbackFamily(span):'"PDFaspect-'+current.family+'"';
  fontError=false;fontLoading=current.family!=='original';refresh();resizeInput();
  try{
    if(fontLoading)await loadFont(current.family,current.bold,current.italic);
    if(token!==fontToken||chosen!==current)return;
    fontLoading=false;resizeInput();refresh();
  }catch(err){
    if(token!==fontToken||chosen!==current)return;
    fontLoading=false;fontError=true;refresh();
    status.textContent='Unable to load this font. Choose another font or Original.';
  }
}
function toggleStyle(property){
  if(busy||!chosen)return;
  chosen[property]=!chosen[property];updateInputStyle();refresh();replacement.focus();
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
    if(record){record.displayBBox=box.bbox;position(record,box.bbox)}
  });
  await Promise.all(Array.from(images,img=>img.decode().catch(()=>{})));
  verifiedPdf=result.blob;
}
async function applyCurrent(){
  if(busy||fontLoading||fontError||!chosen)return false;
  if(!sizePicker.validity.valid){sizePicker.reportValidity();return false}
  const next=draftChanges();
  if(JSON.stringify(next)===JSON.stringify(changes)){closeEditor();return true}
  busy=true;refresh();status.textContent='Applying text and rendering the PDF…';const current=requestId;
  try{
    const result=await renderChanges(next);
    if(current!==requestId)return false;
    history.push(copyChanges(changes));future=[];changes=next;
    await showResult(next,result);closeEditor();
    status.textContent=changes.length+' change(s) applied. Preview shows the saved PDF.'+
      (result.substitutions?' A replacement font was used for '+result.substitutions+' block(s).':'');
    return true;
  }catch(err){
    if(current===requestId)status.textContent='Unable to apply text: '+err.message;
    return false;
  }finally{if(current===requestId){busy=false;refresh()}}
}
function setWidth(width){
  if(!chosen||busy)return;
  chosen.width=Math.min(chosen.maxWidth,Math.max(Math.min(20,chosen.maxWidth),width));
  const box=chosen.record.displayBBox,left=chosen.record.span.bbox[0];
  position(chosen.record,[left,box[1],left+chosen.width,box[3]]);
  widthHandle.setAttribute('aria-valuenow',Math.round(chosen.width));
  resizeInput();refresh();
}
function applyZoom(){
  const fit=zoomPicker.value==='fit',factor=fit?1.4:Number(zoomPicker.value)/100*1.4;
  pages.classList.toggle('zoomed',!fit);
  pages.querySelectorAll('.page').forEach((wrapper,index)=>{
    wrapper.style.width=(originalPages[index].width*factor)+'px';wrapper.style.maxWidth=fit?'100%':'none';
  });
  updateInputStyle();
}
function buildPages(){
  pages.replaceChildren();spanElements.clear();
  originalPages.forEach((info,index)=>{
    const wrapper=document.createElement('div');wrapper.className='page';
    wrapper.style.setProperty('--page-ratio',info.width+'/'+info.height);
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
        const existing=changes.find(c=>changeKey(c)===changeKey(change)),editBox=span.edit_bbox??span.bbox;
        const maxWidth=editBox[2]-editBox[0];
        chosen={change,record,bold:existing?.bold??isBold(span),italic:existing?.italic??isItalic(span),
          family:existing?.font_family??'original',size:existing?.font_size??span.size,
          color:existing?.color??span.color,width:existing?.width??maxWidth,maxWidth};
        editor.hidden=false;setWidth(chosen.width);
        selection.textContent='Page '+(index+1)+' · '+span.font;
        fontPicker.value=chosen.family;sizePicker.value=Number(chosen.size.toFixed(2));colorPicker.value=hexColor(chosen.color);
        replacement.value=existing?.new_text??span.text;
        el.append(replacement,widthHandle);
        widthHandle.setAttribute('aria-valuemin',Math.min(20,maxWidth));
        widthHandle.setAttribute('aria-valuemax',Math.round(maxWidth));
        refresh();await updateInputStyle();
        if(!chosen||chosen.record!==record)return;
        replacement.focus({preventScroll:true});
        replacement.setSelectionRange(replacement.value.length,replacement.value.length);
        replacement.scrollIntoView({block:'center',inline:'nearest'});
      };
      el.addEventListener('click',event=>{if(event.target===el)pick()});
      el.addEventListener('dblclick',event=>{if(event.target===el)pick()});
      el.addEventListener('keydown',event=>{if(event.target===el&&event.key==='Enter'){event.preventDefault();pick()}});
      wrapper.append(el);
    }
  });
  applyZoom();refresh();
}
file.addEventListener('change',async()=>{
  const source=file.files[0];if(!source)return;
  const current=++requestId;closeEditor();loadedFile=null;verifiedPdf=null;changes=[];history=[];future=[];originalPages=[];
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
replacement.addEventListener('input',()=>{resizeInput();refresh()});
replacement.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();applyCurrent()}
  if(event.key.toLowerCase()==='b'&&(event.ctrlKey||event.metaKey)){event.preventDefault();toggleStyle('bold')}
  if(event.key.toLowerCase()==='i'&&(event.ctrlKey||event.metaKey)){event.preventDefault();toggleStyle('italic')}
  if(event.key==='Escape'){event.preventDefault();closeEditor()}
});
boldBtn.addEventListener('click',()=>toggleStyle('bold'));
italicBtn.addEventListener('click',()=>toggleStyle('italic'));
fontPicker.addEventListener('change',async()=>{
  if(!chosen||busy)return;
  chosen.family=fontPicker.value;await updateInputStyle();if(chosen)replacement.focus();
});
sizePicker.addEventListener('input',()=>{
  if(!chosen||busy)return;
  sizePicker.setAttribute('aria-invalid',sizePicker.validity.valid?'false':'true');
  if(sizePicker.validity.valid){chosen.size=sizePicker.valueAsNumber;updateInputStyle()}else refresh();
});
colorPicker.addEventListener('input',()=>{
  if(!chosen||busy)return;
  chosen.color=parseInt(colorPicker.value.slice(1),16);updateInputStyle();
});
zoomPicker.addEventListener('change',applyZoom);
window.addEventListener('resize',updateInputStyle);
widthHandle.addEventListener('pointerdown',event=>{
  if(!chosen||busy)return;
  event.preventDefault();const current=chosen,startX=event.clientX,startWidth=chosen.width;
  const scale=current.record.el.closest('.page').clientWidth/current.record.info.width;
  widthHandle.setPointerCapture(event.pointerId);
  const move=e=>{if(chosen===current)setWidth(startWidth+(e.clientX-startX)/scale)};
  const end=()=>{widthHandle.removeEventListener('pointermove',move);widthHandle.removeEventListener('pointerup',end);widthHandle.removeEventListener('pointercancel',end)};
  widthHandle.addEventListener('pointermove',move);widthHandle.addEventListener('pointerup',end);widthHandle.addEventListener('pointercancel',end);
});
widthHandle.addEventListener('keydown',event=>{
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
    event.preventDefault();if(chosen)setWidth(chosen.width+(event.key==='ArrowRight'?8:-8));
  }
});
pages.addEventListener('click',event=>{if(chosen&&!event.target.closest('.span'))applyCurrent()});
applyBtn.addEventListener('click',applyCurrent);cancelBtn.addEventListener('click',closeEditor);
async function restoreHistory(redo=false){
  if(busy)return;
  if(!redo&&draftDirty()){closeEditor();status.textContent='Unapplied changes cancelled.';return}
  const from=redo?future:history,to=redo?history:future;
  if(!from.length)return;
  busy=true;refresh();status.textContent='Restoring the previous PDF…';const current=requestId;
  try{
    const next=copyChanges(from[from.length-1]),result=await renderChanges(next);
    if(current!==requestId)return;
    from.pop();to.push(copyChanges(changes));changes=next;await showResult(next,result);closeEditor();
    status.textContent=changes.length+' change(s) applied. '+(redo?'PDF reapplied.':'Previous PDF restored.');
  }catch(err){if(current===requestId)status.textContent='Unable to '+(redo?'redo':'undo')+': '+err.message}
  finally{if(current===requestId){busy=false;refresh()}}
}
undoBtn.addEventListener('click',()=>restoreHistory(false));redoBtn.addEventListener('click',()=>restoreHistory(true));
document.addEventListener('keydown',event=>{
  if(event.target===replacement||event.target instanceof HTMLInputElement||event.target instanceof HTMLSelectElement)return;
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){
    event.preventDefault();restoreHistory(event.shiftKey);
  }
});
saveBtn.addEventListener('click',async()=>{
  if(busy||!loadedFile)return;
  if(chosen&&!await applyCurrent())return;
  const url=URL.createObjectURL(verifiedPdf||loadedFile),link=document.createElement('a');
  link.href=url;link.download='edited.pdf';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  status.textContent='Edited PDF downloaded.';
});
(async()=>{
  try{
    const response=await fetch('/api/experimental/edit-pdf-fonts');
    if(!response.ok)throw await responseError(response);
    const data=await response.json();
    for(const font of data.fonts){
      const option=document.createElement('option');option.value=font.id;option.textContent=font.label;fontPicker.append(option);
    }
  }catch{status.textContent='Font choices are unavailable. You can still edit with the original font.'}
})();
refresh();
