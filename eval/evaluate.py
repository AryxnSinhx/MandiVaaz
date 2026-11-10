"""Held-out evaluation for Mandi Vaaz (Unit 7).

What it measures, for every labelled clip:

  A. TEXT     the typed sentence (`said`) -> parser -> agent.   No audio, no credits.
              This is a CEILING: it shows what the parser/agent can do when STT is perfect.
  B. AUDIO    clip -> STT -> parser -> agent.                   The headline number.
  C. WHO TO BLAME   compare A and B per clip: STT-caused, parser-gap, or fine.
  D. LATENCY  p50 / p95 per stage (the first call is a warm-up and is discarded).
  E. TTS      optional: time Timbre on real replies (--tts N).

Run from the project root (mandi-vaaz):

  uv run --env-file .env python -m eval.evaluate --text-only
  uv run --env-file .env python -m eval.evaluate
  uv run --env-file .env python -m eval.evaluate --tts 9

Assumptions about modules this file has not seen are listed in build_real().
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}
REQUIRED_KEYS = ("id", "file", "said", "style", "group", "crop", "mandi", "status")
STYLES = ("punjabi", "punglish")
GROUPS = ("answer", "clarify", "unsupported")
SLOT_METRICS = ("crop_ok", "mandi_ok", "slots_ok", "status_ok")
LATENCY_KEYS = ("stt_client", "stt_convert", "stt_api", "stt_server", "agent")
FINGERPRINT_FILES = (
    "app/aliases.py",
    "app/parser.py",
    "app/agent.py",
    "app/lookup.py",
    "app/templates.py",
    "app/stt.py",
    "data/mandi.db",
)


class WarmupError(RuntimeError):
    """The warm-up STT call failed, so the run was stopped before spending more credits."""


# ----------------------------------------------------------------------------
# Small statistics helpers
# ----------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion k/n (95% when z = 1.96)."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(values, q: float):
    """q-th percentile (0-100) with linear interpolation between ranks. None if empty."""
    xs = sorted(values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ----------------------------------------------------------------------------
# Normalising what the real modules hand back
# ----------------------------------------------------------------------------
def _norm(value):
    """Lower-case stripped string, or None for None / empty."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def norm_status(status):
    """Enum member, 'Status.answer' or 'answer' -> 'answer'. None stays None."""
    if status is None:
        return None
    name = getattr(status, "name", None)
    text = name if isinstance(name, str) else str(status)
    return text.split(".")[-1].strip().lower() or None


def slots_from_parsed(parsed) -> dict:
    """Pull crop / best mandi / day out of parser.parse() output."""
    parsed = parsed if isinstance(parsed, dict) else {}
    mandis = parsed.get("mandis") or []
    return {
        "crop": _norm(parsed.get("crop")),
        "mandi": _norm(mandis[0]) if mandis else None,
        "day": _norm(parsed.get("day")),
    }


def _err(exc: Exception) -> str:
    kind = getattr(exc, "kind", None)
    tail = f" ({kind})" if kind else ""
    return f"{type(exc).__name__}{tail}: {str(exc)[:80]}"


