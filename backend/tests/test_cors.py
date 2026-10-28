from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import configure_cors


PRODUCTION_FRONTEND_ORIGIN = "https://frontend-eta-woad-hr85ur9o01.vercel.app"


def test_production_connection_post_preflight_allows_auth_headers() -> None:
    app = FastAPI()
    # A trailing slash in Render's setting is harmless; browser Origin values
    # never include one.
    configure_cors(app, f"{PRODUCTION_FRONTEND_ORIGIN}/")

    response = TestClient(app).options(
        "/api/connections/test",
        headers={
            "Origin": PRODUCTION_FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == PRODUCTION_FRONTEND_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_localhost_origin_remains_allowed() -> None:
    app = FastAPI()
    configure_cors(app, PRODUCTION_FRONTEND_ORIGIN)

    response = TestClient(app).options(
        "/api/connections/test",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
