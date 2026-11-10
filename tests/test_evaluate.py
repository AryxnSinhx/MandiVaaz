"""Tests for eval/evaluate.py. Fakes only: no network, no Gnani, no app modules.

Run:  uv run python -m tests.test_evaluate
"""
from __future__ import annotations

import json
import tempfile
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from eval import evaluate as ev


class S(Enum):  # upper-case names on purpose: norm_status must lower-case them
    ANSWER = "answer"
    CLARIFY_MANDI = "clarify_mandi"
    CLARIFY_CROP = "clarify_crop"
    NO_DATA_CROP = "no_data_crop"
    NOT_UNDERSTOOD = "not_understood"


class FakeSTTError(Exception):
    def __init__(self, kind):
        super().__init__("boom")
        self.kind = kind


# text -> parser output. Anything else parses as "nothing found".
PARSES = {
    "potato ludhiana": {"status": "ok", "crop": "potato", "mandis": ["ludhiana"], "day": None},
    "potato price": {"status": "need_mandi", "crop": "potato", "mandis": [], "day": None},
    "wheat ludhiana": {"status": "ok", "crop": "wheat", "mandis": ["ludhiana"], "day": None},
    "kal potato ludhiana": {"status": "ok", "crop": "potato", "mandis": ["ludhiana", "khanna"], "day": "yesterday"},
    "": {"status": "unknown", "crop": None, "mandis": [], "day": None},
}


def fake_parse(text):
    return PARSES.get(text, {"status": "unknown", "crop": None, "mandis": [], "day": None})


def fake_step(text):
    p = fake_parse(text)
    if p["crop"] == "wheat":
        return SimpleNamespace(status=S.NO_DATA_CROP)
    if p["crop"] and p["mandis"]:
        return SimpleNamespace(status=S.ANSWER)
    if p["crop"]:
        return SimpleNamespace(status=S.CLARIFY_MANDI)
    if p["mandis"]:
        return SimpleNamespace(status=S.CLARIFY_CROP)
    return SimpleNamespace(status=S.NOT_UNDERSTOOD)


def label(id="c1", said="potato ludhiana", crop="potato", mandi="ludhiana", status="answer",
          group="answer", style="punglish", **extra):
    d = {"id": id, "file": id, "said": said, "style": style, "group": group,
         "crop": crop, "mandi": mandi, "status": status}
    d.update(extra)
    return d


def stt_returning(transcript, **extra):
    base = dict(transcript=transcript, client_seconds=1.0, convert_seconds=0.4,
                api_end_to_end_seconds=0.6, server_seconds=0.2, config="paen_codemix_ctx",
                request_id="r1", detected_language="pa-IN", truncated=False)
    base.update(extra)
    return lambda raw, ctype: SimpleNamespace(**base)


def make_clip_dir(names=("c1",), ext=".wav"):
    tmp = tempfile.TemporaryDirectory()
    for n in names:
        (Path(tmp.name) / (n + ext)).write_bytes(b"RIFFfake")
    return tmp


def approx(a, b, tol=0.002):
    return abs(a - b) <= tol


# ---------------------------------------------------------------- statistics
def test_wilson_known_values():
    lo, hi = ev.wilson(9, 10)
    assert approx(lo, 0.596) and approx(hi, 0.982), (lo, hi)
    lo, hi = ev.wilson(10, 10)
    assert approx(lo, 0.722) and hi == 1.0, (lo, hi)
    lo, hi = ev.wilson(0, 10)
    assert lo == 0.0 and approx(hi, 0.278), (lo, hi)
    assert ev.wilson(0, 0) == (0.0, 0.0)
    assert ev.wilson(5, 10)[0] < 0.5 < ev.wilson(5, 10)[1]
    # more data -> narrower interval
    w_small = ev.wilson(9, 10)
    w_big = ev.wilson(90, 100)
    assert (w_big[1] - w_big[0]) < (w_small[1] - w_small[0])


def test_percentile():
    assert ev.percentile([1, 2, 3, 4], 50) == 2.5
    assert approx(ev.percentile([1, 2, 3, 4], 95), 3.85, 1e-9)
    assert ev.percentile([4, 1, 3, 2], 50) == 2.5          # unsorted input
    assert ev.percentile([7], 95) == 7
    assert ev.percentile([], 50) is None
    assert ev.percentile([1, 2, 3], 0) == 1 and ev.percentile([1, 2, 3], 100) == 3


