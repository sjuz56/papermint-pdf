# PaperMint PDF Toolbox

Functional local prototype of a multi-tool PDF web app.

## Run

```bash
cd pdf_toolbox_web
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://127.0.0.1:8000

## Render deployment

Deploy the service with Render's **Docker** runtime and the root `Dockerfile`.
The image installs LibreOffice Writer and Microsoft-compatible substitute fonts,
which are required for layout-preserving Word to PDF conversion. The container
starts Uvicorn automatically and binds to Render's `PORT` value.

Do not deploy Word to PDF with Render's native Python runtime. It does not include
LibreOffice, so the emergency Pandoc/WeasyPrint fallback cannot preserve floating
Word objects such as positioned images.

## Implemented tools

Merge, split/ranges, compress, PDF→Word (Visual 1:1 + Editable), PDF→PowerPoint, PDF→Excel, PDF→JPG, Word/PPT/Excel→PDF via LibreOffice, JPG→PDF, text signature, watermark, rotate, HTML→PDF, unlock, protect, organize/reorder, repair/rewrite, archival copy, page numbers, scan images→PDF, OCR→DOCX, text compare, text redaction, crop.

## Important production notes

This is a functional MVP, not yet a hardened public SaaS. Before public launch add automatic temp-file deletion, rate limiting, malware scanning, file-size/page limits, job queues, encrypted storage, logging/monitoring, legal/privacy pages, billing, and sandboxed document conversion workers.

`Visual 1:1` PDF→Word preserves visual appearance by placing high-resolution page renders into DOCX pages. It is visually faithful but the page body itself is not fully editable. `Editable` extracts text into normal Word paragraphs and is therefore more editable but less layout-perfect.
