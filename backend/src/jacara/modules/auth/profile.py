"""个人资料：昵称和头像（右上角名字、左边菜单顶上的头像点进去的那一页）。

- 团队模式：昵称就是 users.display_name，本人改、管理员也能改；头像本人换。
- 个人模式：没有用户账号，昵称和头像记在 profiles 表里 PERSONAL_OWNER 那一行。
头像统一裁成正方形、缩到 256 × 256、存成 PNG（去掉照片里的拍摄地点等信息），放在对象存储里。
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.owner import Owner
from jacara.core.permissions import GRANTABLE, allowed
from jacara.core.storage import ObjectStorage
from jacara.models import Profile, Team, User
from jacara.modules.audit.service import record
from jacara.modules.auth.schemas import ProfileOut, ProfileUpdate
from jacara.modules.auth.service import AuthError

AVATAR_SIZE = 256
"""头像存成多大（像素，正方形）。"""
AVATAR_MAX_BYTES = 5 * 1024 * 1024
"""上传的头像图片最大 5 MB。"""
AVATAR_FORMATS = {"PNG", "JPEG", "WEBP"}
PERSONAL_NAME = "本机用户"


def avatar_key(owner: Owner) -> str:
    return f"avatars/{owner.user_id}.png"


def shrink_avatar(data: bytes) -> bytes:
    """任意大小的照片 → 256 × 256 的 PNG。不是图片、格式不支持就报错。"""
    if len(data) > AVATAR_MAX_BYTES:
        raise AuthError("invalid", "头像图片太大了，请选 5 MB 以内的")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in AVATAR_FORMATS:
                raise AuthError("invalid", "头像只支持 PNG、JPG、WEBP 图片")
            picture = ImageOps.exif_transpose(image)  # 手机竖着拍的照片转正
            picture = ImageOps.fit(picture.convert("RGBA"), (AVATAR_SIZE, AVATAR_SIZE))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as err:
        raise AuthError("invalid", "这个文件打不开，不是图片或者已经损坏") from err
    out = io.BytesIO()
    picture.save(out, format="PNG")  # 新存一张，不带原图的 EXIF
    return out.getvalue()


class ProfileService:
    def __init__(
        self, session: Session, owner: Owner, storage: ObjectStorage | None = None
    ) -> None:
        self.session = session
        self.owner = owner
        self.storage = storage
        self.personal = get_settings().jacara_mode == "personal"

    # ---- 读 ----

    def get(self) -> ProfileOut:
        profile = self.session.get(Profile, self.owner.user_id)
        user = None if self.personal else self._user()
        can = allowed(self.owner)
        if user is not None:
            team = self.session.get(Team, user.team_id)
            name = user.display_name
            username: str | None = user.username
            team_name = team.name if team else ""
            since: datetime | None = user.created_at
        else:
            name = (profile.display_name if profile else None) or ""
            username, team_name, since = None, "", None
        return ProfileOut(
            mode="personal" if self.personal else "team",
            username=username,
            display_name=name,
            shown_name=name or username or PERSONAL_NAME,
            role=self.owner.role,
            team=team_name,
            permissions=[label for action, label in GRANTABLE.items() if action in can],
            avatar_at=profile.avatar_at if profile else None,
            member_since=since,
        )

    def avatar(self) -> bytes:
        profile = self.session.get(Profile, self.owner.user_id)
        if profile is None or profile.avatar_at is None:
            raise AuthError("not_found", "还没有头像")
        return self._storage().get(avatar_key(self.owner))

    # ---- 改 ----

    def update(self, change: ProfileUpdate) -> ProfileOut:
        name = change.display_name.strip()
        if self.personal:
            self._profile().display_name = name or None
        else:
            user = self._user()
            if user is None:
                raise AuthError("not_found", "找不到你的账号")
            user.display_name = name
        record(self.session, self.owner, "profile:update", "user", self.owner.user_id)
        self.session.flush()
        return self.get()

    def set_avatar(self, data: bytes) -> ProfileOut:
        picture = shrink_avatar(data)
        self._storage().put(avatar_key(self.owner), picture, "image/png")
        self._profile().avatar_at = datetime.now(UTC)
        record(self.session, self.owner, "profile:avatar", "user", self.owner.user_id)
        self.session.flush()
        return self.get()

    def remove_avatar(self) -> ProfileOut:
        """去掉头像：之后显示名字的第一个字。旧图片留在存储里（存储没有删除接口），下次换头像时覆盖。"""
        profile = self.session.get(Profile, self.owner.user_id)
        if profile is not None and profile.avatar_at is not None:
            profile.avatar_at = None
            record(self.session, self.owner, "profile:avatar_remove", "user", self.owner.user_id)
            self.session.flush()
        return self.get()

    # ---- 内部 ----

    def _user(self) -> User | None:
        user = self.session.get(User, self.owner.user_id)
        return user if user is not None and user.team_id == self.owner.team_id else None

    def _profile(self) -> Profile:
        profile = self.session.get(Profile, self.owner.user_id)
        if profile is None:
            profile = Profile(user_id=self.owner.user_id, team_id=self.owner.team_id)
            self.session.add(profile)
        return profile

    def _storage(self) -> ObjectStorage:
        if self.storage is None:
            raise RuntimeError("ProfileService 没有拿到对象存储")
        return self.storage
