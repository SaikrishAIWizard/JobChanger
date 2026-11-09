from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CandidateProfile(BaseModel):
    full_name: str
    years_of_experience: float = Field(ge=0, le=80)
    primary_roles: list[str] = Field(max_length=12)
    technical_skills: list[str] = Field(max_length=60)
    domain_knowledge: list[str] = Field(max_length=30)
    seniority_level: Literal["Junior", "Mid", "Senior", "Staff", "Lead"]


class JobListing(BaseModel):
    external_id: str
    title: str
    company: str
    location: str = "Not specified"
    url: str
    description: str = ""
    source: str


class JobMatchScore(BaseModel):
    external_id: str
    source: str
    match_score: int = Field(ge=0, le=100)
    rationale: str


class ReferralContact(BaseModel):
    friend_name: str
    phone_number: str = ""
    company_name: str


class JobMatchResult(BaseModel):
    results: list[JobMatchScore]
