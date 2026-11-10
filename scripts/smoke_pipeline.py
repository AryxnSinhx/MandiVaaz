"""scripts/smoke_pipeline.py - check the REAL wiring behind /ask, without audio.

Each text argument is one conversation turn, typed instead of spoken. The real
parser, agent, lookup (data/mandi.db) and templates run; only STT is replaced by
your typed text. TTS is skipped unless you pass --tts (it costs a few paise).

    uv run --env-file .env python scripts/smoke_pipeline.py "ludhiana potato rate"
    uv run --env-file .env python scripts/smoke_pipeline.py "potato price" "ludhiana"
    uv run --env-file .env python scripts/smoke_pipeline.py "ludhiana potato rate" --tts --save smoke.mp3

If the real modules have a different signature than main.py assumes, you will
see a failed stage here (stage=agent) with the reason in the log lines.
"""
import argparse
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import real_error_text, real_step, real_tts  # noqa: E402
from app.pipeline import run_turn  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Gurmukhi on the Windows console
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("turns", nargs="+", help="one typed sentence per conversation turn")
    ap.add_argument("--tts", action="store_true", help="call the real Timbre TTS")
    ap.add_argument("--save", help="with --tts: save the LAST reply's mp3 here")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="  log: %(message)s")

    typed = iter(args.turns)

    def fake_stt(raw, content_type):
        return SimpleNamespace(transcript=next(typed), request_id="smoke",
                               convert_seconds=None, server_seconds=None)

    def fake_tts(text):
        return SimpleNamespace(audio=b"", content_type="audio/mpeg", http_seconds=None)

    state, last = None, None
    for i, _ in enumerate(args.turns, 1):
        res = run_turn(b"x", "audio/wav", state,
                       stt_fn=fake_stt, step_fn=real_step,
                       tts_fn=real_tts if args.tts else fake_tts,
                       error_text_fn=real_error_text)
        print("--- turn %d" % i)
        print("you said   :", res.transcript)
        print("status     :", res.status)
        print("reply      :", res.reply_text)
        print("failed     :", res.failed_stages or "none")
        print("timings    :", res.timings)
        if not any(f["stage"] in ("stt", "agent") for f in res.failed_stages):
            state = res.new_state     # same rule as /ask: only good turns move the state
        last = res
    if args.tts and args.save and last and last.audio:
        Path(args.save).write_bytes(last.audio)
        print("saved", args.save, len(last.audio), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
