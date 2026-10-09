"""Inferring the GTEx region for a disease, from a closed list, cached.

Puck, 2026-10-09: "the target location on the body should also be handled by an
llm call by simply using general knowledge on where the disease is located. we
cache the results so less api calls are used in the future."

Rule 8's fourth paid call. What the guards are for:

* **The closed list.** The model picks one of `run.available_regions()` — the
  GTEx panel this build can read. Anything else is refused and the caller keeps
  its default. It cannot name a tissue GTEx does not have, which is also why
  general knowledge is acceptable here: every possible answer is one the app
  already offers.
* **Never a silent substitution.** Every failure returns an empty tissue with a
  reason. A region chosen for a reader who did not notice is a claim about the
  specificity ring they never agreed to.
* **The cache says it is a cache.** A remembered answer presented as a fresh one
  would misdate a claim the manifest is audited against.

No network: every test here uses a fake client.
"""

from __future__ import annotations

import pytest

from targetring import region_llm
from targetring.region_llm import RegionChooser, RegionOutcome

REGIONS = ["Lung", "Small_Intestine_Terminal_Ileum", "Brain_Cortex", "Whole_Blood"]


class FakeClient:
    model = "claude-opus-5"

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def complete(self, disease, regions, *, max_tokens):
        self.calls += 1
        self.seen = (disease, list(regions))
        return self.outcome


def ok(tissue, reason="because"):
    return RegionOutcome(ok=True, tissue=tissue, reason=reason,
                         model="claude-opus-5", input_tokens=100, output_tokens=20)


class FakeCache:
    def __init__(self): self.store = {}
    def get(self, source, key): return self.store.get((source, key))
    def put(self, source, key, body): self.store[(source, key)] = body


class TestTheClosedList:

    def test_a_tissue_in_the_panel_is_accepted(self) -> None:
        r = RegionChooser(client=FakeClient(ok("Lung"))).choose("COVID-19", REGIONS)
        assert r.tissue == "Lung"
        assert r.cached is False

    def test_a_tissue_outside_the_panel_is_refused(self) -> None:
        """GTEx has no such key; `specificity_cell` would look for one that is
        not there."""
        r = RegionChooser(client=FakeClient(ok("Pancreas_Islets"))).choose(
            "diabetes", REGIONS)
        assert r.tissue == ""
        assert "not one of the 4" in r.reason
        assert r.error == "outside list"

    def test_declining_is_reported_as_declining(self) -> None:
        r = RegionChooser(client=FakeClient(ok("", "no tissue suits"))).choose(
            "x", REGIONS)
        assert r.tissue == ""
        assert r.error == "declined"
        assert "no tissue suits" in r.reason

    def test_a_failed_call_never_substitutes_a_region(self) -> None:
        r = RegionChooser(client=FakeClient(
            RegionOutcome(ok=False, error="no credentials"))).choose("x", REGIONS)
        assert r.tissue == ""
        assert "no credentials" in r.reason

    def test_no_client_is_not_an_error_and_chooses_nothing(self) -> None:
        r = RegionChooser(client=None).choose("x", REGIONS)
        assert r.tissue == ""
        assert "no region chooser is configured" in r.reason


class TestTheCache:

    def test_the_second_ask_does_not_call_the_model(self) -> None:
        client = FakeClient(ok("Lung"))
        ch = RegionChooser(client=client, cache=FakeCache())
        first = ch.choose("COVID-19", REGIONS)
        second = ch.choose("COVID-19", REGIONS)
        assert client.calls == 1, "the cached ask must not spend"
        assert second.tissue == first.tissue == "Lung"

    def test_a_cached_answer_says_it_is_cached(self) -> None:
        """A remembered answer presented as fresh misdates a claim the manifest
        is audited against."""
        ch = RegionChooser(client=FakeClient(ok("Lung")), cache=FakeCache())
        assert ch.choose("COVID-19", REGIONS).cached is False
        again = ch.choose("COVID-19", REGIONS)
        assert again.cached is True
        assert again.chosen_utc, "it must carry when it was first decided"

    def test_a_different_disease_is_a_different_key(self) -> None:
        client = FakeClient(ok("Lung"))
        ch = RegionChooser(client=client, cache=FakeCache())
        ch.choose("COVID-19", REGIONS)
        ch.choose("Crohn disease", REGIONS)
        assert client.calls == 2

    def test_a_changed_panel_invalidates(self) -> None:
        """A tissue list that gained or lost entries can change the right
        answer, so it belongs in the key."""
        a = region_llm.cache_key("x", REGIONS)
        b = region_llm.cache_key("x", REGIONS + ["Liver"])
        assert a != b

    def test_a_changed_prompt_invalidates(self) -> None:
        key = region_llm.cache_key("x", REGIONS)
        assert region_llm.prompt_hash() in key

    def test_the_cache_source_has_a_registered_schema_version(self) -> None:
        """`Cache._path` refuses an unregistered source — found live when this
        route 500ed on its first call."""
        from targetring import config
        assert config.SCHEMA_VERSIONS["region_llm"]

    def test_a_failure_is_not_cached(self) -> None:
        """Caching "the call failed" would make one outage permanent."""
        cache = FakeCache()
        RegionChooser(client=FakeClient(RegionOutcome(ok=False, error="boom")),
                      cache=cache).choose("x", REGIONS)
        assert cache.store == {}


class TestWhatReachesTheModel:

    def test_only_a_disease_label_and_the_public_tissue_list(self) -> None:
        """Rule 10. Nothing Puck typed, no run id, no gene."""
        import json
        sent = json.loads(region_llm.user_content("COVID-19", REGIONS))
        assert set(sent) == {"disease", "tissues"}
        assert sent["disease"] == "COVID-19"
        assert sorted(sent["tissues"]) == sorted(REGIONS)

    def test_the_prompt_bounds_the_sentence(self) -> None:
        """`chooser_llm` was truncated live at too small a budget; this one
        bounds the ask and has room for it."""
        assert "30 words" in region_llm.SYSTEM_PROMPT
        assert "truncated" in region_llm.SYSTEM_PROMPT
        assert RegionChooser(client=None).max_tokens >= 400

    def test_it_returns_one_tissue_and_no_scores(self) -> None:
        assert set(region_llm.RegionChoice.model_fields) == {"tissue", "reason"}
