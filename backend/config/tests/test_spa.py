import pytest


@pytest.fixture
def built_frontend(settings, tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    settings.FRONTEND_DIST_DIR = tmp_path
    return tmp_path


def test_a_client_side_route_gets_the_spa_shell(client, built_frontend):
    response = client.get("/experiments/3")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert b'<div id="root">' in response.content


def test_the_site_root_gets_the_spa_shell(client, built_frontend):
    response = client.get("/")
    assert response.status_code == 200
    assert b'<div id="root">' in response.content


def test_an_unknown_api_path_is_a_404_not_the_shell(client, built_frontend):
    response = client.get("/api/does-not-exist/")
    assert response.status_code == 404
    assert b'<div id="root">' not in response.content


def test_admin_without_a_trailing_slash_still_reaches_the_admin(client, built_frontend):
    response = client.get("/admin")
    assert response.status_code == 301
    assert response["Location"] == "/admin/"


def test_without_a_frontend_build_the_shell_is_a_404(client, settings, tmp_path):
    settings.FRONTEND_DIST_DIR = tmp_path / "missing"
    assert client.get("/dashboard").status_code == 404
