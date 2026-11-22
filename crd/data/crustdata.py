"""Owner: Nilay (P2). The one Crustdata client: mock, cache, budget, rate limit, retry, ledger.

Every call goes through `_call()`. Pipeline order (NILAY_data.md section 5):
    forced failure -> mock -> cache -> budget -> rate limit -> HTTP with retries -> cost -> ledger -> store

Return shapes (what callers get):
    identify()      raw list  [{matched_on, match_type, matches:[{confidence_score, company_data}]}]
    enrich()        company_data dict of the best match ({} when nothing matched)
    job_search()    list of job_listings
    web_search()    list of results [{title, url, snippet, source, position}]
    web_fetch()     list [{success, url, title, content, timestamp}]
    person_enrich() person_data dict of the best match ({} when not found)

Measured on 2026-11-22 (tests/fixtures/README.md): our key gets 5 req/min on the company/person/job
endpoints and 10/min on web. Every response carries `X-Credits-Used`, which is what we bill.
The API key is never logged or written to disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from crd import config, db
from crd.data.errors import BudgetExceeded, CreditsExhausted, SourceUnavailable
from crd.models import LedgerEntry

BASE_URL = "https://api.crustdata.com"
API_VERSION = "2025-11-01"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

DEFAULT_FIELDS = ["basic_info", "headcount", "funding", "people", "news", "employee_reviews",
                  "web_traffic", "hiring"]

# requests per 60 s, per endpoint (observed headers; override with CRD_RATE_<ENDPOINT>=n)
RATE_LIMITS = {"identify": 5, "enrich": 5, "job_search": 5, "person_enrich": 5,
               "web_search": 10, "web_fetch": 10, "watch": 10, "credits": 300, "registries": 10}
# Public government pages fetched directly (no Crustdata auth header, 0 credits, ledger provider "registry").
PUBLIC_ENDPOINTS = {"registries"}
PUBLIC_UA = "Mozilla/5.0 (compatible; CounterpartyRiskDesk/0.1)"
BACKOFF = [1, 3, 9]
MAX_TRIES = 3
TIMEOUT_S = float(os.getenv("CRD_HTTP_TIMEOUT", "45"))   # first enrich call took 31 s in the probe
JOB_PAGE = 25                                            # job search bills ~0.03 per result returned


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", str(s).lower()).strip("-")[:80]


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s).strip().strip('"').strip("'").lower())


def _load_key() -> str | None:
    key = os.getenv("CRUSTDATA_API_KEY")
    if key:
        return key
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("CRUSTDATA_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def _env_mock() -> bool:
    return os.getenv("CRD_MOCK", "0").strip().lower() in ("1", "true", "yes")


def _failed_sources() -> set[str]:
    return {s.strip() for s in os.getenv("CRD_FAIL_SOURCES", "").split(",") if s.strip()}


# --------------------------------------------------------------------------- rate limiting
class _Bucket:
    """Token bucket, `rate` tokens per 60 s, thread-safe. `hold()` honours a server-side reset."""

    def __init__(self, rate: int, sleep: Callable[[float], None]):
        self.cap = float(max(rate, 1))
        self.tokens = self.cap
        self.per_s = self.cap / 60.0
        self.t = time.monotonic()
        self.blocked_until = 0.0
        self.lock = threading.Lock()
        self.sleep = sleep

    def take(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.per_s)
                self.t = now
                wait = max(0.0, self.blocked_until - now)
                if wait == 0 and self.tokens >= 1:
                    self.tokens -= 1
                    return
                if wait == 0:
                    wait = (1 - self.tokens) / self.per_s
            self.sleep(min(wait, 60))

    def hold(self, seconds: float) -> None:
        with self.lock:
            self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)
            self.tokens = 0


_BUCKETS: dict[str, _Bucket] = {}
_BUCKETS_LOCK = threading.Lock()


def _bucket(endpoint: str, sleep: Callable[[float], None]) -> _Bucket:
    with _BUCKETS_LOCK:
        b = _BUCKETS.get(endpoint)
        if b is None:
            rate = int(os.getenv(f"CRD_RATE_{endpoint.upper()}", RATE_LIMITS.get(endpoint, 10)))
            b = _BUCKETS[endpoint] = _Bucket(rate, sleep)
        b.sleep = sleep
        return b


# --------------------------------------------------------------------------- mock fixtures
class _FixtureIndex:
    """Maps lookup values (domain, name, company id, query, url, profile url) to fixture keys.

    Built from tests/fixtures/_probe_log.json (request bodies of the recorded calls) and from the
    identify/enrich fixtures (company id, domain and name of each recorded company).
    """

    def __init__(self, root: Path):
        self.root = root
        self.by_ep: dict[tuple[str, str], str] = {}
        self.alias: dict[str, str] = {}
        log = root / "_probe_log.json"
        if log.exists():
            for row in json.loads(log.read_text()):
                if row.get("status") != 200:
                    continue
                for v in _lookup_values(row.get("request") or {}):
                    self.by_ep.setdefault((row["endpoint"], v), slug(row["key"]))
        for ep in ("identify", "enrich"):
            for f in sorted((root / ep).glob("*.json")) if (root / ep).exists() else []:
                if f.stem.startswith("_"):
                    continue
                try:
                    data = json.loads(f.read_text())
                except ValueError:
                    continue
                for item in data if isinstance(data, list) else []:
                    for m in item.get("matches") or []:
                        cd = m.get("company_data") or {}
                        bi = cd.get("basic_info") or {}
                        for v in (cd.get("crustdata_company_id"), bi.get("primary_domain"), bi.get("name")):
                            if v not in (None, ""):
                                self.alias.setdefault(_norm(v), f.stem)
                    break  # only the first entry of each file describes that company

    def find(self, endpoint: str, payload: dict) -> Path | None:
        values = _lookup_values(payload)
        for v in values:
            for key in (self.by_ep.get((endpoint, v)), self.alias.get(v), slug(v)):
                if key and (self.root / endpoint / f"{key}.json").exists():
                    return self.root / endpoint / f"{key}.json"
        # web search queries like '"Delhivery" Mumbai': match any known company name inside the query
        if endpoint == "web_search":
            for v in values:
                for name, key in self.alias.items():
                    if len(name) > 3 and name in v and (self.root / endpoint / f"{key}.json").exists():
                        return self.root / endpoint / f"{key}.json"
        return None


def _lookup_values(payload: dict) -> list[str]:
    out: list[str] = []
    for k in ("domains", "names", "crustdata_company_ids", "urls", "professional_network_profile_urls"):
        out += [_norm(v) for v in payload.get(k) or []]
    if payload.get("query"):
        out.append(_norm(payload["query"]))
    if payload.get("key"):
        out.append(_norm(payload["key"]))
    f = payload.get("filters")
    stack = [f] if f else []
    while stack:
        c = stack.pop()
        if isinstance(c, dict):
            if "value" in c and not isinstance(c["value"], (list, dict)):
                out.append(_norm(c["value"]))
            stack += c.get("conditions") or []
    return out


_INDEX: _FixtureIndex | None = None


def _index() -> _FixtureIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = _FixtureIndex(FIXTURES)
    return _INDEX


# --------------------------------------------------------------------------- the client
class CrustdataClient:
    def __init__(self, run_id: str, mock: bool | None = None, *,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.run_id = run_id
        self.mock = _env_mock() if mock is None else mock
        self.last_raw_ref: str | None = None
        self._sleep = sleep
        self._cp_credits: dict[str, float] = {}
        self._lock = threading.Lock()
        self._http: httpx.Client | None = None
        self._transport = transport

    # ---------------------------------------------------------------- public API
    def identify(self, *, domains: list[str] | None = None, names: list[str] | None = None,
                 cp_key: str | None = None) -> list[dict]:
        if domains:
            payload: dict = {"domains": list(domains)}
        elif names:
            payload = {"names": list(names)}
        else:
            raise ValueError("identify needs domains or names")
        resp = self._call("POST", "/company/identify", payload, "identify", cp_key=cp_key)
        return resp if isinstance(resp, list) else []

    def enrich(self, *, company_id: int | None = None, domain: str | None = None,
               fields: list[str] = DEFAULT_FIELDS, cp_key: str | None = None) -> dict:
        if company_id is not None:
            payload: dict = {"crustdata_company_ids": [int(company_id)]}
        elif domain:
            payload = {"domains": [domain]}
        else:
            raise ValueError("enrich needs company_id or domain")
        payload["fields"] = list(fields)
        resp = self._call("POST", "/company/enrich", payload, "enrich", cp_key=cp_key)
        return _best_match(resp, "company_data")

    def job_search(self, *, company_id: int | None = None, domain: str | None = None, days: int = 120,
                   limit: int = JOB_PAGE, cp_key: str | None = None) -> list[dict]:
        """Most recent postings first. Filter by date client-side (metadata.date_added); `days` is a hint."""
        if company_id is not None:
            cond = {"field": "company.basic_info.crustdata_company_id", "type": "=", "value": int(company_id)}
        elif domain:
            cond = {"field": "company.basic_info.primary_domain", "type": "=", "value": domain}
        else:
            raise ValueError("job_search needs company_id or domain")
        payload = {"filters": cond, "sorts": [{"field": "metadata.date_added", "order": "desc"}],
                   "limit": int(limit)}
        resp = self._call("POST", "/job/search", payload, "job_search", cp_key=cp_key)
        return list((resp or {}).get("job_listings") or []) if isinstance(resp, dict) else []

    def web_search(self, query: str, *, news: bool = True, limit: int = 10,
                   start_date: int | None = None, end_date: int | None = None,
                   cp_key: str | None = None) -> list[dict]:
        """Results carry no publish date; pass start_date/end_date (unix seconds) to bound the window."""
        payload: dict = {"query": query, "location": "IN"}
        if news:
            payload["sources"] = ["news"]
        if start_date is not None:
            payload["start_date"] = int(start_date)
        if end_date is not None:
            payload["end_date"] = int(end_date)
        resp = self._call("POST", "/web/search/live", payload, "web_search", cp_key=cp_key)
        results = (resp or {}).get("results") or [] if isinstance(resp, dict) else []
        return list(results)[:limit]

    def web_fetch(self, urls: list[str], *, cp_key: str | None = None) -> list[dict]:
        urls = [u for u in urls if u][:10]
        if not urls:
            return []
        resp = self._call("POST", "/web/enrich/live", {"urls": urls}, "web_fetch", cp_key=cp_key)
        return resp if isinstance(resp, list) else []

    def person_enrich(self, profile_url: str, *, cp_key: str | None = None) -> dict:
        payload = {"professional_network_profile_urls": [profile_url],
                   "fields": ["basic_profile", "experience"]}
        resp = self._call("POST", "/person/enrich", payload, "person_enrich", cp_key=cp_key)
        return _best_match(resp, "person_data")

    def credits_balance(self) -> float:
        resp = self._call("GET", "/account/credits", None, "credits", use_cache=False)
        return float(((resp or {}).get("account") or {}).get("credits") or 0.0)

    def create_company_watch(self, body: dict) -> dict:
        resp = self._call("POST", "/watch/company", body, "watch", use_cache=False)
        return resp if isinstance(resp, dict) else {}

    def public_get(self, url: str, *, key: str, day: str | None = None) -> dict:
        """GET a public registry page (IBBI). Returns {"url", "status", "html"}. Never sends the API key.

        `key` names the mock fixture (tests/fixtures/registries/<slug(key)>.json); `day` scopes the cache
        to one day so a weekly re-run re-reads the registry.
        """
        payload = {"url": url, "key": key, "day": day or _now().date().isoformat()}
        resp = self._call("GET", url, payload, "registries")
        return resp if isinstance(resp, dict) else {}

    def test_company_watch(self, watch_id: str, body: dict | None = None) -> dict:
        resp = self._call("POST", f"/watch/company/{watch_id}/test", body or {}, "watch", use_cache=False)
        return resp if isinstance(resp, dict) else {}

    # ---------------------------------------------------------------- pipeline
    def _call(self, method: str, path: str, payload: dict | None, endpoint: str, *,
              cp_key: str | None = None, use_cache: bool = True) -> Any:
        body_json = json.dumps(payload, sort_keys=True) if payload is not None else ""
        rhash = hashlib.sha256(f"{method} {path} {body_json}".encode()).hexdigest()

        # 0. forced failure (demo / test case 10)
        if endpoint in _failed_sources():
            self._ledger(endpoint, rhash, status="error", note="forced by CRD_FAIL_SOURCES")
            raise SourceUnavailable("forced by CRD_FAIL_SOURCES")

        # 1. mock
        if self.mock:
            resp, ref = self._mock_response(endpoint, payload or {})
            self.last_raw_ref = ref
            self._ledger(endpoint, rhash, status="cached", note="mock")
            return resp

        # 2. cache
        if use_cache:
            hit = db.cache_get(rhash)
            if hit is not None:
                self.last_raw_ref = self._store_raw(endpoint, rhash, hit)
                self._ledger(endpoint, rhash, status="cached", note="cache hit")
                return hit

        cfg = config.get()

        # 3. budget
        est = self._estimate(endpoint, payload or {}, cfg)
        if est > 0:
            limits = cfg.get("limits") or {}
            spent = float(db.run_costs(self.run_id).get("credits") or 0)
            cap = limits.get("max_credits_per_run")
            if cap is not None and spent + est > float(cap):
                self._ledger(endpoint, rhash, status="error", note=f"budget: run {spent:.2f}+{est:.2f}>{cap}")
                raise BudgetExceeded(f"run budget {cap} credits: spent {spent:.2f}, next call ~{est:.2f}")
            cp_cap = limits.get("max_credits_per_cp")
            if cp_key and cp_cap is not None:
                with self._lock:
                    cp_spent = self._cp_credits.get(cp_key, 0.0)
                if cp_spent + est > float(cp_cap):
                    self._ledger(endpoint, rhash, status="error",
                                 note=f"budget: cp {cp_key} {cp_spent:.2f}+{est:.2f}>{cp_cap}")
                    raise BudgetExceeded(f"counterparty budget {cp_cap} credits for {cp_key}: spent {cp_spent:.2f}")

        # 4-5. rate limit + HTTP with retries
        resp, credits_hdr, latency = self._http_with_retries(method, path, payload, endpoint, rhash)

        # 6. cost
        credits = credits_hdr if credits_hdr is not None else self._actual_price(endpoint, payload or {}, resp, cfg)
        inr = round(credits * float(cfg.get("inr_per_credit") or 0), 4)
        if cp_key:
            with self._lock:
                self._cp_credits[cp_key] = self._cp_credits.get(cp_key, 0.0) + credits

        # 7. ledger + 8. store
        self._ledger(endpoint, rhash, status="ok", credits=credits, inr=inr, latency_ms=latency,
                     note="" if credits_hdr is not None else "price from config")
        if use_cache:
            db.cache_put(rhash, endpoint, resp)
        self.last_raw_ref = self._store_raw(endpoint, rhash, resp)
        return resp

    def _client(self) -> httpx.Client:
        if self._http is None:
            key = _load_key()
            if not key:
                raise SourceUnavailable("auth: CRUSTDATA_API_KEY not set")
            self._http = httpx.Client(
                base_url=BASE_URL, timeout=TIMEOUT_S, transport=self._transport,
                headers={"authorization": f"Bearer {key}", "content-type": "application/json",
                         "x-api-version": API_VERSION})
        return self._http

    def _public_http(self) -> httpx.Client:
        if getattr(self, "_pub", None) is None:
            self._pub = httpx.Client(timeout=TIMEOUT_S, follow_redirects=True, transport=self._transport,
                                     headers={"user-agent": PUBLIC_UA})
        return self._pub

    def _http_with_retries(self, method: str, path: str, payload: dict | None, endpoint: str,
                           rhash: str) -> tuple[Any, float | None, int]:
        bucket = _bucket(endpoint, self._sleep)
        public = endpoint in PUBLIC_ENDPOINTS
        http = self._public_http() if public else self._client()
        fails = 0          # 5xx / timeout / network
        limited = 0        # 429
        last_err = ""
        while True:
            bucket.take()
            t0 = time.perf_counter()
            try:
                if public:
                    r = http.request(method, payload["url"])
                elif payload is not None:
                    r = http.request(method, path, json=payload)
                else:
                    r = http.request(method, path)
            except httpx.TimeoutException as e:
                ms = int((time.perf_counter() - t0) * 1000)
                fails += 1
                last_err = f"timeout ({type(e).__name__})"
                self._ledger(endpoint, rhash, status="timeout", latency_ms=ms, note=f"try {fails}/{MAX_TRIES}")
                if fails >= MAX_TRIES:
                    raise SourceUnavailable(f"{endpoint}: {last_err} after {MAX_TRIES} tries") from e
                self._sleep(BACKOFF[fails - 1])
                continue
            except httpx.HTTPError as e:
                ms = int((time.perf_counter() - t0) * 1000)
                fails += 1
                last_err = f"network ({type(e).__name__})"
                self._ledger(endpoint, rhash, status="error", latency_ms=ms, note=f"{last_err} try {fails}/{MAX_TRIES}")
                if fails >= MAX_TRIES:
                    raise SourceUnavailable(f"{endpoint}: {last_err} after {MAX_TRIES} tries") from e
                self._sleep(BACKOFF[fails - 1])
                continue

            ms = int((time.perf_counter() - t0) * 1000)
            remaining = r.headers.get("x-ratelimit-remaining")
            reset = _num(r.headers.get("x-ratelimit-reset"))
            if remaining == "0" and reset:
                bucket.hold(reset)
            code = r.status_code

            if code == 200 or code == 201:
                if public:
                    return {"url": str(r.url), "status": code, "html": r.text}, 0.0, ms
                credits = _num(r.headers.get("x-credits-used"))
                try:
                    return r.json(), credits, ms
                except ValueError as e:
                    self._ledger(endpoint, rhash, status="error", latency_ms=ms, note="non-JSON 200")
                    raise SourceUnavailable(f"{endpoint}: non-JSON response") from e

            text = r.text[:300]
            if code == 429:
                limited += 1
                wait = reset if reset else 5
                self._ledger(endpoint, rhash, status="rate_limited", latency_ms=ms, note=f"429, waiting {wait:.0f}s")
                if limited > MAX_TRIES:
                    raise SourceUnavailable(f"{endpoint}: still rate limited after {MAX_TRIES} waits")
                bucket.hold(wait)
                self._sleep(wait)
                continue
            if not public and (code == 402 or (code == 400 and "credit" in text.lower())):
                self._ledger(endpoint, rhash, status="error", latency_ms=ms, note=f"{code} credits exhausted")
                raise CreditsExhausted(f"{endpoint}: HTTP {code} {text}")
            if code in (401, 403):
                self._ledger(endpoint, rhash, status="error", latency_ms=ms, note=f"auth {code}")
                raise SourceUnavailable(f"auth: HTTP {code} from {endpoint}")
            if code >= 500:
                fails += 1
                last_err = f"HTTP {code}"
                self._ledger(endpoint, rhash, status="error", latency_ms=ms, note=f"{code} try {fails}/{MAX_TRIES}")
                if fails >= MAX_TRIES:
                    raise SourceUnavailable(f"{endpoint}: {last_err} after {MAX_TRIES} tries")
                self._sleep(BACKOFF[fails - 1])
                continue
            # other 4xx: our request is wrong; retrying won't help
            self._ledger(endpoint, rhash, status="error", latency_ms=ms, note=f"HTTP {code}: {text[:120]}")
            raise SourceUnavailable(f"{endpoint}: HTTP {code} {text}")

    # ---------------------------------------------------------------- cost helpers
    @staticmethod
    def _price(endpoint: str, cfg) -> float:
        prices = cfg.get("prices") or {}
        return float(prices.get(endpoint) or 0.0)

    def _estimate(self, endpoint: str, payload: dict, cfg) -> float:
        """Upper-bound credits for the budget check, before the call."""
        p = self._price(endpoint, cfg)
        if endpoint == "identify":
            return p * len(payload.get("domains") or payload.get("names") or [1])
        if endpoint == "job_search":
            return p * int(payload.get("limit") or JOB_PAGE)
        if endpoint == "web_fetch":
            return p * len(payload.get("urls") or [])
        return p

    def _actual_price(self, endpoint: str, payload: dict, resp: Any, cfg) -> float:
        """Used only when the response has no X-Credits-Used header."""
        p = self._price(endpoint, cfg)
        if endpoint == "identify":
            matched = sum(1 for item in resp or [] if isinstance(item, dict) and item.get("matches"))
            return p * matched
        if endpoint == "job_search":
            return p * len((resp or {}).get("job_listings") or [])
        if endpoint == "web_search":
            return p if (resp or {}).get("results") else 0.0
        if endpoint == "web_fetch":
            return p * len(payload.get("urls") or [])
        return p

    # ---------------------------------------------------------------- ledger / storage
    def _ledger(self, endpoint: str, rhash: str, *, status: str, credits: float = 0.0, inr: float = 0.0,
                latency_ms: int = 0, note: str = "") -> None:
        db.insert_ledger(LedgerEntry(
            call_id=uuid.uuid4().hex, run_id=self.run_id,
            provider="registry" if endpoint in PUBLIC_ENDPOINTS else "crustdata", endpoint=endpoint,
            request_hash=rhash, credits=credits, inr=inr, latency_ms=latency_ms, status=status,
            note=note, ts=_now()))

    def _store_raw(self, endpoint: str, rhash: str, resp: Any) -> str:
        d = Path("var") / "runs" / self.run_id / "raw"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{endpoint}_{rhash[:8]}.json"
        if not p.exists():
            p.write_text(json.dumps(resp, ensure_ascii=False, default=str))
        return str(p)

    # ---------------------------------------------------------------- mock
    def _mock_response(self, endpoint: str, payload: dict) -> tuple[Any, str]:
        idx = _index()
        if endpoint == "identify":  # batch: answer each identifier on its own, like the real API
            kind = "domains" if payload.get("domains") else "names"
            ids = payload.get(kind) or []
            if len(ids) > 1:
                out, refs = [], []
                for v in ids:
                    resp, ref = self._mock_response(endpoint, {kind: [v]})
                    out += resp if isinstance(resp, list) else []
                    refs.append(ref)
                return out, refs[0]
        path = idx.find(endpoint, payload)
        if path is not None:
            return json.loads(path.read_text()), str(path)

        if endpoint == "identify":
            if payload.get("names"):  # unknown name -> no match (realistic not_found)
                return [{"matched_on": n, "match_type": "name", "matches": []} for n in payload["names"]], \
                    "mock:identify:no-match"
            return [_synthetic_identify(d) for d in payload.get("domains") or []], "mock:identify:synthetic"

        default = FIXTURES / endpoint / "_default.json"
        if not default.exists():
            raise SourceUnavailable(f"mock: no fixture for {endpoint} and no _default.json")
        resp = json.loads(default.read_text())
        if endpoint == "enrich":
            resp = _patch_default_enrich(resp, payload)
        elif endpoint == "web_fetch":
            resp = [dict(resp[0], url=u) for u in payload.get("urls") or []] if resp else []
        return resp, str(default)


# --------------------------------------------------------------------------- helpers
def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _best_match(resp: Any, key: str) -> dict:
    if isinstance(resp, list):
        for item in resp:
            ms = (item or {}).get("matches") or []
            if ms:
                best = max(ms, key=lambda m: float(m.get("confidence_score") or 0))
                return best.get(key) or {}
    return {}


def _synthetic_id(domain: str) -> int:
    return 900_000_000 + int(hashlib.sha1(domain.encode()).hexdigest()[:6], 16)


def _synthetic_identify(domain: str) -> dict:
    """Mock identify for a domain without a fixture: one exact match, clearly synthetic."""
    cid = _synthetic_id(domain)
    name = domain.split(".")[0].replace("-", " ").title()
    return {"matched_on": domain, "match_type": "domain", "matches": [{
        "confidence_score": 1.0,
        "company_data": {"crustdata_company_id": cid, "basic_info": {
            "crustdata_company_id": cid, "name": name, "primary_domain": domain,
            "website": f"https://{domain}", "all_domains": [domain],
            "description": "MOCK company (no recorded fixture)"}}}]}


def _patch_default_enrich(resp: Any, payload: dict) -> Any:
    """Point the default enrich fixture at the requested company so mock output is not mislabelled."""
    resp = json.loads(json.dumps(resp))
    domain = (payload.get("domains") or [None])[0]
    cid = (payload.get("crustdata_company_ids") or [None])[0]
    if domain and cid is None:
        cid = _synthetic_id(domain)
    for item in resp if isinstance(resp, list) else []:
        for m in item.get("matches") or []:
            cd = m.setdefault("company_data", {})
            bi = cd.setdefault("basic_info", {})
            if cid is not None:
                cd["crustdata_company_id"] = bi["crustdata_company_id"] = cid
            if domain:
                bi["primary_domain"] = domain
                bi["name"] = domain.split(".")[0].replace("-", " ").title()
            elif cid is not None:
                bi["name"] = f"Mock company {cid}"
            bi["description"] = "MOCK company (default enrich fixture)"
    return resp
