"""Deterministic ICH E2A-style seriousness rules. Used by (a) the fallback and (b) the llm-stub.

Design choice: the fallback is deliberately CONSERVATIVE. A false 'serious' costs a reviewer a minute;
a false 'non-serious' can delay a regulatory-clock case. Keyword match ignores negation on purpose.
"""
from .schemas import AEReport, Outcome, Priority, Seriousness, TriageResult

RULES: dict[str, list[str]] = {
    "death": ["died", "death", "fatal", "passed away", "deceased"],
    "life_threatening": ["life-threatening", "life threatening", "anaphyla", "cardiac arrest",
                         "respiratory failure", "stroke", "status epilepticus", "sepsis"],
    "hospitalization": ["hospitali", "admitted", "icu", "intensive care", "emergency room", "er visit"],
    "disability": ["disabil", "incapacit", "paralys", "permanent damage", "blindness"],
    "congenital_anomaly": ["congenital", "birth defect", "malformation"],
    "medically_important": ["angioedema", "suicid", "liver failure", "stevens-johnson", "agranulocytosis",
                            "seizure", "overdose"],
}


def match_criteria(report: AEReport) -> list[str]:
    text = f"{report.reaction_description} {report.suspect_drug}".lower()
    crit = [name for name, kws in RULES.items() if any(k in text for k in kws)]
    if report.outcome == Outcome.fatal and "death" not in crit:
        crit.append("death")
    return crit


def rule_based(report: AEReport, source: str = "fallback") -> TriageResult:
    crit = match_criteria(report)
    review = source == "fallback"  # a degraded result must always get human eyes
    if crit:
        pri = Priority.P1 if {"death", "life_threatening"} & set(crit) else Priority.P2
        return TriageResult(
            seriousness=Seriousness.serious, criteria=crit, priority=pri, meddra_pt=None,
            rationale=f"Rule match on seriousness criteria: {', '.join(crit)}.",
            needs_human_review=review, source=source)
    return TriageResult(
        seriousness=Seriousness.non_serious, criteria=[], priority=Priority.P3, meddra_pt=None,
        rationale="No seriousness criteria matched.", needs_human_review=review, source=source)