# ---------------------------------------------------------------- normalising
def test_norm_status():
    assert ev.norm_status(S.ANSWER) == "answer"
    assert ev.norm_status("Status.clarify_mandi") == "clarify_mandi"
    assert ev.norm_status("answer") == "answer"
    assert ev.norm_status(None) is None


def test_slots_from_parsed():
    assert ev.slots_from_parsed(None) == {"crop": None, "mandi": None, "day": None}
    s = ev.slots_from_parsed({"crop": "Potato", "mandis": ["Ludhiana", "Khanna"], "day": "yesterday"})
    assert s == {"crop": "potato", "mandi": "ludhiana", "day": "yesterday"}   # best mandi = first
    assert ev.slots_from_parsed({"crop": "", "mandis": []})["crop"] is None


# ---------------------------------------------------------------- scoring
def test_score_basic_and_failed_analysis():
    lab = label()
    good = ev.analyze_text("potato ludhiana", fake_parse, fake_step)
    sc = ev.score(lab, good)
    assert sc == {"crop_ok": True, "mandi_ok": True, "slots_ok": True, "status_ok": True, "day_ok": None}
    wrong = ev.analyze_text("potato price", fake_parse, fake_step)
    sc = ev.score(lab, wrong)
    assert sc["crop_ok"] and not sc["mandi_ok"] and not sc["slots_ok"] and not sc["status_ok"]
    # An analysis that errored must score ALL wrong, even if the label expects None/None
    clarify_label = label(crop=None, mandi=None, status="not_understood")
    failed = {"slots": ev.slots_from_parsed(None), "status": None, "error": "stt: boom"}
    assert ev.score(clarify_label, failed) == {
        "crop_ok": False, "mandi_ok": False, "slots_ok": False, "status_ok": False, "day_ok": None}


def test_score_day_only_when_labelled():
    a = ev.analyze_text("kal potato ludhiana", fake_parse, fake_step)
    assert ev.score(label(day="yesterday"), a)["day_ok"] is True
    assert ev.score(label(day="today"), a)["day_ok"] is False
    assert ev.score(label(), a)["day_ok"] is None


def test_analyze_text_survives_crashes():
    def bad_parse(_):
        raise ValueError("x")

    def bad_step(_):
        raise KeyError("y")

    a = ev.analyze_text("t", bad_parse, fake_step)
    assert a["error"].startswith("parse:") and a["status"] is None
    b = ev.analyze_text("potato ludhiana", fake_parse, bad_step)
    assert b["error"].startswith("agent:") and b["slots"]["crop"] == "potato"
    assert ev.score(label(), b)["slots_ok"] is False


def test_attribute_table():
    ok = {"slots_ok": True}
    bad = {"slots_ok": False}
    assert ev.attribute(ok, ok, None) == "ok"
    assert ev.attribute(ok, bad, None) == "stt_caused"
    assert ev.attribute(bad, bad, None) == "parser_gap"
    assert ev.attribute(bad, ok, None) == "parser_lucky"
    assert ev.attribute(ok, ok, "stt: boom") == "stt_error"


# ---------------------------------------------------------------- run_clip
def run_one(lab, transcript, **kw):
    tmp = make_clip_dir((lab["file"],))
    try:
        return ev.run_clip(lab, tmp.name, transcribe_fn=stt_returning(transcript),
                           parse_fn=fake_parse, step_fn=fake_step, **kw)
    finally:
        tmp.cleanup()


def test_run_clip_attribution_paths():
    lab = label()
    assert run_one(lab, "potato ludhiana")["attribution"] == "ok"
    assert run_one(lab, "potato price")["attribution"] == "stt_caused"      # said fine, heard badly
    bad_said = label(said="garbled text", crop="potato", mandi="ludhiana")  # parser can't read the typed text
    assert run_one(bad_said, "potato price")["attribution"] == "parser_gap"
    assert run_one(bad_said, "potato ludhiana")["attribution"] == "parser_lucky"


