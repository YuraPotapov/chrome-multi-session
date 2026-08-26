"""cms_gui.criteria: what a service's own log says about where it has got to.

The whole of this module is one distinction, so most of these are about it: a
rule is a claim about the **log**, not about a line. ``grep "started" && grep
"!ERRORS"`` asks whether the log contains one thing and never contains the other,
which no single line could ever answer.
"""

import pytest

from cms_gui import criteria as cr


def _criterion(*rules, **kwargs):
    kwargs.setdefault("name", "start")
    return cr.CriterionRow(rules=list(rules), **kwargs)


def _match(pattern, kind=cr.TEXT):
    return cr.Rule(cr.MATCH, kind, pattern)


def _exclude(pattern, kind=cr.TEXT):
    return cr.Rule(cr.EXCLUDE, kind, pattern)


# ------------------------------------------------------ the whole-log semantics
def test_a_match_rule_is_satisfied_by_any_line_at_any_point():
    matcher = cr.Matcher(_criterion(_match("started localhost:8069")))
    assert matcher.lit() is False
    matcher.feed("loading modules")
    assert matcher.lit() is False
    matcher.feed("INFO odoo: started localhost:8069")
    assert matcher.lit() is True


def test_a_match_stays_satisfied_once_it_has_been_seen():
    # It is a claim about the log, so a later line cannot un-say it.
    matcher = cr.Matcher(_criterion(_match("started")))
    matcher.feed("started")
    matcher.feed("some other line entirely")
    assert matcher.lit() is True


def test_two_match_rules_need_not_be_on_the_same_line():
    matcher = cr.Matcher(_criterion(_match("loaded"), _match("started")))
    matcher.feed("loaded")
    assert matcher.lit() is False
    matcher.feed("started")
    assert matcher.lit() is True


def test_an_exclude_rule_holds_until_something_trips_it():
    matcher = cr.Matcher(_criterion(_match("started"), _exclude("CRITICAL")))
    matcher.feed("started")
    assert matcher.lit() is True
    matcher.feed("CRITICAL the database is gone")
    assert matcher.lit() is False


def test_a_tripped_exclude_stays_tripped():
    # "the log does not contain CRITICAL" cannot become true again by printing
    # something else afterwards.
    matcher = cr.Matcher(_criterion(_match("started"), _exclude("CRITICAL")))
    matcher.feed("started")
    matcher.feed("CRITICAL boom")
    matcher.feed("carrying on regardless")
    assert matcher.lit() is False


def test_an_exclude_seen_before_the_match_keeps_it_dark():
    matcher = cr.Matcher(_criterion(_match("started"), _exclude("CRITICAL")))
    matcher.feed("CRITICAL boom")
    matcher.feed("started")
    assert matcher.lit() is False


def test_feed_reports_only_the_changes():
    matcher = cr.Matcher(_criterion(_match("started")))
    assert matcher.feed("nothing here") is False
    assert matcher.feed("started") is True
    assert matcher.feed("started again") is False


def test_a_criterion_with_no_rules_is_never_lit():
    assert cr.Matcher(_criterion()).lit() is False


# --------------------------------------------------------------------- reset
def test_a_reset_forgets_the_previous_run():
    matcher = cr.Matcher(_criterion(_match("started")))
    matcher.feed("started")
    matcher.reset()
    assert matcher.lit() is False


def test_a_reset_also_forgets_a_tripped_exclude():
    matcher = cr.Matcher(_criterion(_match("started"), _exclude("CRITICAL")))
    matcher.feed("CRITICAL boom")
    matcher.reset()
    matcher.feed("started")
    assert matcher.lit() is True


def test_a_whole_buffer_can_be_taken_at_once():
    # So that a criterion added to something already running answers for what it
    # has printed, rather than only for whatever it says next.
    matcher = cr.Matcher(_criterion(_match("started")))
    assert matcher.feed_all(["one", "two", "started", "three"]) is True
    assert matcher.lit() is True


# -------------------------------------------------------------------- patterns
def test_text_is_a_substring_and_needs_no_escaping():
    matcher = cr.Matcher(_criterion(_match("localhost:8069")))
    matcher.feed("running on localhost:8069 now")
    assert matcher.lit() is True


def test_regex_is_how_either_of_these_is_said():
    matcher = cr.Matcher(_criterion(_match("ERRORS|CRITICAL", cr.REGEX)))
    matcher.feed("CRITICAL boom")
    assert matcher.lit() is True


def test_matching_is_case_sensitive_like_grep():
    matcher = cr.Matcher(_criterion(_match("Started")))
    matcher.feed("started")
    assert matcher.lit() is False


def test_a_regex_can_ask_for_case_insensitivity():
    matcher = cr.Matcher(_criterion(_match("(?i)started", cr.REGEX)))
    matcher.feed("STARTED")
    assert matcher.lit() is True


def test_a_regex_that_will_not_compile_matches_nothing_rather_than_raising():
    # problems() catches it before Save, but a hand-edited file gets here first,
    # and one bad pattern must not take the service's whole output with it.
    matcher = cr.Matcher(_criterion(_match("a(", cr.REGEX)))
    assert matcher.feed("a(") is False
    assert matcher.lit() is False


def test_an_empty_pattern_matches_nothing():
    assert cr.Matcher(_criterion(_match(""))).feed("anything") is False


