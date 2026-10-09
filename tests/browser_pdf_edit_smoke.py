"""Browser smoke test: upload, select text, replace, download, inspect PDF."""
import os
import subprocess
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
            page.insert_text((72, 100), "Invoice 1234", fontsize=12)
            page.insert_text((72, 140), "Total 500", fontsize=12)
            doc.save(pdf_path)

        env = {**os.environ, "PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}
        server = subprocess.Popen(
            ["python", "-m", "uvicorn", "tests.browser_pdf_edit_server:app",
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
                page = browser.new_page(accept_downloads=True)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(BASE + "/static/edit-pdf-prototype.html")
                page.locator("#file").set_input_files(str(pdf_path))
                page.get_by_text("Loaded 1 pages.", exact=False).wait_for(timeout=20000)
                page.locator(".span[aria-label='Edit Invoice 1234']").click()
                page.locator("#replacement").fill("Invoice 12")
                page.locator("#apply").click()
                with page.expect_download(timeout=30000) as download_info:
                    page.locator("#save").click()
                download = download_info.value
                output_path = Path(directory) / "edited.pdf"
                download.save_as(output_path)
                with fitz.open(output_path) as edited:
                    result = edited[0].get_text()
                    assert "Invoice 12" in result, result
                    assert "Invoice 1234" not in result, result
                    assert "Total 500" in result, result
                assert not errors, errors
                browser.close()
                print("Browser smoke test passed: CSP, upload, select, edit and download")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
