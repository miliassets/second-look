"""Viewer profiles (design §2.2 viewer_profiles)."""
from typing import Literal

from pydantic import BaseModel


class ViewerProfile(BaseModel):
    profile_id: str
    mode: Literal["analyst", "fan"]
    lang: Literal["en", "es"]
    followed_player_id: str | None = None
    followed_team_id: str | None = None


DEFAULT_PROFILES = [
    ViewerProfile(profile_id="analyst-en", mode="analyst", lang="en"),
    ViewerProfile(profile_id="fan-es", mode="fan", lang="es",
                  followed_player_id="IRV-10", followed_team_id="IRV"),
]
