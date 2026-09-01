"""Pydantic models for the six-part identity configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IdentitySection(BaseModel):
    """Who the assistant is."""

    name: str
    codename: str = ""
    role: str
    origin: str = ""
    self_description: str = ""


class MissionSection(BaseModel):
    """What the assistant is for."""

    primary: str
    principles: list[str] = Field(default_factory=list)


class PersonalitySection(BaseModel):
    """How the assistant behaves and speaks."""

    traits: list[str] = Field(default_factory=list)
    speaking_style: str = ""
    address_user_as: str = "你"


class UserModelSection(BaseModel):
    """Static skeleton of the user model; live facts come from memory."""

    display_name: str = ""
    timezone: str = "Asia/Shanghai"
    language: str = "zh-CN"
    notes: str = ""


class EvolutionSection(BaseModel):
    """How identity is allowed to change over time."""

    enabled: bool = True
    triggers: list[str] = Field(default_factory=list)
    method: str = ""


class IdentityBundle(BaseModel):
    """Complete six-part identity document."""

    identity: IdentitySection
    mission: MissionSection
    personality: PersonalitySection
    user_model: UserModelSection = Field(default_factory=UserModelSection)
    rules: list[str] = Field(default_factory=list)
    evolution: EvolutionSection = Field(default_factory=EvolutionSection)
