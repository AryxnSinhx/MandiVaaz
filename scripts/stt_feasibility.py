#!/usr/bin/env python3
"""
stt_feasibility.py - Mandi Vaaz STT gate test, Prisma v3.0 (updated Oct 29).

Question it answers: which Prisma v3.0 SETTING (language list x output style x hints) gets the
CROP and MANDI right most often on our own recorded clips?

Run order (cheap -> expensive):
  0. python stt_feasibility.py --clips ./clips --init-manifest     # only if filenames lack q##/spk#
  1. python stt_feasibility.py --clips ./clips --convert-only      # no API calls, no credits
  2. python stt_feasibility.py --clips ./clips --probe             # ONE call, prints raw response
  3. python stt_feasibility.py --clips ./clips --limit 10          # small trial of all configs
  4. python stt_feasibility.py --clips ./clips                     # full run (resumable)

Needs: ffmpeg + ffprobe on PATH, `pip install requests rapidfuzz jiwer`, env var GNANI_API_KEY.
Outputs (in --out, default stt_test_out/): results.jsonl (raw cache), results.csv,
side_by_side.csv, summary.md.

Terms (plain language)
  - CONFIG: one combination of settings we want to compare (see CONFIGS below).
  - model=prisma-v3.0: MUST be sent, otherwise the API silently uses the older v2.5.
  - Output style (format): verbatim = Gurmukhi spelling of everything, even English words;
    codemix = Gurmukhi for Punjabi + Latin letters for English words; transliterate = everything
    in Latin letters; transcribe = like codemix but also tidies numbers/dates (ITN).
  - Language list: v3.0 accepts "pa-IN,en-IN" (up to 4) or "indic-auto" as HINTS, not a lock.
  - Slot hit: transcript contains the right crop (and right mandi). The metric that matters.
  - p50 / p95: median latency / "1 in 20 calls is slower than this".
Docs facts used (docs.gnani.ai REST page, read Oct 29): max 30 s per clip; format values
verbatim|transcribe|codemix|transliterate; keyterms up to 200; topic up to 1600 chars; some
accounts allow only 1 active request at a time (so this script is strictly sequential).
Docs warn that context hints "can lower accuracy" - hence the with/without comparison.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # project root, so `from app...` works however you run this
sys.path.insert(0, str(ROOT))

from app.aliases import CROPS, MANDIS, find_all, normalize

SESSION = requests.Session()  # reuses one connection, like the real FastAPI backend will
STT_URL = "https://api.vachana.ai/stt/v3"
MODEL = "prisma-v3.0"
AUDIO_EXT = {".opus", ".m4a", ".wav", ".mp3", ".ogg", ".flac", ".aac", ".webm", ".mp4", ".3gp", ".amr"}
MAX_SECONDS = 30  # hard limit per docs (API returns MAX_AUDIO_DURATION_EXCEEDED above this)

TOPIC = "A farmer asks the price (bhaav / rate) of a crop in a Punjab mandi (grain market)."


def _keyterms():
    terms = ["mandi", "bhaav", "rate", "price"]
    for table in (CROPS, MANDIS):
        for canon, e in table.items():
            guru = [a for a in e["aliases"] if any(0x0A00 <= ord(c) <= 0x0A7F for c in a)][:2]
            terms += [canon] + guru
    return list(dict.fromkeys(terms))[:200]


# name -> form fields. language_code omitted for transliterate (API defaults it to indic-auto).
CONFIGS = {
    "pa_verbatim":      dict(language_code="pa-IN", format="verbatim"),            # baseline
    "pa_codemix":       dict(language_code="pa-IN", format="codemix"),
    "paen_codemix":     dict(language_code="pa-IN,en-IN", format="codemix"),
    "pahien_codemix":   dict(language_code="pa-IN,hi-IN,en-IN", format="codemix"),  # covers group C
    "auto_codemix":     dict(language_code="indic-auto", format="codemix"),
    "translit":         dict(format="transliterate"),
    "paen_codemix_ctx": dict(language_code="pa-IN,en-IN", format="codemix", ctx=True),
}


def build_form(params: dict) -> dict:
    p = dict(params)
    data = {"model": MODEL}
    if p.pop("ctx", False):
        data["topic"] = TOPIC
        data["keyterms"] = json.dumps(_keyterms(), ensure_ascii=False)
    data.update(p)
    return data


# ---------------------------------------------------------------- clip discovery
def parse_name(name: str):
    """q03_spk2.opus -> (3, '2'). Returns None for parts it cannot find."""
    q = re.search(r"(?:^|[^a-z0-9])q0*(\d{1,2})(?!\d)", name.lower())
    s = re.search(r"spk0*(\d+)", name.lower())
    return (int(q.group(1)) if q else None, s.group(1) if s else None)


def find_clips(clips_dir: Path):
    return sorted(p for p in clips_dir.rglob("*") if p.suffix.lower() in AUDIO_EXT)


def load_manifest(path):
    m = {}
    if path and Path(path).exists():
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                qid = int(r["question_id"]) if (r.get("question_id") or "").strip() else None
                m[r["file"]] = (qid, (r.get("speaker") or "").strip() or None)
    return m


def clip_meta(p: Path, manifest):
    qid, spk = parse_name(p.name)
    if p.name in manifest:
        mq, ms = manifest[p.name]
        qid, spk = mq or qid, ms or spk
    return qid, spk or "unknown"


# ---------------------------------------------------------------- audio
def to_wav(src: Path, work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    dst = work / (src.name.replace(".", "_") + ".wav")
    if not dst.exists():
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
             "-sample_fmt", "s16", str(dst)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {src.name}: {r.stderr.strip()[:200]}")
    return dst


def duration_s(wav: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(wav)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return float("nan")


# ---------------------------------------------------------------- API
def transcribe(wav: Path, params: dict, key: str, header: str, timeout=90, retries=2):
    """One STT call. Retries on 429 / 5xx / network errors. Latency = successful attempt only."""
    last = {"status": None, "transcript": "", "latency_s": None, "server_s": None,
            "detected": None, "request_id": None, "error": ""}
    form = build_form(params)
    for attempt in range(retries + 1):
        try:
            with open(wav, "rb") as fh:
                t0 = time.perf_counter()
                r = SESSION.post(STT_URL, headers={header: key},
                                  files={"audio_file": (wav.name, fh, "audio/wav")},
                                  data=form, timeout=timeout)
                dt = time.perf_counter() - t0
            last.update(status=r.status_code, latency_s=round(dt, 3), error="")
            if r.status_code == 200:
                body = r.json()
                last.update(transcript=(body.get("transcript") or "").strip(),
                            request_id=body.get("request_id"), server_s=body.get("processing_time"),
                            detected=body.get("detected_language_code"))
                if "transcript" not in body:
                    last["error"] = f"no 'transcript' field; keys={list(body)}"
                if body.get("truncated"):
                    last["error"] = "truncated"
                if body.get("model") and body["model"] != MODEL:
                    last["error"] = f"WRONG MODEL returned: {body['model']}"
                return last
            last["error"] = r.text[:300]
            if r.status_code not in (429, 500, 502, 503, 504):
                return last
        except (requests.RequestException, ValueError) as e:
            last.update(status=None, error=f"{type(e).__name__}: {e}"[:300])
        time.sleep(3 * (attempt + 1))
    return last


# ---------------------------------------------------------------- scoring
def score(rec, label):
    t = rec.get("transcript") or ""
    crops, mandis = find_all(t, CROPS), find_all(t, MANDIS)
    crop_ok = label["crop"] in crops
    mandi_ok = set(label["mandis"]) <= set(mandis)
    spurious = [c for c in crops if c != label["crop"]] + [m for m in mandis if m not in label["mandis"]]
    return crop_ok, mandi_ok, spurious


def pct(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))]


def wer_pa(rec, label):
    """WER only where reference is Gurmukhi (group A) and output is not Latin-only (not translit)."""
    if rec["config"] == "translit" or label["group"] != "A" or not rec.get("transcript"):
        return ""
    try:
        import jiwer
        return round(jiwer.wer(normalize(label["text"]), normalize(rec["transcript"])), 3)
    except ImportError:
        return ""


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", required=True, help="folder with recordings (searched recursively)")
    ap.add_argument("--labels", default="labels.json")
    ap.add_argument("--manifest", default=None, help="clips_manifest.csv (file,question_id,speaker)")
    ap.add_argument("--init-manifest", action="store_true", help="write clips_manifest.csv template and exit")
    ap.add_argument("--out", default="stt_test_out")
    ap.add_argument("--configs", default=",".join(CONFIGS), help="comma list; available: " + ", ".join(CONFIGS))
    ap.add_argument("--limit", type=int, default=0, help="only first N clips (cheap trial)")
    ap.add_argument("--convert-only", action="store_true", help="convert + list clips, no API calls")
    ap.add_argument("--probe", action="store_true", help="ONE call per --configs entry on the first clip, print raw, exit")
    ap.add_argument("--yes", action="store_true", help="do not ask before a big run")
    ap.add_argument("--header-name", default="X-API-Key-ID")
    ap.add_argument("--sleep", type=float, default=0.3, help="pause between calls (seconds)")
    a = ap.parse_args()

    clips = find_clips(Path(a.clips))
    if a.limit:
        clips = clips[: a.limit]
    if not clips:
        sys.exit(f"No audio files found under {a.clips}")

    if a.init_manifest:
        mp = Path("clips_manifest.csv")
        with open(mp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["file", "question_id", "speaker"])
            for p in clips:
                q, s = parse_name(p.name)
                w.writerow([p.name, q or "", s or ""])
        sys.exit(f"Wrote {mp} ({len(clips)} rows). Fill question_id (1-20) and speaker (1,2,3), "
                 f"then run with --manifest {mp}")

    names = [x.strip() for x in a.configs.split(",") if x.strip()]
    bad = [n for n in names if n not in CONFIGS]
    if bad:
        sys.exit(f"Unknown config(s): {bad}. Available: {list(CONFIGS)}")

    manifest = load_manifest(a.manifest)
    labels = {q["id"]: q for q in json.load(open(a.labels, encoding="utf-8"))}
    out = Path(a.out)
    work = out / "wav"
    out.mkdir(parents=True, exist_ok=True)

    # 1) convert everything + sanity report (free)
    items, unlabelled = [], []
    for p in clips:
        qid, spk = clip_meta(p, manifest)
        wav = to_wav(p, work)
        dur = duration_s(wav)
        if dur > MAX_SECONDS:
            print(f"  WARN {p.name}: {dur:.1f}s (>30s: API will reject it)")
        if qid not in labels:
            unlabelled.append(p.name)
        items.append(dict(path=p, wav=wav, qid=qid, spk=spk, dur=dur))
    spk_counts = defaultdict(int)
    for it in items:
        spk_counts[it["spk"]] += 1
    print(f"{len(items)} clips | speakers: {dict(spk_counts)} | "
          f"total audio {sum(i['dur'] for i in items):.0f}s | unlabelled: {len(unlabelled)}")
    if unlabelled:
        print("  Unlabelled (transcribed but NOT scored):", ", ".join(unlabelled[:8]), "..." if len(unlabelled) > 8 else "")
        print("  Fix with --init-manifest, edit the CSV, then pass --manifest.")
    if a.convert_only:
        return

    key = os.environ.get("GNANI_API_KEY")
    if not key:
        sys.exit("Set GNANI_API_KEY first (export GNANI_API_KEY=...)")

    if a.probe:
        it = items[0]
        for n in names:
            res = transcribe(it["wav"], CONFIGS[n], key, a.header_name, retries=0)
            print(f"\n[{n}] clip={it['path'].name} -> HTTP {res['status']} in {res['latency_s']}s")
            print(json.dumps(res, ensure_ascii=False, indent=1))
            time.sleep(a.sleep)
        return

    # 2) transcribe (resumable: finished (clip, config) pairs with identical settings are skipped)
    sig = {n: json.dumps(build_form(CONFIGS[n]), sort_keys=True, ensure_ascii=False) for n in names}
    cache_path = out / "results.jsonl"
    done = {}
    if cache_path.exists():
        for line in open(cache_path, encoding="utf-8"):
            r = json.loads(line)
            if r.get("status") == 200 and r.get("sig") == sig.get(r.get("config")):
                done[(r["file"], r["config"])] = r
    todo = [(it, n) for it in items for n in names if (it["path"].name, n) not in done]
    print(f"{len(done)} calls cached, {len(todo)} to run ({len(items)} clips x {len(names)} configs)")
    if len(todo) > 100 and not a.yes:
        if input(f"This will make {len(todo)} API calls (credits!). Continue? [y/N] ").strip().lower() != "y":
            sys.exit("Stopped. Try --limit 10 or --configs a,b to spend less.")
    fatal_seen = 0
    with open(cache_path, "a", encoding="utf-8") as cache:
        for k, (it, n) in enumerate(todo, 1):
            res = transcribe(it["wav"], CONFIGS[n], key, a.header_name)
            rec = dict(file=it["path"].name, config=n, sig=sig[n], qid=it["qid"], spk=it["spk"],
                       dur=round(it["dur"], 2), **res)
            cache.write(json.dumps(rec, ensure_ascii=False) + "\n")
            cache.flush()
            if res["status"] == 200:
                done[(rec["file"], n)] = rec
            print(f"[{k}/{len(todo)}] {rec['file']} {n} -> {res['status']} {res['latency_s']}s "
                  f"{res['transcript'][:50]!r} {res['error'][:80]}")
            if res["status"] in (401, 402, 403):
                fatal_seen += 1
                if fatal_seen >= 2:
                    sys.exit("Auth/credit failure (401 bad key, 402 no credit, 403 scope). "
                             "If 401/403, retry with --header-name X-APIKey-ID. Re-run to resume.")
            time.sleep(a.sleep)

    # 3) score + report
    meta = {it["path"].name: it for it in items}
    recs = []
    for it in items:
        for n in names:
            r = done.get((it["path"].name, n)) or dict(file=it["path"].name, config=n, status="no-result",
                                                       transcript="", latency_s=None, error="")
            r = {**r, "qid": it["qid"], "spk": it["spk"]}  # manifest may have changed since cache write
            lab = labels.get(r["qid"])
            if lab:
                r["crop_ok"], r["mandi_ok"], r["spurious"] = score(r, lab)
                r["both_ok"] = r["crop_ok"] and r["mandi_ok"]
                r["group"], r["wer"] = lab["group"], wer_pa(r, lab)
            recs.append(r)
    write_outputs(recs, labels, out, meta, names)


def write_outputs(recs, labels, out, meta, names):
    cols = ["file", "spk", "qid", "group", "config", "status", "latency_s", "server_s", "detected",
            "transcript", "crop_ok", "mandi_ok", "both_ok", "spurious", "wer", "error"]
    with open(out / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(recs, key=lambda r: (r["spk"], r["qid"] or 0, r["config"])):
            w.writerow({**r, "spurious": " ".join(r.get("spurious", []))})

    by_clip = defaultdict(dict)
    for r in recs:
        by_clip[r["file"]][r["config"]] = r
    with open(out / "side_by_side.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["speaker", "qid", "file", "reference"] + [x for n in names for x in (n, n + " both_ok")])
        for fn, d in sorted(by_clip.items(), key=lambda kv: (meta[kv[0]]["spk"], meta[kv[0]]["qid"] or 0)):
            m = meta[fn]
            row = [m["spk"], m["qid"] or "", fn, labels[m["qid"]]["text"] if m["qid"] in labels else ""]
            for n in names:
                row += [d[n].get("transcript", ""), d[n].get("both_ok", "")]
            w.writerow(row)

    def hits(rows, k):
        return sum(1 for r in rows if r.get(k))

    scored = [r for r in recs if "both_ok" in r]
    spk = sorted({r["spk"] for r in scored})
    n_clips = len({r["file"] for r in scored})
    L = ["# STT feasibility summary (Prisma v3.0)", "",
         f"Clips scored: **{n_clips}** from **{len(spk)}** speaker label(s) ({', '.join(spk)}). "
         "Counts are small: treat differences under ~10 percentage points as noise.", "",
         "## Slot accuracy per config (hits / clips)", "",
         "| Config | Crop | Mandi | **Both** | Empty/err | Latency p50 | p95 | max |", "|---|---|---|---|---|---|---|---|"]
    rank = {}
    for n in names:
        rows = [r for r in scored if r["config"] == n]
        if not rows:
            continue
        bad = sum(1 for r in rows if r["status"] != 200 or not r.get("transcript"))
        lat = [r["latency_s"] for r in recs if r["config"] == n and r["status"] == 200 and r["latency_s"] is not None][1:]
        rank[n] = hits(rows, "both_ok") / len(rows)
        L.append(f"| {n} | {hits(rows,'crop_ok')}/{len(rows)} | {hits(rows,'mandi_ok')}/{len(rows)} | "
                 f"**{hits(rows,'both_ok')}/{len(rows)}** | {bad} | {pct(lat,50):.2f}s | {pct(lat,95):.2f}s | "
                 f"{(max(lat) if lat else float('nan')):.2f}s |")
    L += ["", "Latency = client-measured round trip (includes upload), first call per config discarded.", "",
          "## By question group (Both hits / clips)", "", "A = pure Punjabi, B = Punglish, C = Hindi-mixed", "",
          "| Config | A | B | C |", "|---|---|---|---|"]
    for n in names:
        cells = []
        for g in "ABC":
            rows = [r for r in scored if r["config"] == n and r["group"] == g]
            cells.append(f"{hits(rows,'both_ok')}/{len(rows)}" if rows else "-")
        L.append(f"| {n} | " + " | ".join(cells) + " |")
    L += ["", "## By speaker (Both hits / clips)", "", "| Config | " + " | ".join(spk) + " |", "|---|" + "---|" * len(spk)]
    for n in names:
        cells = []
        for s in spk:
            rows = [r for r in scored if r["config"] == n and r["spk"] == s]
            cells.append(f"{hits(rows,'both_ok')}/{len(rows)}" if rows else "-")
        L.append(f"| {n} | " + " | ".join(cells) + " |")

    clip_ok = defaultdict(dict)
    for r in scored:
        clip_ok[r["file"]][r["config"]] = bool(r["both_ok"])
    if len(rank) >= 2 and clip_ok:
        top2 = sorted(rank, key=rank.get, reverse=True)[:2]
        u2 = sum(1 for d in clip_ok.values() if any(d.get(c) for c in top2))
        uall = sum(1 for d in clip_ok.values() if any(d.values()))
        L += ["", "## Upper bounds if we ran several configs per clip", "",
              "Assumes a perfect chooser (we would not know which is right), so reality is lower.", "",
              f"- best two ({' + '.join(top2)}): {u2}/{len(clip_ok)}", f"- any config: {uall}/{len(clip_ok)}"]
    wers = [r["wer"] for r in scored if r["wer"] != ""]
    if wers:
        L += ["", f"WER on group A, non-translit configs: mean {sum(wers)/len(wers):.2f} over {len(wers)} outputs. "
                  "Diagnostic only; your references must match what was actually said."]
    err = [r for r in recs if r["status"] != 200]
    if err:
        L += ["", f"**{len(err)} calls failed** - see results.csv 'error' column."]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8")
    print("\n" + "\n".join(L))
    print(f"\nFiles written to {out}/: results.csv, side_by_side.csv, summary.md, results.jsonl")


if __name__ == "__main__":
    main()