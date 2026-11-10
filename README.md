# Mandi Vaaz

**A voice-first mandi-price assistant for Punjab. Ask in Punjabi or Punglish and hear a Punjabi response.**

Mandi Vaaz is a prototype web application that lets a user ask about a crop's mandi (market) price using speech. It combines Gnani Prisma speech-to-text, a lightweight rule-based parser and dialogue state machine, a local Agmarknet price snapshot, fixed Punjabi response templates, and Gnani Timbre text-to-speech.

> [!IMPORTANT]
> **This is not a live-price service.** The bundled SQLite database is a historical Agmarknet snapshot. Its 19,959 rows span **1 November 2025 to 26 May 2026**, but records for the three enabled crops are concentrated around March and early April 2026. The application reports the record date and warns when a price is older than three days. Do not use the displayed values as today's market rates or as the sole basis for a trading decision.

## At a glance

| Area | Current implementation |
|---|---|
| Interface | Browser-based voice interface with transcript, reply text, audio playback and replay control |
| Main input styles | Punjabi (Gurmukhi) and Punglish (Punjabi grammar with English words) |
| Reply language | Punjabi, generated from fixed templates and spoken by Timbre v3.0 |
| Speech recognition | Prisma v3.0; default `paen_codemix_ctx` configuration (`pa-IN,en-IN`, `codemix`, topic and keyterms) |
| Understanding | Unicode normalization, alias tables, RapidFuzz matching and a Python state machine |
| Price source | Local SQLite snapshot ingested from Agmarknet via data.gov.in |
| Crops enabled for answers | Potato, onion and tomato |
| Markets recognised | Nine Punjab mandis; Barnala and Sangrur are deliberately marked unanswerable in the current agent |
| Backend | FastAPI |
| Response generation | Deterministic Punjabi templates; no LLM is used to invent or phrase prices |

## Why Mandi Vaaz?

Market-price information is less useful when a farmer has to navigate a text-heavy interface or work around unfamiliar language. Mandi Vaaz explores a voice-first interaction: speak a crop and market question naturally, have the key details extracted, and hear a short answer in Punjabi. The project is deliberately narrow so that the speech, parsing, dialogue and data-lookup stages can be tested separately.

## Features

- **Voice input in the browser:** the page records a short audio clip using `MediaRecorder` and sends it to the backend.
- **Punjabi and Punglish recognition:** Prisma v3.0 is configured for Punjabi and English code-mixing. The alias tables include Gurmukhi, Roman and selected Devanagari spellings for crop and mandi names.
- **Crop and mandi extraction:** Unicode normalization, exact aliases and RapidFuzz similarity matching map variants and spelling errors to canonical IDs.
- **Dialogue handling:** the state machine asks for a missing crop or mandi, asks for confirmation when a match is weak, and refuses unsupported or unavailable requests rather than fabricating a price.
- **Historical price lookup:** parameterized SQLite queries select the most recent suitable record. If an eligible mandi has no exact record, the lookup can try a nearby mandi within 120 km and identify the substitute market in the reply.
- **Freshness disclosure:** replies state the record date and include a warning when the record is more than three days old. The app does not relabel old data as today's price.
- **Punjabi speech output:** fixed templates produce a short Punjabi response; the price and date numbers are converted to Punjabi words before Timbre v3.0 generates MP3 audio.
- **Comparison handling:** a comparison request can return up to two mandi results. The controller avoids silently answering only one market when a two-market comparison was requested but only one was recognised.
- **Session handling:** the backend keeps dialogue state per session, expires inactive sessions after 15 minutes, caps the store at 500 sessions, and limits concurrent Gnani calls by default.
- **Degraded responses:** when STT or the agent fails, the pipeline returns an error message; when TTS fails, it keeps the text response available without audio.

## Language support: what is and is not supported

| Capability | Status | Evidence and qualification |
|---|---|---|
| Punjabi speech input | **Implemented and evaluated** | The current default uses `pa-IN,en-IN` codemix. The final 18-clip evaluation contained nine Punjabi clips. |
| Punglish speech input | **Implemented and evaluated** | The same evaluation contained nine Punglish clips; both styles were tested through the real STT-to-parser path. |
| Hindi-mixed input | **Experimental / partial** | The parser has selected Devanagari aliases, and an earlier 40-clip feasibility set included 12 Hindi-mixed clips. The candidate `pa-IN,hi-IN,en-IN` configuration scored 11/12 on that group, while the app's chosen `pa-IN,en-IN` contextual configuration scored 7/12. These are small feasibility results, not a dedicated Hindi release test. |
| Full Hindi conversation and Hindi spoken replies | **Not currently supported as an end-to-end mode** | The runtime STT configuration does not expose the `pa-IN,hi-IN,en-IN` test configuration, and the app's reply templates and TTS default are Punjabi (`pa-IN`). The final 18-clip held-out evaluation did not include Hindi clips. |