# ----------------------------------------------------------------------------
# Labels
# ----------------------------------------------------------------------------
def load_labels(path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return data["clips"] if isinstance(data, dict) else data


def resolve_clip(clips_dir, name: str):
    """Find the audio file for a label: exact name, else name/stem + any known extension."""
    base = Path(clips_dir)
    exact = base / name
    if exact.is_file():
        return exact
    for stem in {name, Path(name).stem}:
        for ext in CONTENT_TYPES:
            candidate = base / (stem + ext)
            if candidate.is_file():
                return candidate
    return None


def validate_labels(labels, clips_dir, crops=None, mandis=None, statuses=None,
                    check_files=True) -> list[str]:
    """Return a list of human-readable problems (empty list = labels are usable)."""
    problems: list[str] = []
    seen = set()
    for i, lab in enumerate(labels):
        tag = lab.get("id", f"#{i}") if isinstance(lab, dict) else f"#{i}"
        if not isinstance(lab, dict):
            problems.append(f"{tag}: not an object")
            continue
        for key in REQUIRED_KEYS:
            if key not in lab:
                problems.append(f"{tag}: missing key '{key}'")
        if lab.get("id") in seen:
            problems.append(f"{tag}: duplicate id")
        seen.add(lab.get("id"))
        if not str(lab.get("said") or "").strip():
            problems.append(f"{tag}: 'said' is empty")
        if lab.get("style") not in STYLES:
            problems.append(f"{tag}: style must be one of {STYLES}")
        if lab.get("group") not in GROUPS:
            problems.append(f"{tag}: group must be one of {GROUPS}")
        if crops is not None and _norm(lab.get("crop")) not in (None, *crops):
            problems.append(f"{tag}: crop '{lab.get('crop')}' is not a key in aliases.CROPS")
        if mandis is not None and _norm(lab.get("mandi")) not in (None, *mandis):
            problems.append(f"{tag}: mandi '{lab.get('mandi')}' is not a key in aliases.MANDIS")
        if statuses is not None and _norm(lab.get("status")) not in statuses:
            problems.append(f"{tag}: status '{lab.get('status')}' is not a templates.Status name")
        if check_files and "file" in lab and resolve_clip(clips_dir, lab["file"]) is None:
            problems.append(f"{tag}: audio file '{lab['file']}' not found in {clips_dir}")
    return problems


# ----------------------------------------------------------------------------
# Scoring one sentence
# ----------------------------------------------------------------------------
def analyze_text(text, parse_fn, step_fn, clock=time.perf_counter) -> dict:
    """Run parser (for slots) and agent (for status) on one piece of text."""
    out = {
        "text": text,
        "slots": slots_from_parsed(None),
        "parser_status": None,
        "status": None,
        "step_seconds": None,
        "error": None,
    }
    try:
        parsed = parse_fn(text)
    except Exception as exc:  # a crash is a result, not a reason to stop the run
        out["error"] = f"parse: {_err(exc)}"
        return out
    out["slots"] = slots_from_parsed(parsed)
    if isinstance(parsed, dict):
        out["parser_status"] = _norm(parsed.get("status"))
    try:
        started = clock()
        reply = step_fn(text)
        out["step_seconds"] = clock() - started
        out["status"] = norm_status(getattr(reply, "status", None))
    except Exception as exc:
        out["error"] = f"agent: {_err(exc)}"
    return out


def score(label: dict, analysis: dict) -> dict:
    """Compare one analysis with the label. Any error or missing status scores as all-wrong."""
    day_label = _norm(label.get("day"))
    if analysis.get("error") or analysis.get("status") is None:
        return {"crop_ok": False, "mandi_ok": False, "slots_ok": False, "status_ok": False,
                "day_ok": None if day_label is None else False}
    slots = analysis["slots"]
    crop_ok = slots["crop"] == _norm(label["crop"])
    mandi_ok = slots["mandi"] == _norm(label["mandi"])
    return {
        "crop_ok": crop_ok,
        "mandi_ok": mandi_ok,
        "slots_ok": crop_ok and mandi_ok,
        "status_ok": analysis["status"] == _norm(label["status"]),
        "day_ok": None if day_label is None else slots["day"] == day_label,
    }


def attribute(text_score: dict, audio_score: dict, audio_error) -> str:
    """Blame assignment for one clip, based on crop+mandi correctness only."""
    if audio_error:
        return "stt_error"
    text_ok, audio_ok = text_score["slots_ok"], audio_score["slots_ok"]
    if text_ok and audio_ok:
        return "ok"
    if text_ok and not audio_ok:
        return "stt_caused"
    if not text_ok and audio_ok:
        return "parser_lucky"
    return "parser_gap"


def run_clip(label, clips_dir, *, transcribe_fn, parse_fn, step_fn, text_only=False,
             read_fn=None) -> dict:
    record = {
        "id": label["id"],
        "group": label["group"],
        "style": label["style"],
        "said": label["said"],
        "expected": {
            "crop": _norm(label["crop"]),
            "mandi": _norm(label["mandi"]),
            "status": _norm(label["status"]),
            "day": _norm(label.get("day")),
        },
    }
    text = analyze_text(label["said"], parse_fn, step_fn)
    text["score"] = score(label, text)
    record["text"] = text
    if text_only:
        record["audio"] = None
        record["attribution"] = None
        return record

    reader = read_fn or (lambda p: Path(p).read_bytes())
    timings: dict = {}
    meta: dict = {}
    stt_error = None
    transcript = ""
    try:
        path = resolve_clip(clips_dir, label["file"])
        if path is None:
            raise FileNotFoundError(label["file"])
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        result = transcribe_fn(reader(path), ctype)
        transcript = getattr(result, "transcript", "") or ""
        timings = {
            "stt_client": getattr(result, "client_seconds", None),
            "stt_convert": getattr(result, "convert_seconds", None),
            "stt_api": getattr(result, "api_end_to_end_seconds", None),
            "stt_server": getattr(result, "server_seconds", None),
        }
        meta = {
            "config": getattr(result, "config", None),
            "request_id": getattr(result, "request_id", None),
            "detected_language": getattr(result, "detected_language", None),
            "truncated": getattr(result, "truncated", None),
        }
    except Exception as exc:
        stt_error = f"stt: {_err(exc)}"

    if stt_error:
        audio = {
            "text": None, "slots": slots_from_parsed(None), "parser_status": None,
            "status": None, "step_seconds": None, "error": stt_error,
        }
    else:
        audio = analyze_text(transcript, parse_fn, step_fn)
        timings["agent"] = audio["step_seconds"]
    audio["transcript"] = transcript if not stt_error else None
    audio["timings"] = timings
    audio.update(meta)
    audio["score"] = score(label, audio)
    record["audio"] = audio
    record["attribution"] = attribute(text["score"], audio["score"], stt_error)
    return record


def run_all(labels, clips_dir, *, transcribe_fn, parse_fn, step_fn, text_only=False,
            warmup=True, pause=0.0, sleep=time.sleep, read_fn=None, progress=None) -> list[dict]:
    """Run every label. The warm-up call is made first and never counted."""
    if warmup and labels:
        try:
            step_fn(labels[0]["said"])  # agent cold start (first run was 1.34 s)
        except Exception:
            pass
        if not text_only:
            reader = read_fn or (lambda p: Path(p).read_bytes())
            try:
                path = resolve_clip(clips_dir, labels[0]["file"])
                if path is None:
                    raise FileNotFoundError(labels[0]["file"])
                ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
                transcribe_fn(reader(path), ctype)  # ffmpeg / connection cold start
            except Exception as exc:
                raise WarmupError(_err(exc)) from exc
    records = []
    for i, label in enumerate(labels):
        rec = run_clip(label, clips_dir, transcribe_fn=transcribe_fn, parse_fn=parse_fn,
                       step_fn=step_fn, text_only=text_only, read_fn=read_fn)
        records.append(rec)
        if progress:
            progress(i, len(labels), rec)
        if pause and not text_only and i < len(labels) - 1:
            sleep(pause)
    return records


# ----------------------------------------------------------------------------
# Summaries
# ----------------------------------------------------------------------------
def _count(records, scope, metric) -> int:
    return sum(1 for r in records if r.get(scope) and r[scope]["score"][metric])


def _split(records, scope, field) -> dict:
    out = {}
    for value in sorted({r[field] for r in records}):
        sub = [r for r in records if r[field] == value]
        out[value] = {
            "n": len(sub),
            "slots_ok": _count(sub, scope, "slots_ok"),
            "status_ok": _count(sub, scope, "status_ok"),
        }
    return out


def latency_summary(records) -> dict:
    cols = {k: [] for k in LATENCY_KEYS}
    for r in records:
        audio = r.get("audio")
        if not audio or audio.get("error"):
            continue
        for key in LATENCY_KEYS:
            value = audio["timings"].get(key)
            if isinstance(value, (int, float)):
                cols[key].append(float(value))
    return {
        k: {"n": len(v), "p50": percentile(v, 50), "p95": percentile(v, 95),
            "max": max(v) if v else None}
        for k, v in cols.items()
    }


def summarize(records) -> dict:
    n = len(records)
    has_audio = any(r.get("audio") for r in records)
    summary: dict = {"n": n, "has_audio": has_audio}
    for scope in ("text", "audio") if has_audio else ("text",):
        block = {m: [_count(records, scope, m), n] for m in SLOT_METRICS}
        days = [r[scope]["score"]["day_ok"] for r in records
                if r[scope]["score"]["day_ok"] is not None]
        block["day_ok"] = [sum(1 for d in days if d), len(days)]
        summary[scope] = block
        summary[scope + "_by_group"] = _split(records, scope, "group")
        summary[scope + "_by_style"] = _split(records, scope, "style")
    summary["attribution"] = dict(Counter(r["attribution"] for r in records if r.get("attribution")))
    main_scope = "audio" if has_audio else "text"
    summary["stt_errors"] = [r["id"] for r in records
                             if r.get("audio") and r["audio"].get("error")]
    mismatches = []
    for r in records:
        block = r[main_scope]
        sc = block["score"]
        if sc["slots_ok"] and sc["status_ok"]:
            continue
        mismatches.append({
            "id": r["id"], "group": r["group"], "said": r["said"],
            "heard": block.get("transcript") if main_scope == "audio" else r["said"],
            "expected": r["expected"],
            "got": {**block["slots"], "status": block["status"]},
            "parser_status": block["parser_status"],
            "error": block["error"],
        })
    summary["mismatches"] = mismatches
    summary["latency"] = latency_summary(records) if has_audio else {}
    return summary


def time_tts(texts, synth_fn, warmup=True) -> list[dict]:
    """Time Timbre on real reply texts. The first call is a discarded warm-up."""
    if warmup and texts:
        try:
            synth_fn(texts[0])
        except Exception:
            pass
    rows = []
    for text in texts:
        try:
            res = synth_fn(text)
            rows.append({"chars": len(text), "error": None,
                         "http_seconds": getattr(res, "http_seconds", None),
                         "client_seconds": getattr(res, "client_seconds", None)})
        except Exception as exc:
            rows.append({"chars": len(text), "error": _err(exc),
                         "http_seconds": None, "client_seconds": None})
    return rows


def tts_summary(rows) -> dict:
    ok = [r for r in rows if not r["error"] and isinstance(r["http_seconds"], (int, float))]
    http = [r["http_seconds"] for r in ok]
    return {"n": len(rows), "n_ok": len(ok), "p50": percentile(http, 50),
            "p95": percentile(http, 95), "max": max(http) if http else None}


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def fmt_rate(k: int, n: int) -> str:
    if n == 0:
        return "n/a (0 clips)"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:5.1f}%   95% CI {100 * lo:.0f}-{100 * hi:.0f}%"


