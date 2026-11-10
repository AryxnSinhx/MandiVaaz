# Mandi Vaaz — Output and Validation Report

> This document consolidates the terminal outputs and test logs supplied during development. It records what those runs demonstrate; it is not a claim that the application has been independently re-tested in a fresh environment.

## 1. Overview

The supplied outputs provide evidence for four areas of Mandi Vaaz:

1. Automated tests for agent behaviour, API handling, Punjabi number conversion, response templates, and TTS handling.
2. End-to-end examples of text and voice interactions, including clarification questions and price responses.
3. A small STT feasibility probe comparing recognition modes on a labelled audio set.
4. Database diagnostics describing the stored market-price snapshot and its coverage limitations.

## 2. Automated test results

The supplied test logs show the following results:

| Test module | Result in supplied log |
|---|---:|
| `tests.test_agent` | 25 passed |
| `tests.test_main` | 23 passed, 0 failed |
| `tests.test_punjabi_numbers` | 5 passed |
| `tests.test_templates` | 11 passed |
| `tests.test_tts` | 28 passed, 0 failed |
| **Total across these five logged modules** | **92 passed, 0 reported failures** |

These are the counts visible in the uploaded logs. They are not necessarily the total number of tests in the repository: outputs for every test module were not included in this evidence set.

### What the tests cover

- **Agent:** clarification when the crop or mandi is missing, supported and unsupported crops, comparison handling, no-data cases, and conversation-state behaviour.
- **API / main pipeline:** JSON and audio responses, session IDs, session state, reset behaviour, request validation, health endpoint, and graceful handling of STT, agent, or TTS failures.
- **Punjabi numbers:** table-based conversion, hundreds/thousands/lakhs, whole-number handling, fractional-value rejection, and price rounding.
- **Templates:** rendering of response statuses, required facts, stale-data warnings, mandi fallbacks, and supported crop names.
- **TTS:** request validation, payload and headers, authentication and credit errors, retry/backoff behaviour, timeouts, response parsing, and saving generated audio.

### Test-log observations

- `tests.test_main` prints a Starlette deprecation warning concerning use of `httpx` with `starlette.testclient`. The tests still report 23 passed and 0 failed. Review the dependency guidance when updating the test environment.
- Lines such as `agent exploded`, `secret detail`, and `boom` appear in failure-path tests. In this log they are simulated errors, and the associated tests pass; they are not evidence that those failures occurred during a normal user session.

## 3. Interaction and pipeline examples

The supplied command-line examples demonstrate the following behaviour:

- Asking `tomato rate` causes the agent to ask which mandi is intended. Supplying `Ludhiana` then returns a tomato price response.
- Asking for wheat returns a no-data response. The response identifies potato, onion, and tomato as the crops supported by the application in the tested configuration.
- A comparison query for potato in Ludhiana and Khanna returns a separate answer for each mandi rather than silently substituting one mandi for the other.
- Asking `potato price` without a mandi produces a clarification question. Supplying `ludhiana` in the next turn completes the query.
- Asking only for `ludhiana` produces a crop clarification question.

The examples also show that answers include the market-record date and warn the user when the returned record is old. For example, the supplied runs return a Ludhiana potato record dated 30 March 2026 and explicitly state that it is not today's price.

These are sample interactions from the supplied runs, not a guarantee that every crop/mandi combination has a result.

## 4. Punjabi number rendering and audio output

The render-mode comparison was run for `ludhiana potato rate` with TTS enabled. It compared three text-rendering choices:

- `none`: keeps numeric digits, for example `200`.
- `number_to_words`: converts the amount and date into Punjabi words.
- `price_to_words`: the supplied example contains duplicated or misplaced `ਰੁਪਏ` wording around the amount/date.

The accompanying output explicitly records the preference for the `number_to_words` version. That is the better of the three variants in the supplied comparison and is the recommended rendering mode to retain, subject to further listening tests.

The render-mode command saved three MP3 files. A separate TTS run also saved `hello.mp3`, and the digits comparison reported:

| TTS sample | Reported file size | Reported generation time |
|---|---:|---:|
| `digits_raw.mp3` | 75,264 bytes | 1.71 s |
| `digits_words.mp3` | 72,576 bytes | 0.97 s |

These are individual run measurements, not benchmark averages. The supplied voice-chat example shows that an English-only `Hello` was not understood, while a Punjabi-English mixed query — “ਲੁਧਿਆਣਾ ਮੰਡੀ ਵਿੱਚ potato ਦਾ price ਕੀ ਹੈ?” — received a Punjabi potato-price response.

## 5. STT feasibility probe

The supplied probe summary reports:

- 40 audio clips
- 5 speakers
- 158 seconds of audio in total
- 0 unlabelled clips

The visible per-mode output is for the sample clip `q01_spk1.opus`. It shows these results:

| Recognition mode | HTTP result | Wall latency | Output noted in log |
|---|---:|---:|---|
| `pa_verbatim` | Connection failure | — | Host-side connection-aborted error |
| `pa_codemix` | 200 | 1.106 s | Punjabi-script transcript; detected `pa-IN` |
| `paen_codemix` | 200 | 4.032 s | Punjabi-script transcript; detected `pa-IN` |
| `pahien_codemix` | 200 | 1.279 s | Punjabi-script transcript; detected `pa-IN` |
| `auto_codemix` | 200 | 3.121 s | Punjabi-script transcript; detected `pa-IN` |
| `translit` | 200 | 1.036 s | Romanised Punjabi transcript; no detected-language value shown |
| `paen_codemix_ctx` | 200 | 1.068 s | Punjabi-script transcript; detected `pa-IN` |

