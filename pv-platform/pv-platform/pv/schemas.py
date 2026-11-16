"""D1 - Pydantic validation + quarantine models.

ASSUMPTION: field names are my guess at an AE intake form (ICH E2B-ish, simplified).
If Partner A's triage file uses different names, change them HERE only.
"""
from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _no_ctrl(v: str) -> str:
    if _CTRL.search(v):
        raise ValueError("contains control characters")
    return v


class ReporterType(str, Enum):
    physician = "physician"
    pharmacist = "pharmacist"
    nurse = "nurse"
    consumer = "consumer"
    other = "other"


class Sex(str, Enum):
    male = "M"
    female = "F"
    unknown = "U"


class Outcome(str, Enum):
    recovered = "recovered"
    recovering = "recovering"
    not_recovered = "not_recovered"
    fatal = "fatal"
    unknown = "unknown"


class AEReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reporter_type: ReporterType
    patient_age_years: int = Field(ge=0, le=120)
    patient_sex: Sex = Sex.unknown
    suspect_drug: str = Field(min_length=2, max_length=200)
    dose: Optional[str] = Field(default=None, max_length=100)
    indication: Optional[str] = Field(default=None, max_length=200)
    concomitant_drugs: list[str] = Field(default_factory=list, max_length=20)
    reaction_description: str = Field(min_length=10, max_length=5000)
    onset_date: date
    outcome: Outcome = Outcome.unknown

    @field_validator("suspect_drug", "reaction_description", "dose", "indication")
    @classmethod
    def _ctrl(cls, v):
        return v if v is None else _no_ctrl(v)

    @field_validator("reaction_description")
    @classmethod
    def _min_words(cls, v: str) -> str:
        if len(v.split()) < 3:
            raise ValueError("describe the reaction in at least 3 words")
        return v

    @field_validator("concomitant_drugs")
    @classmethod
    def _drugs(cls, v: list[str]) -> list[str]:
        out = []
        for d in v:
            d = _no_ctrl(d.strip())
            if not 2 <= len(d) <= 100:
                raise ValueError("each concomitant drug must be 2-100 characters")
            out.append(d)
        return out

    @field_validator("onset_date")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("onset_date cannot be in the future")
        return v


class Seriousness(str, Enum):
    serious = "serious"
    non_serious = "non_serious"


class Priority(str, Enum):
    P1 = "P1"  # expedite (death / life-threatening)
    P2 = "P2"  # serious, standard clock
    P3 = "P3"  # non-serious


class TriageResult(BaseModel):
    """Output contract of the triage step (LLM or fallback). Partner A: align with your file."""
    model_config = ConfigDict(extra="ignore")
    seriousness: Seriousness
    criteria: list[str] = Field(default_factory=list)
    priority: Priority
    meddra_pt: Optional[str] = None
    rationale: str = Field(max_length=1000)
    needs_human_review: bool = False
    source: Literal["llm", "fallback"] = "llm"


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    job_id: str = Field(min_length=8, max_length=64)
    verdict: Literal["agree", "disagree"]
    corrected_seriousness: Optional[Seriousness] = None
    comment: Optional[str] = Field(default=None, max_length=500)


class QuarantineRecord(BaseModel):
    id: str
    received_at: float
    client: str
    reason: Literal["malformed_json", "not_an_object", "schema_validation"]
    errors: list[dict]
    raw: str  # truncated; may contain PII -> restrict access in prod


def format_errors(exc: ValidationError) -> list[dict]:
    """Safe error list: never echoes the offending input back (PII)."""
    return [
        {"field": ".".join(str(p) for p in e["loc"]) or "<root>", "type": e["type"], "msg": e["msg"]}
        for e in exc.errors(include_url=False, include_context=False, include_input=False)
    ]