def _f(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def _rate_block(add, block):
    names = (("crop_ok", "crop correct"), ("mandi_ok", "mandi correct"),
             ("slots_ok", "crop AND mandi correct"), ("status_ok", "reply status correct"))
    for key, label in names:
        add(f"  {label:<24s} {fmt_rate(*block[key])}")
    if block["day_ok"][1]:
        add(f"  {'day correct (labelled)':<24s} {fmt_rate(*block['day_ok'])}")


def _split_block(add, split, title):
    add(f"  {title}")
    for key, v in split.items():
        add(f"    {key:<12s} n={v['n']:2d}   slots {v['slots_ok']}/{v['n']}   status {v['status_ok']}/{v['n']}")


def format_report(meta: dict, summary: dict, tts: dict | None = None) -> str:
    L: list[str] = []
    add = L.append
    add("=" * 74)
    add("MANDI VAAZ - HELD-OUT EVALUATION")
    add("=" * 74)
    add(f"Run at      : {meta.get('run_at')}")
    add(f"Clips       : {summary['n']}    mode: {meta.get('mode')}")
    add(f"STT config  : requested={meta.get('stt_config_requested')}   used={meta.get('stt_config_used')}")
    fp = meta.get("fingerprints") or {}
    add("Fingerprints: " + "  ".join(f"{k.split('/')[-1]}={v}" for k, v in fp.items()))
    add("")
    add("A. TYPED SENTENCE -> parser -> agent   (CEILING: perfect STT; not a real result)")
    _rate_block(add, summary["text"])
    if summary["has_audio"]:
        add("")
        add("B. AUDIO -> STT -> parser -> agent   (HEADLINE)")
        _rate_block(add, summary["audio"])
        add(f"  STT/API failures (counted as wrong): {len(summary['stt_errors'])}"
            + (f"  {summary['stt_errors']}" if summary["stt_errors"] else ""))
        _split_block(add, summary["audio_by_group"], "by group")
        _split_block(add, summary["audio_by_style"], "by style (punjabi = Main Character, punglish = Mixed Vibes)")
        add("")
        add("C. WHERE END-TO-END CROP/MANDI ERRORS COME FROM")
        att = summary["attribution"]
        for key, text in (("ok", "typed and spoken both right"),
                          ("stt_caused", "typed right, spoken wrong  -> STT / speech"),
                          ("parser_gap", "typed wrong too           -> parser / aliases"),
                          ("parser_lucky", "typed wrong, spoken right   -> parser fragile"),
                          ("stt_error", "API call failed")):
            add(f"  {key:<13s} {att.get(key, 0):3d}   {text}")
    else:
        add("")
        add("(text-only run: no audio was sent, so sections B-E are skipped)")
    add("")
    add("D. CLIPS THAT DID NOT PASS (slots or status wrong)")
    if not summary["mismatches"]:
        add("  none")
    for m in summary["mismatches"]:
        add(f"  {m['id']} [{m['group']}] expected {m['expected']['crop']}/{m['expected']['mandi']}/{m['expected']['status']}"
            f"   got {m['got']['crop']}/{m['got']['mandi']}/{m['got']['status']}   parser={m['parser_status']}")
        add(f"      said : {m['said']}")
        if summary["has_audio"]:
            add(f"      heard: {m['heard']}")
        if m["error"]:
            add(f"      error: {m['error']}")
    if summary["has_audio"]:
        add("")
        add("E. LATENCY, seconds (first call discarded; small n)")
        for key, v in summary["latency"].items():
            add(f"  {key:<12s} n={v['n']:2d}  p50={_f(v['p50']):>6s}  p95={_f(v['p95']):>6s}  max={_f(v['max']):>6s}")
        add("  stt_client = whole STT call incl. ffmpeg; stt_api = Gnani-side end to end;")
        add("  agent = parser + agent + SQLite lookup (no network)")
    if tts:
        add("")
        add("F. TTS (Timbre v3.0, real replies, first call discarded)")
        add(f"  n={tts['n']} ok={tts['n_ok']}  http p50={_f(tts['p50'])}  p95={_f(tts['p95'])}  max={_f(tts['max'])}")
    add("")
    add("HOW TO READ THIS (limits, printed on purpose)")
    add("  - CI = Wilson 95% interval. At n=18 even 17/18 only supports 'true rate roughly 74-99%'.")
    add("  - p95 with fewer than ~20 samples is almost the maximum; treat as indicative only.")
    add("  - Sentences were READ from a sheet; natural farmer speech is messier.")
    add("  - Valid as HELD-OUT only if aliases/parser/agent were NOT edited after seeing these clips:")
    add("    compare the fingerprints above between runs. Numbers after tuning must be labelled 'tuned'.")
    add("  - Browser-to-ear time is NOT measured here.")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# Wiring to the real modules (the only part that needs the builder's code)
# ----------------------------------------------------------------------------
def sha_prefix(path) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else None


def build_real(config: str | None = None) -> dict:
    """ASSUMPTIONS (not seen by Claude; confirm by running):
      - stt.transcribe_audio(raw, content_type, config=...) accepts a `config` keyword
        (guess from the CLI flag --config and STTResult.config)
      - agent.step(text, None, parse_fn=parser.parse, price_fn=lookup.get_price) works from
        the project root (lookup finds data/mandi.db there)
      - reply.status is a templates.Status member; reply.render(to_words=...) returns str
      - aliases.CROPS / aliases.MANDIS keys are lower-case English names
    """
    try:
        from app import agent, aliases, lookup, parser, stt, templates, tts
        from app.punjabi_numbers import number_to_words
    except ImportError as exc:
        raise SystemExit(f"Could not import app modules ({exc}). Run from the project root with "
                         f"'uv run --env-file .env python -m eval.evaluate'.")

    def transcribe(raw, content_type):
        kw = {"config": config} if config else {}
        return stt.transcribe_audio(raw, content_type, **kw)

    def step(text):
        return agent.step(text, None, parse_fn=parser.parse, price_fn=lookup.get_price)

    def render_reply(text):
        out = step(text).render(to_words=number_to_words)
        return out if isinstance(out, str) else " ".join(str(x) for x in out)

    return {
        "transcribe": transcribe,
        "parse": parser.parse,
        "step": step,
        "render_reply": render_reply,
        "synth": lambda text: tts.synthesize(text),
        "crops": {_norm(k) for k in aliases.CROPS},
        "mandis": {_norm(k) for k in aliases.MANDIS},
        "statuses": {s.name.lower() for s in templates.Status},
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Held-out evaluation for Mandi Vaaz")
    ap.add_argument("--labels", default="eval/heldout_labels.json")
    ap.add_argument("--clips", default="eval/heldout")
    ap.add_argument("--out-dir", default="eval/results")
    ap.add_argument("--text-only", action="store_true", help="no audio, no STT calls, no credits")
    ap.add_argument("--config", default=None, help="STT config name; default = stt.py's own default")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--pause", type=float, default=0.3, help="seconds between STT calls")
    ap.add_argument("--tts", type=int, default=0, metavar="N", help="also time TTS on up to N real replies")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    wiring = build_real(args.config)
    labels = load_labels(args.labels)
    problems = validate_labels(labels, args.clips, wiring["crops"], wiring["mandis"],
                               wiring["statuses"], check_files=not args.text_only)
    if problems:
        print("Label problems (nothing was sent to any API):")
        for p in problems:
            print("  -", p)
        return 2

    def progress(i, total, rec):
        block = rec["audio"] or rec["text"]
        sc = block["score"]
        heard = block.get("transcript") if rec["audio"] else rec["said"]
        print(f"[{i + 1:2d}/{total}] {rec['id']:<4s} slots {'OK ' if sc['slots_ok'] else 'BAD'} "
              f"status {'OK ' if sc['status_ok'] else 'BAD'} | {heard}")

    try:
        records = run_all(labels, args.clips, transcribe_fn=wiring["transcribe"],
                          parse_fn=wiring["parse"], step_fn=wiring["step"],
                          text_only=args.text_only, warmup=not args.no_warmup,
                          pause=args.pause, progress=progress)
    except WarmupError as exc:
        print(f"Warm-up STT call failed, run stopped: {exc}")
        print("If this is a TypeError about 'config', tell Claude the real keyword name in stt.transcribe_audio.")
        return 3

    summary = summarize(records)
    tts_rows, tts_sum = None, None
    if args.tts and not args.text_only:
        texts = []
        for r in records:
            a = r["audio"]
            if a and not a["error"] and a["status"] == "answer" and len(texts) < args.tts:
                try:
                    texts.append(wiring["render_reply"](a["transcript"]))
                except Exception as exc:
                    print(f"(could not render reply for {r['id']}: {_err(exc)})")
        tts_rows = time_tts(texts, wiring["synth"], warmup=not args.no_warmup)
        tts_sum = tts_summary(tts_rows)

    used = next((r["audio"].get("config") for r in records
                 if r["audio"] and r["audio"].get("config")), None)
    meta = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "text-only" if args.text_only else "audio+text",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "labels_file": str(args.labels),
        "clips_dir": str(args.clips),
        "stt_config_requested": args.config or "(stt.py default)",
        "stt_config_used": used,
        "warmup": not args.no_warmup,
        "fingerprints": {f: sha_prefix(f) for f in FINGERPRINT_FILES},
    }
    report = format_report(meta, summary, tts_sum)
    print()
    print(report)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + ("_text" if args.text_only else "")
    (out_dir / f"heldout_{stamp}.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "records": records,
                    "tts_rows": tts_rows, "tts_summary": tts_sum},
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / f"heldout_{stamp}.txt").write_text(report, encoding="utf-8")
    print(f"\nSaved to {out_dir}/heldout_{stamp}.json and .txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
