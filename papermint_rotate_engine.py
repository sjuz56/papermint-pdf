"""PaperMint Rotate PDF engine.

Rotates every page without rasterizing or recompressing page content.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter


class RotateError(RuntimeError):
    """A user-safe rotation failure."""


def rotate_pdf(
    source: str | Path,
    output: str | Path,
    rotation: int = 90,
    *,
    max_input_mb: float = 100.0,
    max_pages: int = 1000,
) -> dict:
    input_path = Path(source)
    destination = Path(output)

    if input_path.suffix.lower() != ".pdf":
        raise RotateError("The uploaded file is not a PDF.")
    if not input_path.is_file():
        raise RotateError("The uploaded PDF was not found.")
    if input_path.stat().st_size == 0:
        raise RotateError("The uploaded PDF is empty.")
    if input_path.stat().st_size > max_input_mb * 1024 * 1024:
        raise RotateError(f"The uploaded PDF is larger than {max_input_mb:g} MB.")

    try:
        angle = int(rotation)
    except (TypeError, ValueError) as exc:
        raise RotateError("Choose a rotation of 90, 180 or 270 degrees.") from exc
    if angle not in {90, 180, 270}:
        raise RotateError("Choose a rotation of 90, 180 or 270 degrees.")

    try:
        reader = PdfReader(str(input_path), strict=False)
    except Exception as exc:
        raise RotateError("The uploaded file is not a readable PDF.") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise RotateError("The PDF is password-protected. Unlock it before rotating.")

    page_count = len(reader.pages)
    if page_count == 0:
        raise RotateError("The PDF contains no pages.")
    if page_count > max_pages:
        raise RotateError(f"The PDF exceeds the {max_pages}-page limit.")

    writer = PdfWriter()
    temporary = destination.with_name(destination.name + ".partial")

    try:
        for page in reader.pages:
            page.rotate(angle)
            writer.add_page(page)

        if reader.metadata:
            metadata = {
                str(key): str(value)
                for key, value in reader.metadata.items()
                if key and value is not None
            }
            if metadata:
                writer.add_metadata(metadata)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as stream:
            writer.write(stream)
        temporary.replace(destination)
    except RotateError:
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise RotateError("The PDF could not be rotated.") from exc
    finally:
        writer.close()

    try:
        result = PdfReader(str(destination), strict=False)
        actual_pages = len(result.pages)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise RotateError("The rotated PDF failed its final integrity check.") from exc
    finally:
        temporary.unlink(missing_ok=True)

    if actual_pages != page_count:
        destination.unlink(missing_ok=True)
        raise RotateError("The rotated PDF has an unexpected page count.")

    return {
        "ok": True,
        "output": str(destination),
        "pages": actual_pages,
        "rotation": angle,
        "input_bytes": input_path.stat().st_size,
        "output_bytes": destination.stat().st_size,
    }
