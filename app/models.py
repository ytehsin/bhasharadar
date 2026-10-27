"""Pydantic data models shared across modules."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RemoteType = Literal["remote", "hybrid", "onsite", "unknown"]
JobType = Literal["full_time", "part_time", "contract", "freelance", "internship", "temporary", "unknown"]

TRACKER_STATUSES = [
    "Discovered",
    "Shortlisted",
    "Ready to Apply",
    "Applied",
    "Email Sent",
    "Follow-up Due",
    "Interview",
    "Assessment",
    "Rejected",
    "Offer Received",
    "Closed",
]

MATCH_LABELS = ["Excellent Match", "Strong Match", "Possible Match", "Low Match", "Not Eligible"]


class JobRecord(BaseModel):
    """The common, normalized job format every connector produces."""

    source_id: str
    source_name: str
    source_job_id: Optional[str] = None
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    remote_type: RemoteType = "unknown"
    eligible_regions: list[str] = Field(default_factory=list)  # e.g. ["Worldwide"], ["India"], ["USA"]
    job_type: JobType = "unknown"
    seniority: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None  # year | month | hour | ...
    salary_text: Optional[str] = None
    description: str = ""
    url: Optional[str] = None
    apply_url: Optional[str] = None
    posted_at: Optional[str] = None  # ISO 8601
    deadline: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    expired: bool = False


class SearchFilters(BaseModel):
    categories: list[str] = Field(default_factory=list)
    keywords: str = ""
    job_types: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=lambda: ["Remote (Worldwide)", "India"])
    work_modes: list[str] = Field(default_factory=lambda: ["remote", "hybrid", "onsite"])
    experience_level: str = "any"
    min_salary: Optional[float] = None
    min_score: int = 0
    posted_within_days: Optional[int] = 60
    sources: list[str] = Field(default_factory=list)  # empty = all enabled
    max_queries: int = 14


class Profile(BaseModel):
    name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    total_years_experience: Optional[float] = None
    languages: list[dict[str, str]] = Field(default_factory=list)  # {"language","level"}
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    job_titles: list[str] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    work_history: list[dict[str, Any]] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    transferable_skills: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    preferences: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    verified: bool = False  # False = provisional seed, not from an uploaded resume
    notes: list[str] = Field(default_factory=list)
