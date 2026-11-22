# Fixtures (P2 — Nilay)

Recorded by `scripts/probe.py` on 2026-11-22 against `https://api.crustdata.com`, `x-api-version: 2025-11-01`.
Layout: `tests/fixtures/<endpoint>/<key>.json`, where key = domain or slugified name. Non-200 responses are saved as `<key>.<status>.json`.
`_probe_log.json` holds the request, status, latency, `X-Credits-Used` and rate-limit headers for every call.

Companies: delhivery.com (#1), bluedart.com (#2), mamaearth.in (#3), "Sharma Transport" (#5, identify only).
#4 (small private trucking firm) and #6 (negative news after 1 Sep 2026) are still TODO.

Probe run cost: **15.83 credits** (balance 15200.00 → 15184.17), which matches the sum of `X-Credits-Used`.

## Endpoints

| Endpoint | Path used | Credits / call (measured) | Fields often empty for Indian private firms | Notes |
|---|---|---|---|---|
| identify | `POST /company/identify` | **0.2** per matched identifier | `primary_domain` on name matches | `{"domains":[..]}` or `{"names":[..]}`. ~0.9–4.4 s |
| enrich | `POST /company/enrich` | **2** (with 9 field groups) | `funding.*` (Mamaearth: all null), `hiring.openings_count`, `hiring.recent_openings` (empty for all 3) | 3–31 s (first call 31 s, so use a timeout ≥ 45 s) |
| job_search | `POST /job/search` | **0.03 per result returned** (50 → 1.5; 1 → 0.03) | n/a | filter `{"field":"company.basic_info.crustdata_company_id","type":"=","value":<id>}` works. Keep `limit` small or use `aggregations` |
| web_search | `POST /web/search/live` | **1** per page; **0** when there are no results | n/a | `{"query":"\"<name>\"","sources":["news"],"location":"IN"}`. Results are `{position,title,url,snippet,source}`, **no date**. Blue Dart returned 0 results |
| web_fetch | `POST /web/enrich/live` | **1** per URL | n/a | returns `[{success,url,timestamp,title,content(html)}]` |
| person_enrich | `POST /person/enrich` | **1** | | `{"professional_network_profile_urls":[..],"fields":["basic_profile","experience"]}` |
| credits | `GET /account/credits` | 0 | n/a | `account.credits` |
| watch (entity) | `POST /watch/company` | baseline free; 5 per notification at 30-day refresh (docs, not measured) | n/a | test: `POST /watch/company/{id}/test` (free) |

**Rate limits are lower than the docs say:** our key returns `x-ratelimit-limit: 5` (per 60 s) for identify, enrich, job_search and person_enrich, and `10` for web_search and web_fetch. Headers: `x-ratelimit-limit`, `x-ratelimit-remaining`, `x-ratelimit-reset` (seconds). The client's token buckets should use 5/min and 10/min.

## Observations

- **`updated_at` / `indexed_at` are not returned** by enrich, even when requested in `fields`. For `record_date` use the **last `headcount.timeseries[].date`** (e.g. `2026-11-16`, 6 days old, format `YYYY-MM-DD`). Each CXO has its own `updated_at` (ISO, e.g. `2026-11-05T07:44:08.900Z`).
- **News dates:** Enrich `news[].article_publish_date` is `YYYY-MM-DD`. Enrich gives about 100 items with direct article URLs, `source: google_news`, and a `confidence_score`. Web Search news results have **no date** and their URLs are often `https://www.google.com/goto?url=…` redirects. Fetching those returns the Google page (title "Google"), not the article.
  → **Use Enrich `news[]` as the main candidate source.** Use Web Search only as a supplement, and drop or resolve `google.com/goto` URLs.
- **Lookalike "Sharma Transport":** 5+ matches. One has confidence **1.0** (id 4263566, no domain); then "Sharma Transports" 0.95 (sharmatransports.com), and "SHARMA TRANSPORT LTD", "SHARMA TRANSPORT SERVICES" and "Sharma Transport (Regd)" at 0.8. **A 1.0 score does not mean the match is unique.** Resolution must treat multiple matches ≥ 0.8 as ambiguous.
- **People data is noisy:** Mamaearth `people.cxos` lists 3 "Chief Executive Officer" entries, some stale (`updated_at` 2025-09). Delhivery has 19 cxos, mostly non-CEO/CFO titles. Keep the title filter and the company-id check in `people.py`. Person Enrich `experience.employment_details.{current,past}[]` has `crustdata_company_id`, `start_date`, `end_date`, `title` and `name`.
- **Job Search:** `metadata.date_added` is `YYYY-MM-DDTHH:MM:SS`, with no timezone. Delhivery has 790 listings and Blue Dart 1672, but Enrich `hiring.openings_count` shows 12 for Delhivery. Use Job Search for hiring counts, with a small `limit` plus date filters to control cost.

## Field names that differ from the plan (§4.1)

- Web search is `/web/search/live`; web fetch is `/web/enrich/live`.
- `employee_reviews.overall_rating` is an object `{rating, total_count, rating_1_count..rating_5_count}`. There is no `review_count`.
- Web traffic is `web_traffic.domain_traffic.<domain>.{monthly_visitors, mom_pct, qoq_pct}`, with several domains per company. Pick the company's `primary_domain`.
- Headcount growth is `headcount.growth_percent.{mom,qoq,six_months,yoy,two_years}`, in percent (e.g. 2.84 = +2.84%). `by_role_percent` keys are role names such as `Operations` and `Business Development`.
- `hiring.openings_growth_percent` is a fraction (0.2 = 20%), unlike `headcount.growth_percent`.
- Enrich `basic_info` has `status`, which may be useful for legal-status hints.
- Identify and enrich responses are a top-level array: `[{matched_on, match_type, matches:[{confidence_score, company_data}]}]`.
- Webhook signature header: `x-crustdata-signature: t=<unix>,v1=<hex>`, HMAC-SHA256 over `"<t>.<raw body>"`, keyed by the API key (docs).

## Registries (checked 2026-11-22)

| Registry | Reached how | Works? | Cost | Notes |
|---|---|---|---|---|
| IBBI public announcements | `GET https://ibbi.gov.in/public-announcement?title=<name>` via `client.public_get` | **Yes**, no login or CAPTCHA | free (ledger provider `registry`) | Table: type of PA, date, debtor, applicant, IP, PDF. Fixtures in `registries/` (`noel-pharma.json` = a real CIRP of 25 Jul 2026) |
| MCA company master data | portal | **No**: HTTP 403 to automated access, plus a CAPTCHA | n/a | Not bypassed. `legal_status` comes from Crustdata `basic_info.status`, and its claim says MCA was not checked |
| GST taxpayer search | portal | **No**: JavaScript app behind a CAPTCHA | n/a | Not bypassed. Reported as `empty` ("not checked") when a GSTIN is given |

## Costs (measured 2026-11-22, live run on 5 counterparties)

Run `run_e062d856`: Delhivery, Blue Dart, Mamaearth, Ecom Express (resolved) + Sharma Transport (ambiguous).
**32.13 credits**, equal to the account balance drop (15184.17 → 15152.04), so the ledger is exact.
Reproduce with `python scripts/costs.py <run_id>`.

| Endpoint | Calls | Credits | Per resolved counterparty | List price (docs) |
|---|---|---|---|---|
| identify (batch) | 2 | 1.00 | 0.25 | 0.2 per matched identifier |
| enrich | 4 | 8.00 | 2.00 | 2 per record (+2 technographics, not used) |
| job_search (25 results max) | 4 | 2.13 | 0.53 | 0.03 per result |
| web_search | 8 | 1.00 | 0.25 | 1 per page; 0 when no results |
| web_fetch (3 URLs per call) | 4 | 12.00 | 3.00 | 1 per page |
| person_enrich (≤3 leaders) | 8 | 8.00 | 2.00 | 1 per record (basic profile + experience) |
| IBBI registry | 4 | 0 | 0 | free |
| **Total** | | **32.13** | **8.03** | |

- Same-day re-run (`run_cd74e09a`): 3.00 credits, cache-hit rate 97% (49/50 calls). The cache lasts
  `CRD_CACHE_TTL_HOURS` (default 24 h), so a weekly review refetches; resume after a kill is free.
- An ambiguous name costs 0.2 credits (identify only) and stops there.
- Ceiling per counterparty: about 11 credits (all 3 leaders enriched, 2 web searches with results).
- LLM: no `ANTHROPIC_API_KEY` in this run, so 0. Estimate with a key: about 4 calls per counterparty
  (3 news + 1 explanation), ~12k input and ~0.7k output tokens → about $0.05 per counterparty at $3/$15 per MTok.
- Costs are reported in **credits only**: Crustdata does not publish a $ price per credit (it is only on the
  logged-in billing page), so `inr_per_credit` stays 0 and the ledger's `inr` column is 0 for Crustdata calls.

### Summary for the README and finance pitch (credits)

| Item | Credits |
|---|---|
| New counterparty, full review | 8.03 average (about 11 at most) |
| Ambiguous name (stops at identify) | 0.2 |
| Same-day re-run or resume after a kill | ~0 (97% from cache) |
| Weekly re-check (cache expires after 24 h, data refetched) | ~8 |
| Watcher notification (only on change) | 5 |
| LLM per counterparty | 0 credits (separate Anthropic bill; ~$0.05 estimated with a key) |
| **50-counterparty weekly review** | **~400** |
| 50 counterparties on Watcher, re-review only on change | 5 + ~8 per changed company; quiet companies 0 |
