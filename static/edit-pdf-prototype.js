const byId=id=>document.getElementById(id);
const file=byId('file'),pages=byId('pages'),status=byId('status'),editor=byId('editor'),selection=byId('selection'),replacement=byId('replacement');
const saveBtn=byId('save'),applyBtn=byId('apply'),undoBtn=byId('undo'),redoBtn=byId('redo'),cancelBtn=byId('cancel');
const boldBtn=byId('bold'),italicBtn=byId('italic'),fontPicker=byId('font-family'),sizePicker=byId('font-size'),colorPicker=byId('font-color'),zoomPicker=byId('zoom'),widthHandle=byId('width-handle');
let changes=[],history=[],future=[],chosen=null,spanElements=new Map(),loadedFile=null,verifiedPdf=null,originalPages=[],requestId=0,busy=false,fontLoading=false,fontError=false,fontToken=0;
const fontLoads=new Map();
const originalFonts=new Map();
const fontLabels=new Map(),fontSources=new Map(),fontIdentities=new Map(),clipboardType='application/x-pdfaspect-text+json';
let documentId='',lastCopied=null,plainPasteUntil=0;
const changeKey=c=>[c.page,c.old_text,c.occurrence].join('\u0000');
const copyChanges=value=>value.map(c=>({...c}));
const isBold=span=>Boolean(span.flags&16)||/bold|demi|black/i.test(span.font);
const isItalic=span=>Boolean(span.flags&2)||/italic|oblique/i.test(span.font);
const hexColor=value=>'#'+value.toString(16).padStart(6,'0');
const sourceSpan=current=>spanElements.get(changeKey(current.fontSource??current.change))?.span??current.record.span;
const fontNameKey=name=>name.replace(/^[A-Z]{6}\+/,'').replace(/[^a-z0-9]/gi,'').toLowerCase();
const fontFamilyKey=name=>fontNameKey(name).replace(/(?:semibold|bold|italic|oblique|regular|roman|demi|black|psmt|mt)+$/g,'');
function knownFamily(name){
  const normalized=fontNameKey(name);
  for(const [id,label] of [...fontLabels].sort((a,b)=>b[1].length-a[1].length)){
    if(normalized.startsWith(label.replace(/[^a-z0-9]/gi,'').toLowerCase()))return id;
  }
  return null;
}
function syncFormatControls(){
  const originalOption=fontPicker.querySelector('option[value="original"]');
  originalOption.textContent='Original: '+chosen.record.span.font.replace(/^[A-Z]{6}\+/,'');
  originalOption.title=chosen.record.span.font;
  const activeName=chosen.family==='original'?sourceSpan(chosen).font:fontLabels.get(chosen.family);
  selection.textContent='Page '+(chosen.change.page+1)+' · '+(fontLabels.get(knownFamily(activeName))??activeName.replace(/^[A-Z]{6}\+/,''));
  fontPicker.querySelector('option[value="copied-original"]')?.remove();
  if(chosen.fontSource){
    const option=document.createElement('option');option.value='copied-original';
    const name=sourceSpan(chosen).font;
    option.textContent=(fontLabels.get(knownFamily(name))??name)+' (copied)';option.title=name;fontPicker.append(option);
  }
  fontPicker.value=chosen.fontSource?'copied-original':chosen.family;
  sizePicker.value=Number(chosen.size.toFixed(2));sizePicker.setAttribute('aria-invalid','false');
  colorPicker.value=hexColor(chosen.color);
}
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
    Math.abs(chosen.width-chosen.maxWidth)>0.01||Boolean(chosen.fontSource);
  if(changed){
    const change={...chosen.change,new_text:value,bold:chosen.bold,italic:chosen.italic,
      width:chosen.width,font_size:chosen.size,color:chosen.color};
    if(chosen.family!=='original')change.font_family=chosen.family;
    if(chosen.fontSource)change.font_source={...chosen.fontSource};
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
  const ready=!busy&&(!chosen||sizePicker.validity.valid);
  saveBtn.disabled=!ready||!loadedFile||(!changes.length&&!chosen);
  applyBtn.disabled=busy||!chosen;cancelBtn.disabled=busy;
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
  fontPicker.querySelector('option[value="copied-original"]')?.remove();
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
async function loadOriginalFont(font){
  if(!font.face){
    const bytes=Uint8Array.from(atob(font.data),char=>char.charCodeAt(0));
    font.face=new FontFace('PDFaspect-document-'+documentId+'-'+font.id,bytes,
      {weight:font.bold?'700':'400',style:font.italic?'italic':'normal'});
    const currentDocument=documentId;
    font.loading=font.face.load().then(face=>{if(documentId===currentDocument)document.fonts.add(face);return face});
  }
  return font.loading;
}
function originalFontFor(span,bold,italic){
  const exact=originalFonts.get(span.browser_font);
  if(exact&&exact.bold===bold&&exact.italic===italic)return exact;
  return [...originalFonts.values()].find(font=>fontFamilyKey(font.name)===fontFamilyKey(span.font)&&
    font.bold===bold&&font.italic===italic);
}
async function updateInputStyle(){
  if(!chosen)return;
  const current=chosen,token=++fontToken,span=sourceSpan(current);
  const embedded=current.family==='original'?originalFontFor(span,current.bold,current.italic):null;
  const displayFamily=current.family==='original'?knownFamily(span.font):current.family;
  const scale=current.record.el.closest('.page').clientWidth/current.record.info.width;
  replacement.style.fontSize=(current.size*scale)+'px';
  replacement.style.fontWeight=current.bold?'700':'400';replacement.style.fontStyle=current.italic?'italic':'normal';
  replacement.style.color=hexColor(current.color);
  replacement.style.fontFamily=embedded?'"PDFaspect-document-'+documentId+'-'+embedded.id+'"':
    displayFamily?'"PDFaspect-'+displayFamily+'"':fallbackFamily(span);
  fontError=false;fontLoading=Boolean(embedded||displayFamily);refresh();resizeInput();
  try{
    if(embedded){
      try{await loadOriginalFont(embedded)}catch(err){
        if(token!==fontToken||chosen!==current)return;
        replacement.style.fontFamily=displayFamily?'"PDFaspect-'+displayFamily+'"':fallbackFamily(span);
        if(displayFamily)await loadFont(displayFamily,current.bold,current.italic);
        status.textContent='This font cannot be displayed while typing. Apply the text to view its PDF font.';
      }
    }
    else if(displayFamily)await loadFont(displayFamily,current.bold,current.italic);
    if(token!==fontToken||chosen!==current)return;
    fontLoading=false;resizeInput();refresh();
  }catch(err){
    if(token!==fontToken||chosen!==current)return;
    fontLoading=false;fontError=true;refresh();
    status.textContent='Font preview unavailable; you can still apply your text or choose another font.';
  }
}
function toggleStyle(property){
  if(busy||!chosen)return;
  chosen[property]=!chosen[property];updateInputStyle();refresh();replacement.focus();
}
function copySelection(event){
  if(!chosen||busy||!event.clipboardData)return;
  const start=replacement.selectionStart,end=replacement.selectionEnd,text=replacement.value.slice(start,end);
  if(!text)return;
  const span=sourceSpan(chosen),source=spanElements.get(changeKey(chosen.fontSource??chosen.change));
  const format={version:1,text,documentId,family:chosen.family,bold:chosen.bold,italic:chosen.italic,
    size:chosen.size,color:chosen.color,fontName:span.font};
  if(chosen.family==='original')format.sourceId=source.fontId;
  const html=document.createElement('span');html.textContent=text;html.dataset.pdfaspectFormat=JSON.stringify(format);
  const family=chosen.family==='original'?(fontLabels.get(knownFamily(span.font))??span.font.replace(/^[A-Z]{6}\+/,'')):fontLabels.get(chosen.family);
  html.style.fontFamily=JSON.stringify(family);html.style.fontSize=chosen.size+'pt';
  html.style.fontWeight=chosen.bold?'700':'400';html.style.fontStyle=chosen.italic?'italic':'normal';
  html.style.color=hexColor(chosen.color);html.style.whiteSpace='pre-wrap';
  event.clipboardData.setData('text/plain',text);event.clipboardData.setData('text/html',html.outerHTML);
  try{event.clipboardData.setData(clipboardType,JSON.stringify(format))}catch{}
  lastCopied=format;event.preventDefault();
  if(event.type==='cut')insertClipboardText('');
}
function recordFormat(record){
  const change=changes.find(item=>changeKey(item)===changeKey({page:record.info.page,old_text:record.span.text,occurrence:record.span.occurrence}));
  const source=change?.font_source?spanElements.get(changeKey(change.font_source))??record:record;
  return {family:change?.font_family??'original',fontName:source.span.font,
    fontSource:change?.font_family?null:{page:source.info.page,old_text:source.span.text,occurrence:source.span.occurrence},
    bold:change?.bold??isBold(source.span),italic:change?.italic??isItalic(source.span),
    size:change?.font_size??record.span.size,color:change?.color??record.span.color,
    text:change?.new_text??record.span.text};
}
function findSourceFormat(name,bold,italic){
  const names=name.split(',').map(value=>value.trim().replace(/^['"]|['"]$/g,''));
  for(const label of names){
    const candidates=[...spanElements.values()].map(recordFormat).filter(format=>format.family==='original'&&
      (fontNameKey(format.fontName)===fontNameKey(label)||fontFamilyKey(format.fontName)===fontFamilyKey(label))&&
      (bold===undefined||format.bold===bold)&&(italic===undefined||format.italic===italic));
    if(candidates.length)return candidates[0];
  }
  return null;
}
function validateCopiedFormat(format,text){
  try{
    if(!format||format.version!==1||typeof format.text!=='string'||format.text.replace(/\r\n?/g,'\n')!==text||
      typeof format.bold!=='boolean'||typeof format.italic!=='boolean'||
      typeof format.size!=='number'||!Number.isFinite(format.size)||format.size<4||format.size>144||
      !Number.isInteger(format.color)||format.color<0||format.color>0xffffff)return null;
    if(format.family==='original'){
      const source=fontSources.get(format.sourceId);
      if(format.documentId===documentId&&source)return {...format,fontSource:{...source}};
      const original=typeof format.fontName==='string'?findSourceFormat(format.fontName,format.bold,format.italic):null;
      if(original)return {...format,fontSource:original.fontSource};
      const family=typeof format.fontName==='string'?knownFamily(format.fontName):null;
      return family?{...format,family,fontSource:null}:null;
    }
    return fontLabels.has(format.family)?{...format,fontSource:null}:null;
  }catch{return null}
}
function htmlClipboardFormat(template){
  const sheets=[];
  for(const style of template.content.querySelectorAll('style')){
    try{const sheet=new CSSStyleSheet();sheet.replaceSync(style.textContent);sheets.push(sheet)}catch{}
  }
  const colour=value=>{
    if(!CSS.supports('color',value)||/var\(|currentcolor|inherit|initial|unset|transparent/i.test(value))return undefined;
    const canvas=document.createElement('canvas'),context=canvas.getContext('2d');context.fillStyle=value;
    const result=context.fillStyle;
    if(/^#[0-9a-f]{6}$/i.test(result))return parseInt(result.slice(1),16);
    const rgb=result.match(/^rgba?\(\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)/);
    return rgb?Math.round(Number(rgb[1]))*65536+Math.round(Number(rgb[2]))*256+Math.round(Number(rgb[3])):undefined;
  };
  const size=(value,parent)=>{
    const match=value.trim().match(/^([\d.]+)(pt|px|em|rem|%)$/i);if(!match)return undefined;
    const number=Number(match[1]),unit=match[2].toLowerCase();
    const result=number*(unit==='px'?0.75:unit==='em'?(parent??12):unit==='rem'?12:unit==='%'?(parent??12)/100:1);
    return Number.isFinite(result)&&result>=4&&result<=144?result:undefined;
  };
  const apply=(format,style)=>{
    if(style.fontFamily)format.fontName=style.fontFamily;
    if(style.fontSize){const value=size(style.fontSize,format.size);if(value!==undefined)format.size=value}
    if(style.fontWeight&&/^(normal|bold|bolder|[1-9]00)$/.test(style.fontWeight))
      format.bold=/bold/.test(style.fontWeight)||Number(style.fontWeight)>=600;
    if(style.fontStyle&&/^(normal|italic|oblique)/.test(style.fontStyle))format.italic=style.fontStyle!=='normal';
    if(style.color){const value=colour(style.color);if(value!==undefined)format.color=value}
  };
  const walker=document.createTreeWalker(template.content,NodeFilter.SHOW_TEXT);let node,uniform=null;
  while((node=walker.nextNode())){
    if(!node.textContent.trim()||node.parentElement?.closest('style,script,title,noscript'))continue;
    const parents=[];for(let parent=node.parentElement;parent;parent=parent.parentElement)parents.unshift(parent);
    const format={};
    for(const element of parents){
      if(element.matches('b,strong'))format.bold=true;
      if(element.matches('i,em'))format.italic=true;
      if(element.matches('font[face]'))format.fontName=element.getAttribute('face');
      for(const sheet of sheets)for(const rule of sheet.cssRules){
        if(rule.selectorText){try{if(element.matches(rule.selectorText))apply(format,rule.style)}catch{}}
      }
      apply(format,element.style);
    }
    if(!Object.keys(format).length)return null;
    const signature=value=>JSON.stringify([value.fontName??null,value.size??null,value.bold??null,value.italic??null,value.color??null]);
    if(uniform&&signature(uniform)!==signature(format))return {mixed:true};
    uniform=format;
  }
  return uniform;
}
function readCopiedFormat(data,text){
  const custom=data.getData(clipboardType),html=data.getData('text/html');let template=null;
  try{
    if(custom){const format=validateCopiedFormat(JSON.parse(custom),text);if(format)return format}
  }catch{}
  if(html&&html.length<=100000){
    template=document.createElement('template');template.innerHTML=html;
    const node=template.content.querySelector('[data-pdfaspect-format]');
    if(node){try{const format=validateCopiedFormat(JSON.parse(node.getAttribute('data-pdfaspect-format')),text);if(format)return format}catch{}}
    const style=htmlClipboardFormat(template);
    if(style?.mixed)return {mixed:true};
    if(style){
      const source=style.fontName?findSourceFormat(style.fontName,style.bold,style.italic):null;
      const family=style.fontName?knownFamily(style.fontName):null;
      if(style.fontName&&!source&&!family&&lastCopied?.text.replace(/\r\n?/g,'\n')===text){
        const previous=validateCopiedFormat(lastCopied,text);if(previous)return previous;
      }
      return {...(source??{family:chosen.family,fontSource:chosen.fontSource,bold:chosen.bold,italic:chosen.italic,size:chosen.size,color:chosen.color}),
        ...style,family:source?'original':family??chosen.family,fontSource:source?.fontSource??(family?null:chosen.fontSource),
        unavailableFont:style.fontName&&!source&&!family?style.fontName:null};
    }
  }
  if(lastCopied?.text.replace(/\r\n?/g,'\n')===text){const format=validateCopiedFormat(lastCopied,text);if(format)return format}
  // PDF viewers and some phone keyboards provide only text. Recover a unique
  // matching block's format from this PDF instead of adopting the destination font.
  const normalize=value=>value.replace(/\s+/g,' ').trim();
  const matches=[...spanElements.values()].map(recordFormat).filter(format=>normalize(format.text)===normalize(text));
  const formats=new Set(matches.map(format=>JSON.stringify([format.family,format.fontName,format.bold,format.italic,format.size,format.color])));
  return formats.size===1?matches[0]:null;
}
function insertClipboardText(text){
  const start=replacement.selectionStart,end=replacement.selectionEnd;
  const available=replacement.maxLength-(replacement.value.length-(end-start));
  text=text.slice(0,Math.max(0,available));replacement.focus();
  // Native insertion keeps the browser's text undo history; setRangeText is the fallback.
  const inserted=document.execCommand(text?'insertText':'delete',false,text);
  if(!inserted){replacement.setRangeText(text,start,end,'end');replacement.dispatchEvent(new Event('input',{bubbles:true}))}
  resizeInput();refresh();
}
replacement.addEventListener('copy',copySelection);replacement.addEventListener('cut',copySelection);
replacement.addEventListener('paste',event=>{
  if(!chosen||busy||!event.clipboardData)return;
  const plain=performance.now()<plainPasteUntil;plainPasteUntil=0;
  const text=event.clipboardData.getData('text/plain').replace(/\r\n?/g,'\n');
  const format=plain?null:readCopiedFormat(event.clipboardData,text);
  if(format?.mixed){status.textContent='The pasted text has several styles. This editor formats one block at a time; choose the font for this block.';return}
  if(!format)return;
  event.preventDefault();insertClipboardText(text);
  Object.assign(chosen,{family:format.family,fontSource:format.fontSource??null,
    bold:format.bold,italic:format.italic,size:format.size,color:format.color});
  syncFormatControls();updateInputStyle();
  status.textContent=format.unavailableFont?'The clipboard font '+format.unavailableFont+' is unavailable in this PDF. Choose a font for the pasted text.':
    'Pasted with the source font and formatting. Ctrl+Shift+V pastes text only.';
});
async function responseError(response){
  let message='HTTP '+response.status;
  try{const data=await response.json();const detail=data.detail;message=typeof detail==='string'?detail:detail?JSON.stringify(detail):message}catch{}
  return new Error(message);
}
async function timedFetch(url,options,timeoutMs=30000){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),timeoutMs);
  try{return await fetch(url,{...options,signal:controller.signal})}
  catch(err){if(err.name==='AbortError')throw new Error('Server did not respond within '+Math.round(timeoutMs/1000)+' seconds. Please retry.');throw err}
  finally{clearTimeout(timeout)}
}
async function inspect(source,includeFonts=true){
  const form=new FormData();form.append('file',source,source.name||'edited.pdf');
  form.append('include_fonts',String(includeFonts));
  const response=await timedFetch('/api/experimental/inspect-pdf',{method:'POST',body:form},45000);
  if(!response.ok)throw await responseError(response);
  return response.json();
}
async function renderChanges(next){
  if(!next.length)return {blob:null,images:originalPages.map(p=>p.image),boxes:[],substitutions:0};
  const form=new FormData();form.append('file',loadedFile);form.append('changes',JSON.stringify(next));
  status.textContent='Sending edited PDF to server…';
   const response=await timedFetch('/api/experimental/edit-pdf',{method:'POST',body:form},45000);
   status.textContent='Server responded. Checking edited PDF…';
  if(!response.ok)throw await responseError(response);
  const boxes=JSON.parse(response.headers.get('X-PDFaspect-Edit-Boxes')||'[]');
  if(boxes.length!==next.length)throw new Error('Edited text positions are unavailable. Reload the editor.');
  const substitutions=Number(response.headers.get('X-PDFaspect-Font-Substitutions')||0);
  const blob=await response.blob();
  // The edited PDF is ready immediately; do not block Apply on preview rendering.
  return {blob,images:originalPages.map(p=>p.image),boxes,substitutions};

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
  verifiedPdf=result.blob;
  // The edited PDF is already saved on the server response. Do not block Apply on image decoding.
  for(const img of images){if(img.decode)img.decode().catch(()=>{});}
}
async function applyCurrent(){
  if(busy||!chosen)return false;
  if(!sizePicker.validity.valid){status.textContent='Invalid font size. Choose a number between 4 and 144.';sizePicker.reportValidity();return false}
  let next;
  try{next=draftChanges()}catch(err){status.textContent='Unable to prepare changes: '+err.message;return false}
  if(JSON.stringify(next)===JSON.stringify(changes)){closeEditor();status.textContent='No text changes to apply.';return true}
  busy=true;refresh();status.textContent='Applying text and rendering the PDF…';const current=requestId;
  try{
    const result=await renderChanges(next);
    if(current!==requestId)return false;
    // Inspect the exported PDF and rebuild the editor from the actual saved bytes.
    // Never reuse the original page image or original text hitboxes after an edit.
    status.textContent='Verifying the saved PDF and refreshing its text…';
    const inspected=await inspect(result.blob,false);
    if(current!==requestId)return false;
    if(inspected.pages.length!==originalPages.length)throw new Error('Saved PDF page count changed');
    closeEditor();
    loadedFile=new File([result.blob],'edited.pdf',{type:'application/pdf'});
    verifiedPdf=result.blob;
    changes=[];history=[];future=[];
    originalPages=inspected.pages;
    spanElements.clear();pages.replaceChildren();
    buildPages();
    status.textContent=result.substitutions > 0
      ? 'Text saved and preview refreshed. Warning: '+result.substitutions+' font substitution(s) were needed; compare the result visually before using the PDF.'
      : 'Text saved in PDF and preview refreshed. Download is ready.';
    return true;
  }catch(err){
    if(current===requestId){status.textContent='Unable to apply text: '+err.message;status.scrollIntoView({block:'nearest'});console.error('PDFaspect apply failed',err);window.alert('PDF could not be saved: '+err.message)}
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
function stableFontId(change){
  const key=changeKey(change);
  if(!fontIdentities.has(key))fontIdentities.set(key,crypto.randomUUID());
  return fontIdentities.get(key);
}
function buildPages(){
  pages.replaceChildren();spanElements.clear();fontSources.clear();
  originalPages.forEach((info,index)=>{
    const wrapper=document.createElement('div');wrapper.className='page';
    wrapper.style.setProperty('--page-ratio',info.width+'/'+info.height);
    const image=document.createElement('img');image.src=info.image;image.alt='PDF page '+(index+1);
    wrapper.append(image);pages.append(wrapper);
    // Show the exact image embedded in a signature widget, not a recreated label.
    // This is a visual preview layer only; the downloaded PDF is untouched.
    for(const signature of info.signature_previews||[]){
      const overlay=document.createElement('img');
      overlay.src=signature.image;
      overlay.alt='Original PDF signature appearance';
      overlay.className='signature-preview';
      const [x0,y0,x1,y1]=signature.bbox;
      Object.assign(overlay.style,{
        position:'absolute',left:(x0/info.width*100)+'%',top:(y0/info.height*100)+'%',
        width:((x1-x0)/info.width*100)+'%',height:((y1-y0)/info.height*100)+'%',
        objectFit:'fill',pointerEvents:'none',zIndex:'1'
      });
      wrapper.append(overlay);
    }
    for(const span of info.spans){
      const el=document.createElement('div');el.className='span';el.tabIndex=0;el.setAttribute('role','button');
      const change={page:index,old_text:span.text,new_text:span.text,occurrence:span.occurrence};
      const record={el,span,info,displayBBox:span.bbox,fontId:stableFontId(change)};position(record,span.bbox);spanElements.set(changeKey(change),record);
      fontSources.set(record.fontId,{page:index,old_text:span.text,occurrence:span.occurrence});
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
          color:existing?.color??span.color,width:existing?.width??maxWidth,maxWidth,
          fontSource:existing?.font_source??null};
        editor.hidden=false;setWidth(chosen.width);
        selection.textContent='Page '+(index+1)+' · '+span.font;
        syncFormatControls();
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
let lastUploadToken='';
async function handlePdfSelection(source){
  if(!source){status.textContent='No PDF selected. Please choose a PDF file.';return}
  const token=[source.name,source.size,source.lastModified].join(':');
  if(token===lastUploadToken)return;
  lastUploadToken=token;
  setTimeout(()=>{if(lastUploadToken===token)lastUploadToken=''},1500);
  status.textContent='Selected '+source.name+' — opening PDF…';
  const current=++requestId;closeEditor();loadedFile=null;verifiedPdf=null;changes=[];history=[];future=[];originalPages=[];
  pages.replaceChildren();spanElements.clear();busy=true;refresh();
  try{
    if(source.size>10*1024*1024)throw new Error('PDF exceeds the 10 MB editor limit.');
    status.textContent='Checking editable text…';
    const inspected=await inspect(source);if(current!==requestId)return;
    loadedFile=source;documentId=crypto.randomUUID();fontIdentities.clear();
    for(const font of originalFonts.values())if(font.face)document.fonts.delete(font.face);
    originalFonts.clear();for(const font of inspected.fonts??[])originalFonts.set(font.id,font);
    originalPages=inspected.pages;buildPages();
    status.textContent='Loaded '+originalPages.length+' pages. Click or double-click text to edit directly.';
    if(!originalPages.some(p=>p.spans.length))status.textContent='No selectable text found. Scanned PDFs need OCR before editing.';
  }catch(err){if(current===requestId)status.textContent='Unable to open PDF: '+err.message}
  finally{if(current===requestId){busy=false;refresh();file.value=''}}
}
file.addEventListener('change',()=>handlePdfSelection(file.files?.[0]));
file.addEventListener('input',()=>handlePdfSelection(file.files?.[0]));
const dropZone=document.getElementById('upload-dropzone');
if(dropZone){
  dropZone.addEventListener('dragover',event=>{event.preventDefault();dropZone.classList.add('drag-over')});
  dropZone.addEventListener('dragleave',()=>dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop',event=>{event.preventDefault();dropZone.classList.remove('drag-over');handlePdfSelection(event.dataTransfer?.files?.[0])});
}
replacement.addEventListener('input',()=>{resizeInput();refresh()});
replacement.addEventListener('keydown',event=>{
  if(event.key.toLowerCase()==='v'&&(event.ctrlKey||event.metaKey))plainPasteUntil=event.shiftKey?performance.now()+1000:0;
  if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();applyCurrent()}
  if(event.key.toLowerCase()==='b'&&(event.ctrlKey||event.metaKey)){event.preventDefault();toggleStyle('bold')}
  if(event.key.toLowerCase()==='i'&&(event.ctrlKey||event.metaKey)){event.preventDefault();toggleStyle('italic')}
  if(event.key==='Escape'){event.preventDefault();closeEditor()}
});
boldBtn.addEventListener('click',()=>toggleStyle('bold'));
italicBtn.addEventListener('click',()=>toggleStyle('italic'));
fontPicker.addEventListener('change',async()=>{
  if(!chosen||busy)return;
  if(fontPicker.value==='copied-original')chosen.family='original';
  else{chosen.family=fontPicker.value;chosen.fontSource=null}
  syncFormatControls();await updateInputStyle();if(chosen)replacement.focus();
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
applyBtn.addEventListener('click',async()=>{status.textContent='Apply clicked — preparing PDF…';applyBtn.textContent='Applying…';try{const ok=await applyCurrent();if(!ok&&status.textContent.startsWith('Apply clicked'))status.textContent='Unable to apply: the editor did not finish.'}catch(err){status.textContent='Unable to apply text: '+err.message}finally{applyBtn.textContent='Apply text';status.scrollIntoView({block:'nearest'})}});cancelBtn.addEventListener('click',closeEditor);
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
      fontLabels.set(font.id,font.label);
      const option=document.createElement('option');option.value=font.id;option.textContent=font.label;fontPicker.append(option);
    }
  }catch{status.textContent='Font choices are unavailable. You can still edit with the original font.'}
})();
refresh();
