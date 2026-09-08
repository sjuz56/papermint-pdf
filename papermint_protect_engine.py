from __future__ import annotations

from pathlib import Path
import os
import secrets

from pypdf import PdfReader, PdfWriter


class ProtectError(Exception):
    """A safe, user-facing Protect PDF error."""


def protect_pdf(source: str | Path, output: str | Path, password: str) -> dict:
    """Create and verify an AES-256 encrypted PDF copy."""
    source_path = Path(source)
    output_path = Path(output)
    temporary = output_path.with_suffix(output_path.suffix + ".part")

    if not source_path.is_file() or source_path.stat().st_size == 0:
        raise ProtectError("The uploaded PDF is empty or could not be read.")
    if len(password) < 6:
        raise ProtectError("Password must contain at least 6 characters.")
    if len(password) > 128:
        raise ProtectError("Password can contain at most 128 characters.")

    try:
        reader = PdfReader(str(source_path), strict=False)
        if reader.is_encrypted:
            raise ProtectError(
                "This PDF is already password-protected. Unlock it first."
            )
        page_count = len(reader.pages)
        if page_count == 0:
            raise ProtectError("The uploaded PDF has no pages.")

        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        writer.encrypt(
            user_password=password,
            owner_password=secrets.token_urlsafe(32),
            algorithm="AES-256",
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as stream:
            writer.write(stream)

        check = PdfReader(str(temporary), strict=False)
        if not check.is_encrypted or check.decrypt(password) == 0:
            raise ProtectError("The protected PDF could not be verified.")
        if len(check.pages) != page_count:
            raise ProtectError("The protected PDF failed its page integrity check.")

        os.replace(temporary, output_path)
        return {
            "pages": page_count,
            "bytes_in": source_path.stat().st_size,
            "bytes_out": output_path.stat().st_size,
            "encryption": "AES-256",
        }
    except ProtectError:
        raise
    except Exception as exc:
        raise ProtectError(f"Could not protect this PDF: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
