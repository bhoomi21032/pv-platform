"""SEAM between the platform (Partner B) and the triage logic (Partner A).

Partner A: put/import your triage function here. It must return (TriageResult, usage_dict).
Everything it needs from the LLM should go through `llm.triage` so timeouts / retries / breaker / fallback /
token accounting / tracing all apply for free.
"""
from .llm import LLMClient
from .schemas import AEReport, TriageResult


async def run(llm: LLMClient, report: AEReport) -> tuple[TriageResult, dict]:
    return await llm.triage(report)
