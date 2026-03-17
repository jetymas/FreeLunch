from __future__ import annotations


def test_admin_ui_assets_are_served(client):
    index_response = client.get("/admin/ui")
    assert index_response.status_code == 200
    assert "text/html" in index_response.headers["content-type"]
    assert "FreeLunch Admin" in index_response.text
    assert "Control Console" in index_response.text
    assert 'data-page-link="health"' in index_response.text

    script_response = client.get("/admin/ui/app.js")
    assert script_response.status_code == 200
    assert "javascript" in script_response.headers["content-type"]
    assert "renderHealthPage" in script_response.text
    assert "refreshCurrentPage" in script_response.text

    module_response = client.get("/admin/ui/page-health.js")
    assert module_response.status_code == 200
    assert "javascript" in module_response.headers["content-type"]
    assert "renderHealthPage" in module_response.text

    settings_module_response = client.get("/admin/ui/page-settings.js")
    assert settings_module_response.status_code == 200
    assert "javascript" in settings_module_response.headers["content-type"]
    assert "Gateway bearer auth" in settings_module_response.text

    css_response = client.get("/admin/ui/app.css")
    assert css_response.status_code == 200
    assert "text/css" in css_response.headers["content-type"]
    assert ".shell" in css_response.text
    assert "color-scheme: dark" in css_response.text


def test_admin_ui_trailing_slash_redirects(client):
    response = client.get("/admin/ui/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/ui"


def test_admin_ui_rejects_invalid_asset_paths(client):
    traversal = client.get("/admin/ui/%2e%2e/README.md")
    assert traversal.status_code == 404
    assert traversal.json()["detail"] == "asset not found"

    missing = client.get("/admin/ui/does-not-exist.js")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "asset not found"
