from crd import config
from crd.models import Rating, ResolvedEntity
from crd.scoring import score
from tests.helpers import cp, ev, resolved

CFG = config.get()


def run(evs, cps=None, ent=None, prev=None):
    return score(cps or [cp()], ent or resolved(), evs, CFG, "run_t", prev)


def healthy():
    return [ev("identity", 1.0), ev("headcount_qoq", 4.0), ev("hiring", {"recent": 5, "prior": 9, "distress_roles": []}),
            ev("news", {"category": "minor", "relevant": True}, claim="small fine")]


def test_healthy_is_green():
    a = run([ev("identity", 1.0), ev("headcount_qoq", 4.0), ev("hiring", {"recent": 5, "prior": 9, "distress_roles": []}),
             ev("employee_reviews", {"rating": 4.1, "count": 50})])
    assert a.rating == Rating.GREEN and a.score == 70


def test_thin_evidence_cannot_verify():
    a = run([ev("identity", 1.0), ev("employee_reviews", {"rating": 4.0, "count": 40})])
    assert a.rating == Rating.CANNOT_VERIFY
    assert "Missing" in a.cannot_verify[0] and "headcount_qoq" in a.cannot_verify[0]


def test_headcount_drop_points():
    a = run(healthy() + [ev("headcount_6m", -5.0)] + [ev("headcount_qoq", -25.0)][:0])
    b = run([ev("identity", 1.0), ev("headcount_qoq", -25.0), ev("hiring", {"recent": 2, "prior": 2})])
    s = [x for x in b.signals if x.signal == "headcount_qoq"][0]
    assert s.points == -20 and b.score == 50 and b.rating == Rating.AMBER


def test_cirp_hard_stop():
    a = run(healthy() + [ev("insolvency", "cirp_admitted", source_type="registry")])
    assert a.rating == Rating.RED and a.hard_stop == "cirp_admitted"


def test_struck_off_hard_stop():
    a = run(healthy() + [ev("legal_status", "struck_off", source_type="registry")])
    assert a.rating == Rating.RED and a.hard_stop == "struck_off"


def test_ambiguous_identity():
    ent = ResolvedEntity(cp_ids=["C-1"], status="ambiguous", candidates=[{"name": "A"}, {"name": "B"}])
    a = run([ev("identity", None, status="empty")], ent=ent)
    assert a.rating == Rating.CANNOT_VERIFY and "2 candidates" in a.cannot_verify[0]


def test_identity_unavailable_is_held():
    ent = ResolvedEntity(cp_ids=["C-1"], status="not_found")
    a = run([ev("identity", None, status="unavailable")], ent=ent)
    assert a.rating == Rating.HELD


def test_green_with_news_unavailable_is_held():
    a = run([ev("identity", 1.0), ev("headcount_qoq", 4.0), ev("hiring", {"recent": 5, "prior": 9}),
             ev("news", None, status="unavailable", claim="news unavailable: timeout")])
    assert a.rating == Rating.HELD


def test_acquisition_neutralises_headcount():
    a = run([ev("identity", 1.0), ev("headcount_qoq", -30.0), ev("hiring", {"recent": 3, "prior": 3}),
             ev("news", {"category": "irrelevant", "subtype": "acquisition", "relevant": True})])
    assert a.rating == Rating.GREEN and a.conflicts


def test_positive_cap():
    a = run(healthy() + [ev("positive", {"kind": k}) for k in ("recent_raise", "headcount_growth", "big_contract")]
            + [ev("positive", {"kind": "recent_raise"})])
    p = [x for x in a.signals if x.signal == "positive"][0]
    assert p.points == 15


def test_stale_ignored():
    a = run([ev("identity", 1.0), ev("headcount_qoq", -40.0, stale=True), ev("hiring", {"recent": 5, "prior": 5}),
             ev("employee_reviews", {"rating": 4.0, "count": 30})])
    assert a.rating == Rating.GREEN and any("stale" in c for c in a.conflicts)


def test_duplicate_sums_exposure():
    a = run(healthy(), cps=[cp("C-1", exposure_inr=1_000_000), cp("C-2", exposure_inr=500_000)],
            ent=resolved(("C-1", "C-2")))
    assert a.exposure_inr == 1_500_000 and a.cp_ids == ["C-1", "C-2"]


def test_deterministic_order():
    evs = healthy() + [ev("headcount_6m", -16.0), ev("leadership_exit", {"title": "CFO"})]
    a, b = run(evs), run(list(reversed(evs)))
    assert (a.rating, a.score, [s.points for s in a.signals]) == (b.rating, b.score, [s.points for s in b.signals])


def test_namesake_news_ignored():
    a = run(healthy() + [ev("news", {"category": "layoffs_or_shutdown", "relevant": False})])
    assert a.rating == Rating.GREEN


# --- the graded cases' logic on synthetic evidence (the cases themselves need live data/fixtures)

def test_case02_shutdown_news_is_amber():
    a = run(healthy()[:3] + [ev("news", {"category": "layoffs_or_shutdown", "relevant": True},
                                claim="Tide to wind down its India member business")])
    assert a.rating == Rating.AMBER and a.score == 50


def test_case07_demerger_neutralises_headcount_drop():
    a = run([ev("identity", 1.0), ev("headcount_qoq", -25.0), ev("headcount_6m", -30.0),
             ev("hiring", {"recent": 3, "prior": 3}),
             ev("news", {"category": "irrelevant", "subtype": "demerger", "relevant": True},
                claim="India Glycols demerger effective 1 Sep 2026")])
    assert a.rating != Rating.RED and a.score == 70 and a.conflicts


def test_case08_ceo_exit_is_amber():
    a = run(healthy()[:3] + [ev("news", {"category": "leadership_exit", "relevant": True},
                                claim="MD and CEO resigns")])
    assert a.rating == Rating.AMBER and a.score == 60


def test_ibbi_none_found_not_counted_for_coverage():
    a = run([ev("identity", 1.0), ev("insolvency", "none_found", source_type="registry"),
             ev("employee_reviews", {"rating": 4.0, "count": 30})])
    assert a.rating == Rating.CANNOT_VERIFY
