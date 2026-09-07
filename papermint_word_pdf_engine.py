"""PaperMint Word to PDF engine.

Converts DOC and DOCX documents with LibreOffice Writer in an isolated,
headless profile and validates the resulting PDF before publication.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import fitz


class WordPdfError(RuntimeError):
    """A user-safe Word to PDF failure."""


def _find_soffice() -> str | None:
    configured = os.environ.get("PAPERMINT_SOFFICE", "").strip()
    candidates = [
        configured,
        shutil.which("libreoffice") or "",
        shutil.which("soffice") or "",
        "/usr/bin/libreoffice",
        "/usr/bin/soffice",
        "/opt/libreoffice/program/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _find_program(*names: str) -> str | None:
    for name in names:
        candidate = shutil.which(name)
        if candidate:
            return candidate
    return None


def _run_command(command: list[str], environment: dict, timeout_seconds: int) -> None:
    try:
        completed = subprocess.run(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WordPdfError("Word to PDF conversion timed out.") from exc

    if completed.returncode != 0:
        raise WordPdfError("The server could not convert this Word document.")


def _convert_with_libreoffice(
    soffice: str,
    source: Path,
    output_dir: Path,
    profile_dir: Path,
    environment: dict,
    timeout_seconds: int,
) -> Path:
    command = [
        soffice,
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    _run_command(command, environment, timeout_seconds)
    return output_dir / "document.pdf"


def _convert_with_pandoc(
    source: Path,
    output_dir: Path,
    work_root: Path,
    environment: dict,
    timeout_seconds: int,
) -> Path:
    """Fallback for Render's native runtime (Pandoc + WeasyPrint)."""
    pandoc = _find_program("pandoc")
    if not pandoc:
        raise WordPdfError(
            "Word to PDF is not available on this server because its document converter is missing."
        )

    stylesheet = work_root / "word-pdf.css"
    stylesheet.write_text(
        """
@page { size: A4; margin: 2.54cm; }
html { font-size: 11pt; }
body { color: #000; font-family: Arial, Helvetica, sans-serif; line-height: 1.15; }
p { margin: 0 0 8pt; }
h1, h2, h3, h4, h5, h6 { page-break-after: avoid; }
table { border-collapse: collapse; max-width: 100%; margin: 8pt 0; }
th, td { padding: 4pt 6pt; vertical-align: top; }
img { max-width: 100%; height: auto; }
""".strip(),
        encoding="utf-8",
    )
    converted = output_dir / "document.pdf"
    html_file = work_root / "document.html"
    media_dir = work_root / "media"
    pandoc_command = [
        pandoc,
        str(source),
        "--from=docx",
        "--to=html5",
        "--standalone",
        f"--extract-media={media_dir}",
        f"--output={html_file}",
    ]
    _run_command(pandoc_command, environment, timeout_seconds)

    # WeasyPrint is already part of PaperMint's Python requirements. Unlike
    # PrinceXML it is open source and never adds a vendor logo to the PDF.
    weasyprint_command = [
        sys.executable,
        "-m",
        "weasyprint",
        str(html_file),
        str(converted),
        "--stylesheet",
        str(stylesheet),
        "--presentational-hints",
    ]
    _run_command(weasyprint_command, environment, timeout_seconds)
    return converted


def _validate_word_file(path: Path, max_input_mb: float) -> None:
    suffix = path.suffix.lower()
    if suffix not in {".doc", ".docx"}:
        raise WordPdfError("Word to PDF accepts DOC and DOCX files only.")
    if not path.is_file() or path.stat().st_size == 0:
        raise WordPdfError("The uploaded Word document is missing or empty.")
    if path.stat().st_size > max_input_mb * 1024 * 1024:
        raise WordPdfError(
            f"The uploaded Word document is larger than {max_input_mb:g} MB."
        )

    if suffix == ".docx":
        try:
            with ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise WordPdfError("The uploaded DOCX file is not valid.")
        except WordPdfError:
            raise
        except (BadZipFile, OSError) as exc:
            raise WordPdfError("The uploaded DOCX file is not valid.") from exc
    else:
        # Legacy .doc files use the OLE Compound File signature.
        try:
            with path.open("rb") as stream:
                signature = stream.read(8)
        except OSError as exc:
            raise WordPdfError("The uploaded DOC file could not be read.") from exc
        if signature != bytes.fromhex("D0CF11E0A1B11AE1"):
            raise WordPdfError("The uploaded DOC file is not valid.")