# ----------------------------------------------------------------- outstanding
def test_a_dark_criterion_says_what_it_is_waiting_for():
    matcher = cr.Matcher(_criterion(_match("started")))
    assert matcher.outstanding() == ["waiting for 'started'"]


def test_a_dark_criterion_says_what_tripped_it():
    matcher = cr.Matcher(_criterion(_match("started"), _exclude("CRITICAL")))
    matcher.feed("started")
    matcher.feed("CRITICAL boom")
    assert matcher.outstanding() == ["saw 'CRITICAL'"]


def test_a_lit_criterion_is_waiting_for_nothing():
    matcher = cr.Matcher(_criterion(_match("started")))
    matcher.feed("started")
    assert matcher.outstanding() == []


# --------------------------------------------------------------------- colours
def test_a_colour_is_the_colour_it_is_called():
    # The theme's palette is a blueprint one and has no green - theme.OK is a
    # slate blue - so a criterion coloured "green" must not come out blue.
    green = cr.color_of("green")
    assert green != cr.color_of("blue")
    assert green == cr.GREEN


def test_a_colour_is_read_when_it_is_painted_not_when_this_was_imported():
    from cms_gui import theme

    before = cr.color_of("grey")
    theme.set_dark_mode(True)
    try:
        assert cr.color_of("grey") != before      # follows the palette
    finally:
        theme.set_dark_mode(False)
    assert cr.color_of("grey") == before


def test_an_unknown_colour_falls_back_rather_than_failing():
    assert cr.color_of("puce") == cr.color_of(cr.DEFAULT_COLOR)


# ------------------------------------------------------------------ validation
def test_a_criterion_needs_a_name():
    problems = cr.problems(_criterion(_match("x"), name=""), "c")
    assert problems == ["c: a name is required."]


def test_a_criterion_with_no_rules_is_refused():
    assert "at least one rule" in " ".join(cr.problems(_criterion(), "c"))


def test_a_rule_needs_a_pattern():
    assert "a pattern is required" in " ".join(
        cr.problems(_criterion(_match("")), "c"))


def test_a_regex_that_will_not_compile_is_caught_before_it_is_saved():
    problems = cr.problems(_criterion(_match("a(", cr.REGEX)), "c")
    assert problems and "does not compile" in problems[0]


def test_a_text_pattern_is_never_asked_to_compile():
    assert cr.problems(_criterion(_match("a(")), "c") == []


def test_an_unknown_colour_is_reported():
    criterion = _criterion(_match("x"))
    criterion.color = "puce"
    assert "unknown colour" in " ".join(cr.problems(criterion, "c"))


# ------------------------------------------------------------------ round trip
def test_a_criterion_survives_the_file():
    criterion = cr.CriterionRow(name="start", color="blue", source="/x/odoo.log",
                                rules=[_match("up"), _exclude("bad", cr.REGEX)])
    back = cr.CriterionRow.from_entry(criterion.to_entry())
    assert (back.name, back.color, back.source) == ("start", "blue", "/x/odoo.log")
    assert [(r.mode, r.kind, r.pattern) for r in back.rules] == [
        (cr.MATCH, cr.TEXT, "up"), (cr.EXCLUDE, cr.REGEX, "bad")]


def test_keys_this_module_never_heard_of_survive():
    entry = {"name": "start", "color": "green", "note": "by hand",
             "rules": [{"mode": "match", "kind": "text", "pattern": "up",
                        "why": "because"}]}
    back = cr.CriterionRow.from_entry(entry).to_entry()
    assert back["note"] == "by hand"
    assert back["rules"][0]["why"] == "because"


def test_watching_its_own_output_writes_no_source():
    assert "source" not in cr.CriterionRow(name="a", rules=[_match("x")]).to_entry()


def test_nonsense_in_the_file_falls_back_rather_than_failing():
    back = cr.CriterionRow.from_entry({"name": "a", "color": "puce",
                                       "rules": [{"mode": "sideways",
                                                  "kind": "runes",
                                                  "pattern": "x"}]})
    assert back.color == cr.DEFAULT_COLOR
    assert (back.rules[0].mode, back.rules[0].kind) == (cr.MATCH, cr.TEXT)


def test_something_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError):
        cr.CriterionRow.from_entry(["not", "a", "criterion"])
    with pytest.raises(ValueError):
        cr.CriterionRow.from_entry({"name": "a", "rules": "not an array"})


def test_a_copy_shares_nothing_with_its_original():
    criterion = cr.CriterionRow(name="a", rules=[_match("x")])
    clone = criterion.copy()
    clone.rules[0].pattern = "y"
    clone.name = "b"
    assert criterion.rules[0].pattern == "x" and criterion.name == "a"


# --------------------------------------------------------------------- helpers
def test_the_files_to_watch_are_the_distinct_ones():
    criteria = [cr.CriterionRow(name="a", source="/x.log"),
                cr.CriterionRow(name="b", source="/x.log"),
                cr.CriterionRow(name="c", source=""),
                cr.CriterionRow(name="d", source="/y.log")]
    assert cr.sources_of(criteria) == ["/x.log", "/y.log"]


def test_a_matcher_per_criterion_in_order():
    matchers = cr.matchers_for([cr.CriterionRow(name="a"),
                                cr.CriterionRow(name="b")])
    assert [m.name for m in matchers] == ["a", "b"]
