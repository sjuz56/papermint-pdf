"""Browser smoke test: upload, select text, replace, download, inspect PDF."""
import os
import base64
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

import fitz
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 18765
BASE = f"http://127.0.0.1:{PORT}"


def main():
    with tempfile.TemporaryDirectory(prefix="pdfaspect-browser-") as directory:
        pdf_path = Path(directory) / "invoice.pdf"
        with fitz.open() as doc:
            page = doc.new_page()
            page.draw_rect(fitz.Rect(65, 80, 180, 125), fill=(0.2, 0.7, 0.4), color=None)
            page.insert_text((72, 100), "Invoice 1234", fontsize=12)
            page.insert_text((72, 140), "Total 500", fontsize=12)
            doc.save(pdf_path)

        env = {**os.environ, "PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "browser_pdf_edit_server:app", "--app-dir", "tests",
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            for _ in range(80):
                if server.poll() is not None:
                    raise RuntimeError("PDF editor test server exited unexpectedly")
                try:
                    with urlopen(BASE + "/static/edit-pdf-prototype.html", timeout=1):
                        break
                except Exception:
                    time.sleep(0.25)
            else:
                raise RuntimeError("PDF editor test server did not start")

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                for mobile in (False, True):
                    page = browser.new_page(accept_downloads=True,
                        viewport={"width": 390 if mobile else 1100, "height": 900},
                        is_mobile=mobile, has_touch=mobile)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(BASE + "/static/edit-pdf-prototype.html")
                    page.locator("#file").set_input_files(str(pdf_path))
                    page.get_by_text("Loaded 1 pages.", exact=False).wait_for(timeout=20000)
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "PDF page overflows the viewport"
                    original_image = page.locator(".page > img").first.get_attribute("src")
                    target = page.locator(".span").first
                    target.dblclick()
                    assert page.locator(".span > #replacement").count() == 1
                    page.locator("#replacement").fill("Invoice number 12")
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    assert page.locator(".page > img").first.get_attribute("src") != original_image

                    def download_text():
                        with page.expect_download(timeout=30000) as download_info:
                            page.locator("#save").click()
                        output_path = Path(directory) / ("mobile.pdf" if mobile else "desktop.pdf")
                        download_info.value.save_as(output_path)
                        with fitz.open(output_path) as edited:
                            image = page.locator(".page > img").first.get_attribute("src")
                            preview = fitz.Pixmap(base64.b64decode(image.split(",", 1)[1]))
                            rendered = edited[0].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
                            assert preview.samples == rendered.samples, "Preview differs from exported PDF"
                            return edited[0].get_text()

                    result = download_text()
                    assert "Invoicenumber12" in "".join(result.split()), result
                    assert "Invoice 1234" not in result and "Total 500" in result, result
                    qa_dir = os.getenv("PDFASPECT_QA_DIR")
                    if qa_dir:
                        Path(qa_dir).mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(Path(qa_dir) / ("mobile.png" if mobile else "desktop.png")))
                    target.click()
                    page.locator("#replacement").fill("Příjem 12\nDruhý řádek")
                    if mobile:
                        page.locator("#apply").click()
                    else:
                        page.locator("#replacement").press("Control+Enter")
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    result = download_text()
                    assert "Příjem12Druhýřádek" in "".join(result.split()), result
                    page.locator("#undo").click()
                    page.get_by_text("Previous PDF restored.", exact=False).wait_for()
                    result = download_text()
                    assert "Invoicenumber12" in "".join(result.split()) and "Příjem" not in result
                    before_failure = page.locator(".page > img").first.get_attribute("src")
                    target.click()
                    page.locator("#replacement").fill("This replacement is far too long " * 20)
                    page.locator("#apply").click()
                    page.get_by_text("Unable to apply text:", exact=False).wait_for()
                    assert page.locator(".page > img").first.get_attribute("src") == before_failure
                    page.locator("#cancel").click()
                    target.click()
                    page.locator("#replacement").fill("")
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    result = download_text()
                    assert "Invoice" not in result and "Total 500" in result, result
                    assert not errors, errors
                    page.close()
                browser.close()
                print("Browser smoke test passed on desktop and mobile: inline edit, wrapping, Czech, undo, overflow, delete, and preview/export pixel equality")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
