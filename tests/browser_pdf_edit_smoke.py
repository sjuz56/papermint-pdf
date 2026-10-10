"""Browser smoke test: upload, select text, replace, download, inspect PDF."""
import os
import base64
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlparse

import fitz
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 18765
BASE = os.getenv("PDFASPECT_EDITOR_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")


def main():
    with tempfile.TemporaryDirectory(prefix="pdfaspect-browser-") as directory:
        pdf_path = Path(directory) / "invoice.pdf"
        with fitz.open() as doc:
            page = doc.new_page()
            page.draw_rect(fitz.Rect(65, 80, 180, 125), fill=(0.2, 0.7, 0.4), color=None)
            page.insert_text((72, 100), "Invoice 1234", fontsize=12)
            page.insert_text((72, 140), "Total 500", fontsize=12)
            page.insert_text((72, 240), "Hi", fontsize=12)
            page.insert_text((72, 280), "Preserve this line", fontsize=12)
            doc.save(pdf_path)

        env = {**os.environ, "PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}
        server = None if os.getenv("PDFASPECT_EDITOR_BASE_URL") else subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "browser_pdf_edit_server:app", "--app-dir", "tests",
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            for _ in range(80 if server else 1):
                if server and server.poll() is not None:
                    raise RuntimeError("PDF editor test server exited unexpectedly")
                try:
                    with urlopen(BASE + "/static/edit-pdf-prototype.html", timeout=1 if server else 45):
                        break
                except Exception:
                    time.sleep(0.25)
            else:
                raise RuntimeError("PDF editor test server did not start")

            with sync_playwright() as playwright:
                launch_options = {"headless": True}
                # Use the supplied network proxy only for synthetic public-preview QA.
                if not server and (proxy_url := os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")):
                    proxy = urlparse(proxy_url)
                    launch_options["proxy"] = {"server": f"{proxy.scheme}://{proxy.hostname}:{proxy.port}"}
                    if proxy.username:
                        launch_options["proxy"].update(username=proxy.username, password=proxy.password or "")
                browser = playwright.chromium.launch(**launch_options)
                for mobile in (False, True):
                    page = browser.new_page(accept_downloads=True,
                        viewport={"width": 390 if mobile else 1100, "height": 900},
                        is_mobile=mobile, has_touch=mobile, ignore_https_errors=not bool(server))
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

                    def download_text(formatted_text=None, bold=None):
                        with page.expect_download(timeout=30000) as download_info:
                            page.locator("#save").click()
                        output_path = Path(directory) / ("mobile.pdf" if mobile else "desktop.pdf")
                        download_info.value.save_as(output_path)
                        with fitz.open(output_path) as edited:
                            image = page.locator(".page > img").first.get_attribute("src")
                            preview = fitz.Pixmap(base64.b64decode(image.split(",", 1)[1]))
                            rendered = edited[0].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
                            assert preview.samples == rendered.samples, "Preview differs from exported PDF"
                            if formatted_text is not None:
                                spans = [s for b in edited[0].get_text("dict")["blocks"]
                                         for line in b.get("lines", []) for s in line["spans"]]
                                match = next((s for s in spans if s["text"] == formatted_text), None)
                                assert match, "Text was unexpectedly split across lines"
                                assert bool(match["flags"] & 16) == bold, "PDF font weight differs from the editor"
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

                    short_target = page.locator(".span").nth(2)
                    short_target.click()
                    field_width = page.locator("#replacement").bounding_box()["width"]
                    assert field_width > (250 if mobile else 500), "Short words still have a narrow input box"
                    assert page.locator("#bold").get_attribute("aria-pressed") == "false"
                    page.locator("#bold").click()
                    assert page.locator("#replacement").evaluate("el => getComputedStyle(el).fontWeight") == "700"
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    download_text("Hi", bold=True)
                    page.locator("#undo").click()
                    page.get_by_text("Previous PDF restored.", exact=False).wait_for()
                    download_text("Hi", bold=False)

                    short_target.click()
                    sentence = "Příliš žluťoučký kůň běží dál po celé stránce"
                    page.locator("#replacement").fill(sentence)
                    if mobile:
                        page.locator("#bold").click()
                    else:
                        page.locator("#replacement").press("Control+b")
                    assert page.locator("#replacement").evaluate("el => el.scrollHeight") <= 28, "Sentence wraps in the input despite free horizontal space"
                    if qa_dir:
                        page.screenshot(path=str(Path(qa_dir) / ("mobile-input.png" if mobile else "desktop-input.png")), full_page=True)
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    download_text(sentence, bold=True)
                    if qa_dir:
                        page.screenshot(path=str(Path(qa_dir) / ("mobile-bold.png" if mobile else "desktop-bold.png")), full_page=True)
                    short_target.click()
                    assert page.locator("#bold").get_attribute("aria-pressed") == "true"
                    page.locator("#bold").click()
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    download_text(sentence, bold=False)
                    page.locator("#undo").click()
                    page.get_by_text("Previous PDF restored.", exact=False).wait_for()
                    download_text(sentence, bold=True)

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
                print("Browser smoke test passed on desktop and mobile: horizontal typing, bold toggle and shortcut, Czech, undo, wrapping, overflow, delete, and preview/export pixel equality")
        finally:
            if server:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
