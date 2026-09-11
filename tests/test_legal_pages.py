from fastapi.testclient import TestClient

import app as papermint_app


client = TestClient(papermint_app.app)


def test_terms_page_is_available_and_contains_withdrawal_notice():
    response = client.get("/terms")

    assert response.status_code == 200
    assert "Obchodní podmínky" in response.text
    assert "prvním skutečným využitím placené funkce" in response.text
    assert "práva z vadného plnění" in response.text


def test_home_page_links_to_terms():
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/terms"' in response.text
