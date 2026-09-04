from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import List, Optional
import tempfile, shutil, os, subprocess, uuid, json, re
import fitz
from pypdf import PdfReader, PdfWriter
from docx import Document
from docx.shared import Inches, Pt
from pptx import Presentation
from pptx.util import Inches as PptInches
from openpyxl import Workbook
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
import pdfplumber
import pytesseract
from weasyprint import HTML

BASE = Path(__file__).parent
TMP = BASE / 'tmp'
TMP.mkdir(exist_ok=True)

app = FastAPI(title='PaperMint PDF Toolbox')
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')

TOOLS = [
    ('merge','Merge PDF','Combine PDFs in the order you want.'),
    ('split','Split PDF','Split a PDF into separate files or ranges.'),
    ('compress','Compress PDF','Reduce PDF file size while preserving quality.'),
    ('pdf-word','PDF to Word','Convert PDF to a genuinely editable DOCX, with OCR fallback for scanned pages.'),
    ('pdf-ppt','PDF to PowerPoint','Convert each PDF page to a PowerPoint slide.'),
    ('pdf-excel','PDF to Excel','Extract detected tables into an XLSX workbook.'),
    ('pdf-jpg','PDF to JPG','Render PDF pages as JPG images.'),
    ('word-pdf','Word to PDF','Convert DOC/DOCX to PDF.'),
    ('ppt-pdf','PowerPoint to PDF','Convert PPT/PPTX to PDF.'),
    ('excel-pdf','Excel to PDF','Convert XLS/XLSX to PDF.'),
    ('jpg-pdf','JPG to PDF','Combine images into a PDF.'),
    ('sign','Sign PDF','Add a simple text signature to a PDF page.'),
    ('watermark','Watermark','Add text watermark to every page.'),
    ('rotate','Rotate PDF','Rotate every page by 90, 180 or 270 degrees.'),
    ('html-pdf','HTML to PDF','Convert uploaded HTML into PDF.'),
    ('unlock','Unlock PDF','Remove password protection when you know the password.'),
    ('protect','Protect PDF','Encrypt a PDF with a password.'),
    ('organize','Organize PDF','Reorder pages using a page list such as 3,1,2.'),
    ('pdfa','PDF to PDF/A','Create an archival-style PDF copy.'),
    ('repair','Repair PDF','Rewrite a damaged/readable PDF into a fresh file.'),
    ('page-numbers','Page numbers','Add page numbers to every page.'),
    ('scan-pdf','Scan to PDF','Convert phone scans/images into a PDF.'),
    ('ocr','OCR PDF','Recognize text from scanned PDF pages.'),
    ('compare','Compare PDF','Create a text difference report for two PDFs.'),
    ('redact','Redact PDF','Search and permanently redact specified text.'),
    ('crop','Crop PDF','Crop all pages by margins in millimeters.'),
]

@app.get('/', response_class=HTMLResponse)
def home():
    return (BASE/'static'/'index.html').read_text(encoding='utf-8')

@app.get('/api/tools')
def tools():
    return [{'id':a,'name':b,'description':c} for a,b,c in TOOLS]

def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or '').suffix
    p = TMP / f'{uuid.uuid4().hex}{suffix}'
    with p.open('wb') as f:
        shutil.copyfileobj(upload.file, f)
    return p

def outpath(ext:str) -> Path:
    return TMP / f'{uuid.uuid4().hex}{ext}'

def libreoffice_to_pdf(src: Path) -> Path:
    outdir = Path(tempfile.mkdtemp(dir=TMP))
    cmd = ['libreoffice','--headless','--convert-to','pdf','--outdir',str(outdir),str(src)]
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    candidates = list(outdir.glob('*.pdf'))
    if not candidates:
        raise HTTPException(500, f'LibreOffice conversion failed: {cp.stderr[-500:]}')
    return candidates[0]

