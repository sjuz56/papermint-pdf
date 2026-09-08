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


## Background processing and overload protection

Merge, split, compress, Word→PDF, rotate and organize jobs are placed in a bounded
Redis/RQ queue. One background worker processes one of these jobs at a time while
the web process remains responsive. Up to 20 jobs may wait; additional submissions
receive HTTP 429 and can be retried later. Each file is limited to 50 MB, a request
to 100 MB, and each job to 10 minutes by default.

The Docker image starts a small local Redis instance automatically. If `REDIS_URL`
is provided, it uses that external Redis/Render Key Value instance instead. The
PDF→Word V28 engine retains its separate bounded queue (one worker and six waiting
jobs).

The local Redis setup protects a single Render instance from overload, but its queue
is not durable across a container restart. Horizontal scaling requires external
Redis plus shared temporary object storage.

## Implemented tools

Merge, split/ranges, compress, PDF→Word (Visual 1:1 + Editable), PDF→PowerPoint, PDF→Excel, PDF→JPG, Word/PPT/Excel→PDF via LibreOffice, JPG→PDF, text signature, watermark, rotate, HTML→PDF, unlock, protect, organize/reorder, repair/rewrite, archival copy, page numbers, scan images→PDF, OCR→DOCX, text compare, text redaction, crop.

## Important production notes

This is a functional MVP, not yet a hardened public SaaS. Before public launch add rate limiting, malware scanning, page limits, encrypted storage, logging/monitoring, legal/privacy pages, billing, and sandboxed document conversion workers.

`Visual 1:1` PDF→Word preserves visual appearance by placing high-resolution page renders into DOCX pages. It is visually faithful but the page body itself is not fully editable. `Editable` extracts text into normal Word paragraphs and is therefore more editable but less layout-perfect.
