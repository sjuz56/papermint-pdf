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
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from papermint_edit_engine import editing_font_path
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
            page.insert_text((72, 360), "Původní písmo", fontsize=17, fontname="sourcefont",
                             fontfile=str(editing_font_path("dejavu-serif", True, True)), color=(0x33 / 255, 0x4C / 255, 0x66 / 255))
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
                    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
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

                    def download_text(formatted_text=None, bold=None, italic=None, font=None, size=None, color=None, baseline=None):
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
                                # Some installed TTF versions map the space glyph to NBSP during extraction.
                                match = next((s for s in spans if s["text"].replace("\u00a0", " ") == formatted_text
                                              and (baseline is None or abs(s["origin"][1] - baseline) < 0.01)), None)
                                assert match, "Expected text is missing or split: " + repr([(s["text"], s["font"], s["size"]) for s in spans])
                                if bold is not None:
                                    assert bool(match["flags"] & 16) == bold, "PDF font weight differs from the editor"
                                if italic is not None:
                                    assert bool(match["flags"] & 2) == italic, "PDF italic style differs from the editor"
                                if font is not None:
                                    assert match["font"] == font, match["font"]
                                if size is not None:
                                    assert abs(match["size"] - size) < 0.01, match["size"]
                                if color is not None:
                                    assert match["color"] == color, match["color"]
                            return edited[0].get_text().replace("\u00a0", " ")

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
                    original_source = page.locator(".span").nth(4)
                    original_source.click()
                    page.locator("#replacement").press("Control+a")
                    page.locator("#replacement").press("Control+c")
                    clipboard = page.evaluate("""async () => {
                        const item = (await navigator.clipboard.read())[0];
                        return {text: await (await item.getType('text/plain')).text(),
                                html: await (await item.getType('text/html')).text()};
                    }""")
                    assert clipboard["text"] == "Původní písmo"
                    assert 'font-family:' in clipboard["html"] and '17pt' in clipboard["html"]
                    assert 'data-pdfaspect-format' in clipboard["html"]
                    page.locator("#replacement").evaluate("el => el.setSelectionRange(0, 7)")
                    page.locator("#replacement").press("Control+c")
                    partial_html = page.evaluate("async () => (await (await navigator.clipboard.read())[0].getType('text/html')).text()")
                    assert "Původní písmo" not in partial_html, "Copy leaked unselected text in hidden clipboard metadata"
                    page.locator("#replacement").press("Control+a")
                    page.locator("#replacement").press("Control+c")
                    short_target.click()
                    page.locator("#replacement").press("Control+a")
                    page.locator("#replacement").press("Control+v")
                    expect(page.locator("#replacement")).to_have_value("Původní písmo")
                    expect(page.locator("#font-family")).to_have_value("copied-original")
                    expect(page.locator("#font-size")).to_have_value("17")
                    expect(page.locator("#font-color")).to_have_value("#334c66")
                    expect(page.locator("#bold")).to_have_attribute("aria-pressed", "true")
                    expect(page.locator("#italic")).to_have_attribute("aria-pressed", "true")
                    expect(page.locator("#apply")).to_be_enabled()
                    if qa_dir:
                        page.screenshot(path=str(Path(qa_dir) / ("mobile-paste.png" if mobile else "desktop-paste.png")), full_page=True)
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    download_text("Původní písmo", bold=True, italic=True, font="DejaVuSerif-BoldItalic", size=17, color=0x334C66, baseline=240)
                    page.locator("#undo").click()
                    page.get_by_text("Previous PDF restored.", exact=False).wait_for()
                    download_text("Hi", bold=False, italic=False, size=12, baseline=240)
                    page.locator("#redo").click()
                    page.get_by_text("PDF reapplied.", exact=False).wait_for()
                    short_target.click()
                    expect(page.locator("#font-family")).to_have_value("copied-original")
                    page.locator("#cancel").click()
                    page.locator("#undo").click()
                    page.get_by_text("Previous PDF restored.", exact=False).wait_for()

                    # Mobile clipboards that keep only text still retain this editor's copied font.
                    short_target.click()
                    page.locator("#replacement").press("Control+a")
                    page.locator("#replacement").evaluate("""el => {
                        const data = new DataTransfer(); data.setData('text/plain', 'Původní písmo');
                        el.dispatchEvent(new ClipboardEvent('paste', {clipboardData:data, bubbles:true, cancelable:true}));
                    }""")
                    expect(page.locator("#replacement")).to_have_value("Původní písmo")
                    expect(page.locator("#font-family")).to_have_value("copied-original")
                    page.locator("#cancel").click()

                    # The same rich clipboard can intentionally be pasted as plain text.
                    short_target.click()
                    page.locator("#replacement").press("Control+a")
                    page.locator("#replacement").press("Control+Shift+v")
                    expect(page.locator("#replacement")).to_have_value("Původní písmo")
                    expect(page.locator("#font-family")).to_have_value("original")
                    expect(page.locator("#font-size")).to_have_value("12")
                    expect(page.locator("#bold")).to_have_attribute("aria-pressed", "false")
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    download_text("Původní písmo", bold=False, italic=False, size=12, color=0, baseline=240)
                    page.locator("#undo").click()
                    page.get_by_text("Previous PDF restored.", exact=False).wait_for()

                    # Cut uses the same font metadata and retains native text undo.
                    original_source.click()
                    page.locator("#replacement").press("Control+a")
                    page.locator("#replacement").press("Control+x")
                    expect(page.locator("#replacement")).to_have_value("")
                    page.locator("#replacement").press("Control+z")
                    expect(page.locator("#replacement")).to_have_value("Původní písmo")
                    page.locator("#cancel").click()

                    # Opening a block keeps a caret, so typing appends instead of erasing the word.
                    short_target.click()
                    expect(page.locator("#replacement")).to_be_focused()
                    page.locator("#replacement").press("End")
                    page.locator("#replacement").press("!")
                    expect(page.locator("#replacement")).to_have_value("Hi!")
                    page.locator(".page > img").click(position={"x": 20, "y": 400})
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    download_text("Hi!")
                    page.locator("#undo").click()
                    page.get_by_text("Previous PDF restored.", exact=False).wait_for()

                    # Embed the same real font in both the typing field and exported PDF.
                    for family, font in (("dejavu-serif", "DejaVuSerif-BoldItalic"),
                                         ("liberation-mono", "LiberationMono-BoldItalic")):
                        short_target.click()
                        expect(page.locator("#font-family option")).to_have_count(7)
                        page.locator("#replacement").fill("Příliš žluťoučký kůň")
                        page.locator("#font-family").select_option(family)
                        page.locator("#bold").click()
                        if mobile:
                            page.locator("#italic").click()
                        else:
                            page.locator("#replacement").press("Control+i")
                        page.locator("#font-size").fill("15")
                        page.locator("#font-color").evaluate("el => { el.value = '#2456a8'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
                        expect(page.locator("#apply")).to_be_enabled()
                        assert "PDFaspect-" + family in page.locator("#replacement").evaluate("el => getComputedStyle(el).fontFamily")
                        assert page.locator("#replacement").evaluate("el => getComputedStyle(el).fontStyle") == "italic"
                        before_zoom = float(page.locator("#replacement").evaluate("el => parseFloat(getComputedStyle(el).fontSize)"))
                        page.locator("#zoom").select_option("200")
                        expect(page.locator("#apply")).to_be_enabled()
                        assert float(page.locator("#replacement").evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) > before_zoom
                        assert page.locator("#pages").evaluate("el => el.scrollWidth > el.clientWidth")
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Zoom overflows the whole viewport"
                        page.locator("#zoom").select_option("fit")
                        expect(page.locator("#apply")).to_be_enabled()
                        if qa_dir and family == "dejavu-serif":
                            page.screenshot(path=str(Path(qa_dir) / ("mobile-fonts.png" if mobile else "desktop-fonts.png")), full_page=True)
                        page.locator("#apply").click()
                        page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                        download_text("Příliš žluťoučký kůň", bold=True, italic=True, font=font, size=15, color=0x2456A8)
                        page.locator("#undo").click()
                        page.get_by_text("Previous PDF restored.", exact=False).wait_for()
                        download_text("Hi", bold=False, italic=False, size=12, color=0)
                        page.locator("#redo").click()
                        page.get_by_text("PDF reapplied.", exact=False).wait_for()
                        download_text("Příliš žluťoučký kůň", bold=True, italic=True, font=font, size=15, color=0x2456A8)
                        short_target.click()
                        expect(page.locator("#font-family")).to_have_value(family)
                        expect(page.locator("#font-size")).to_have_value("15")
                        expect(page.locator("#font-color")).to_have_value("#2456a8")
                        expect(page.locator("#bold")).to_have_attribute("aria-pressed", "true")
                        expect(page.locator("#italic")).to_have_attribute("aria-pressed", "true")
                        # A partial selection also carries the chosen family to another block.
                        page.locator("#replacement").evaluate("el => el.setSelectionRange(0, 6)")
                        page.locator("#replacement").press("Control+c")
                        destination = page.locator(".span").nth(3)
                        destination.click()
                        page.locator("#replacement").press("Control+a")
                        page.locator("#replacement").press("Control+v")
                        expect(page.locator("#replacement")).to_have_value("Příliš")
                        expect(page.locator("#font-family")).to_have_value(family)
                        expect(page.locator("#font-size")).to_have_value("15")
                        expect(page.locator("#apply")).to_be_enabled()
                        page.locator("#apply").click()
                        page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                        download_text("Příliš", bold=True, italic=True, font=font, size=15, color=0x2456A8, baseline=280)
                        page.locator("#undo").click()
                        page.get_by_text("Previous PDF restored.", exact=False).wait_for()
                        short_target.click()
                        before_invalid = page.locator(".page > img").first.get_attribute("src")
                        page.locator("#font-size").fill("145")
                        expect(page.locator("#apply")).to_be_disabled()
                        expect(page.locator("#save")).to_be_disabled()
                        assert page.locator(".page > img").first.get_attribute("src") == before_invalid
                        page.locator("#cancel").click()
                        page.locator("#undo").click()
                        page.get_by_text("Previous PDF restored.", exact=False).wait_for()

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

                    # Pointer resizing controls wrapping and survives reopening the selected block.
                    short_target.click()
                    handle = page.locator("#width-handle")
                    maximum = float(handle.get_attribute("aria-valuemax"))
                    scale = page.locator("#replacement").bounding_box()["width"] / maximum
                    handle.scroll_into_view_if_needed()
                    box = handle.bounding_box()
                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    page.mouse.down()
                    page.mouse.move(box["x"] + box["width"] / 2 - (maximum - 200) * scale,
                                    box["y"] + box["height"] / 2, steps=8)
                    page.mouse.up()
                    width = float(handle.get_attribute("aria-valuenow"))
                    assert abs(width - 200) <= 2, width
                    assert page.locator("#replacement").evaluate("""el => {
                        const style = getComputedStyle(el), ctx = document.createElement('canvas').getContext('2d');
                        ctx.font = style.font;
                        return ctx.measureText(el.value).width > el.clientWidth;
                    }"""), "Resized text field is still too wide to test wrapping"
                    page.locator("#apply").click()
                    page.get_by_text("Preview shows the saved PDF.", exact=False).wait_for()
                    result = download_text()
                    assert "".join(sentence.split()) in "".join(result.split()), result
                    assert sentence not in result, "Export did not wrap to match the narrower field"
                    short_target.click()
                    assert abs(float(handle.get_attribute("aria-valuenow")) - width) <= 1
                    page.locator("#cancel").click()
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
                print("Browser smoke test passed on desktop and mobile: native copy/cut/paste, original and selected font preservation, plain-text paste, caret typing, font styles, zoom, resize, Czech, undo/redo, overflow, delete, and preview/export pixel equality")
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
