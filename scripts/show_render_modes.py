"""scripts/show_render_modes.py - what does reply.render() say under each to_words mode?

Your smoke run showed that reply.render() with NO argument prints digits
("200 ਰੁਪਏ", "30 ਮਾਰਚ 2026"). This script runs the real agent once and renders the
same reply three ways, so we can see which to_words function produces which text.

    uv run --env-file .env python scripts/show_render_modes.py "ludhiana potato rate"
    uv run --env-file .env python scripts/show_render_modes.py "ludhiana potato rate" --tts

--tts speaks every DIFFERENT text with the real Timbre voice and saves
render_<mode>.mp3 (a few paise per file) so you can hear digits vs words in a
full reply, including the date. Nothing in the app is changed by this script.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import real_step  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="one typed question, e.g. 'ludhiana potato rate'")
    ap.add_argument("--tts", action="store_true", help="speak each different text and save mp3s")
    args = ap.parse_args()

    from app import punjabi_numbers as pn
    modes = {
        "none": {},                                          # what the server does today
        "number_to_words": {"to_words": pn.number_to_words},
        "price_to_words": {"to_words": pn.price_to_words},
    }
    reply = real_step(args.text, None)
    print("status:", getattr(reply.status, "value", reply.status))

    texts = {}
    for name, kw in modes.items():
        try:
            out = reply.render(**kw)
            out = " ".join(map(str, out)) if isinstance(out, (list, tuple)) else str(out)
            texts[name] = out
            print("\n[%s]\n%s" % (name, out))
        except Exception as exc:  # noqa: BLE001 - we WANT to see which modes fail
            print("\n[%s]\nFAILED: %s: %s" % (name, type(exc).__name__, exc))

    if args.tts:
        from app import tts
        seen = {}
        for name, out in texts.items():
            if out in seen:
                print("\n%s: same text as '%s', not spoken again" % (name, seen[out]))
                continue
            seen[out] = name
            res = tts.synthesize(out)
            path = "render_%s.mp3" % name
            res.save(path)
            print("\nsaved %s (%d bytes, http %.2fs)" % (path, len(res.audio), res.http_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