def _validate_pdf(path: Path, max_pages: int) -> int:
    if not path.is_file() or path.stat().st_size < 100:
        raise WordPdfError("The server did not create a usable PDF.")

    try:
        document = fitz.open(path)
        try:
            if not document.is_pdf or document.needs_pass or document.page_count < 1:
                raise WordPdfError("The converted PDF failed its integrity check.")
            if document.page_count > max_pages:
                raise WordPdfError(
                    f"The converted PDF exceeds the {max_pages}-page limit."
                )
            # Force representative pages to load and render. This catches missing
            # resources that a page-count-only check would overlook.
            sample_pages = sorted({0, document.page_count // 2, document.page_count - 1})
            for page_number in sample_pages:
                page = document.load_page(page_number)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25))
                if not pixmap.samples:
                    raise WordPdfError("The converted PDF failed its visual check.")
            return document.page_count
        finally:
            document.close()
    except WordPdfError:
        raise
    except Exception as exc:
        raise WordPdfError("The converted PDF failed its integrity check.") from exc


def word_to_pdf(
    source: str | Path,
    output: str | Path,
    *,
    max_input_mb: float = 50.0,
    max_pages: int = 500,
    timeout_seconds: int = 180,
) -> dict:
    source_path = Path(source)
    destination = Path(output)
    _validate_word_file(source_path, max_input_mb)
    soffice = _find_soffice()
    require_libreoffice = os.environ.get("PAPERMINT_REQUIRE_LIBREOFFICE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if require_libreoffice and not soffice:
        raise WordPdfError(
            "High-fidelity Word to PDF conversion is unavailable because LibreOffice is missing."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    work_root = Path(
        tempfile.mkdtemp(prefix="papermint-word-pdf-", dir=destination.parent)
    )
    input_dir = work_root / "input"
    output_dir = work_root / "output"
    profile_dir = work_root / "profile"
    home_dir = work_root / "home"
    for directory in (input_dir, output_dir, profile_dir, home_dir):
        directory.mkdir(parents=True, exist_ok=True)

    local_source = input_dir / ("document" + source_path.suffix.lower())
    temporary = destination.with_name(destination.name + ".partial")

    try:
        shutil.copyfile(source_path, local_source)
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home_dir),
                "XDG_CACHE_HOME": str(home_dir / ".cache"),
                "XDG_CONFIG_HOME": str(home_dir / ".config"),
            }
        )

        if soffice:
            converter = "libreoffice"
            converted = _convert_with_libreoffice(
                soffice,
                local_source,
                output_dir,
                profile_dir,
                environment,
                timeout_seconds,
            )
        else:
            converter = "pandoc-weasyprint"
            converted = _convert_with_pandoc(
                local_source,
                output_dir,
                work_root,
                environment,
                timeout_seconds,
            )

        if not converted.is_file():
            raise WordPdfError("The server did not create a usable PDF.")

        shutil.copyfile(converted, temporary)
        pages = _validate_pdf(temporary, max_pages)
        temporary.replace(destination)
    except WordPdfError:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise WordPdfError(f"Word to PDF conversion failed: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(work_root, ignore_errors=True)

    return {
        "ok": True,
        "output": str(destination),
        "pages": pages,
        "input_bytes": source_path.stat().st_size,
        "output_bytes": destination.stat().st_size,
        "converter": converter,
    }
