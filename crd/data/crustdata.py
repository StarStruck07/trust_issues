"""Owner: Nilay (P2). STUB from Arth — replace with the real client (NILAY_data.md section 5)."""
from __future__ import annotations

DEFAULT_FIELDS = ["headcount", "funding", "people", "news", "employee_reviews", "web_traffic", "hiring"]


class CrustdataClient:
    def __init__(self, run_id: str, mock: bool | None = None):
        self.run_id = run_id
        self.mock = mock
        self.last_raw_ref: str | None = None

    def identify(self, *, domains: list[str] | None = None, names: list[str] | None = None) -> list[dict]:
        raise NotImplementedError

    def enrich(self, *, company_id: int | None = None, domain: str | None = None,
               fields: list[str] = DEFAULT_FIELDS) -> dict:
        raise NotImplementedError

    def job_search(self, *, company_id: int | None = None, domain: str | None = None, days: int = 120) -> list[dict]:
        raise NotImplementedError

    def web_search(self, query: str, *, news: bool = True, limit: int = 10) -> list[dict]:
        raise NotImplementedError

    def web_fetch(self, urls: list[str]) -> list[dict]:
        raise NotImplementedError

    def person_enrich(self, profile_url: str) -> dict:
        raise NotImplementedError

    def credits_balance(self) -> float:
        raise NotImplementedError

    def create_company_watch(self, body: dict) -> dict:
        raise NotImplementedError