def pdf_to_word_visual(src: Path) -> Path:
    pdf = fitz.open(src)
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Inches(0)
    sec.bottom_margin = Inches(0)
    sec.left_margin = Inches(0)
    sec.right_margin = Inches(0)
    for i, page in enumerate(pdf):
        rect = page.rect
        width_in = rect.width/72
        height_in = rect.height/72
        sec.page_width = Inches(width_in)
        sec.page_height = Inches(height_in)
        pix = page.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False)
        img = outpath('.png')
        pix.save(img)
        p = doc.add_paragraph()
        p.paragraph_format.space_after = 0
        r = p.add_run()
        r.add_picture(str(img), width=Inches(width_in))
        if i < len(pdf)-1:
            doc.add_page_break()
    out = outpath('.docx')
    doc.save(out)
    return out

def _ocr_page_to_text(page) -> str:
    """OCR fallback for scanned/image-only PDF pages."""
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    # Prefer Czech + English when Czech language data is available.
    # Fall back to English if Render/Tesseract does not have Czech installed.
    try:
        return pytesseract.image_to_string(img, lang="ces+eng").strip()
    except Exception:
        try:
            return pytesseract.image_to_string(img, lang="eng").strip()
        except Exception:
            return ""


def pdf_to_word_editable(src: Path) -> Path:
    """
    Convert PDF to a genuinely editable DOCX.

    Digital PDFs:
      - extract text spans from the PDF
      - preserve approximate font size, bold and italic styling
      - keep paragraphs in reading order

    Scanned/image-only PDFs:
      - use OCR and insert recognized text as editable paragraphs

    The PDF page itself is NOT inserted as an image.
    """
    pdf = fitz.open(src)
    doc = Document()

    # Slightly tighter defaults so extracted documents do not become too long.
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)

    for page_index, page in enumerate(pdf):
        page_dict = page.get_text("dict")
        text_blocks = [
            b for b in page_dict.get("blocks", [])
            if b.get("type") == 0 and b.get("lines")
        ]

        # Reading order: top-to-bottom, then left-to-right.
        text_blocks.sort(
            key=lambda b: (
                round(float(b.get("bbox", [0, 0, 0, 0])[1]) / 4) * 4,
                float(b.get("bbox", [0, 0, 0, 0])[0]),
            )
        )

        page_has_text = False

        for block in text_blocks:
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                visible_spans = [s for s in spans if s.get("text", "").strip()]
                if not visible_spans:
                    continue

                page_has_text = True
                paragraph = doc.add_paragraph()
                paragraph.paragraph_format.space_after = Pt(1)
                paragraph.paragraph_format.space_before = Pt(0)

                for span in visible_spans:
                    value = span.get("text", "")
                    run = paragraph.add_run(value)

                    # Preserve approximate font size.
                    size = span.get("size")
                    if isinstance(size, (int, float)) and 5 <= size <= 72:
                        run.font.size = Pt(float(size))

                    font_name = str(span.get("font", "")).lower()
                    flags = int(span.get("flags", 0) or 0)

                    # PyMuPDF font flags may vary by PDF, so use both flags and font names.
                    if "bold" in font_name or flags & 16:
                        run.bold = True
                    if "italic" in font_name or "oblique" in font_name or flags & 2:
                        run.italic = True

        # OCR only when the page contains no extractable text.
        if not page_has_text:
            recognized = _ocr_page_to_text(page)
            if recognized:
                for line in recognized.splitlines():
                    if line.strip():
                        p = doc.add_paragraph(line.strip())
                        p.paragraph_format.space_after = Pt(1)
            else:
                doc.add_paragraph("[No editable text could be extracted from this page.]")

        if page_index < len(pdf) - 1:
            doc.add_page_break()

    out = outpath(".docx")
    doc.save(out)
    return out

