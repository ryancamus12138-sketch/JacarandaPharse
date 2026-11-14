"""User profile: display name and avatar (the page opened from the name in the top-right corner or
the avatar above the sidebar).

- Team mode: the display name is users.display_name, editable by the user and by administrators;
  the avatar is changed by the user.
- Personal mode: no user accounts; display name and avatar are stored in the profiles row of
  PERSONAL_OWNER.
Avatars are cropped to a square, scaled to 256 × 256 and stored as PNG in object storage
(dropping metadata such as photo location)."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.i18n import N_, tr
from jacara.core.owner import Owner
from jacara.core.permissions import GRANTABLE, allowed
from jacara.core.storage import ObjectStorage
from jacara.models import Profile, Team, User
from jacara.modules.audit.service import record
from jacara.modules.auth.schemas import ProfileOut, ProfileUpdate
from jacara.modules.auth.service import AuthError

AVATAR_SIZE = 256
"""Stored avatar size (pixels, square)."""
AVATAR_MAX_BYTES = 5 * 1024 * 1024
"""Maximum uploaded avatar size: 5 MB."""
AVATAR_FORMATS = {"PNG", "JPEG", "WEBP"}
PERSONAL_NAME = N_("本机用户")


def avatar_key(owner: Owner) -> str:
    return f"avatars/{owner.user_id}.png"


def shrink_avatar(data: bytes) -> bytes:
    """Photo of any size → 256 × 256 PNG. Raises for non-images and unsupported formats."""
    if len(data) > AVATAR_MAX_BYTES:
        raise AuthError("invalid", tr("头像图片过大，请选择 5 MB 以内的图片"))
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in AVATAR_FORMATS:
                raise AuthError("invalid", tr("头像只支持 PNG、JPG、WEBP 图片"))
            # Apply EXIF orientation (portrait phone photos).
            picture = ImageOps.exif_transpose(image)
            picture = ImageOps.fit(picture.convert("RGBA"), (AVATAR_SIZE, AVATAR_SIZE))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as err:
        raise AuthError("invalid", tr("无法打开此文件：不是图片或文件已损坏")) from err
    out = io.BytesIO()
    picture.save(out, format="PNG")  # Save a new image without the original EXIF data.
    return out.getvalue()


class ProfileService:
    def __init__(
        self, session: Session, owner: Owner, storage: ObjectStorage | None = None
    ) -> None:
        self.session = session
        self.owner = owner
        self.storage = storage
        self.personal = get_settings().jacara_mode == "personal"

    # ---- Read ----

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
            shown_name=name or username or tr(PERSONAL_NAME),
            role=self.owner.role,
            team=team_name,
            permissions=[tr(label) for action, label in GRANTABLE.items() if action in can],
            avatar_at=profile.avatar_at if profile else None,
            member_since=since,
        )

    def avatar(self) -> bytes:
        profile = self.session.get(Profile, self.owner.user_id)
        if profile is None or profile.avatar_at is None:
            raise AuthError("not_found", tr("尚未设置头像"))
        return self._storage().get(avatar_key(self.owner))

    # ---- Update ----

    def update(self, change: ProfileUpdate) -> ProfileOut:
        name = change.display_name.strip()
        if self.personal:
            self._profile().display_name = name or None
        else:
            user = self._user()
            if user is None:
                raise AuthError("not_found", tr("未找到你的账号"))
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
        """Remove the avatar; the first character of the name is shown instead. The old image stays
        in storage (the storage interface used here has no delete) and is overwritten by the next
        upload."""
        profile = self.session.get(Profile, self.owner.user_id)
        if profile is not None and profile.avatar_at is not None:
            profile.avatar_at = None
            record(self.session, self.owner, "profile:avatar_remove", "user", self.owner.user_id)
            self.session.flush()
        return self.get()

    # ---- Internal ----

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
            raise RuntimeError(tr("ProfileService 没有拿到对象存储"))
        return self.storage