**Recommended public wording:** “Punjabi and Punglish voice input with Punjabi spoken responses; limited experimental compatibility with Hindi-mixed input.” Avoid claiming full Hindi support until a Hindi runtime mode, Hindi response templates/TTS selection, and a separate Hindi test set have been implemented and evaluated.

## How it works

```mermaid
flowchart TD
    A[Browser microphone / MediaRecorder] --> B[FastAPI POST /ask]
    B --> C[ffmpeg: browser audio to 16 kHz mono WAV]
    C --> D[Gnani Prisma v3.0 speech-to-text]
    D --> E[Normalize text and match crop / mandi aliases]
    E --> F[Dialogue state machine]
    F -->|Need details or confirmation| G[Punjabi clarification template]
    F -->|Ready to answer| H[SQLite Agmarknet snapshot lookup]
    H --> I[Punjabi template + number-to-words]
    I --> J[Gnani Timbre v3.0 MP3]
    G --> J
    J --> K[Browser shows transcript / reply and plays audio]
```

1. **Capture and transport:** the browser records audio and uploads it to FastAPI. The Gnani API key remains on the server.
2. **Audio conversion and transcription:** ffmpeg converts browser audio to 16 kHz mono WAV; Prisma v3.0 returns a transcript.
3. **Normalization and parsing:** text is normalized and crop/mandi names are matched against aliases. The parser returns canonical IDs, match scores, intent and status.
4. **Dialogue decision:** the state machine decides whether to clarify, confirm, answer or refuse.
5. **Price lookup:** a parameterized SQLite query retrieves the relevant historical record, or applies an eligible nearby-market fallback.
6. **Response and playback:** fixed Punjabi templates are filled with database values, numbers are rendered as Punjabi words, Timbre creates an MP3, and the browser displays and plays the result.

The controller is intentionally a small Python state machine—not an LLM agent or LangGraph workflow. Price facts come from the database, while fixed templates reduce the chance of a language model inventing a number.

## Current data scope

### Crops

The parser knows aliases for nine crops, but the agent only enables price answers for:

- Potato
- Onion
- Tomato

Wheat, cotton, mustard, moong, paddy and maize can be recognised as crop names but are not enabled for price answers in the current application. The database contains a few paddy and maize records, but the current `SUPPORTED_CROPS` allowlist is intentionally limited to the three vegetables above.

### Mandis

The parser recognises Ludhiana, Khanna, Bathinda, Jalandhar, Amritsar, Patiala, Moga, Sangrur and Barnala. **Barnala and Sangrur are explicitly marked unanswerable** in the current agent. For the other markets, exact crop-market coverage is uneven; if no exact record is available, the lookup may use a nearby mandi within the configured 120 km limit. The reply template is designed to name the substitute mandi.

### Snapshot characteristics

- Database: `data/mandi.db`
- Rows: **19,959**
- Overall date range: **2025-11-01 to 2026-05-26**
- Rows by month: Nov 2025: 2,240; Dec: 6,080; Jan 2026: 3,430; Feb: 3,294; Mar: 4,886; Apr: 18; May: 11
- Product records in the snapshot: potato 987, onion 1,089, tomato 780 (the application enables these three)
- Unit: rupees per quintal (100 kg)

The latest database row is not the same as fresh coverage for every crop-market pair. Most markets' latest records are around 31 March 2026, and some pairs have only a handful of records. The interface therefore labels the data as a snapshot and speaks the record date.

## Evaluation and observed performance

### Held-out audio evaluation

The final held-out set contains **18 read-aloud clips**: nine Punjabi (Gurmukhi) and nine Punglish (Roman script). Each evaluation scores the first turn. Speaker identities and recording conditions were not documented, so this is not a representative farmer or field-noise study.

| Run | Crop and mandi both correct | Notes |
|---|---:|---|
| 9 Nov run 1 | 16/18 (88.9%; 95% Wilson CI 67–97%) | Two mandi-name substitutions by STT |
| 9 Nov run 2 | 16/18 (88.9%; 95% Wilson CI 67–97%) | Same two failures; application code fingerprints unchanged |
| 10 Nov repeat | 18/18 (100%; 95% Wilson CI 82–100%) | Same 18 clips and code fingerprints; no mismatches on this run |