@app.post('/api/convert')
async def convert(tool: str = Form(...), files: List[UploadFile] = File(default=[]), mode: str = Form('editable'),
                  password: str = Form(''), text: str = Form(''), rotation: int = Form(90),
                  pages: str = Form(''), margin: float = Form(10.0), page_number_start: int = Form(1),
                  signature_page: int = Form(1), signature_x: float = Form(30), signature_y: float = Form(30)):
    if tool != 'html-pdf' and not files:
        raise HTTPException(400,'Upload at least one file.')
    paths = [save_upload(f) for f in files]

    try:
        if tool == 'merge':
            w=PdfWriter()
            for p in paths:
                r=PdfReader(str(p))
                for page in r.pages: w.add_page(page)
            out=outpath('.pdf'); w.write(str(out)); return FileResponse(out, filename='merged.pdf')

        if tool == 'split':
            r=PdfReader(str(paths[0]));
            # Simple behavior: selected range/list -> one PDF, otherwise first page.
            idxs=[]
            if pages.strip():
                for token in pages.split(','):
                    token=token.strip()
                    if '-' in token:
                        a,b=map(int,token.split('-',1)); idxs.extend(range(a-1,b))
                    elif token: idxs.append(int(token)-1)
            else: idxs=[0]
            w=PdfWriter()
            for i in idxs:
                if 0<=i<len(r.pages): w.add_page(r.pages[i])
            out=outpath('.pdf'); w.write(str(out)); return FileResponse(out, filename='split.pdf')

        if tool == 'compress':
            doc=fitz.open(paths[0]); out=outpath('.pdf'); doc.save(out, garbage=4, deflate=True, clean=True); return FileResponse(out, filename='compressed.pdf')

        if tool == 'pdf-word':
            out=pdf_to_word_editable(paths[0])
            return FileResponse(out, filename='converted.docx')

        if tool == 'pdf-ppt':
            pdf=fitz.open(paths[0]); prs=Presentation(); prs.slide_width=PptInches(13.333); prs.slide_height=PptInches(7.5)
            blank=prs.slide_layouts[6]
            for page in pdf:
                slide=prs.slides.add_slide(blank)
                pix=page.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False); img=outpath('.png'); pix.save(img)
                slide.shapes.add_picture(str(img),0,0,width=prs.slide_width,height=prs.slide_height)
            if len(prs.slides)>len(pdf):
                pass
            out=outpath('.pptx'); prs.save(out); return FileResponse(out, filename='converted.pptx')

        if tool == 'pdf-excel':
            wb=Workbook(); wb.remove(wb.active)
            with pdfplumber.open(paths[0]) as pdf:
                for pi,page in enumerate(pdf.pages,1):
                    tables=page.extract_tables()
                    if not tables:
                        ws=wb.create_sheet(f'Page {pi}');
                        for ri,line in enumerate((page.extract_text() or '').splitlines(),1): ws.cell(ri,1,line)
                    else:
                        for ti,table in enumerate(tables,1):
                            ws=wb.create_sheet(f'P{pi} T{ti}'[:31])
                            for r,row in enumerate(table,1):
                                for c,val in enumerate(row,1): ws.cell(r,c,val)
            out=outpath('.xlsx'); wb.save(out); return FileResponse(out, filename='converted.xlsx')

        if tool == 'pdf-jpg':
            pdf=fitz.open(paths[0]);
            if len(pdf)==1:
                pix=pdf[0].get_pixmap(matrix=fitz.Matrix(2,2), alpha=False); out=outpath('.jpg'); pix.save(out); return FileResponse(out, filename='page-1.jpg')
            import zipfile
            out=outpath('.zip')
            with zipfile.ZipFile(out,'w') as z:
                for i,page in enumerate(pdf,1):
                    img=outpath('.jpg'); page.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False).save(img); z.write(img, f'page-{i}.jpg')
            return FileResponse(out, filename='pages.zip')

        if tool in {'word-pdf','ppt-pdf','excel-pdf'}:
            out=libreoffice_to_pdf(paths[0]); return FileResponse(out, filename='converted.pdf')

        if tool in {'jpg-pdf','scan-pdf'}:
            imgs=[Image.open(p).convert('RGB') for p in paths]; out=outpath('.pdf'); imgs[0].save(out, save_all=True, append_images=imgs[1:]); return FileResponse(out, filename='images.pdf')

        if tool == 'watermark':
            doc=fitz.open(paths[0]); wm=text or 'WATERMARK'
            for page in doc:
                rect=page.rect
                page.insert_text((rect.width*0.22, rect.height*0.52), wm, fontsize=34, rotate=0, overlay=True)
            out=outpath('.pdf'); doc.save(out); return FileResponse(out, filename='watermarked.pdf')

        if tool == 'rotate':
            doc=fitz.open(paths[0]);
            for page in doc: page.set_rotation((page.rotation + rotation)%360)
            out=outpath('.pdf'); doc.save(out); return FileResponse(out, filename='rotated.pdf')

        if tool == 'protect':
            doc=fitz.open(paths[0]); out=outpath('.pdf')
            doc.save(out, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=password or 'owner', user_pw=password or 'password', permissions=fitz.PDF_PERM_ACCESSIBILITY|fitz.PDF_PERM_PRINT)
            return FileResponse(out, filename='protected.pdf')

        if tool == 'unlock':
            doc=fitz.open(paths[0]);
            if doc.needs_pass and not doc.authenticate(password): raise HTTPException(400,'Incorrect password.')
            out=outpath('.pdf'); doc.save(out); return FileResponse(out, filename='unlocked.pdf')

        if tool == 'organize':
            doc=fitz.open(paths[0]); order=[int(x.strip())-1 for x in pages.split(',') if x.strip()]
            if not order: raise HTTPException(400,'Enter page order, e.g. 3,1,2')
            outdoc=fitz.open()
            for i in order:
                if 0<=i<len(doc): outdoc.insert_pdf(doc, from_page=i, to_page=i)
            out=outpath('.pdf'); outdoc.save(out); return FileResponse(out, filename='organized.pdf')

        if tool in {'repair','pdfa'}:
            doc=fitz.open(paths[0]); out=outpath('.pdf'); doc.save(out, garbage=4, deflate=True, clean=True); return FileResponse(out, filename='repaired.pdf' if tool=='repair' else 'archive-copy.pdf')

        if tool == 'page-numbers':
            doc=fitz.open(paths[0])
            for i,page in enumerate(doc,page_number_start):
                rect=page.rect; page.insert_text((rect.width/2-10, rect.height-18), str(i), fontsize=10)
            out=outpath('.pdf'); doc.save(out); return FileResponse(out, filename='numbered.pdf')

        if tool == 'ocr':
            pdf=fitz.open(paths[0]); doc=Document()
            for i,page in enumerate(pdf):
                pix=page.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False); img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
                recognized=pytesseract.image_to_string(img, lang='eng')
                doc.add_paragraph(recognized)
                if i<len(pdf)-1: doc.add_page_break()
            out=outpath('.docx'); doc.save(out); return FileResponse(out, filename='ocr.docx')

        if tool == 'compare':
            if len(paths)<2: raise HTTPException(400,'Upload two PDFs to compare.')
            import difflib
            ta='\n'.join(p.get_text() for p in fitz.open(paths[0])); tb='\n'.join(p.get_text() for p in fitz.open(paths[1]))
            diff='\n'.join(difflib.unified_diff(ta.splitlines(),tb.splitlines(),fromfile='PDF A',tofile='PDF B',lineterm=''))
            out=outpath('.txt'); out.write_text(diff,encoding='utf-8'); return FileResponse(out, filename='comparison.txt')

        if tool == 'redact':
            if not text.strip(): raise HTTPException(400,'Enter text to redact.')
            doc=fitz.open(paths[0])
            for page in doc:
                for r in page.search_for(text): page.add_redact_annot(r, fill=(0,0,0))
                page.apply_redactions()
            out=outpath('.pdf'); doc.save(out); return FileResponse(out, filename='redacted.pdf')

        if tool == 'crop':
            doc=fitz.open(paths[0]); m=margin*72/25.4
            for page in doc:
                r=page.rect; page.set_cropbox(fitz.Rect(r.x0+m,r.y0+m,r.x1-m,r.y1-m))
            out=outpath('.pdf'); doc.save(out); return FileResponse(out, filename='cropped.pdf')

        if tool == 'sign':
            doc=fitz.open(paths[0]); idx=max(0,min(signature_page-1,len(doc)-1)); page=doc[idx]
            page.insert_text((signature_x*72/25.4, signature_y*72/25.4), text or 'Signature', fontsize=18)
            out=outpath('.pdf'); doc.save(out); return FileResponse(out, filename='signed.pdf')

        if tool == 'html-pdf':
            if paths:
                html=paths[0].read_text(encoding='utf-8',errors='ignore')
            else: html=text
            out=outpath('.pdf'); HTML(string=html).write_pdf(out); return FileResponse(out, filename='page.pdf')

        raise HTTPException(400,'Unknown tool.')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f'Conversion failed: {e}')
