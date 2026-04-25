from datetime import timedelta

from app import app


def test_health_response_includes_basic_security_headers():
    original_testing = app.config.get("TESTING", False)
    app.config["TESTING"] = True

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = 1
                session["role"] = "admin"

            response = client.get("/health")
    finally:
        app.config["TESTING"] = original_testing

    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=()"


def test_authenticated_session_cookie_uses_secure_flag_when_enabled():
    original_testing = app.config.get("TESTING", False)
    original_secure = app.config["SESSION_COOKIE_SECURE"]
    original_lifetime = app.config["PERMANENT_SESSION_LIFETIME"]
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = 1
                session["role"] = "admin"

            response = client.get("/health")
    finally:
        app.config["TESTING"] = original_testing
        app.config["SESSION_COOKIE_SECURE"] = original_secure
        app.config["PERMANENT_SESSION_LIFETIME"] = original_lifetime

    set_cookie = response.headers.get("Set-Cookie", "")
    assert response.status_code == 200
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Expires=" in set_cookie
