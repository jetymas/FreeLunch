from __future__ import annotations


def test_admin_ui_assets_are_served(client):
    index_response = client.get("/admin/ui")
    assert index_response.status_code == 200
    assert "text/html" in index_response.headers["content-type"]
    assert "FreeLunch Admin" in index_response.text
    assert "runtime-unlocked provider secret vault" in index_response.text

    script_response = client.get("/admin/ui/app.js")
    assert script_response.status_code == 200
    assert "application/javascript" in script_response.headers["content-type"]
    assert "loadUninstallInfo" in script_response.text
    assert "vaultSetupForm" in script_response.text

    css_response = client.get("/admin/ui/app.css")
    assert css_response.status_code == 200
    assert css_response.status_code == 200
    assert "text/css" in css_response.headers["content-type"]
    assert ".page" in css_response.text
    assert "color-scheme: dark" in css_response.text
