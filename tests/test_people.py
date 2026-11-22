"""Owner: Nilay (P2). people.leadership_exits: selection, exit window, namesakes, minimal data, purge."""
import json
from datetime import date
from pathlib import Path

import pytest

from crd import config
from crd.data import people
from crd.data.errors import CreditsExhausted, SourceUnavailable
from tests.helpers import resolved

AS_OF = date(2026, 11, 22)
ENT = resolved(cid=5798, domain="delhivery.com", legal_name="Delhivery")


def delhivery_enrich():
    d = json.loads(Path("tests/fixtures/enrich/delhivery.com.json").read_text())
    return d[0]["matches"][0]["company_data"]


def job(name, cid, title, start, end=None):
    return {"name": name, "crustdata_company_id": cid, "title": title, "start_date": start, "end_date": end,
            "location": "Gurugram", "description": "x"}


def person(name, current=(), past=()):
    return {"basic_profile": {"name": name, "headline": "h", "location": "Delhi"},
            "experience": {"employment_details": {"current": list(current), "past": list(past)}},
            "social_handles": {"email": "should-not-be-kept@example.com"}}


class FakePeople:
    run_id = "run-p"
    last_raw_ref = "var/runs/run-p/raw/person_enrich_x.json"

    def __init__(self, by_url=None, error=None):
        self.by_url = by_url or {}
        self.error = error
        self.calls = []

    def person_enrich(self, url, **kw):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.by_url.get(url, {})


def exits(client, enrich=None):
    return people.leadership_exits(ENT, enrich or delhivery_enrich(), client, config.get(), AS_OF)


def test_selects_one_real_leader_per_role():
    picks = people.select_leaders(delhivery_enrich()["people"]["cxos"])
    names = {role: p["basic_profile"]["name"] for role, p in picks}
    assert names == {"CEO": "Sahil Barua", "CFO": "Vivek Pabari", "COO": "Ajith Pai"}


def test_recent_exit_is_emitted_with_minimal_fields():
    cfo_url = next(people.profile_url(p) for r, p in people.select_leaders(delhivery_enrich()["people"]["cxos"])
                   if r == "CFO")
    c = FakePeople({cfo_url: person("Vivek Pabari",
                                    current=[job("Other Freight Ltd", 999, "CFO", "2026-10-15")],
                                    past=[job("Delhivery", 5798, "CFO", "2026-01-01", "2026-10-01")])})
    evs = exits(c)
    assert len(c.calls) == 3
    e = evs[0]
    assert e.signal == "leadership_exit" and e.status == "ok"
    assert e.claim == "CFO Vivek Pabari left Delhivery on 2026-10-01 (now at Other Freight Ltd)"
    assert e.value == {"name": "Vivek Pabari", "title": "CFO", "left_on": "2026-10-01",
                       "new_employer": "Other Freight Ltd"}
    assert e.source == "Person Enrich" and e.field_path == "experience[].end_date" and e.raw_ref
    assert "example.com" not in json.dumps(e.model_dump(mode="json"))


def test_still_employed_and_old_exits_are_not_emitted():
    picks = people.select_leaders(delhivery_enrich()["people"]["cxos"])
    urls = [people.profile_url(p) for _, p in picks]
    c = FakePeople({
        urls[0]: person("Sahil Barua", current=[job("Delhivery", 5798, "CEO", "2011-03-01")]),
        urls[1]: person("Old Exit", current=[job("Elsewhere", 1, "CFO", "2023-01-01")],
                        past=[job("Delhivery", 5798, "CFO", "2020-01-01", "2022-12-01")]),
    })
    assert exits(c) == []


def test_namesake_person_record_is_skipped():
    url = people.profile_url(people.select_leaders(delhivery_enrich()["people"]["cxos"])[0][1])
    c = FakePeople({url: person("Sahil Barua", past=[job("Some Other Co", 4242, "CEO", "2020-01-01", "2026-11-01")])})
    assert exits(c) == []


def test_name_match_used_when_no_company_id():
    p = person("X", past=[job("Delhivery Limited", None, "COO", "2020-01-01", "2026-11-01")])
    ex = people.find_exit(p, ENT.model_copy(update={"crustdata_company_id": None}), "Delhivery", AS_OF)
    assert ex and ex["left_on"] == date(2026, 11, 1)


def test_person_enrich_failure_is_one_unavailable_item():
    evs = exits(FakePeople(error=SourceUnavailable("timeout x3")))
    assert len(evs) == 1 and evs[0].status == "unavailable" and evs[0].signal == "leadership_exit"


def test_credits_exhausted_propagates():
    with pytest.raises(CreditsExhausted):
        exits(FakePeople(error=CreditsExhausted("402")))


def test_no_cxos_means_no_calls():
    c = FakePeople()
    assert exits(c, {"people": {"cxos": []}}) == [] and c.calls == []


def test_purge_people_raw(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw = tmp_path / "var" / "runs" / "run-p" / "raw"
    raw.mkdir(parents=True)
    (raw / "person_enrich_ab12cd34.json").write_text("{}")
    (raw / "enrich_ab12cd34.json").write_text("{}")
    people.purge_people_raw("run-p")
    assert [f.name for f in raw.iterdir()] == ["enrich_ab12cd34.json"]
    people.purge_people_raw("no-such-run")            # no error
