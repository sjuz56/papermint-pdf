const saveBtn=document.getElementById('save');
const file=document.getElementById('file'),pages=document.getElementById('pages'),status=document.getElementById('status'),editor=document.getElementById('editor'),selection=document.getElementById('selection'),replacement=document.getElementById('replacement'),exportBtn=document.getElementById('export');
let changes=[],chosen=null,spanElements=new Map(),loadedFile=null,requestId=0,saving=false;
const changeKey=c=>[c.page,c.old_text,c.occurrence].join('\u0000');
const refresh=()=>{for(const [key,el] of spanElements)el.style.outline=changes.some(c=>changeKey(c)===key)?'2px solid #16a085':'';exportBtn.disabled=changes.length===0;saveBtn.disabled=saving||!loadedFile||changes.length===0;};
file.addEventListener('change',async()=>{
  const f=file.files[0];if(!f)return;
  const currentRequest=++requestId;loadedFile=null;
  if(f.size>10*1024*1024){pages.replaceChildren();editor.hidden=true;changes=[];chosen=null;spanElements.clear();refresh();status.textContent='PDF exceeds the 10 MB editor limit.';return;}
  pages.replaceChildren();editor.hidden=true;changes=[];chosen=null;spanElements.clear();refresh();
  try{
    status.textContent='Checking editable text…';
    const inspectForm=new FormData();inspectForm.append('file',f);
    const inspectResponse=await fetch('/api/experimental/inspect-pdf',{method:'POST',body:inspectForm});
    if(!inspectResponse.ok){let detail='HTTP '+inspectResponse.status;try{detail=(await inspectResponse.json()).detail||detail}catch{}throw new Error(inspectResponse.status===404?'PDF editor is not enabled on this server.':'Text inspection unavailable: '+detail)}
    const inspected=await inspectResponse.json();
    if(currentRequest!==requestId)return;
    loadedFile=f;refresh();
    status.textContent=`Loaded ${inspected.pages.length} pages. Click highlighted text to edit.`;
    if(!inspected.pages.some(p=>p.spans.length))status.textContent='No selectable text found. Scanned PDFs need OCR before editing.';
    for(let number=1;number<=inspected.pages.length;number++){
      const pageInfo=inspected.pages[number-1],scale=1.4;
      const wrapper=document.createElement('div');wrapper.className='page';wrapper.style.setProperty('--page-ratio',pageInfo.width+'/'+pageInfo.height);
      wrapper.style.width=(pageInfo.width*scale)+'px';wrapper.style.height=(pageInfo.height*scale)+'px';
      const preview=document.createElement('img');preview.src=pageInfo.image;preview.alt='PDF page '+number;
      preview.style.display='block';preview.style.width='100%';preview.style.height='100%';
      wrapper.append(preview);pages.append(wrapper);
      for(const span of pageInfo.spans){
        const [x0,y0,x1,y1]=span.bbox;
        const left=x0*scale,top=y0*scale;
        const width=(x1-x0)*scale,height=(y1-y0)*scale;
        const div=document.createElement('div');div.className='span';div.tabIndex=0;
        div.setAttribute('role','button');div.setAttribute('aria-label','Edit '+span.text);
        div.style.left=(left/(pageInfo.width*scale)*100)+'%';div.style.top=(top/(pageInfo.height*scale)*100)+'%';
        div.style.width=(Math.max(4,width)/(pageInfo.width*scale)*100)+'%';div.style.height=(Math.max(5,height)/(pageInfo.height*scale)*100)+'%';
        const original=span.text,occurrence=span.occurrence;
        div.title=original;
        spanElements.set(changeKey({page:number-1,old_text:original,occurrence}),div);
        const pick=()=>{
          chosen={page:number-1,old_text:original,new_text:original,occurrence,element:div};
          editor.hidden=false;selection.textContent=`Page ${number}: ${original}`;
          replacement.value=changes.find(c=>c.page===number-1&&c.old_text===original&&c.occurrence===occurrence)?.new_text??original;replacement.focus();replacement.select();
        };
        div.addEventListener('click',pick);div.addEventListener('dblclick',pick);div.addEventListener('keydown',e=>{if(e.key==='Enter')pick()});
        wrapper.append(div);
      }
    }
  }catch(err){if(currentRequest===requestId)status.textContent='Unable to open PDF: '+err.message}
});
replacement.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();document.getElementById('apply').click();}});
document.getElementById('apply').addEventListener('click',()=>{
 if(!chosen||!replacement.value.trim())return;
 changes=changes.filter(c=>!(c.page===chosen.page&&c.old_text===chosen.old_text&&c.occurrence===chosen.occurrence));
 if(replacement.value!==chosen.old_text)changes.push({page:chosen.page,old_text:chosen.old_text,new_text:replacement.value,occurrence:chosen.occurrence});
 refresh();
 status.textContent=`${changes.length} staged change(s). Export JSON for review.`;
});
document.getElementById('undo').addEventListener('click',()=>{
 const last=changes.pop();if(!last)return;
 if(chosen&&changeKey(chosen)===changeKey(last)){replacement.value=chosen.old_text;}
 refresh();status.textContent=`${changes.length} staged change(s).`;
});
saveBtn.addEventListener('click',async()=>{
 if(saving||!loadedFile||!changes.length)return;
 saving=true;refresh();status.textContent='Saving experimental PDF…';
 const sourceFile=loadedFile,submittedChanges=changes.map(c=>({...c})),submissionId=requestId;
 try{
  const form=new FormData();form.append('file',sourceFile);form.append('changes',JSON.stringify(submittedChanges));
  const response=await fetch('/api/experimental/edit-pdf',{method:'POST',body:form});
  if(!response.ok){let message='HTTP '+response.status;try{message=(await response.json()).detail||message}catch{}throw new Error(message)}
  const result=await response.blob();
  if(submissionId!==requestId)return; // A different PDF was selected during save.
  const url=URL.createObjectURL(result),a=document.createElement('a');
  a.href=url;a.download='edited.pdf';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  status.textContent='Edited PDF downloaded. Review the output carefully.';
 }catch(err){if(submissionId===requestId)status.textContent='PDF save unavailable: '+err.message}
 finally{saving=false;refresh()}
});
exportBtn.addEventListener('click',()=>{
 const blob=new Blob([JSON.stringify({schema_version:1,changes},null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='pdfaspect-edits.json';a.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
});
