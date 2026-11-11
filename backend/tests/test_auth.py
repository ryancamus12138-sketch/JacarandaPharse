"""登录、角色权限、用户管理、审计日志。

这些测试在团队模式（JACARA_MODE=team）下运行；其他测试文件仍然是个人模式，不用登录。
"""

import uuid
from datetime import UTC, datetime, timedelta

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
    """切到团队模式。get_settings 有缓存，改了环境变量要清掉缓存，测完再清一次恢复原样。"""
    monkeypatch.setenv("JACARA_MODE", "team")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_user(factory):
    """在默认团队里建一个用户，返回用户名。默认是没有开通任何权限的使用者。"""

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
    """建用户、登录，返回带好 token 的请求头。"""

    def _as(username: str, role: str = "member", allow: tuple[str, ...] = ()) -> dict[str, str]:
        make_user(username, role, allow)
        token = login(client, username).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    return _as


def actions(factory) -> list[str]:
    with session_scope(factory) as session:
        return list(session.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))


# ---------- 登录 ----------


def test_without_token_is_401(client):
    response = client.get("/api/v1/documents")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_and_use_token(client, make_user):
    make_user("Alice", role="admin")
    response = login(client, "ALICE")  # 用户名不区分大小写
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "alice"
    assert "password" not in str(body)  # 返回里没有密码，也没有密码哈希

    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/v1/documents", headers=headers).status_code == 200
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert (me["mode"], me["role"], me["user"]["username"]) == ("team", "admin", "alice")


@pytest.mark.parametrize(("username", "password"), [("alice", "wrong-pass"), ("nobody", PASSWORD)])
def test_bad_login_same_message(client, factory, make_user, username, password):
    make_user("alice")
    response = login(client, username, password)
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码不对"  # 不说是哪一个错了
    assert actions(factory)[-1] == "auth:login_failed"  # 失败也留了记录


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


# ---------- 角色和权限 ----------


def upload(client, headers):
    return client.post(
        "/api/v1/documents", headers=headers, files={"file": ("a.jpg", b"x", "image/jpeg")}
    )


