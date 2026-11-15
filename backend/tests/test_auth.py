"""Login, role permissions, user management, audit log.

These tests run in team mode (JACARA_MODE=team); other test modules remain in personal mode
without login."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from jacara.core.config import get_settings
from jacara.core.db import session_scope
from jacara.core.security import hash_password, token_hash, verify_password
from jacara.models import AuditLog, AuthSession, PasswordResetCode, Team, User
from jacara.modules.auth.repository import AuthRepository
from jacara.modules.auth.schemas import UserCreate
from jacara.modules.auth.service import add_user
from jacara.pipeline.store import save_document

PASSWORD = "correct-horse-1"


@pytest.fixture(autouse=True)
def team_mode(monkeypatch):
    """Switch to team mode. get_settings is cached, so the cache is cleared after changing
    environment variables, and again afterwards to restore."""
    monkeypatch.setenv("JACARA_MODE", "team")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_user(factory):
    """Create a user in the default team and return the user name. Defaults to a member without
    granted permissions."""

    def _make(username: str, role: str = "member", allow: tuple[str, ...] = ()) -> str:
        with session_scope(factory) as session:
            team = AuthRepository(session).default_team()
            data = UserCreate.model_validate(
                {"username": username, "password": PASSWORD, "role": role, "permissions": allow}
            )
            add_user(session, team.id, data)
        return username

    return _make


def login(client, username: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


@pytest.fixture
def as_user(client, make_user):
    """Create a user, log in and return headers carrying the token."""

    def _as(username: str, role: str = "member", allow: tuple[str, ...] = ()) -> dict[str, str]:
        make_user(username, role, allow)
        token = login(client, username).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    return _as


def actions(factory) -> list[str]:
    with session_scope(factory) as session:
        return list(session.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))


# ---------- Login ----------


def test_without_token_is_401(client):
    response = client.get("/api/v1/documents")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_and_use_token(client, make_user):
    make_user("Alice", role="admin")
    response = login(client, "ALICE")  # User names are case-insensitive.
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "alice"
    assert "password" not in str(body)  # The response contains neither the password nor its hash.

    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/v1/documents", headers=headers).status_code == 200
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert (me["mode"], me["role"], me["user"]["username"]) == ("team", "admin", "alice")


@pytest.mark.parametrize(("username", "password"), [("alice", "wrong-pass"), ("nobody", PASSWORD)])
def test_bad_login_same_message(client, factory, make_user, username, password):
    make_user("alice")
    response = login(client, username, password)
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"  # Does not reveal which one was wrong.
    assert actions(factory)[-1] == "auth:login_failed"  # Failures are recorded too.


def test_database_has_no_password_or_token(client, factory, make_user):
    make_user("alice")
    token = login(client, "alice").json()["token"]
    with session_scope(factory) as session:
        user = session.scalars(select(User)).one()
        auth = session.scalars(select(AuthSession)).one()
        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("$argon2id$")
        assert auth.token_hash == token_hash(token) != token


def test_logout(client, as_user):
    headers = as_user("alice")
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    response = client.get("/api/v1/documents", headers=headers)
    assert response.status_code == 401
    assert "重新登录" in response.json()["detail"]


def test_expired_token(client, factory, as_user):
    headers = as_user("alice")
    with session_scope(factory) as session:
        session.scalars(select(AuthSession)).one().expires_at = datetime.now(UTC) - timedelta(
            minutes=1
        )
    assert client.get("/api/v1/documents", headers=headers).status_code == 401


# ---------- Roles and permissions ----------


def upload(client, headers):
    return client.post(
        "/api/v1/documents", headers=headers, files={"file": ("a.jpg", b"x", "image/jpeg")}
    )


def test_member_can_read_and_export_by_default(client, as_user):
    headers = as_user("wang")
    assert client.get("/api/v1/documents", headers=headers).status_code == 200
    response = upload(client, headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "没有权限：上传文件（请联系管理员开通）"


def test_member_with_permission_can_upload(client, as_user):
    headers = as_user("li", allow=("document:create",))
    assert upload(client, headers).status_code == 201
    response = client.get("/api/v1/users", headers=headers)
    assert response.status_code == 403
    assert "仅管理员可执行" in response.json()["detail"]


def test_me_lists_what_i_can_do(client, as_user):
    headers = as_user("li", allow=("value:review",))
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["role"] == "member"
    assert "value:review" in me["permissions"]  # Granted.
    assert "document:export" in me["permissions"]  # Granted to every member.
    assert "document:create" not in me["permissions"]  # Not granted.


def test_other_team_cannot_see_documents(client, factory, report, tmp_path, as_user):
    headers = as_user("alice")
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        other_team = Team(id=uuid.uuid4(), name="别的团队")
        session.add(other_team)
        session.flush()
        document = save_document(session, path, [report])
        document.team_id = other_team.id  # This document belongs to another team.
        document_id = document.id

    assert client.get(f"/api/v1/documents/{document_id}", headers=headers).status_code == 404
    assert client.get("/api/v1/documents", headers=headers).json()["total"] == 0


# ---------- User management ----------


def test_admin_creates_user(client, as_user):
    headers = as_user("boss", role="admin")
    body = {
        "username": "Bob",
        "password": "bob-password",
        "display_name": "鲍勃",
        "permissions": ["document:create", "document:create"],
    }
    response = client.post("/api/v1/users", headers=headers, json=body)
    assert response.status_code == 201
    assert response.json()["username"] == "bob"
    assert response.json()["permissions"] == ["document:create"]  # Duplicates removed.
    assert login(client, "bob", "bob-password").status_code == 200

    again = client.post("/api/v1/users", headers=headers, json=body)
    assert again.status_code == 409  # Duplicate user name.


def test_every_grantable_permission_can_be_granted():
    """Permissions accepted by the API equal those listed on the admin page (GRANTABLE); adding a
    permission requires changing both."""
    from typing import get_args

    from jacara.core.permissions import GRANTABLE
    from jacara.modules.auth.schemas import Permission

    assert set(get_args(Permission)) == set(GRANTABLE)


def test_unknown_permission_is_rejected(client, as_user):
    headers = as_user("boss", role="admin")
    body = {"username": "bob", "password": "bob-password", "permissions": ["user:manage"]}
    assert client.post("/api/v1/users", headers=headers, json=body).status_code == 422


def test_list_grantable_permissions(client, as_user):
    headers = as_user("boss", role="admin")
    permissions = client.get("/api/v1/users/permissions", headers=headers).json()
    assert {"name": "document:create", "label": "上传文件"} in permissions
    assert {"name": "template:manage", "label": "管理模板"} in permissions
    assert len(permissions) == 6


def test_password_too_short(client, as_user):
    headers = as_user("boss", role="admin")
    body = {"username": "bob", "password": "short"}
    assert client.post("/api/v1/users", headers=headers, json=body).status_code == 422


def test_admin_cannot_demote_self(client, as_user):
    headers = as_user("boss", role="admin")
    me = client.get("/api/v1/auth/me", headers=headers).json()["user"]
    response = client.patch(f"/api/v1/users/{me['id']}", headers=headers, json={"role": "member"})
    assert response.status_code == 409


def test_disabled_user_is_logged_out(client, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    bob_id = client.get("/api/v1/auth/me", headers=bob).json()["user"]["id"]

    response = client.patch(f"/api/v1/users/{bob_id}", headers=admin, json={"is_active": False})
    assert response.json()["is_active"] is False
    # Existing sessions are revoked immediately.
    assert client.get("/api/v1/documents", headers=bob).status_code == 401
    assert login(client, "bob").status_code == 401  # And login is refused.


def test_granting_takes_effect_now(client, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    bob_id = client.get("/api/v1/auth/me", headers=bob).json()["user"]["id"]
    assert upload(client, bob).status_code == 403

    client.patch(
        f"/api/v1/users/{bob_id}", headers=admin, json={"permissions": ["document:create"]}
    )
    # Granted permissions take effect without logging in again.
    assert upload(client, bob).status_code == 201

    client.patch(f"/api/v1/users/{bob_id}", headers=admin, json={"permissions": []})
    assert upload(client, bob).status_code == 403  # Revocations take effect immediately too.


# ---------- Password change and reset ----------


def change_password(client, headers, old: str, new: str):
    body = {"old_password": old, "new_password": new}
    return client.post("/api/v1/auth/password", headers=headers, json=body)


def test_change_own_password(client, factory, make_user):
    make_user("bob")
    phone = {"Authorization": f"Bearer {login(client, 'bob').json()['token']}"}
    laptop = {"Authorization": f"Bearer {login(client, 'bob').json()['token']}"}

    wrong = change_password(client, laptop, "not-my-password", "new-password")
    assert (wrong.status_code, wrong.json()["detail"]) == (422, "原密码错误")
    same = change_password(client, laptop, PASSWORD, PASSWORD)
    assert (same.status_code, same.json()["detail"]) == (422, "新密码不能与原密码相同")
    assert change_password(client, laptop, PASSWORD, "short").status_code == 422

    assert change_password(client, laptop, PASSWORD, "new-password").status_code == 204
    # The current session stays.
    assert client.get("/api/v1/documents", headers=laptop).status_code == 200
    # Other devices are logged out.
    assert client.get("/api/v1/documents", headers=phone).status_code == 401
    assert login(client, "bob").status_code == 401
    assert login(client, "bob", "new-password").status_code == 200
    assert "auth:password_change" in actions(factory)


NEW_PASSWORD = "new-password"


def reset(client, username: str, code: str, password: str = NEW_PASSWORD):
    body = {"username": username, "code": code, "password": password}
    return client.post("/api/v1/auth/reset-password", json=body)


def test_forgot_password_with_reset_code(client, factory, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    bob_id = client.get("/api/v1/auth/me", headers=bob).json()["user"]["id"]

    url = f"/api/v1/users/{bob_id}/reset-code"
    # Only administrators can generate codes.
    assert client.post(url, headers=bob).status_code == 403
    issued = client.post(url, headers=admin).json()
    code = issued["code"]
    assert len(code) == 9 and code[4] == "-"  # For example K7QM-3XWD.
    assert issued["username"] == "bob"

    assert reset(client, "bob", "AAAA-AAAA").status_code == 400  # Wrong code.
    assert reset(client, "nobody", code).status_code == 400  # Wrong user name.
    # Same as before (the code remains valid).
    assert reset(client, "bob", code, PASSWORD).status_code == 422
    # Case-insensitive; hyphens and spaces are optional.
    assert reset(client, "bob", code.lower().replace("-", " ")).status_code == 204
    assert reset(client, "bob", code, "other-password").status_code == 400  # Invalid after one use.

    # Existing sessions are logged out.
    assert client.get("/api/v1/documents", headers=bob).status_code == 401
    assert login(client, "bob", "new-password").status_code == 200

    log = actions(factory)
    assert "user:reset_code" in log
    assert "auth:password_reset" in log
    assert "auth:reset_failed" in log
    with session_scope(factory) as session:
        details = str(list(session.scalars(select(AuditLog.detail))))
        assert code not in details  # The audit log does not contain the reset code.
        assert "new-password" not in details  # Nor the password.


def test_new_reset_code_voids_old_one(client, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    url = f"/api/v1/users/{client.get('/api/v1/auth/me', headers=bob).json()['user']['id']}"
    first = client.post(f"{url}/reset-code", headers=admin).json()["code"]
    second = client.post(f"{url}/reset-code", headers=admin).json()["code"]
    assert reset(client, "bob", first).status_code == 400  # The old code is revoked.
    assert reset(client, "bob", second).status_code == 204


def test_reset_code_expires(client, factory, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    bob_id = client.get("/api/v1/auth/me", headers=bob).json()["user"]["id"]
    code = client.post(f"/api/v1/users/{bob_id}/reset-code", headers=admin).json()["code"]
    with session_scope(factory) as session:
        row = session.scalars(select(PasswordResetCode)).one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # Simulate 15 minutes passing.
    assert reset(client, "bob", code).status_code == 400


# ---------- Audit log ----------


def test_audit_log_records_who_did_what(client, factory, as_user):
    headers = as_user("rita", allow=("document:create",))
    upload(client, headers)

    with session_scope(factory) as session:
        log = session.scalars(select(AuditLog).where(AuditLog.action == "document:upload")).one()
        rita = session.scalars(select(User).where(User.username == "rita")).one()
        assert log.user_id == rita.id
        assert log.detail == {"filename": "a.jpg", "template": "receipt"}


def test_only_admin_reads_audit_log(client, as_user):
    admin = as_user("boss", role="admin")
    member = as_user("vic")
    assert client.get("/api/v1/audit-logs", headers=member).status_code == 403

    page = client.get("/api/v1/audit-logs", headers=admin, params={"action": "auth:"}).json()
    assert {item["action"] for item in page["items"]} == {"auth:login"}
    assert page["total"] == 2  # boss and vic log in once each.


def test_audit_log_by_person_and_date(client, factory, as_user):
    admin = as_user("boss", role="admin")
    as_user("vic")
    vic_id = next(
        u["id"] for u in client.get("/api/v1/users", headers=admin).json() if u["username"] == "vic"
    )
    page = client.get("/api/v1/audit-logs", headers=admin, params={"user_id": vic_id}).json()
    assert page["total"] == 1 and page["items"][0]["action"] == "auth:login"
    with session_scope(factory) as session:  # Move vic's login to last month.
        log = session.scalars(select(AuditLog).where(AuditLog.user_id == uuid.UUID(vic_id))).one()
        log.created_at = datetime.now(UTC) - timedelta(days=40)
    today = date.today().isoformat()
    recent = client.get("/api/v1/audit-logs", headers=admin, params={"date_from": today}).json()
    assert vic_id not in {i["user_id"] for i in recent["items"]}
    old = client.get(
        "/api/v1/audit-logs",
        headers=admin,
        params={"date_to": (date.today() - timedelta(days=30)).isoformat()},
    ).json()
    assert [i["user_id"] for i in old["items"]] == [vic_id]


def test_audit_log_export_is_recorded(client, factory, as_user):
    admin = as_user("boss", role="admin")
    member = as_user("vic")
    assert client.get("/api/v1/audit-logs/export", headers=member).status_code == 403
    exported = client.get("/api/v1/audit-logs/export", headers=admin, params={"action": "auth:"})
    assert exported.status_code == 200
    assert exported.json()["total"] == 2
    with session_scope(factory) as session:
        log = session.scalars(select(AuditLog).where(AuditLog.action == "audit:export")).one()
        assert log.detail["count"] == 2 and log.detail["filter_action"] == "auth:"


# ---------- Personal mode ----------


def test_personal_mode_needs_no_login(client, monkeypatch):
    monkeypatch.setenv("JACARA_MODE", "personal")
    get_settings.cache_clear()
    assert client.get("/api/v1/documents").status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert (me["mode"], me["role"], me["user"]) == ("personal", "admin", None)
    assert "user:manage" in me["permissions"]  # Personal mode permits everything.


# ---------- Password utilities ----------


def test_password_hash():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second  # A different salt each time.
    assert verify_password(first, PASSWORD)
    assert not verify_password(first, "wrong")
    assert not verify_password(None, PASSWORD)  # Unknown user.
