"""Owner: Nilay (P2). news.collect: namesake filter, categories, passages, failures."""
import pytest

from crd import config, llm
from crd.data import news
from crd.data.errors import LLMUnavailable, SourceUnavailable
from tests.helpers import cp, resolved


def page(url, body):
    return {"success": True, "url": url, "title": "t", "content": f"<html><body><p>{body}</p></body></html>"}


class FakeNewsClient:
    run_id = "run-n"
    last_raw_ref = "var/runs/run-n/raw/web_fetch_x.json"

    def __init__(self, pages=None, search=None, fetch_error=None):
        self.pages = pages or {}
        self.search = search or []
        self.fetch_error = fetch_error
        self.fetched = []

    def web_search(self, query, **kw):
        return self.search

    def web_fetch(self, urls, **kw):
        if self.fetch_error:
            raise self.fetch_error
        self.fetched.append(list(urls))
        return [self.pages.get(u, {"success": False, "url": u, "content": None}) for u in urls]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("CRD_AS_OF", "2026-11-22")
    monkeypatch.setenv("CRD_MOCK", "1")          # LLM off -> keyword fallback, deterministic


def enrich_news(*items):
    return {"news": [{"article_url": u, "article_title": t, "article_publish_date": d} for u, t, d in items]}


ENT = resolved(legal_name="Acme Logistics Private Limited", domain="acme.in")
CP = cp(name="Acme Logistics", city="Pune")


def run(client, enrich):
    return news.collect(ENT, CP, enrich, client, config.get())


def test_real_layoff_story_is_categorised_with_short_passage():
    url = "https://example.news/acme-layoffs"
    body = ("Pune, 10 Nov. Acme Logistics Private Limited lays off 300 warehouse staff across three "
            "western India hubs after losing volumes, according to people familiar with the matter. Other text.")
    c = FakeNewsClient(pages={url: page(url, body)})
    evs = run(c, enrich_news((url, "Acme Logistics lays off 300", "2026-11-10")))
    e = evs[0]
    assert e.signal == "news" and e.status == "ok" and e.source_type == "web" and e.url == url
    assert e.value["category"] == "layoffs_or_shutdown" and e.value["relevant"] is True
    assert e.record_date.isoformat() == "2026-11-10" and e.raw_ref
    assert e.passage and len(e.passage.split()) < 15 and "Acme" in e.passage


def test_namesake_article_is_filtered():
    url = "https://example.news/other-acme"
    body = "Acme Steel Works in Ludhiana shuts down its plant after a fire. The steelmaker employs 40 people."
    c = FakeNewsClient(pages={url: page(url, body)})
    evs = run(c, enrich_news((url, "Acme plant shuts down after fire", "2026-11-01")))
    e = evs[0]
    assert e.value["relevant"] is False and e.value["category"] == "irrelevant"
    assert "namesake filtered" in e.claim and e.value["namesake_filtered"] is True


def test_llm_sentence_not_in_article_is_filtered(monkeypatch):
    url = "https://example.news/acme"
    c = FakeNewsClient(pages={url: page(url, "Acme Logistics Private Limited opened a new hub in Pune.")})

    def fake_llm(system, user, schema, run_id, **kw):
        return schema(relevant=True, company_named_sentence="Acme Logistics Private Limited defaulted on loans.",
                      category="hard_stop")
    monkeypatch.setattr(llm, "complete_json", fake_llm)
    e = run(c, enrich_news((url, "Acme opens hub", "2026-11-05")))[0]
    assert e.value["relevant"] is False and e.value["category"] == "irrelevant"
    assert "sentence not found" in e.claim


def test_acquisition_story_is_irrelevant_with_subtype():
    url = "https://example.news/acme-acq"
    body = "Global Freight Inc has agreed to acquire Acme Logistics Private Limited for Rs 900 crore. More."
    c = FakeNewsClient(pages={url: page(url, body)})
    e = run(c, enrich_news((url, "Global Freight to acquire Acme Logistics", "2026-10-20")))[0]
    assert e.value["category"] == "irrelevant" and e.value["subtype"] == "acquisition"
    assert e.value["relevant"] is True


def test_nclt_merger_approval_is_not_a_hard_stop():
    url = "https://example.news/acme-merger"
    body = "The NCLT approved the merger of Acme Logistics Private Limited with its parent company."
    c = FakeNewsClient(pages={url: page(url, body)})
    e = run(c, enrich_news((url, "NCLT approves Acme merger", "2026-10-20")))[0]
    assert e.value["category"] == "irrelevant" and e.value["subtype"] == "merger"


def test_fetch_failure_is_unavailable_not_no_news():
    url = "https://example.news/acme"
    c = FakeNewsClient(fetch_error=SourceUnavailable("timeout x3"))
    evs = run(c, enrich_news((url, "Acme Logistics expands", "2026-11-10")))
    assert len(evs) == 1 and evs[0].status == "unavailable" and evs[0].signal == "news"


def test_search_failure_with_no_other_candidates_is_unavailable():
    class Broken(FakeNewsClient):
        def web_search(self, query, **kw):
            raise SourceUnavailable("forced by CRD_FAIL_SOURCES")
    evs = run(Broken(), {"news": []})
    assert evs[0].status == "unavailable"


def test_no_articles_is_empty_not_ok():
    evs = run(FakeNewsClient(), {"news": []})
    assert evs[0].status == "empty" and "No articles" in evs[0].claim


def test_old_and_social_articles_are_excluded_and_top3_fetched():
    items = [(f"https://example.news/a{i}", f"Acme Logistics update {i}", f"2026-11-{10 + i:02d}") for i in range(5)]
    items += [("https://example.news/old", "Acme Logistics old story", "2026-05-01"),
              ("https://www.linkedin.com/posts/x", "Acme Logistics post", "2026-11-20"),
              ("https://example.news/risk", "Acme Logistics hit by strike", "2026-10-01")]
    c = FakeNewsClient()
    run(c, enrich_news(*items))
    fetched = c.fetched[0]
    assert len(fetched) == 3 and fetched[0] == "https://example.news/risk"   # risk headline first
    assert "https://example.news/old" not in fetched and not any("linkedin" in u for u in fetched)


def test_failed_page_falls_back_to_headline():
    url = "https://example.news/acme"
    e = run(FakeNewsClient(), enrich_news((url, "Acme Logistics workers go on strike in Pune", "2026-11-12")))[0]
    assert e.value["category"] == "operations_halt" and "classified on headline" in e.claim


def test_routine_story_is_neutral_without_llm():
    url = "https://example.news/acme-profit"
    body = "Acme Logistics Private Limited shares soar 10% after reporting a quarterly profit."
    e = run(FakeNewsClient(pages={url: page(url, body)}), enrich_news((url, "Acme shares soar", "2026-11-01")))[0]
    assert e.value["relevant"] is True and e.value["category"] == "irrelevant" and e.value["subtype"] is None


def test_html_to_text_drops_scripts():
    t = news.html_to_text("<html><head><title>x</title></head><body><script>var a=1</script><p>Hello.</p></body></html>")
    assert t == "Hello."


def test_same_story_under_two_urls_counts_once():
    items = [("https://a.news/x", "Acme Logistics wins big order", "2026-11-10"),
             ("https://b.news/y", "Acme Logistics wins big order", "2026-11-10"),
             ("https://c.news/z", "Acme Logistics opens hub", "2026-11-09")]
    c = FakeNewsClient()
    run(c, enrich_news(*items))
    assert len(c.fetched[0]) == 2
