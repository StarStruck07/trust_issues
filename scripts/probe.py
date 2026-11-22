"""P2 probe: hit each Crustdata endpoint for a handful of real companies and
record raw responses as fixtures for mock mode.

Usage:
    pip install httpx
    export CRUSTDATA_API_KEY=...        # or put it in .env (never committed)
    python scripts/probe.py                       # all companies in COMPANIES
    python scripts/probe.py --only delhivery.com  # one company
    python scripts/probe.py --skip web_fetch,person_enrich
    python scripts/probe.py --dry-run             # print planned calls, no HTTP

Writes:
    tests/fixtures/<endpoint>/<key>.json   raw response body
    tests/fixtures/_probe_log.json         status / latency / credits / rate-limit headers per call

Paths and bodies come from docs.crustdata.com (API version 2025-11-01).
The API key is never printed or written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

BASE_URL = "https://api.crustdata.com"
API_VERSION = "2025-11-01"
ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"

# ---------------------------------------------------------------------------
# Companies to probe. EDIT #4 and #6 before running (see NILAY_data.md §4.2).
#   kind: "domain" -> identify/enrich by domain; "name" -> by name (lookalike case)
#   cxo:  True -> also person_enrich one CXO (only for #1 or #6)
# ---------------------------------------------------------------------------
COMPANIES: list[dict] = [
    {"n": 1, "name": "Delhivery", "domain": "delhivery.com", "cxo": True},
    {"n": 2, "name": "Blue Dart Express", "domain": "bluedart.com"},
    {"n": 3, "name": "Mamaearth", "domain": "mamaearth.in"},
    {"n": 4, "name": "CHANGE_ME small trucking firm", "domain": None},  # TODO
    {"n": 5, "name": "Sharma Transport", "domain": None},
    {"n": 6, "name": "CHANGE_ME negative-news company", "domain": None, "cxo": False},  # TODO
]

ENRICH_FIELDS = ["basic_info", "headcount", "funding", "people", "news",
                 "employee_reviews", "web_traffic", "hiring", "updated_at", "indexed_at"]

LOG: list[dict] = []


# ---------------------------------------------------------------------------
def load_key() -> str:
    key = os.environ.get("CRUSTDATA_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("CRUSTDATA_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("CRUSTDATA_API_KEY not set (env or .env)")
    return key


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")[:80]


def save(endpoint: str, key: str, body) -> Path:
    p = FIX / endpoint / f"{slug(key)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, indent=2, ensure_ascii=False))
    return p


class Probe:
    def __init__(self, key: str, dry: bool):
        self.dry = dry
        self.http = httpx.Client(
            base_url=BASE_URL, timeout=60,
            headers={"authorization": f"Bearer {key}",
                     "content-type": "application/json",
                     "x-api-version": API_VERSION})

    def call(self, method: str, path: str, endpoint: str, key: str, payload: dict | None = None):
        if self.dry:
            print(f"  [dry] {method} {path} {json.dumps(payload)[:160] if payload else ''}")
            return None
        t0 = time.perf_counter()
        try:
            r = self.http.request(method, path, json=payload)
        except httpx.HTTPError as e:
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"  {endpoint:<14} {key:<28} ERROR {type(e).__name__} {ms}ms")
            LOG.append({"endpoint": endpoint, "key": key, "path": path, "status": "exception",
                        "error": repr(e), "latency_ms": ms, "request": payload})
            return None
        ms = int((time.perf_counter() - t0) * 1000)
        try:
            body = r.json()
        except ValueError:
            body = {"_non_json": r.text[:2000]}
        rl = {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
        credits_hdr = r.headers.get("x-credits-used")
        body_credit = find_credit_fields(body)
        print(f"  {endpoint:<14} {key:<28} {r.status_code} {ms:>5}ms "
              f"credits_hdr={credits_hdr} body_credit={body_credit or '-'} rl={rl or '-'}")
        LOG.append({"endpoint": endpoint, "key": key, "path": path, "method": method,
                    "status": r.status_code, "latency_ms": ms, "x_credits_used": credits_hdr,
                    "body_credit_fields": body_credit, "ratelimit": rl, "request": payload})
        fname_key = key if r.status_code == 200 else f"{key}.{r.status_code}"
        save(endpoint, fname_key, body)
        if r.status_code == 402:
            self.finish()
            sys.exit("402 credits exhausted, stopping")
        return body if r.status_code == 200 else None

    def credits(self) -> float | None:
        if self.dry:
            return None
        r = self.http.get("/account/credits")
        if r.status_code != 200:
            print(f"  credits: HTTP {r.status_code} {r.text[:200]}")
            return None
        body = r.json()
        save("credits", f"balance_{int(time.time())}", body)
        return float(body.get("account", {}).get("credits"))

    def finish(self):
        FIX.mkdir(parents=True, exist_ok=True)
        (FIX / "_probe_log.json").write_text(json.dumps(LOG, indent=2, ensure_ascii=False))


def find_credit_fields(obj, prefix="", out=None, depth=0):
    """Find any body keys that mention credits/cost (top few levels only)."""
    out = {} if out is None else out
    if depth > 3:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if re.search(r"credit|cost", k, re.I) and not isinstance(v, (dict, list)):
                out[path] = v
            find_credit_fields(v, path, out, depth + 1)
    elif isinstance(obj, list) and obj:
        find_credit_fields(obj[0], prefix + "[0]", out, depth + 1)
    return out


def first_match(resp) -> dict | None:
    """identify/enrich return [ {matched_on, match_type, matches:[{confidence_score, company_data}]} ]"""
    if isinstance(resp, list) and resp and resp[0].get("matches"):
        return resp[0]["matches"][0]
    return None


def find_profile_url(person: dict) -> str | None:
    """PersonProfile shape is not fully documented; search for a profile URL."""
    stack = [person]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(v, str) and ("linkedin.com/in/" in v or
                                           ("profile_url" in k and v.startswith("http"))):
                    return v
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


# ---------------------------------------------------------------------------
def probe_company(p: Probe, c: dict, skip: set[str]):
    key = c["domain"] or c["name"]
    print(f"\n#{c['n']} {c['name']}  (key={key})")
    ident_body = {"domains": [c["domain"]]} if c["domain"] else {"names": [c["name"]]}

    company_id = None
    if "identify" not in skip:
        resp = p.call("POST", "/company/identify", "identify", key, ident_body)
        if isinstance(resp, list):
            for entry in resp:
                for m in entry.get("matches", [])[:5]:
                    bi = m.get("company_data", {}).get("basic_info", {})
                    print(f"     match conf={m.get('confidence_score')} id={bi.get('crustdata_company_id')}"
                          f" name={bi.get('name')!r} domain={bi.get('primary_domain')}")
        m = first_match(resp)
        if m:
            company_id = m.get("company_data", {}).get("crustdata_company_id")
    if c["domain"] is None and c["n"] == 5:
        print("     lookalike case: stopping after identify (no enrich on an ambiguous name)")
        return

    enrich = None
    if "enrich" not in skip:
        body = ({"crustdata_company_ids": [company_id]} if company_id else ident_body) | {"fields": ENRICH_FIELDS}
        enrich = p.call("POST", "/company/enrich", "enrich", key, body)
    ed = (first_match(enrich) or {}).get("company_data", {}) if enrich else {}
    if ed:
        print(f"     updated_at={ed.get('updated_at')} indexed_at={ed.get('indexed_at')} "
              f"groups={sorted(k for k, v in ed.items() if v)}")
        if company_id is None:
            company_id = ed.get("crustdata_company_id")

    if "job_search" not in skip and (company_id or c["domain"]):
        # Docs example filters on company.basic_info.company_id; the Job object exposes
        # crustdata_company_id / primary_domain. Try in order, keep the first 200.
        candidates = []
        if company_id:
            candidates += [("company.basic_info.crustdata_company_id", company_id),
                           ("company.basic_info.company_id", company_id)]
        if c["domain"]:
            candidates.append(("company.basic_info.primary_domain", c["domain"]))
        for field, val in candidates:
            body = {"filters": {"field": field, "type": "=", "value": val},
                    "sorts": [{"field": "metadata.date_added", "order": "desc"}],
                    "limit": 50}
            resp = p.call("POST", "/job/search", "job_search", key, body)
            if resp is not None:
                print(f"     job filter field that worked: {field}  total_count={resp.get('total_count')}")
                break

    articles = []
    if "web_search" not in skip:
        body = {"query": f'"{c["name"]}"', "sources": ["news"], "location": "IN"}
        resp = p.call("POST", "/web/search/live", "web_search", key, body)
        if resp:
            articles = [r.get("url") for r in resp.get("results", []) if r.get("url")]
            for r in resp.get("results", [])[:3]:
                print(f"     news: {r.get('title')!r} {r.get('url')}  keys={sorted(r)}")
    if not articles and ed.get("news"):
        articles = [n.get("article_url") for n in ed["news"] if n.get("article_url")]

    if "web_fetch" not in skip and articles:
        resp = p.call("POST", "/web/enrich/live", "web_fetch", key, {"urls": articles[:1]})
        if isinstance(resp, list) and resp:
            print(f"     fetch success={resp[0].get('success')} title={resp[0].get('title')!r} "
                  f"content_len={len(resp[0].get('content') or '')}")

    if "person_enrich" not in skip and c.get("cxo") and ed:
        cxos = (ed.get("people") or {}).get("cxos") or []
        url = next((u for u in (find_profile_url(x) for x in cxos) if u), None)
        if cxos:
            print(f"     cxo[0] keys={sorted(cxos[0]) if isinstance(cxos[0], dict) else type(cxos[0])}")
        if url:
            body = {"professional_network_profile_urls": [url], "fields": ["basic_profile", "experience"]}
            p.call("POST", "/person/enrich", "person_enrich", key, body)
        else:
            print("     no CXO profile URL found in people.cxos; skipping person_enrich")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="domain or name of a single company to probe")
    ap.add_argument("--skip", default="", help="comma list of endpoints to skip")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    todo = [c for c in COMPANIES if not c["name"].startswith("CHANGE_ME")]
    if len(todo) < len(COMPANIES):
        print("NOTE: skipping CHANGE_ME placeholders; edit COMPANIES #4/#6 in scripts/probe.py")
    if a.only:
        todo = [c for c in todo if a.only in (c["domain"], c["name"])]
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}

    p = Probe(load_key() if not a.dry_run else "dry", a.dry_run)
    before = p.credits()
    print(f"credits before: {before}")
    try:
        for c in todo:
            probe_company(p, c, skip)
    finally:
        after = p.credits()
        print(f"\ncredits after: {after}   spent: "
              f"{round(before - after, 3) if before is not None and after is not None else '?'}")
        by_ep: dict[str, float] = {}
        for row in LOG:
            try:
                by_ep[row["endpoint"]] = by_ep.get(row["endpoint"], 0) + float(row.get("x_credits_used") or 0)
            except ValueError:
                pass
        print("X-Credits-Used by endpoint:", json.dumps(by_ep, indent=1))
        p.finish()
        print(f"log -> {FIX / '_probe_log.json'}")


if __name__ == "__main__":
    main()
