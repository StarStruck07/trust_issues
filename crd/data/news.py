"""Owner: Nilay (P2). News evidence: candidates -> fetch -> classify -> namesake filter (NILAY_data.md section 8).

Candidates
    Enrich news[] (direct article URLs with publish dates; free, already fetched) plus Web Search
    for '"<legal name>"' and '"<brand> <city>"', bounded to the last 120 days with start/end dates.
    Web Search returns no publish date and often google.com/goto redirect URLs, which cannot be
    fetched: those are dropped, and social-media posts are skipped.
Fetch
    The top 3 candidates in one Web Fetch call. Headlines with risk words go first, then the most recent.
    If an article page fails to load, it is classified on its headline alone, and the evidence says so.
Classify
    llm.complete_json(NewsClass), or the keyword fallback when no LLM is available.
Namesake filter (code, not the model)
    The model's company_named_sentence must appear in the fetched text (whitespace/case-normalised)
    and must name the company: its legal name, its domain, or the brand together with the city.
    Otherwise the article is emitted as relevant=False / category=irrelevant ("namesake filtered").
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from html.parser import HTMLParser
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel

from crd.data.errors import BudgetExceeded, CreditsExhausted, LLMUnavailable
from crd.models import Counterparty, Evidence, ResolvedEntity

WINDOW_DAYS = 120
FETCH_TOP = 3
MAX_TEXT_CHARS = 12000
SKIP_HOSTS = ("google.com", "facebook.com", "linkedin.com", "youtube.com", "instagram.com", "x.com",
              "twitter.com", "t.co")
LEGAL_SUFFIXES = r"\b(private limited|pvt\.? ?ltd\.?|limited|ltd\.?|llp|inc\.?|corporation|corp\.?|co\.?)$"
RISK_WORDS = re.compile(
    r"insolven|nclt|cirp|liquidat|raid|fraud|evasion|layoff|lays off|retrench|shut ?down|shutter|strike|"
    r"fire|flood|halt|default|downgrade|resign|quits|exit|probe|arrest|loss", re.I)
NEUTRAL_RE = re.compile(r"\b(acquir\w*|acquisition|merger|merge[sd]?|demerger|amalgamat\w*)\b", re.I)
STOP = (BudgetExceeded, CreditsExhausted)

Category = Literal["hard_stop", "fraud_or_raid", "layoffs_or_shutdown", "operations_halt",
                   "lost_client_or_downgrade", "leadership_exit", "minor", "positive", "irrelevant"]


class NewsClass(BaseModel):
    relevant: bool
    company_named_sentence: str
    category: Category
    subtype: Optional[str] = None
    event_date: Optional[str] = None


SYSTEM = (
    "You classify one news article for a credit-risk desk at an Indian logistics company. "
    "Decide whether the article is about the named company (not a namesake), and what the article "
    "means for that company's ability to pay its bills or keep operating. "
    "Copy company_named_sentence exactly, character for character, from the article: the sentence "
    "that names the company and states the main event. "
    "Categories: hard_stop = insolvency admitted, CIRP, liquidation, default on debt; fraud_or_raid = "
    "fraud, tax evasion, ED/CBI/GST raid; layoffs_or_shutdown = layoffs, closure of units or the "
    "business; operations_halt = strike, fire, flood, regulatory halt of operations; "
    "lost_client_or_downgrade = lost a major client, credit rating downgrade; leadership_exit = CEO, "
    "CFO, COO or MD left; minor = mildly negative; positive = funding, big contract, growth; "
    "irrelevant = not about this company, or neutral. For an acquisition, merger or demerger story, "
    "use category irrelevant and set subtype to acquisition, merger or demerger. "
    "Set event_date (YYYY-MM-DD) only if the article states it."
)

# keyword fallback: order matters (a merger approved by NCLT is not an insolvency)
FALLBACK_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\binsolvency\b|\bNCLT\b|\bCIRP\b|\bliquidation\b", re.I), "hard_stop"),
    (re.compile(r"\braids?\b|\bfraud\b|\bED\b|\bGST evasion\b"), "fraud_or_raid"),
    (re.compile(r"\blayoffs?\b|\blays off\b|\bretrench\w*|\bshut(s|ting)? down\b|\bshutter\w*", re.I),
     "layoffs_or_shutdown"),
    (re.compile(r"\bstrikes?\b(?! (a |an |the )?(deal|partnership|pact|agreement|chord|gold))|\bfire\b|"
                r"\bflood\w*|\bhalt\w*|\bsuspend\w* (operations|services|licen[cs]e)|\blicen[cs]es? (suspended|cancelled)",
                re.I), "operations_halt"),
    (re.compile(r"\bdowngrad\w*|\blos(es|t|ing) (\w+ ){0,2}(client|customer|contract|account)s?\b|"
                r"\bdefaults? on\b|\bdelayed (salaries|salary|payments?)\b|\bunpaid (salaries|dues|vendors)\b", re.I),
     "lost_client_or_downgrade"),
    (re.compile(r"\b(ceo|cfo|coo|md|managing director|chief executive|chief financial|chief operating|founder)\b"
                r"[^.]{0,60}\b(resigns?|resigned|quits?|steps? down|stepped down|exits?|exited)\b|"
                r"\b(resigns?|resigned|quits?|steps? down|stepped down)\b[^.]{0,40}\b(ceo|cfo|coo|md|chief)\b", re.I),
     "leadership_exit"),
]
POSITIVE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(wins?|won|bags?|bagged|secures?|secured|signs?|signed|awarded)\b[^.]{0,50}"
                r"\b(order|contract|deal|mandate)s?\b", re.I), "contract"),
    (re.compile(r"\braises?\b[^.]{0,40}\b(funding|series [a-h]|crore|million|round)\b", re.I), "funding"),
]


# --------------------------------------------------------------------------- entry point
def collect(entity: ResolvedEntity, cp: Counterparty, enrich_resp: dict, client, cfg) -> list[Evidence]:
    from crd.data.signals import _Ctx  # shared make_ev / unavailable helpers

    ctx = _Ctx(cp, entity, client, cfg)
    names = _Names(entity, cp)
    start = ctx.as_of - timedelta(days=WINDOW_DAYS)

    # 1. candidates
    cands: dict[str, dict] = {}
    for n in (enrich_resp or {}).get("news") or []:
        d = _date(n.get("article_publish_date"))
        url = n.get("article_url")
        if url and d and start <= d <= ctx.as_of and not _skip(url):
            cands.setdefault(url, {"url": url, "title": n.get("article_title") or "", "date": d,
                                   "origin": "Company Enrich news"})
    search_errors: list[Exception] = []
    queries = [f'"{names.legal}"']
    if cp.city:
        queries.append(f'"{names.brand}" {cp.city}')
    t0 = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.combine(ctx.as_of, time.max, tzinfo=timezone.utc).timestamp())
    for q in queries:
        try:
            results = ctx.call(client.web_search, q, news=True, limit=10, start_date=t0, end_date=t1)
        except STOP:
            raise
        except Exception as e:  # noqa: BLE001
            search_errors.append(e)
            continue
        for r in results or []:
            url = r.get("url")
            if url and not _skip(url):
                cands.setdefault(url, {"url": url, "title": r.get("title") or "", "date": None,
                                       "snippet": r.get("snippet") or "", "origin": "Web Search"})

    if not cands:
        if search_errors:
            return [ctx.unavailable("news", "News (Web Search)", search_errors[0], "web")]
        return [ctx.make_ev("news", f"No articles naming {names.legal} found in the last {WINDOW_DAYS} days "
                                    f"(Company Enrich news, Web Search)", None, "Company Enrich news + Web Search",
                            None, ctx.as_of, source_type="web", status="empty")]

    # 2. fetch the top few (risk headlines first, then most recent)
    def rank(c: dict):
        return (0 if RISK_WORDS.search(c["title"]) else 1, -(c["date"].toordinal() if c["date"] else 0))
    top, seen_titles = [], set()
    for c in sorted(cands.values(), key=rank):         # same story under two URLs counts once
        t = _norm_ws(c["title"])
        if t and t in seen_titles:
            continue
        seen_titles.add(t)
        top.append(c)
        if len(top) >= FETCH_TOP:
            break
    try:
        pages = ctx.call(client.web_fetch, [c["url"] for c in top]) or []
    except STOP:
        raise
    except Exception as e:  # noqa: BLE001
        return [ctx.unavailable("news", "News (Web Fetch)", e, "web")]
    raw = getattr(client, "last_raw_ref", None)
    by_url = {p.get("url"): p for p in pages if isinstance(p, dict)}

    # 3-5. classify, filter, emit
    out: list[Evidence] = []
    for c in top:
        page = by_url.get(c["url"]) or {}
        text = html_to_text(page.get("content") or "") if page.get("success") else ""
        headline_only = not text.strip()
        if headline_only:
            text = ". ".join(x for x in (c["title"], c.get("snippet", "")) if x)
        title = c["title"] or page.get("title") or c["url"]
        cls, how = classify(title, text, names, cp, entity, ctx.run_id)
        cls, note = namesake_filter(cls, text, names, cp.city)
        out.append(_emit(ctx, c, title, cls, raw, how, note, headline_only))
        if cls.relevant and cls.category == "positive" and "contract" in (cls.subtype or "").lower():
            out.append(ctx.make_ev("positive", title, {"kind": "big_contract"}, f"{c['origin']} + Web Fetch",
                                   "content", c["date"], source_type="web", url=c["url"],
                                   passage=_passage(cls.company_named_sentence, names), raw_ref=raw))
    return out


# --------------------------------------------------------------------------- classification
def classify(title: str, text: str, names: "_Names", cp: Counterparty, entity: ResolvedEntity,
             run_id: str) -> tuple[NewsClass, str]:
    from crd import llm
    user = (f"Company legal name: {entity.legal_name or cp.legal_name or cp.name}\n"
            f"Brand: {cp.name}\nDomain: {entity.domain or cp.domain or 'unknown'}\n"
            f"City: {cp.city or 'unknown'}\nHeadline: {title}\n"
            f"<document>\n{text[:MAX_TEXT_CHARS]}\n</document>")
    try:
        return llm.complete_json(SYSTEM, user, NewsClass, run_id), "llm"
    except LLMUnavailable:
        return keyword_classify(title, text, names), "keywords"
    except Exception:  # noqa: BLE001  (never let the model break news)
        return keyword_classify(title, text, names), "keywords"


def keyword_classify(title: str, text: str, names: "_Names") -> NewsClass:
    sentence = names.first_named_sentence(text) or (title if names.mentions(title) else "")
    scope = f"{title} {sentence}"
    m = NEUTRAL_RE.search(scope)
    if m:
        word = m.group(1).lower()
        sub = "demerger" if word.startswith("demerg") else "merger" if word.startswith(("merg", "amalg")) \
            else "acquisition"
        return NewsClass(relevant=bool(sentence), company_named_sentence=sentence, category="irrelevant", subtype=sub)
    for rx, cat in FALLBACK_RULES:
        if rx.search(scope):
            return NewsClass(relevant=bool(sentence), company_named_sentence=sentence, category=cat)
    for rx, sub in POSITIVE_RULES:
        if rx.search(scope):
            return NewsClass(relevant=bool(sentence), company_named_sentence=sentence, category="positive",
                             subtype=sub)
    # no risk keyword: about the company but neutral (keywords can't tell "minor" negatives apart)
    return NewsClass(relevant=bool(sentence), company_named_sentence=sentence, category="irrelevant")


def namesake_filter(cls: NewsClass, text: str, names: "_Names", city: str | None) -> tuple[NewsClass, str]:
    sent = _norm_ws(cls.company_named_sentence)
    if not sent:
        return cls.model_copy(update={"relevant": False, "category": "irrelevant"}), "namesake filtered: no named sentence"
    if sent not in _norm_ws(text):
        return cls.model_copy(update={"relevant": False, "category": "irrelevant"}), \
            "namesake filtered: sentence not found in article"
    if not names.names_company(cls.company_named_sentence, city):
        return cls.model_copy(update={"relevant": False, "category": "irrelevant"}), \
            "namesake filtered: sentence does not name this company"
    if not cls.relevant and cls.category not in ("irrelevant",):
        cls = cls.model_copy(update={"category": "irrelevant"})
    return cls, ""


# --------------------------------------------------------------------------- emit
def _emit(ctx, c: dict, title: str, cls: NewsClass, raw: str | None, how: str, note: str,
          headline_only: bool) -> Evidence:
    extra = []
    if note:
        extra.append(note)
    if headline_only:
        extra.append("article text not fetched; classified on headline")
    if c["date"] is None:
        extra.append("publish date unknown (Web Search window)")
    claim = title + (f" [{'; '.join(extra)}]" if extra else "")
    passage = _passage(cls.company_named_sentence, ctx_names(ctx)) if cls.company_named_sentence else None
    return ctx.make_ev(
        "news", claim[:300],
        {"category": cls.category, "subtype": cls.subtype, "relevant": cls.relevant,
         "event_date": cls.event_date, "classified_by": how, "namesake_filtered": bool(note)},
        f"{c['origin']} + Web Fetch", "content", c["date"], source_type="web", url=c["url"],
        passage=passage, raw_ref=raw)


def ctx_names(ctx) -> "_Names":
    return _Names(ctx.entity, ctx.cp)


def _passage(sentence: str, names: "_Names", max_words: int = 14) -> str:
    """At most 14 words of the named sentence, centred on the company name."""
    words = _norm_ws(sentence, lower=False).split()
    if len(words) <= max_words:
        return " ".join(words)
    lw = [re.sub(r"\W", "", w.lower()) for w in words]
    first = names.tokens[0] if names.tokens else ""
    i = next((k for k, w in enumerate(lw) if first and w.startswith(first)), 0)
    lo = max(0, min(i - max_words // 2, len(words) - max_words))
    return " ".join(words[lo:lo + max_words])


# --------------------------------------------------------------------------- names
class _Names:
    def __init__(self, entity: ResolvedEntity, cp: Counterparty):
        self.legal = (entity.legal_name or cp.legal_name or cp.name).strip()
        self.brand = cp.name.strip()
        self.brand_core = _core(self.brand)
        # legal name(s); when none is known, the brand is the only name we have
        self.cores = {c for c in (_core(entity.legal_name), _core(cp.legal_name)) if c} or {self.brand_core}
        domain = (entity.domain or cp.domain or "").lower().removeprefix("www.")
        self.domain = domain
        self.tokens = (sorted(self.cores, key=len)[0] if self.cores else self.brand_core).split()

    def _has(self, name: str, s: str) -> bool:
        return bool(name) and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", s) is not None

    def names_company(self, sentence: str, city: str | None) -> bool:
        s = _norm_ws(sentence)
        if any(self._has(c, s) for c in self.cores):
            return True
        if self.domain and self.domain in s:
            return True
        return bool(city) and self._has(self.brand_core, s) and self._has(city.lower(), s)

    def mentions(self, s: str) -> bool:
        s = _norm_ws(s)
        return any(self._has(c, s) for c in self.cores | {self.brand_core}) or bool(self.domain and self.domain in s)

    def first_named_sentence(self, text: str) -> str:
        for sent in _sentences(text):
            if self.mentions(sent):
                return sent
        return ""


def _core(name: str | None) -> str:
    if not name:
        return ""
    s = _norm_ws(name)
    for _ in range(2):
        s = re.sub(LEGAL_SUFFIXES, "", s).strip(" ,.")
    return s


# --------------------------------------------------------------------------- text helpers
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "head", "svg", "nav", "footer", "form", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    if "<" not in html:
        return html
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", html)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in "".join(p.parts).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", line) if s.strip()]
    return out


def _norm_ws(s: str | None, lower: bool = True) -> str:
    s = re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return s.lower() if lower else s


def _date(v) -> "datetime.date | None":
    from crd.data.signals import _date as d
    return d(v)


def _skip(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)
