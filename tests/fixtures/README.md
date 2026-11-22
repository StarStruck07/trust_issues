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
