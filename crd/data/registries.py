"""Owner: Nilay (P2). Indian registries: hard-stop checks (NILAY_data.md section 9).

What works (checked 2026-11-22):
    IBBI public announcements   https://ibbi.gov.in/public-announcement?title=<name>
        Public GET listing, no login or CAPTCHA. Columns: type of PA, date of announcement, last date of
        submission, name of corporate debtor, applicant, insolvency professional, PDF link. Free.
    MCA company master data     Blocks automated access (HTTP 403) and needs a CAPTCHA. Not checked.
                                 legal_status comes from Crustdata's company record instead
                                 (signals.legal_status_from_enrich), and its claim says MCA was not checked.
    GST taxpayer search          JavaScript app behind a CAPTCHA. Not checked: reported as status "empty"
                                 (blocked by design, not a failed check, so it does not hold a rating).
We never bypass a CAPTCHA or login.

IBBI rules
    - Match on the normalised legal name (suffixes like "Private Limited" removed).
    - One exact match within the last 2 years -> "cirp_admitted" or "liquidation" (hard stop).
      Older announcements are reported as historical_* (shown, not a hard stop: a CIRP from years ago may
      have been resolved). record_date = the day we read the registry; the announcement date is in value.
    - 2+ different debtors with a similar name -> status "empty", "ambiguous registry match: N companies".
    - No match -> value "none_found": "No IBBI announcement found for <name> as of <date>". A weak positive:
      list it in scoring.coverage_exclude (already there).
"""
from __future__ import annotations

import re
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import quote_plus

from crd.data.errors import BudgetExceeded, CreditsExhausted
from crd.models import Counterparty, Evidence, ResolvedEntity

IBBI_URL = "https://ibbi.gov.in/public-announcement?title={q}"
MCA_URL = "https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS.html"
GST_URL = "https://services.gst.gov.in/services/searchtp"
HARD_STOP_YEARS = 2
SUFFIX_RE = re.compile(
    r"\b(private limited|pvt\.? ?ltd\.?|pvt\.?|private|limited|ltd\.?|llp|opc|\(opc\)|india|inc\.?|corporation|corp\.?|"
    r"company|co\.?)\s*$")
STOP = (BudgetExceeded, CreditsExhausted)

MCA_NOTE = "MCA not checked: portal blocks automated access and requires a CAPTCHA (not bypassed)"
GST_NOTE = "GST registration status not checked: taxpayer search requires a CAPTCHA (not bypassed)"


def check(entity: ResolvedEntity, cp: Counterparty, client, cfg) -> list[Evidence]:
    from crd.data.signals import _Ctx

    ctx = _Ctx(cp, entity, client, cfg)
    out: list[Evidence] = []
    try:
        out.append(ibbi(ctx))
    except STOP:
        raise
    except Exception as e:  # noqa: BLE001
        out.append(ctx.unavailable("insolvency", "IBBI public announcements", e, "registry"))
    if cp.gstin:
        out.append(ctx.make_ev("legal_status", GST_NOTE, None, "GST portal", "sts", None,
                               source_type="registry", url=GST_URL, status="empty"))
    return out


def lookup_by_id(cin: str | None = None, gstin: str | None = None, client=None) -> dict | None:
    """MCA (CIN) and GST (GSTIN) lookups both need a CAPTCHA, so there is no automated answer: None."""
    return None


