from __future__ import annotations

from pathlib import Path
import os

from pypdf import PdfReader, PdfWriter


class UnlockError(Exception):
    """A safe, user-facing Unlock PDF error."""


def unlock_pdf(source: str | Path, output: str | Path, password: str) -> dict:
    """Remove password encryption and verify the resulting PDF."""
    source_path = Path(source)
    output_path = Path(output)
    temporary = output_path.with_suffix(output_path.suffix + ".part")

    if not source_path.is_file() or source_path.stat().st_size == 0:
        raise UnlockError("The uploaded PDF is empty or could not be read.")
    if not password:
        raise UnlockError("Please enter the PDF password.")
    if len(password) > 128:
        raise UnlockError("Password can contain at most 128 characters.")

    try:
        reader = PdfReader(str(source_path), strict=False)
        if not reader.is_encrypted:
            raise UnlockError("This PDF is not password-protected.")
        if reader.decrypt(password) == 0:
            raise UnlockError("The password is incorrect.")

        page_count = len(reader.pages)
        if page_count == 0:
            raise UnlockError("The uploaded PDF has no pages.")

        writer = PdfWriter()
        writer.clone_document_from_reader(reader)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as stream:
            writer.write(stream)

        check = PdfReader(str(temporary), strict=False)
        if check.is_encrypted:
            raise UnlockError("The unlocked PDF could not be verified.")
        if len(check.pages) != page_count:
            raise UnlockError("The unlocked PDF failed its page integrity check.")

        os.replace(temporary, output_path)
        return {
            "pages": page_count,
            "bytes_in": source_path.stat().st_size,
            "bytes_out": output_path.stat().st_size,
            "encryption_removed": True,
        }
    except UnlockError:
        raise
    except Exception as exc:
        raise UnlockError(f"Could not unlock this PDF: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
