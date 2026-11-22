from crd import config
from crd.resolve import dedupe, norm, resolve_one
from tests.helpers import FakeClient, cp

CFG = config.get()


def test_norm():
    assert norm("Delhivery Pvt. Ltd.") == norm("DELHIVERY PRIVATE LIMITED") == "delhivery"


def test_domain_resolves():
    c = FakeClient({"acme.in": [("Acme Logistics", "acme.in", 1.0, 7, "Pune")]})
    e, evs = resolve_one(cp(domain="acme.in"), c, "r", CFG)
    assert e.status == "resolved" and e.match_type == "domain" and e.crustdata_company_id == 7
    assert evs[0].signal == "identity" and evs[0].raw_ref


def test_ambiguous_name():
    c = FakeClient({"Sharma Transport": [("Sharma Transport", "a.in", 0.9, 1, "Jaipur"),
                                         ("Sharma Transport Co", "b.in", 0.85, 2, "Delhi")]})
    e, _ = resolve_one(cp(name="Sharma Transport"), c, "r", CFG)
    assert e.status == "ambiguous" and len(e.candidates) == 2


def test_city_breaks_tie():
    c = FakeClient({"Sharma Transport": [("Sharma Transport", "a.in", 0.9, 1, "Jaipur, Rajasthan"),
                                         ("Sharma Transport Co", "b.in", 0.85, 2, "Delhi")]})
    e, _ = resolve_one(cp(name="Sharma Transport", city="Jaipur"), c, "r", CFG)
    assert e.status == "resolved" and e.crustdata_company_id == 1


def test_source_failure_is_unavailable_not_notfound_evidence():
    e, evs = resolve_one(cp(domain="acme.in"), FakeClient(fail=True), "r", CFG)
    assert e.status == "not_found" and evs[0].status == "unavailable"


def test_dedupe_by_company_id_and_pan():
    c = FakeClient({"acme.in": [("Acme", "acme.in", 1.0, 7, "")], "Acme": [("Acme", "acme.in", 0.95, 7, "")]})
    a = cp("C-1", name="Acme", domain="acme.in")
    b = cp("C-2", name="Acme")
    items = [(*resolve_one(x, c, "r", CFG)[:1], x, resolve_one(x, c, "r", CFG)[1]) for x in (a, b)]
    out = dedupe(items)
    assert len(out) == 1 and out[0][0].cp_ids == ["C-1", "C-2"]
    g1 = cp("C-3", name="X", gstin="27AAACX1234A1Z5")
    g2 = cp("C-4", name="X Pvt", gstin="29AAACX1234A1Z9")
    f = FakeClient()
    items = [(*resolve_one(x, f, "r", CFG)[:1], x, []) for x in (g1, g2)]
    assert len(dedupe(items)) == 1


def test_ambiguous_names_never_merged():
    f = FakeClient()
    items = [(*resolve_one(x, f, "r", CFG)[:1], x, []) for x in (cp("C-5", name="Shree"), cp("C-6", name="Shree"))]
    assert len(dedupe(items)) == 2


# ---- against Nilay's real recorded responses (tests/fixtures/identify) ----
import json
import os

FIX = "tests/fixtures/identify"


class FixtureClient:
    last_raw_ref = None

    def __init__(self):
        self.calls = 0

    def identify(self, *, domains=None, names=None):
        self.calls += 1
        out = []
        for k in (domains or names):
            slug = k.lower().replace(" ", "-")
            p = os.path.join(FIX, f"{slug}.json")
            out.extend(json.load(open(p)) if os.path.exists(p) else [{"matched_on": k, "matches": []}])
        return out


def test_real_fixture_domain_and_lookalike():
    from crd.resolve import resolve_all
    c = FixtureClient()
    out = resolve_all([cp("C-1", name="Delhivery", domain="delhivery.com"),
                       cp("C-2", name="Mamaearth", domain="mamaearth.in", role="customer"),
                       cp("C-3", name="Sharma Transport")], c, "r", CFG)
    st = {g[0].cp_ids[0]: g[0] for g in out}
    assert st["C-1"].status == "resolved" and st["C-1"].crustdata_company_id == 5798
    assert st["C-2"].status == "resolved"
    assert st["C-3"].status == "ambiguous" and len(st["C-3"].candidates) == 5
    assert c.calls == 2          # one batched domain call + one batched name call