def test_member_can_read_and_export_by_default(client, as_user):
    headers = as_user("wang")
    assert client.get("/api/v1/documents", headers=headers).status_code == 200
    response = upload(client, headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "没有权限：上传文件（请找管理员开通）"


def test_member_with_permission_can_upload(client, as_user):
    headers = as_user("li", allow=("document:create",))
    assert upload(client, headers).status_code == 201
    response = client.get("/api/v1/users", headers=headers)
    assert response.status_code == 403
    assert "只有管理员能做" in response.json()["detail"]


def test_me_lists_what_i_can_do(client, as_user):
    headers = as_user("li", allow=("value:review",))
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["role"] == "member"
    assert "value:review" in me["permissions"]  # 开通的
    assert "document:export" in me["permissions"]  # 每人都有的
    assert "document:create" not in me["permissions"]  # 没开通的


def test_other_team_cannot_see_documents(client, factory, report, tmp_path, as_user):
    headers = as_user("alice")
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        other_team = Team(id=uuid.uuid4(), name="别的团队")
        session.add(other_team)
        session.flush()
        document = save_document(session, path, [report])
        document.team_id = other_team.id  # 这个文档属于别的团队
        document_id = document.id

    assert client.get(f"/api/v1/documents/{document_id}", headers=headers).status_code == 404
    assert client.get("/api/v1/documents", headers=headers).json()["total"] == 0


# ---------- 用户管理 ----------


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
    assert response.json()["permissions"] == ["document:create"]  # 重复的去掉
    assert login(client, "bob", "bob-password").status_code == 200

    again = client.post("/api/v1/users", headers=headers, json=body)
    assert again.status_code == 409  # 用户名重复


def test_unknown_permission_is_rejected(client, as_user):
    headers = as_user("boss", role="admin")
    body = {"username": "bob", "password": "bob-password", "permissions": ["user:manage"]}
    assert client.post("/api/v1/users", headers=headers, json=body).status_code == 422


def test_list_grantable_permissions(client, as_user):
    headers = as_user("boss", role="admin")
    permissions = client.get("/api/v1/users/permissions", headers=headers).json()
    assert {"name": "document:create", "label": "上传文件"} in permissions
    assert len(permissions) == 4


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
    assert client.get("/api/v1/documents", headers=bob).status_code == 401  # 已登录的马上失效
    assert login(client, "bob").status_code == 401  # 也不能再登录


def test_granting_takes_effect_now(client, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    bob_id = client.get("/api/v1/auth/me", headers=bob).json()["user"]["id"]
    assert upload(client, bob).status_code == 403

    client.patch(
        f"/api/v1/users/{bob_id}", headers=admin, json={"permissions": ["document:create"]}
    )
    assert upload(client, bob).status_code == 201  # 不用重新登录，开通了马上能用

    client.patch(f"/api/v1/users/{bob_id}", headers=admin, json={"permissions": []})
    assert upload(client, bob).status_code == 403  # 取消了也马上生效


# ---------- 改密码、忘记密码 ----------


def change_password(client, headers, old: str, new: str):
    body = {"old_password": old, "new_password": new}
    return client.post("/api/v1/auth/password", headers=headers, json=body)


def test_change_own_password(client, factory, make_user):
    make_user("bob")
    phone = {"Authorization": f"Bearer {login(client, 'bob').json()['token']}"}
    laptop = {"Authorization": f"Bearer {login(client, 'bob').json()['token']}"}

    wrong = change_password(client, laptop, "not-my-password", "new-password")
    assert (wrong.status_code, wrong.json()["detail"]) == (422, "原密码不对")
    same = change_password(client, laptop, PASSWORD, PASSWORD)
    assert (same.status_code, same.json()["detail"]) == (422, "新密码不能和原来的一样")
    assert change_password(client, laptop, PASSWORD, "short").status_code == 422

    assert change_password(client, laptop, PASSWORD, "new-password").status_code == 204
    assert client.get("/api/v1/documents", headers=laptop).status_code == 200  # 正在用的不退出
    assert client.get("/api/v1/documents", headers=phone).status_code == 401  # 别的设备退出了
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
    assert client.post(url, headers=bob).status_code == 403  # 只有管理员能生成
    issued = client.post(url, headers=admin).json()
    code = issued["code"]
    assert len(code) == 9 and code[4] == "-"  # 比如 K7QM-3XWD
    assert issued["username"] == "bob"

    assert reset(client, "bob", "AAAA-AAAA").status_code == 400  # 码不对
    assert reset(client, "nobody", code).status_code == 400  # 用户名不对
    assert reset(client, "bob", code, PASSWORD).status_code == 422  # 和原来的一样（码还能用）
    # 不分大小写，横线和空格都可以不打
    assert reset(client, "bob", code.lower().replace("-", " ")).status_code == 204
    assert reset(client, "bob", code, "other-password").status_code == 400  # 用过一次就作废

    assert client.get("/api/v1/documents", headers=bob).status_code == 401  # 已登录的退出了
    assert login(client, "bob", "new-password").status_code == 200

    log = actions(factory)
    assert "user:reset_code" in log
    assert "auth:password_reset" in log
    assert "auth:reset_failed" in log
    with session_scope(factory) as session:
        details = str(list(session.scalars(select(AuditLog.detail))))
        assert code not in details  # 审计日志里没有重设码
        assert "new-password" not in details  # 也没有密码


def test_new_reset_code_voids_old_one(client, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    url = f"/api/v1/users/{client.get('/api/v1/auth/me', headers=bob).json()['user']['id']}"
    first = client.post(f"{url}/reset-code", headers=admin).json()["code"]
    second = client.post(f"{url}/reset-code", headers=admin).json()["code"]
    assert reset(client, "bob", first).status_code == 400  # 旧的作废了
    assert reset(client, "bob", second).status_code == 204


def test_reset_code_expires(client, factory, as_user):
    admin = as_user("boss", role="admin")
    bob = as_user("bob")
    bob_id = client.get("/api/v1/auth/me", headers=bob).json()["user"]["id"]
    code = client.post(f"/api/v1/users/{bob_id}/reset-code", headers=admin).json()["code"]
    with session_scope(factory) as session:
        row = session.scalars(select(PasswordResetCode)).one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # 假装过了 15 分钟
    assert reset(client, "bob", code).status_code == 400


# ---------- 审计日志 ----------


def test_audit_log_records_who_did_what(client, factory, as_user):
    headers = as_user("rita", allow=("document:create",))
    upload(client, headers)

    with session_scope(factory) as session:
        log = session.scalars(select(AuditLog).where(AuditLog.action == "document:upload")).one()
        rita = session.scalars(select(User).where(User.username == "rita")).one()
        assert log.user_id == rita.id
        assert log.detail == {"filename": "a.jpg"}


def test_only_admin_reads_audit_log(client, as_user):
    admin = as_user("boss", role="admin")
    member = as_user("vic")
    assert client.get("/api/v1/audit-logs", headers=member).status_code == 403

    page = client.get("/api/v1/audit-logs", headers=admin, params={"action": "auth:"}).json()
    assert {item["action"] for item in page["items"]} == {"auth:login"}
    assert page["total"] == 2  # boss 和 vic 各登录一次


# ---------- 个人模式 ----------


def test_personal_mode_needs_no_login(client, monkeypatch):
    monkeypatch.setenv("JACARA_MODE", "personal")
    get_settings.cache_clear()
    assert client.get("/api/v1/documents").status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert (me["mode"], me["role"], me["user"]) == ("personal", "admin", None)
    assert "user:manage" in me["permissions"]  # 个人模式什么都能做


# ---------- 密码工具 ----------


def test_password_hash():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second  # 每次的盐不一样
    assert verify_password(first, PASSWORD)
    assert not verify_password(first, "wrong")
    assert not verify_password(None, PASSWORD)  # 用户不存在
