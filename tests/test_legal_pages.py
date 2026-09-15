from fastapi.testclient import TestClient

import app as papermint_app


client = TestClient(papermint_app.app)


def test_terms_page_is_available_and_contains_withdrawal_notice():
    response = client.get("/terms")

    assert response.status_code == 200
    assert "Obchodní podmínky" in response.text
    assert "§ 2389t" in response.text
    assert "§ 1837 písm. l)" in response.text
    assert "Skutečným využitím placené funkce" in response.text
    assert "Pouhé prohlížení webu" in response.text
    assert "práva z vadného plnění" in response.text
    assert 'href="mailto:pdfaspect@gmail.com"' in response.text
    assert "[DOPLNIT KONTAKTNÍ E-MAIL]" not in response.text


def test_home_page_links_to_terms():
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/terms"' in response.text
    assert 'href="/privacy"' in response.text
    assert '<link rel="canonical" href="https://pdfaspect.com/"' in response.text
    assert '/tools/merge' in response.text


def test_privacy_robots_sitemap_and_tool_pages_are_available():
    privacy = client.get("/privacy")
    robots = client.get("/robots.txt")
    sitemap = client.get("/sitemap.xml")
    tool = client.get("/tools/compress")

    assert privacy.status_code == 200
    assert "Ochrana osobních údajů" in privacy.text
    assert "Sitemap: https://pdfaspect.com/sitemap.xml" in robots.text
    assert "https://pdfaspect.com/tools/compress" in sitemap.text
    assert tool.status_code == 200
    assert "Compress PDF online" in tool.text


def test_security_headers_protect_responses():
    response = client.get("/", headers={"x-forwarded-proto": "https"})
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["strict-transport-security"].startswith("max-age=")