def test_run_clip_records_timings_and_meta():
    rec = run_one(label(), "potato ludhiana")
    t = rec["audio"]["timings"]
    assert t["stt_client"] == 1.0 and t["stt_convert"] == 0.4 and t["stt_api"] == 0.6 and t["stt_server"] == 0.2
    assert isinstance(t["agent"], float) and t["agent"] >= 0
    assert rec["audio"]["config"] == "paen_codemix_ctx" and rec["audio"]["transcript"] == "potato ludhiana"
    assert rec["expected"] == {"crop": "potato", "mandi": "ludhiana", "status": "answer", "day": None}


def test_run_clip_stt_failure_is_recorded_not_raised():
    tmp = make_clip_dir(("c1",))

    def boom(raw, ctype):
        raise FakeSTTError("rate_limit")

    try:
        rec = ev.run_clip(label(), tmp.name, transcribe_fn=boom, parse_fn=fake_parse, step_fn=fake_step)
    finally:
        tmp.cleanup()
    assert rec["attribution"] == "stt_error"
    assert "rate_limit" in rec["audio"]["error"]
    assert rec["audio"]["score"]["slots_ok"] is False and rec["audio"]["score"]["status_ok"] is False
    assert rec["text"]["score"]["slots_ok"] is True            # typed part still scored


def test_run_clip_missing_file_is_recorded():
    rec = ev.run_clip(label(), "/nonexistent_dir_xyz", transcribe_fn=stt_returning("x"),
                      parse_fn=fake_parse, step_fn=fake_step)
    assert rec["attribution"] == "stt_error" and "FileNotFoundError" in rec["audio"]["error"]


def test_run_clip_text_only_never_calls_stt():
    calls = []

    def spy(raw, ctype):
        calls.append(1)
        return SimpleNamespace(transcript="")

    rec = ev.run_clip(label(), "/nope", transcribe_fn=spy, parse_fn=fake_parse,
                      step_fn=fake_step, text_only=True)
    assert calls == [] and rec["audio"] is None and rec["attribution"] is None


def test_content_type_from_extension():
    seen = []
    tmp = make_clip_dir(("c1",), ext=".m4a")
    try:
        ev.run_clip(label(), tmp.name, parse_fn=fake_parse, step_fn=fake_step,
                    transcribe_fn=lambda raw, ct: seen.append(ct) or SimpleNamespace(transcript="x"))
    finally:
        tmp.cleanup()
    assert seen == ["audio/mp4"]


def test_resolve_clip():
    tmp = make_clip_dir(("a1",), ext=".m4a")
    try:
        assert ev.resolve_clip(tmp.name, "a1").name == "a1.m4a"       # stem -> any extension
        assert ev.resolve_clip(tmp.name, "a1.wav").name == "a1.m4a"   # wrong ext in label still found
        assert ev.resolve_clip(tmp.name, "zzz") is None
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------- run_all
def test_run_all_warmup_not_counted_and_pause():
    tmp = make_clip_dir(("c1", "c2", "c3"))
    stt_calls, sleeps = [], []

    def stt(raw, ctype):
        stt_calls.append(1)
        return SimpleNamespace(transcript="potato ludhiana", client_seconds=1.0)

    labels = [label(id=f"c{i}") for i in (1, 2, 3)]
    try:
        recs = ev.run_all(labels, tmp.name, transcribe_fn=stt, parse_fn=fake_parse, step_fn=fake_step,
                          pause=0.5, sleep=sleeps.append)
        assert len(recs) == 3 and len(stt_calls) == 4          # 3 clips + 1 warm-up
        assert sleeps == [0.5, 0.5]                            # between clips, not after the last
        stt_calls.clear()
        recs = ev.run_all(labels, tmp.name, transcribe_fn=stt, parse_fn=fake_parse, step_fn=fake_step,
                          warmup=False, sleep=sleeps.append)
        assert len(stt_calls) == 3
    finally:
        tmp.cleanup()


