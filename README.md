# PaperMint PDF Toolbox

Functional local prototype of a multi-tool PDF web app.

Planned subscription price: **€7/month** or **€60/year**.

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

Set `DATABASE_URL` to a Render PostgreSQL connection string before enabling public
accounts. Local development uses `data/papermint.sqlite3`; Render's default disk is
ephemeral, so SQLite must not be used for production accounts.


## Background processing and overload protection

All tools except the dedicated PDF→Word V28 pipeline
are placed in a bounded Redis/RQ queue. One background worker processes one of
these jobs at a time while
the web process remains responsive. Up to 20 jobs may wait; additional submissions
receive HTTP 429 and can be retried later. Each file is limited to 50 MB, a request
to 100 MB, and each job to 10 minutes by default.

The Docker image starts a small local Redis instance automatically. If `REDIS_URL`
is provided, it uses that external Redis/Render Key Value instance instead. The
PDF→Word V28 engine retains its separate bounded queue (one worker and six waiting
jobs).

Uploaded originals are removed as soon as queued processing finishes. Results are
removed after download or automatically after 30 minutes by default.

The local Redis setup protects a single Render instance from overload, but its queue
is not durable across a container restart. Horizontal scaling requires external
Redis plus shared temporary object storage.

## Ask Octo AI

Ask Octo is the 27th tool and is available only to signed-in Pro users. It extracts
PDF text locally, uses the existing multilingual OCR pipeline for scanned pages,
creates a cited summary, and answers up to three questions about the document.
The default limits are 30 AI documents per billing month, 3 questions per document,
and 50 pages per document.

Configure these Render environment variables:

```text
OPENAI_API_KEY=...
PAPERMINT_AI_MODEL=gpt-5.6-luna
```

Until Stripe webhooks activate subscriptions in the `subscriptions` table, test Pro
access by setting `PAPERMINT_PRO_EMAILS` to a comma-separated list of account email
addresses. Never put `OPENAI_API_KEY` in frontend code or commit it to Git.

The uploaded PDF is deleted immediately after extraction. Extracted text is kept in
process memory for the question session and removed after 30 minutes; OpenAI requests
set `store` to `false`.

## Implemented tools

Merge, split/ranges, compress, editable PDF→Word, PDF→PowerPoint, PDF→Excel, PDF→JPG ZIP, Word/PPT/Excel→PDF via LibreOffice, JPG/images→PDF, text signature, watermark, rotate, HTML→PDF, unlock, protect, organize/reorder, repair/rewrite, PDF/A-2b, page numbers, enhanced scan images→PDF, multilingual OCR→DOCX, text compare, permanent text redaction, crop, and paid Ask Octo AI summaries and document Q&A.

## Important production notes

This is a functional MVP, not yet a hardened public SaaS. Before public launch add email verification and password reset, rate limiting, malware scanning, page limits, encrypted storage, logging/monitoring, legal/privacy pages, billing, and sandboxed document conversion workers.

PDF→Word creates an editable DOCX and preserves the original layout as closely as the source permits.

PDF→PowerPoint creates one high-resolution page image per slide. It preserves the
PDF appearance and page proportions, but the content inside the page image is not
individually editable.