The two failures in the 16/18 runs were **Ludhiana heard as Jalandhar** and **Bathinda heard as Patiala**. These are significant because the mistaken names were valid mandi aliases, so the parser accepted them as exact. A correct status alone does not guarantee a correct market price. The variation across repeated calls also means the 18/18 run should not be presented as proof of 100% accuracy.

### Earlier STT feasibility study

An earlier comparison used 40 clips from five speaker labels and 20 prompts split across pure Punjabi, Punglish and Hindi-mixed groups. On the Hindi-mixed subset (12 clips), the `pa-IN,hi-IN,en-IN` codemix candidate scored 11/12, while the app's contextual `pa-IN,en-IN` configuration scored 7/12. These were feasibility results from a small test set; alias tuning on the same clips improved some overall scores and makes those “after tuning” numbers optimistic. They should not be confused with the later 18-clip held-out evaluation.

### Latency measurements

Measurements below are from the 9 Nov run on the builder's machine. The STT client round-trip **excludes** ffmpeg conversion; the approximate typical STT path is therefore conversion plus client round-trip.

| Stage | p50 | p95 | Maximum / sample |
|---|---:|---:|---|
| ffmpeg conversion | 0.18 s | 1.22 s | 4.90 s; 18 clips |
| STT HTTP client round-trip | 1.16 s | 1.29 s | 18 clips |
| Gnani-side STT end-to-end | 0.16 s | 0.19 s | 18 clips |
| Parser + agent + SQLite | 0.01 s | 0.03 s | 18 clips |
| Timbre TTS HTTP call | 2.43 s | 2.64 s | 9 real replies |

A browser-measured, end-to-end “user stops speaking → audio starts playing” latency was **not** established. The sum of stage medians is only an estimate and excludes browser recording, upload and playback.

### Automated tests

During preparation of this README, all **165 tests across the 10 included Python test modules passed** in the review environment. These tests cover aliases/parser-adjacent logic, agent behaviour, lookup selection, Punjabi number conversion, templates, STT/TTS error handling and mocked requests, FastAPI routes, web files and evaluation logic. Mocked API tests do not replace a live Gnani API or real-browser check.

The FastAPI test client also needs `httpx`, which is a test-only dependency for this snapshot:

```bash
python -m pip install httpx
```

Run the suites from the repository root:

```bash
python -m tests.test_punjabi_numbers
python -m tests.test_templates
python -m tests.test_agent
python -m tests.test_lookup_pick
python -m tests.test_stt
python -m tests.test_tts
python -m tests.test_main
python -m tests.test_to_words
python -m tests.test_web_files
python -m tests.test_evaluate
```

The optional browser JavaScript test is `tests/web/app.test.js`; it requires Node.js and `jsdom`.

## Limitations and how to address them

| Limitation / risk | Why it matters | Practical next step |
|---|---|---|
| Historical, stale snapshot | The app cannot tell a farmer today's price from this dataset. | Re-ingest on a schedule, verify the API's total/page count, track a dataset timestamp, and stop answering when freshness rules fail. |
| Narrow crop coverage | Only potato, onion and tomato are enabled for answers. | Expand the allowlist only after verifying crop names, varieties and market coverage in the source data. |
| Uneven mandi coverage | Some crop-market pairs have few or no exact rows; two mandis are blocked. | Improve source coverage and coordinates; make fallback distance and actual source mandi prominent; do not silently substitute markets. |
| Valid-but-wrong STT entities | A wrong recognised mandi can look exact and bypass fuzzy-match confirmation. | Echo the recognised mandi and require explicit confirmation for risky cases; build a held-out confusion test for similar market names. |
| Hindi is not a complete runtime mode | Hindi-mixed performance varied by STT configuration, and the app speaks Punjabi. | Add a selectable/detected Hindi configuration, Hindi response templates and `hi-IN` TTS; then evaluate with a separate Hindi-only and Hindi-mixed test set. |
| Small, read-aloud test set | Current accuracy does not establish performance for farmers, accents or noisy fields. | Collect consented recordings from more speakers, including field noise, different devices and unseen question phrasing. |
| Punjabi wording and number forms need native review | A technically correct number can still be pronounced or phrased unnaturally. | Ask fluent Punjabi speakers to review every template and number conversion, then run a listening test. |
| Browser-to-ear latency and mobile compatibility are not fully tested | Backend stage timings do not include the full user wait; phone browsers may behave differently. | Measure mic-release-to-playback latency and test Android Chrome, iOS Safari and real network conditions. |
| Context/keyterm bias may over-favour known names | A word outside the alias list could be nudged toward a supported crop or mandi. | Add out-of-list crop/mandi test clips and refuse ambiguous or out-of-domain entities rather than forcing a match. |

