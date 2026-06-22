from typing import cast
from uuid import uuid4

from httpx import AsyncClient

SESSION_COOKIE_NAME = "visitor_session"
TEST_PASSWORD = "correct-horse-battery-staple"


def unique_email() -> str:
    return f"user-{uuid4().hex}@example.com"


async def register_user(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": TEST_PASSWORD},
    )

    assert response.status_code == 201
    return cast(dict[str, str], response.json())


async def test_create_anonymous_session_sets_secure_cookie_attributes(
    client: AsyncClient,
) -> None:
    response = await client.post("/v1/sessions/anonymous")

    assert response.status_code == 201
    assert response.json()["visitor_session_id"]
    assert client.cookies.get(SESSION_COOKIE_NAME)

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie


async def test_read_current_session(client: AsyncClient) -> None:
    create_response = await client.post("/v1/sessions/anonymous")
    visitor_session_id = create_response.json()["visitor_session_id"]

    response = await client.get("/v1/me/session")

    assert response.status_code == 200
    assert response.json()["id"] == visitor_session_id
    assert response.json()["user_id"] is None


async def test_read_current_session_without_cookie_returns_401(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/me/session")

    assert response.status_code == 401


async def test_register_user_returns_only_public_data(client: AsyncClient) -> None:
    email = unique_email()

    response = await client.post(
        "/v1/auth/register",
        json={"email": email.upper(), "password": TEST_PASSWORD},
    )

    assert response.status_code == 201
    assert response.json()["email"] == email
    assert "password" not in response.json()
    assert "password_hash" not in response.json()


async def test_login_links_existing_anonymous_session(client: AsyncClient) -> None:
    session_response = await client.post("/v1/sessions/anonymous")
    visitor_session_id = session_response.json()["visitor_session_id"]
    email = unique_email()
    user = await register_user(client, email)

    login_response = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    current_session_response = await client.get("/v1/me/session")

    assert login_response.status_code == 200
    assert login_response.json()["id"] == user["id"]
    assert current_session_response.status_code == 200
    assert current_session_response.json()["id"] == visitor_session_id
    assert current_session_response.json()["user_id"] == user["id"]


async def test_logout_invalidates_session_and_deletes_cookie(
    client: AsyncClient,
) -> None:
    await client.post("/v1/sessions/anonymous")
    raw_session_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert raw_session_token is not None

    email = unique_email()
    await register_user(client, email)
    await client.post(
        "/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )

    logout_response = await client.post("/v1/auth/logout")

    assert logout_response.status_code == 204
    assert client.cookies.get(SESSION_COOKIE_NAME) is None

    client.cookies.set(SESSION_COOKIE_NAME, raw_session_token)
    session_response = await client.get("/v1/me/session")
    assert session_response.status_code == 401