# --------------------------------------------------------------------------- IBBI
def ibbi(ctx) -> Evidence:
    legal = ctx.cp.legal_name or ctx.entity.legal_name or ctx.cp.name
    core = normalise(legal)
    if len(core) < 3:
        return ctx.make_ev("insolvency", f"IBBI check skipped: name '{legal}' too short to search", None,
                           "IBBI public announcements", None, None, source_type="registry", status="empty")
    url = IBBI_URL.format(q=quote_plus(core))
    page = ctx.call(ctx.client.public_get, url, key=core, day=ctx.as_of.isoformat())
    raw = getattr(ctx.client, "last_raw_ref", None)
    status = page.get("status")
    if status != 200:
        raise RuntimeError(f"IBBI returned HTTP {status}")
    rows = parse_ibbi(page.get("html") or "")
    exact = [r for r in rows if normalise(r["debtor"]) == core]
    # one company can appear in several announcements, in different capitalisation: count distinct names
    similar: dict[str, str] = {}
    for r in rows:
        n = normalise(r["debtor"])
        if r not in exact and _similar(core, n):
            similar.setdefault(n, r["debtor"])
    debtors_exact = {normalise(r["debtor"]) for r in exact}

    if len(debtors_exact) > 1 or (not exact and len(similar) >= 2):
        names = sorted({r["debtor"].upper() for r in exact} if len(debtors_exact) > 1 else similar.values())
        return ctx.make_ev("insolvency", f"ambiguous registry match: {len(names)} companies ({'; '.join(names[:3])})",
                           {"status": "ambiguous", "candidates": names[:10]}, "IBBI public announcements",
                           "Name of Corporate Debtor", ctx.as_of, source_type="registry", url=url, raw_ref=raw,
                           status="empty")
    if not exact:
        note = f" (1 similar name not matched: {next(iter(similar.values()))})" if similar else ""
        return ctx.make_ev("insolvency", f"No IBBI announcement found for {legal} as of {ctx.as_of.isoformat()}{note}",
                           "none_found", "IBBI public announcements", "Name of Corporate Debtor", ctx.as_of,
                           source_type="registry", url=url, raw_ref=raw)

    latest = max(exact, key=lambda r: r["date"] or ctx.as_of.replace(year=1900))
    kind = _kind(latest["type"])
    recent = latest["date"] and latest["date"] >= ctx.as_of - timedelta(days=365 * HARD_STOP_YEARS)
    value = kind if recent else f"historical_{kind}"
    when = latest["date"].isoformat() if latest["date"] else "unknown date"
    claim = f"IBBI: {latest['type']} for {latest['debtor']} announced {when}"
    if not recent:
        claim += f" (over {HARD_STOP_YEARS} years ago; not treated as a hard stop)"
    return ctx.make_ev("insolvency", claim,
                       {"status": value, "type": latest["type"], "announced": when, "applicant": latest["applicant"],
                        "announcement_pdf": latest["pdf"]},
                       "IBBI public announcements", "Type of PA", ctx.as_of, source_type="registry",
                       url=latest["pdf"] or url, raw_ref=raw)


def _kind(pa_type: str) -> str:
    t = pa_type.lower()
    if "liquidation" in t:
        return "liquidation"          # includes voluntary liquidation: the company is being wound up
    return "cirp_admitted"            # CIRP and pre-pack are both insolvency resolution processes


def normalise(name: str | None) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower().replace("&", " and "))
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(3):
        s2 = SUFFIX_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    return s


def _similar(a: str, b: str) -> bool:
    return bool(a and b) and (re.search(rf"(?<!\w){re.escape(a)}(?!\w)", b) is not None
                              or re.search(rf"(?<!\w){re.escape(b)}(?!\w)", a) is not None)


class _Table(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.links: list[list[str]] = []
        self.in_body = self.in_td = False
        self.cur: list[str] | None = None
        self.cur_links: list[str] = []
        self.buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self.in_body = True
        elif tag == "tr" and self.in_body:
            self.cur, self.cur_links = [], []
        elif tag == "td" and self.cur is not None:
            self.in_td, self.buf = True, []
        elif tag == "a" and self.in_td:
            href = dict(attrs).get("href")
            if href:
                self.cur_links.append(href)

    def handle_endtag(self, tag):
        if tag == "td" and self.in_td and self.cur is not None:
            self.cur.append(re.sub(r"\s+", " ", "".join(self.buf)).strip())
            self.in_td = False
        elif tag == "tr" and self.cur is not None:
            if self.cur:
                self.rows.append(self.cur)
                self.links.append(self.cur_links)
            self.cur = None
        elif tag == "tbody":
            self.in_body = False

    def handle_data(self, data):
        if self.in_td:
            self.buf.append(data)


def parse_ibbi(html: str) -> list[dict]:
    """Rows of the public-announcement table: type, date, debtor, applicant, IP, pdf."""
    from crd.data.signals import _date
    p = _Table()
    p.feed(html)
    out = []
    for cells, links in zip(p.rows, p.links):
        if len(cells) < 4 or "announcement" not in cells[0].lower():
            continue
        d = None
        m = re.match(r"(\d{2})-(\d{2})-(\d{4})", cells[1])
        if m:
            d = _date(f"{m.group(3)}-{m.group(2)}-{m.group(1)}")
        pdf = next((h for h in links if h.lower().endswith(".pdf")), None)
        out.append({"type": cells[0], "date": d, "debtor": cells[3],
                    "applicant": cells[4] if len(cells) > 4 else "", "pdf": pdf})
    return out
