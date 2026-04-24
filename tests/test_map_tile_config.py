from app import app


def test_guest_map_page_redirects_to_login():
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/map")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_admin_page_injects_coord_map_warning_and_tile_provider_config():
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["role"] = "admin"
            session["user_id"] = 1

        response = client.get("/admin")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "coord-map-warning" in html
    assert "MAP_TILE_PROVIDERS" in html
    assert "webrd0" in html