## Tech stack

- **Backend:** Python, FastAPI, Uvicorn
- **Speech:** Gnani Prisma v3.0 (STT), Gnani Timbre v3.0 (TTS)
- **Audio conversion:** ffmpeg
- **Parsing:** Unicode normalization, handwritten alias tables, RapidFuzz
- **Dialogue:** small Python finite-state controller
- **Data:** SQLite, pandas/CSV ingestion utilities, Agmarknet via data.gov.in
- **Frontend:** HTML, CSS, JavaScript, `MediaRecorder` and browser audio playback
- **Testing:** Python test modules with fake HTTP/API dependencies; optional Node.js + jsdom UI test

## Run locally

### Prerequisites

- Python 3.13 (the uploaded project was tested with Python 3.13)
- ffmpeg installed and available on `PATH`
- A Gnani API key with access to Prisma v3.0 and Timbre v3.0
- Internet access for the speech APIs

The uploaded ZIP does not contain `pyproject.toml`, `uv.lock` or `requirements.txt`, so install the runtime packages directly for this snapshot:

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

python -m pip install fastapi uvicorn python-multipart requests rapidfuzz
```

### Configure the API key

Set the key in the backend process environment. For Windows PowerShell:

```powershell
$env:GNANI_API_KEY = "YOUR_GNANI_API_KEY"
```

For macOS/Linux:

```bash
export GNANI_API_KEY="YOUR_GNANI_API_KEY"
```

Do not place the key in frontend JavaScript or commit it to Git.

### Start the application

From the project root:

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in a browser and allow microphone access.

### API endpoints

| Method and path | Purpose |
|---|---|
| `GET /` | Serves the browser application from `web/` |
| `POST /ask` | Accepts multipart form data (`audio` file and optional `session_id`) and returns transcript, reply text, status, audio payload and timings |
| `POST /reset` | Resets the supplied `session_id` |
| `GET /health` | Returns basic backend and web-directory status |

### Refresh the data snapshot

The database is already bundled, so ingestion is not required just to run the demo. To refresh it, obtain a data.gov.in API key and use the ingestion script:

```bash
python scripts/ingest_prices.py --source ogd --api-key "YOUR_DATA_GOV_IN_API_KEY"
```

Inspect the resulting coverage and dates before describing the dataset as current. The API feed, pagination and Punjab coverage should be checked on every refresh.

## Repository hygiene before publishing

The uploaded ZIP includes raw voice recordings, temporary outputs, backup files and Python cache files. **Do not push the ZIP contents unchanged.** Before making the repository public:

- Exclude `.env`, API keys, `.venv/`, `__pycache__/`, `*.pyc`, `*.bak`, `node_modules/`, scratch MP3 files and temporary STT output folders.
- Exclude `eval/heldout/`, `scripts/clips*` and `stt_test_out/wav/` unless every speaker has explicitly consented to public redistribution of their recordings. Keep only the labels and aggregate results that are appropriate to publish.
- Check the data.gov.in source's reuse/attribution terms before redistributing `data/mandi.db`; clearly label it as a dated snapshot.
- Add a dependency manifest (`requirements.txt` or `pyproject.toml`) and a `.env.example` containing variable names only.

A minimal `.gitignore` should include at least:

```gitignore
.env
.venv/
__pycache__/
*.py[cod]
*.bak
node_modules/
```

## Project structure

```text
app/
  agent.py             # dialogue state machine
  aliases.py           # crop and mandi alias tables
  lookup.py            # SQLite price lookup and fallback logic
  main.py              # FastAPI application and endpoints
  parser.py            # intent, crop, mandi and date parsing
  pipeline.py          # one audio-to-reply turn and session store
  punjabi_numbers.py   # integer-to-Punjabi-word conversion
  stt.py               # ffmpeg conversion and Prisma v3.0 client
  templates.py         # fixed Punjabi reply templates
  tts.py               # Timbre v3.0 client
web/
  index.html
  app.js
data/
  mandi.db             # historical Agmarknet snapshot
scripts/                # data ingestion, diagnostics and STT feasibility tools
eval/                   # held-out labels, evaluator and aggregate results
tests/                  # Python unit/integration-style tests
```

## Responsible-use note

Mandi Vaaz is an experimental prototype, not a government price portal or a verified market advisory system. Always check the date, crop, requested mandi and any substitute mandi stated in the response. Historical data, sparse coverage and speech-recognition errors can all produce an answer that is unsuitable for a real transaction.