**Interpretation limits:** the supplied excerpt shows a probe summary and the first clip's mode comparisons. It does not provide aggregate per-mode accuracy, word-error rate, success rate, or latency statistics across all 40 clips. Do not describe these individual timings as a complete STT benchmark. The `pa_verbatim` connection failure should remain visible in any account of this run.

## 6. Database snapshot and coverage

The database diagnostic output identifies `data/mandi.db` as containing **19,959 rows**, with the newest recorded date shown as **26 May 2026**.

The monthly row counts in the supplied diagnostic listing are:

| Month | Rows |
|---|---:|
| November 2025 | 2,240 |
| December 2025 | 6,080 |
| January 2026 | 3,430 |
| February 2026 | 3,294 |
| March 2026 | 4,886 |
| April 2026 | 18 |
| May 2026 | 11 |
| **Total** | **19,959** |

This distribution is heavily concentrated between November 2025 and March 2026. The latest date in the snapshot does not mean that all crops or mandis have records through that date.

### Application coverage shown by the diagnostics

The application test output identifies potato, onion, and tomato as the supported crops in the tested configuration. The database contains additional commodity names, but their presence in the raw database does not by itself mean that the application can answer queries for them.

The nine-mandi coverage listing shows:

| Crop | Mandis with records in the supplied coverage listing |
|---|---|
| Potato | Ludhiana, Khanna, Bathinda, Jalandhar, Amritsar, Patiala, Moga |
| Onion | Ludhiana, Khanna, Bathinda, Jalandhar, Amritsar, Patiala, Moga, Sangrur |
| Tomato | Ludhiana, Khanna, Bathinda, Jalandhar, Amritsar, Patiala, Moga, Sangrur |
| Paddy | Ludhiana, Khanna, Jalandhar, Patiala (sparse records) |
| Maize | Jalandhar only (very sparse) |
| Wheat, cotton, mustard, moong | No matching records in the listed nine-mandi coverage check |

Barnala is listed as a configured mandi in the coverage check but is reported as absent from that database snapshot. Even where a crop/mandi combination has records, the row counts and latest dates vary considerably. The diagnostics, for example, show only a small number of potato, onion, or tomato rows for some mandis.

### Data freshness limitation

The newest database record reported in these outputs is dated **26 May 2026**. These logs were supplied on 10 November 2026, so the snapshot is not current enough to present as live mandi pricing. The application should continue to state each record date and clearly warn users when data is stale. Refresh the dataset and rerun coverage diagnostics before describing the service as providing current prices.

## 7. Issues and follow-up checks

1. **STT connection failure:** investigate the host-side connection abort reported for `pa_verbatim`; do not treat it as a recognition-quality failure without isolating the network/request cause.
2. **STT evaluation completeness:** produce an aggregate report across all 40 labelled clips, including per-mode successful requests, failed requests, latency distribution, and transcript quality against labels. The uploaded excerpt does not establish those aggregate metrics.
3. **TTS wording:** retain `number_to_words` over the malformed `price_to_words` example, and validate the chosen text by listening to a broader set of amounts and dates.
4. **Data freshness and coverage:** refresh the market-price database, rerun the crop-by-mandi coverage check, and preserve the record date in every user-facing answer.
5. **Root URL check:** one manual Uvicorn log shows `GET /` returning `404 Not Found`, while the API test log reports that static web files are served without shadowing the API. Verify the root URL and static-file mount in a fresh local run so the browser entry point is confirmed end to end.
6. **Test completeness:** run the full test suite from the repository root before release. The 92-test figure in this report covers only the five test-module logs listed in Section 2.

## 8. Reproduction commands represented in the supplied logs

Run these from the project root, with the project's dependencies installed. Commands that access Gnani services require a valid local `.env` file; never commit that file or its API keys.

```powershell
uv run python -m tests.test_agent
uv run python -m tests.test_main
uv run python -m tests.test_punjabi_numbers
uv run python -m tests.test_templates
uv run python -m tests.test_tts

uv run --env-file .env python scripts/show_render_modes.py "ludhiana potato rate" --tts
uv run --env-file .env python scripts/smoke_pipeline.py "potato price" "ludhiana"
uv run --env-file .env python scripts/stt_feasibility.py --clips scripts/clips --labels scripts/labels.json --probe
uv run --env-file .env uvicorn app.main:app --reload
```

The STT command above reflects the paths used in the supplied log. If the labels file has since been moved to `eval/labels.json`, update the command and the script's default label path accordingly.

## 9. Suggested placement in the GitHub repository

Save this file at:

```text
docs/OUTPUTS_AND_VALIDATION.md
```

Then link it from the main `README.md`, for example:

```markdown
## Validation and test evidence

See the [Output and Validation Report](docs/OUTPUTS_AND_VALIDATION.md) for automated test results, sample interactions, STT/TTS observations, and database coverage limitations.
```

Keep raw terminal captures, generated MP3 files, temporary probe outputs, and any recordings outside the repository unless there is a clear reason and permission to publish them. This report is the curated, shareable summary.