def test_run_all_warmup_failure_stops_run():
    tmp = make_clip_dir(("c1", "c2"))
    calls = []

    def boom(raw, ctype):
        calls.append(1)
        raise FakeSTTError("auth")

    try:
        try:
            ev.run_all([label(id="c1"), label(id="c2")], tmp.name, transcribe_fn=boom,
                       parse_fn=fake_parse, step_fn=fake_step)
            assert False, "expected WarmupError"
        except ev.WarmupError as exc:
            assert "auth" in str(exc)
        assert len(calls) == 1                                  # no credits spent on the other clips
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------- labels
def test_validate_labels():
    tmp = make_clip_dir(("c1",))
    crops, mandis = {"potato", "wheat"}, {"ludhiana"}
    statuses = {"answer", "clarify_mandi"}
    try:
        assert ev.validate_labels([label()], tmp.name, crops, mandis, statuses) == []
        bad = [
            label(id="d1"), label(id="d1"),                         # duplicate id + missing file
            label(id="x1", crop="aloo"),                            # unknown crop key
            label(id="x2", mandi="delhi"),                          # unknown mandi key
            label(id="x3", status="no_data_nowhere"),               # unknown status name
            label(id="x4", style="hindi"),                          # bad style
            label(id="x5", group="misc"),                           # bad group
            label(id="x6", said="  "),                              # empty sentence
        ]
        del bad[2]["file"]                                          # missing key
        text = "\n".join(ev.validate_labels(bad, tmp.name, crops, mandis, statuses))
        for needle in ("duplicate id", "not found", "aloo", "delhi", "no_data_nowhere", "style",
                       "group", "'said' is empty", "missing key 'file'"):
            assert needle in text, (needle, text)
        # text-only skips file checks
        assert ev.validate_labels([label(id="zz")], tmp.name, check_files=False) == []
    finally:
        tmp.cleanup()


def test_shipped_labels_file():
    path = Path(__file__).resolve().parent.parent / "eval" / "heldout_labels.json"
    labels = ev.load_labels(path)
    assert len(labels) == 18
    assert len({l["id"] for l in labels}) == 18
    crops = {"potato", "onion", "tomato", "wheat", "cotton", "mustard", "moong", "paddy", "maize"}
    mandis = {"ludhiana", "khanna", "jalandhar", "patiala", "bathinda", "amritsar", "moga", "sangrur", "barnala"}
    statuses = {"answer", "clarify_crop", "clarify_mandi", "no_data_crop", "no_data_mandi"}
    assert ev.validate_labels(labels, "x", crops, mandis, statuses, check_files=False) == []
    by = {}
    for l in labels:
        by.setdefault(l["group"], []).append(l)
        if l["group"] == "answer":
            assert l["status"] == "answer" and l["crop"] in {"potato", "onion", "tomato"} and l["mandi"]
        if l["group"] == "clarify":
            assert l["status"].startswith("clarify_")
        if l["group"] == "unsupported":
            assert l["status"].startswith("no_data_")
    assert {k: len(v) for k, v in by.items()} == {"answer": 9, "clarify": 3, "unsupported": 6}
    assert sum(1 for l in labels if l["style"] == "punjabi") == 9


# ---------------------------------------------------------------- summary / report
def sample_records():
    tmp = make_clip_dir(("a", "b", "c", "d"))
    labels = [
        label(id="a", group="answer", style="punjabi"),                                   # ok
        label(id="b", group="answer", style="punglish"),                                  # heard badly
        label(id="c", group="unsupported", said="wheat ludhiana", crop="wheat",
              mandi="ludhiana", status="no_data_crop", style="punjabi"),                  # ok
        label(id="d", group="answer", style="punglish"),                                  # stt failure
    ]
    transcripts = {"a": "potato ludhiana", "b": "potato price", "c": "wheat ludhiana"}
    state = {"i": 0}
    order = ["a", "b", "c", "d"]

    def stt(raw, ctype):
        key = order[state["i"]]
        state["i"] += 1
        if key == "d":
            raise FakeSTTError("timeout")
        return SimpleNamespace(transcript=transcripts[key], client_seconds={"a": 1.0, "b": 2.0, "c": 3.0}[key],
                               convert_seconds=0.5, api_end_to_end_seconds=0.7, server_seconds=0.2,
                               config="cfgX")
    try:
        return ev.run_all(labels, tmp.name, transcribe_fn=stt, parse_fn=fake_parse, step_fn=fake_step,
                          warmup=False)
    finally:
        tmp.cleanup()


