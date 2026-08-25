from typing import TypedDict


class LoanAgentState(TypedDict, total=False):
    application_id: str

    parsed_documents: dict[str, str]
    stated_messages: dict[str, str]

    extracted_fact_ids: list[str]
    anomalies: list[dict[str, object]]
    missing_requirements: list[str]
    degraded: bool
    outcome: str
