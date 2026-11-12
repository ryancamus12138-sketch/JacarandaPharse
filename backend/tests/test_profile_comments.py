"""v3 第 1 批：个人资料（昵称、头像）、按字段内容找文档、先跳过、备注。"""

import io

import pytest
from PIL import Image
from sqlalchemy import select

from jacara.core.config import get_settings
from jacara.core.db import session_scope
from jacara.models import AuditLog, Comment, Document
from jacara.modules.auth.repository import AuthRepository
from jacara.modules.auth.schemas import UserCreate
from jacara.modules.auth.service import add_user

from .test_workbench import docs, names  # noqa: F401  docs 是 fixture

PASSWORD = "correct-horse-1"


def png(width: int, height: int, color: str = "red", fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format=fmt)
    return out.getvalue()


# ---- 个人资料：个人模式 ----


def test_personal_profile_name_and_avatar(client, storage):
    profile = client.get("/api/v1/auth/profile").json()
    assert profile["mode"] == "personal"
    assert profile["shown_name"] == "本机用户"
    assert profile["avatar_at"] is None
    assert client.get("/api/v1/auth/profile/avatar").status_code == 404

    changed = client.patch("/api/v1/auth/profile", json={"display_name": "  小王 "}).json()
    assert changed["display_name"] == "小王"
    assert client.get("/api/v1/auth/me").json()["shown_name"] == "小王"

    # 长方形照片 → 裁成 256 × 256 的 PNG
    response = client.put(
        "/api/v1/auth/profile/avatar",
        files={"file": ("me.jpg", png(800, 400, fmt="JPEG"), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["avatar_at"] is not None
    picture = client.get("/api/v1/auth/profile/avatar")
    assert picture.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(picture.content)) as image:
        assert image.size == (256, 256)
    assert client.get("/api/v1/auth/me").json()["avatar_at"] is not None

    removed = client.delete("/api/v1/auth/profile/avatar").json()
    assert removed["avatar_at"] is None
    assert client.get("/api/v1/auth/profile/avatar").status_code == 404


def test_avatar_rejects_bad_files(client):
    bad = client.put(
        "/api/v1/auth/profile/avatar", files={"file": ("x.png", b"not an image", "image/png")}
    )
    assert bad.status_code == 422
    assert "打不开" in bad.json()["detail"]
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    wrong = client.put(
        "/api/v1/auth/profile/avatar", files={"file": ("x.gif", gif.getvalue(), "image/gif")}
    )
    assert wrong.status_code == 422
    big = client.put(
        "/api/v1/auth/profile/avatar",
        files={"file": ("x.png", b"0" * (5 * 1024 * 1024 + 1), "image/png")},
    )
    assert "5 MB" in big.json()["detail"]


# ---- 个人资料：团队模式 ----


@pytest.fixture
def team(monkeypatch, factory, client):
    monkeypatch.setenv("JACARA_MODE", "team")
    get_settings.cache_clear()

    def login(username: str, role: str = "member") -> dict[str, str]:
        with session_scope(factory) as session:
            team_row = AuthRepository(session).default_team()
            data = UserCreate.model_validate(
                {"username": username, "password": PASSWORD, "role": role}
            )
            add_user(session, team_row.id, data)
        token = client.post(
            "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
        ).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    yield login
    get_settings.cache_clear()


def test_team_profile_uses_account(client, team, factory):
    me = team("xiaowang")
    profile = client.get("/api/v1/auth/profile", headers=me).json()
    assert profile["mode"] == "team"
    assert profile["username"] == "xiaowang"
    assert profile["shown_name"] == "xiaowang"  # 没有昵称就显示用户名
    assert profile["role"] == "member"
    assert profile["permissions"] == []  # 还没开通任何权限
    assert profile["member_since"] is not None

    client.patch("/api/v1/auth/profile", json={"display_name": "王小明"}, headers=me)
    assert client.get("/api/v1/auth/me", headers=me).json()["user"]["display_name"] == "王小明"
    assert client.get("/api/v1/auth/me", headers=me).json()["shown_name"] == "王小明"

    # 两个人的头像分开存
    other = team("xiaoli")
    client.put(
        "/api/v1/auth/profile/avatar",
        files={"file": ("a.png", png(300, 300), "image/png")},
        headers=me,
    )
    assert client.get("/api/v1/auth/profile/avatar", headers=me).status_code == 200
    assert client.get("/api/v1/auth/profile/avatar", headers=other).status_code == 404

    with session_scope(factory) as session:
        actions = set(session.scalars(select(AuditLog.action)))
    assert {"profile:update", "profile:avatar"} <= actions


def test_profile_needs_login(client, team):
    assert client.get("/api/v1/auth/profile").status_code == 401


# ---- 按字段内容找文档 ----


def test_search_field_values(client, docs, factory):  # noqa: F811
    # report 里：商家 BOOK TA _K (TAMAN DAYA) SDN BHD，总额 9.0，找零 100.0
    assert names(client, "value_q=taman") == ["a", "b", "c", "d", "e"]
    assert names(client, "value_q=taman&field=merchant") == ["a", "b", "c", "d", "e"]
    assert names(client, "value_q=taman&field=total") == []
    assert names(client, "value_q=costco") == []  # 文件名里有，字段里没有
    assert names(client, "value_q=_K") == ["a", "b", "c", "d", "e"]  # _ 是普通字符
    assert names(client, "value_q=%25") == []

    # 人改过的值也能找到
    detail = client.get(f"/api/v1/documents/{docs['a']}").json()
    merchant = next(
        v for e in detail["extractions"] for v in e["values"] if v["name"] == "merchant"
    )
    response = client.post(
        f"/api/v1/values/{merchant['id']}/revisions",
        json={"action": "edit", "value": "海底捞火锅", "reason": "OCR 错了"},
    )
    assert response.status_code in (200, 201), response.text
    assert names(client, "value_q=海底捞") == ["a"]


# ---- 先跳过 ----


def test_skip_and_unskip(client, docs, factory):  # noqa: F811
    a, b, d = docs["a"], docs["b"], docs["d"]
    assert client.get(f"/api/v1/documents/{a}/neighbors").json()["next"] == str(b)

    skipped = client.post(f"/api/v1/documents/{b}/skip", json={"reason": "看不清，问财务"}).json()
    assert skipped["skipped_at"] is not None
    assert skipped["skip_reason"] == "看不清，问财务"
    # 跳过的不算进"下一份"
    assert client.get(f"/api/v1/documents/{a}/neighbors").json()["next"] == str(d)
    assert names(client, "skipped=true") == ["b"]
    assert names(client, "skipped=false") == ["a", "c", "d", "e"]
    dashboard = client.get("/api/v1/dashboard").json()
    assert dashboard["counts"]["skipped"] == 1
    assert str(b) not in [x["id"] for x in dashboard["to_review"]]  # 先跳过的不在"需要先处理"里

    back = client.post(f"/api/v1/documents/{b}/unskip").json()
    assert back["skipped_at"] is None
    assert client.get(f"/api/v1/documents/{a}/neighbors").json()["next"] == str(b)

    # 只有待审核的能跳过
    assert client.post(f"/api/v1/documents/{docs['c']}/skip", json={}).status_code == 409
    assert client.post(f"/api/v1/documents/{docs['e']}/skip", json={}).status_code == 409


def test_review_clears_skip(client, docs, factory):  # noqa: F811
    b = docs["b"]
    client.post(f"/api/v1/documents/{b}/skip", json={})
    with session_scope(factory) as session:
        document = session.get_one(Document, b)
        for extraction in document.extractions:
            for value in extraction.values:
                value.needs_review = False
    assert client.post(f"/api/v1/documents/{b}/review").status_code == 200
    assert client.get("/api/v1/documents?skipped=true").json()["items"] == []


# ---- 备注 ----


def test_comments(client, docs, factory):  # noqa: F811
    a = docs["a"]
    url = f"/api/v1/documents/{a}/comments"
    assert client.get(url).json() == []

    whole = client.post(url, json={"body": "这张要问财务"})
    assert whole.status_code == 201
    on_field = client.post(
        url, json={"body": "总额好像不对", "field_name": "total", "page": 1}
    ).json()
    assert on_field["field_name"] == "total"
    assert on_field["page"] == 1
    no_page = client.post(url, json={"body": "x", "field_name": "total"})
    assert no_page.status_code == 422
    past_end = client.post(url, json={"body": "x", "field_name": "total", "page": 9})
    assert "只有 1 页" in past_end.json()["detail"]
    assert on_field["author"] == "本机用户"
    assert on_field["mine"] is True
    assert client.post(url, json={"body": "   "}).status_code == 422

    listed = client.get(url).json()
    # 先写的在前（测试用的 SQLite 时间只到秒，两条可能同一秒，这里只比内容）
    assert sorted(c["body"] for c in listed) == sorted(["这张要问财务", "总额好像不对"])
    item = next(d for d in client.get("/api/v1/documents").json()["items"] if d["id"] == str(a))
    assert item["open_comments"] == 2

    resolved = client.post(f"/api/v1/comments/{on_field['id']}/resolve").json()
    assert resolved["resolved_at"] is not None
    assert resolved["resolved_by"] == "本机用户"
    item = next(d for d in client.get("/api/v1/documents").json()["items"] if d["id"] == str(a))
    assert item["open_comments"] == 1
    reopened = client.post(f"/api/v1/comments/{on_field['id']}/reopen").json()
    assert reopened["resolved_at"] is None

    assert client.delete(f"/api/v1/comments/{on_field['id']}").status_code == 204
    assert len(client.get(url).json()) == 1

    # 审计日志只记谁在哪份文档上写了，不记内容
    with session_scope(factory) as session:
        logs = list(session.scalars(select(AuditLog).where(AuditLog.action.like("comment:%"))))
        assert {log.action for log in logs} == {
            "comment:create",
            "comment:resolve",
            "comment:reopen",
            "comment:delete",
        }
        assert all("财务" not in str(log.detail) and "总额" not in str(log.detail) for log in logs)
        assert session.scalar(select(Comment).where(Comment.body == "总额好像不对")) is None


def test_comment_delete_only_author_or_admin(client, docs, team):  # noqa: F811
    a = docs["a"]
    wang, li, boss = team("xiaowang"), team("xiaoli"), team("boss", role="admin")
    comment = client.post(
        f"/api/v1/documents/{a}/comments", json={"body": "我写的"}, headers=wang
    ).json()
    seen = client.get(f"/api/v1/documents/{a}/comments", headers=li).json()
    assert seen[0]["author"] == "xiaowang"
    assert seen[0]["mine"] is False
    assert client.delete(f"/api/v1/comments/{comment['id']}", headers=li).status_code == 403
    assert client.delete(f"/api/v1/comments/{comment['id']}", headers=boss).status_code == 204


def test_comments_unknown_document(client):
    url = "/api/v1/documents/00000000-0000-0000-0000-00000000abcd/comments"
    assert client.get(url).status_code == 404