def test_summarize_counts():
    recs = sample_records()
    s = ev.summarize(recs)
    assert s["n"] == 4 and s["has_audio"]
    assert s["audio"]["slots_ok"] == [2, 4] and s["audio"]["status_ok"] == [2, 4]
    assert s["audio"]["crop_ok"] == [3, 4]          # b heard 'potato' but no mandi -> crop ok, mandi wrong
    assert s["audio"]["mandi_ok"] == [2, 4]
    assert s["text"]["slots_ok"] == [4, 4]          # typed sentences are all fine
    assert s["attribution"] == {"ok": 2, "stt_caused": 1, "stt_error": 1}
    assert s["stt_errors"] == ["d"]
    assert s["audio_by_group"]["answer"] == {"n": 3, "slots_ok": 1, "status_ok": 1}
    assert s["audio_by_group"]["unsupported"] == {"n": 1, "slots_ok": 1, "status_ok": 1}
    assert s["audio_by_style"]["punjabi"]["slots_ok"] == 2
    assert [m["id"] for m in s["mismatches"]] == ["b", "d"]
    assert s["mismatches"][0]["heard"] == "potato price"
    assert s["mismatches"][0]["got"]["status"] == "clarify_mandi"


def test_latency_excludes_failed_clips():
    s = ev.summarize(sample_records())
    lat = s["latency"]["stt_client"]
    assert lat["n"] == 3                                   # d failed -> not in latency
    assert lat["p50"] == 2.0 and lat["max"] == 3.0
    assert approx(lat["p95"], 2.9, 1e-9)
    assert s["latency"]["agent"]["n"] == 3


def test_latency_excludes_clips_whose_agent_crashed():
    tmp = make_clip_dir(("c1", "c2"))

    def step(text):
        if text == "boom":
            raise KeyError("agent crash")
        return fake_step(text)

    texts = iter(["potato ludhiana", "boom"])
    try:
        recs = ev.run_all([label(id="c1"), label(id="c2")], tmp.name, warmup=False,
                          transcribe_fn=lambda raw, ct: SimpleNamespace(transcript=next(texts), client_seconds=1.0),
                          parse_fn=fake_parse, step_fn=step)
    finally:
        tmp.cleanup()
    assert recs[1]["audio"]["error"].startswith("agent:")
    assert recs[1]["audio"]["timings"]["stt_client"] == 1.0      # STT time exists...
    assert ev.latency_summary(recs)["stt_client"]["n"] == 1      # ...but the failed clip is excluded


def test_text_only_summary_and_report():
    recs = [ev.run_clip(label(), "/x", transcribe_fn=None, parse_fn=fake_parse,
                        step_fn=fake_step, text_only=True)]
    s = ev.summarize(recs)
    assert s["has_audio"] is False and "audio" not in s and s["latency"] == {}
    rep = ev.format_report({"mode": "text-only"}, s)
    assert "text-only run" in rep and "B. AUDIO" not in rep


def test_format_report_contents():
    recs = sample_records()
    rep = ev.format_report({"run_at": "t", "mode": "audio+text", "stt_config_requested": "x",
                            "stt_config_used": "cfgX", "fingerprints": {"app/aliases.py": "abc123"}},
                           ev.summarize(recs), {"n": 2, "n_ok": 2, "p50": 1.5, "p95": 1.9, "max": 2.0})
    for needle in ("HEADLINE", "95% CI", "2/4 =  50.0%", "stt_caused", "heard: potato price",
                   "aliases.py=abc123", "p50=  2.00", "F. TTS", "HOW TO READ"):
        assert needle in rep, (needle, rep)


def test_fmt_rate():
    assert ev.fmt_rate(0, 0) == "n/a (0 clips)"
    assert "17/18" in ev.fmt_rate(17, 18) and "CI" in ev.fmt_rate(17, 18)


# ---------------------------------------------------------------- tts timing
def test_time_tts():
    calls = []

    def synth(text):
        calls.append(text)
        if text == "bad":
            raise FakeSTTError("server")
        return SimpleNamespace(http_seconds=2.0, client_seconds=2.2)

    rows = ev.time_tts(["one", "bad", "three"], synth)
    assert len(calls) == 4                                   # 3 + 1 warm-up
    assert [r["error"] is None for r in rows] == [True, False, True]
    sm = ev.tts_summary(rows)
    assert sm["n"] == 3 and sm["n_ok"] == 2 and sm["p50"] == 2.0


def test_json_roundtrip_with_gurmukhi():
    recs = sample_records()
    dumped = json.dumps({"records": recs}, ensure_ascii=False, default=str)
    assert json.loads(dumped)["records"][0]["id"] == "a"


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed} passed, {failed} failed ({len(tests)} tests)")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
