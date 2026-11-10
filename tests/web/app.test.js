// tests/web/app.test.js - page logic tests with a simulated browser (jsdom).
//
// OPTIONAL for you: needs Node.js.   npm install --no-save jsdom   then   node --test tests/web/
// Every browser piece (microphone, recorder, audio player, server) is a fake.
// The real test of the page is still your own browser; see the Unit 6 notes.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const HTML = fs.readFileSync(path.join(__dirname, "..", "..", "web", "index.html"), "utf8");
const M = require(path.join(__dirname, "..", "..", "web", "app.js"));

const flush = async () => { for (let i = 0; i < 6; i++) { await new Promise((r) => setImmediate(r)); } };

// ---------------------------------------------------------------- the fake world
function makeWorld(opts = {}) {
  const dom = new JSDOM(HTML, { url: "http://localhost:8000/" });
  const { document, Blob, FormData } = dom.window;
  const w = {
    dom, document,
    fetchCalls: [], urls: { created: [], revoked: [] }, audios: [], recorders: [],
    stoppedTracks: 0, timers: [], clock: 1000, nextTimerId: 1,
    fetchResponse: opts.fetchResponse || (() => ({ ok: true, status: 200, json: async () => w.replyData })),
    getUserMediaError: opts.getUserMediaError || null,
    supported: opts.supported || ["audio/webm;codecs=opus", "audio/webm"],
    playRejects: !!opts.playRejects,
    replyData: {
      session_id: "server-session-1", request_id: "r1", transcript: "ludhiana potato rate",
      reply_text: "ਲੁਧਿਆਣਾ ਵਿੱਚ ਆਲੂ ਦਾ ਭਾਅ", status: "answer", audio_mime: "audio/mpeg",
      audio_b64: Buffer.from("fake-mp3").toString("base64"), failed_stages: [],
      timings: { stt_wall: 1.2, agent: 0.1, tts_wall: 1.0, total: 2.4 }
    }
  };

  class FakeMediaRecorder {
    static isTypeSupported(m) { return w.supported.includes(m); }
    constructor(stream, options) { this.stream = stream; this.options = options; this.mimeType = (options && options.mimeType) || "audio/webm"; this.state = "inactive"; w.recorders.push(this); }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      if (w.recordBytes !== 0) { this.ondataavailable({ data: new Blob([Buffer.alloc(w.recordBytes || 4000)]) }); }
      this.onstop();
    }
  }
  class FakeAudio {
    constructor(url) { this.url = url; this.paused = false; w.audios.push(this); }
    play() { return w.playRejects ? Promise.reject(new Error("NotAllowedError")) : Promise.resolve(); }
    pause() { this.paused = true; }
    finish() { this.onended(); }
  }

  w.env = {
    document,
    fetch: async (url, init) => { w.fetchCalls.push({ url, init }); return w.fetchResponse(url, init); },
    mediaDevices: opts.noMic ? undefined : {
      getUserMedia: async () => {
        if (w.getUserMediaError) { const e = new Error("denied"); e.name = w.getUserMediaError; throw e; }
        return { getTracks: () => [{ stop() { w.stoppedTracks++; } }] };
      }
    },
    MediaRecorder: opts.noMic ? undefined : FakeMediaRecorder,
    Audio: FakeAudio, Blob, FormData,
    URL: { createObjectURL: () => { const u = "blob:fake" + (w.urls.created.length + 1); w.urls.created.push(u); return u; },
           revokeObjectURL: (u) => w.urls.revoked.push(u) },
    crypto: { randomUUID: () => "11111111-2222-3333-4444-" + String(w.nextTimerId++).padStart(12, "0") },
    setTimeout: (fn, ms) => { const id = w.timers.length + 1; w.timers.push({ id, fn, ms, live: true }); return id; },
    clearTimeout: (id) => { const t = w.timers.find((x) => x.id === id); if (t) { t.live = false; } },
    now: () => w.clock
  };
  w.app = M.createApp(w.env);
  w.$ = (id) => document.getElementById(id);
  w.click = (id) => w.$(id).dispatchEvent(new dom.window.Event("click"));
  w.state = () => document.body.getAttribute("data-state");
  w.fireTimer = () => { const t = w.timers.find((x) => x.live); t.live = false; t.fn(); };
  return w;
}

// record -> stop -> wait for the (fake) server round trip
async function speak(w, ms = 2000) {
  w.click("mic"); await flush();
  assert.equal(w.state(), "recording");
  w.clock += ms;
  w.click("mic"); await flush();
}

// ------------------------------------------------------------------ helpers
test("pickMimeType follows the preference order and falls back to empty", () => {
  assert.equal(M.pickMimeType({ isTypeSupported: (m) => m === "audio/webm" }), "audio/webm");
  assert.equal(M.pickMimeType({ isTypeSupported: () => true }), "audio/webm;codecs=opus");
  assert.equal(M.pickMimeType({ isTypeSupported: (m) => m === "audio/mp4" }), "audio/mp4");
  assert.equal(M.pickMimeType({ isTypeSupported: () => false }), "");
  assert.equal(M.pickMimeType(undefined), "");
});

test("extensionFor strips codec parameters", () => {
  assert.equal(M.extensionFor("audio/webm;codecs=opus"), "webm");
  assert.equal(M.extensionFor("audio/mp4"), "mp4");
  assert.equal(M.extensionFor("audio/ogg;codecs=opus"), "ogg");
  assert.equal(M.extensionFor(""), "webm");
});

test("b64ToBytes round trips", () => {
  assert.deepEqual(Array.from(M.b64ToBytes(Buffer.from("abc").toString("base64"))), [97, 98, 99]);
});

test("session ids always satisfy the server's pattern", () => {
  const re = /^[A-Za-z0-9_-]{8,64}$/;
  assert.match(M.newSessionId({ randomUUID: () => "11111111-2222-3333-4444-555555555555" }), re);
  assert.match(M.newSessionId(undefined), re);
});

test("formatTimings handles missing values", () => {
  assert.equal(M.formatTimings(null), "No timings.");
  assert.equal(M.formatTimings({ stt_wall: 1.234, total: 2 }), "speech-to-text 1.23 s \u00b7 total 2.00 s");
});

// ------------------------------------------------------------- the page flow
test("starts idle with the right label", () => {
  const w = makeWorld();
  assert.equal(w.state(), "idle");
  assert.equal(w.$("mic").textContent, "Speak");
  assert.equal(w.$("state-label").textContent, "Tap to speak");
  assert.equal(w.$("error").hidden, true);
});

test("full round trip: record, upload, show, play, back to idle", async () => {
  const w = makeWorld();
  await speak(w);

  assert.equal(w.fetchCalls.length, 1);
  const { url, init } = w.fetchCalls[0];
  assert.equal(url, "/ask");
  assert.equal(init.method, "POST");
  assert.match(init.body.get("session_id"), /^[A-Za-z0-9_-]{8,64}$/);
  assert.equal(init.body.get("audio").name, "clip.webm");
  assert.equal(w.recorders[0].options.mimeType, "audio/webm;codecs=opus");
  assert.equal(w.stoppedTracks, 1, "microphone tracks must be released");

  assert.match(w.$("log").textContent, /ludhiana potato rate/);
  assert.match(w.$("log").textContent, /ਲੁਧਿਆਣਾ/);
  assert.equal(w.state(), "speaking");
  assert.equal(w.audios.length, 1);
  assert.match(w.$("timings").textContent, /total 2.40 s/);

  w.audios[0].finish();
  assert.equal(w.state(), "idle");
  assert.deepEqual(w.urls.revoked, ["blob:fake1"], "object URL is released after playback");
  assert.equal(w.$("replay").hidden, false);
});

test("the server's session id is adopted and reused on the next turn", async () => {
  const w = makeWorld();
  await speak(w); w.audios[0].finish();
  await speak(w);
  assert.equal(w.fetchCalls[1].init.body.get("session_id"), "server-session-1");
});

test("server text is shown as text, never as HTML", async () => {
  const w = makeWorld();
  w.replyData.reply_text = '<img src=x onerror="window.pwned=1">';
  w.replyData.transcript = "<b>hi</b>";
  await speak(w);
  assert.equal(w.$("log").querySelectorAll("img, b").length, 0);
  assert.match(w.$("log").textContent, /<img src=x/);
});

test("auto-stop after the maximum recording time", async () => {
  const w = makeWorld();
  w.click("mic"); await flush();
  assert.equal(w.state(), "recording");
  assert.equal(w.timers[0].ms, M.MAX_RECORD_MS);
  w.clock += M.MAX_RECORD_MS;
  w.fireTimer(); await flush();
  assert.equal(w.fetchCalls.length, 1);
});

test("manual stop cancels the auto-stop timer", async () => {
  const w = makeWorld();
  await speak(w);
  assert.equal(w.timers[0].live, false);
});

test("a mis-tap (too short) sends nothing and says so", async () => {
  const w = makeWorld();
  await speak(w, 100);
  assert.equal(w.fetchCalls.length, 0);
  assert.equal(w.state(), "idle");
  assert.match(w.$("error").textContent, /too short/);
  assert.equal(w.stoppedTracks, 1);
});

test("an empty recording sends nothing", async () => {
  const w = makeWorld();
  w.recordBytes = 0;
  await speak(w);
  assert.equal(w.fetchCalls.length, 0);
  assert.match(w.$("error").textContent, /too short/);
});

test("microphone permission denied: clear message, button usable again", async () => {
  const w = makeWorld({ getUserMediaError: "NotAllowedError" });
  w.click("mic"); await flush();
  assert.equal(w.state(), "idle");
  assert.match(w.$("error").textContent, /blocked/);
  assert.equal(w.$("mic").disabled, false);
});

test("no microphone found has its own message", async () => {
  const w = makeWorld({ getUserMediaError: "NotFoundError" });
  w.click("mic"); await flush();
  assert.match(w.$("error").textContent, /No microphone/);
});

test("browser without recording support: message shown, mic disabled", () => {
  const w = makeWorld({ noMic: true });
  assert.equal(w.$("mic").disabled, true);
  assert.match(w.$("error").textContent, /localhost/);
});

test("server error (HTTP 500) shows a message and returns to idle", async () => {
  const w = makeWorld({ fetchResponse: () => ({ ok: false, status: 500, json: async () => ({}) }) });
  await speak(w);
  assert.equal(w.state(), "idle");
  assert.match(w.$("error").textContent, /500/);
});

test("network failure shows a message and returns to idle", async () => {
  const w = makeWorld({ fetchResponse: () => { throw new Error("Failed to fetch"); } });
  await speak(w);
  assert.equal(w.state(), "idle");
  assert.match(w.$("error").textContent, /Failed to fetch/);
});

test("clicks while thinking are ignored (no double upload)", async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const w = makeWorld({ fetchResponse: async () => { await gate; return { ok: true, status: 200, json: async () => w.replyData }; } });
  await speak(w);
  assert.equal(w.state(), "thinking");
  assert.equal(w.$("mic").disabled, true);
  w.click("mic"); w.click("mic"); await flush();
  assert.equal(w.fetchCalls.length, 1);
  release(); await flush();
  assert.equal(w.state(), "speaking");
});

test("tapping while speaking stops the audio", async () => {
  const w = makeWorld();
  await speak(w);
  w.click("mic");
  assert.equal(w.audios[0].paused, true);
  assert.equal(w.state(), "idle");
  assert.equal(w.urls.revoked.length, 1);
});

test("blocked autoplay falls back to the Replay button", async () => {
  const w = makeWorld({ playRejects: true });
  await speak(w);
  assert.equal(w.state(), "idle");
  assert.match(w.$("notes").textContent, /Replay/);
  assert.equal(w.$("replay").hidden, false);
  w.playRejects = false;
  w.click("replay"); await flush();
  assert.equal(w.state(), "speaking");
  assert.equal(w.audios.length, 2);
});

test("question statuses show the hint and clear it on the next answer", async () => {
  const w = makeWorld();
  w.replyData.status = "clarify_mandi";
  await speak(w); w.audios[0].finish();
  assert.equal(w.$("hint").hidden, false);
  assert.equal(w.document.body.getAttribute("data-asking"), "1");
  w.replyData.status = "answer";
  await speak(w);
  assert.equal(w.$("hint").hidden, true);
  assert.equal(w.document.body.getAttribute("data-asking"), null);
});

test("text-only reply (TTS failed) says so and offers no stale replay", async () => {
  const w = makeWorld();
  await speak(w); w.audios[0].finish();                  // a first, good turn with audio
  w.replyData.audio_b64 = null; w.replyData.audio_mime = null;
  w.replyData.failed_stages = [{ stage: "tts", kind: "timeout" }];
  await speak(w);
  assert.equal(w.state(), "idle");
  assert.match(w.$("notes").textContent, /text only/);
  assert.equal(w.$("replay").hidden, true, "must not replay the previous answer");
});

test("a log of more than the limit keeps only the newest turns", async () => {
  const w = makeWorld();
  for (let i = 0; i < M.MAX_TURNS_SHOWN + 2; i++) { w.replyData.transcript = "q" + i; await speak(w); w.audios[i].finish(); }
  assert.equal(w.$("log").children.length, M.MAX_TURNS_SHOWN);
  assert.doesNotMatch(w.$("log").textContent, /q0\b/);
  assert.match(w.$("log").textContent, new RegExp("q" + (M.MAX_TURNS_SHOWN + 1)));
});

test("New conversation resets the session on the server and clears the page", async () => {
  const w = makeWorld();
  await speak(w); w.audios[0].finish();
  const before = w.app.getSessionId();
  w.click("reset"); await flush();
  const resetCall = w.fetchCalls[w.fetchCalls.length - 1];
  assert.equal(resetCall.url, "/reset");
  assert.equal(resetCall.init.body.get("session_id"), before);
  assert.notEqual(w.app.getSessionId(), before);
  assert.equal(w.$("log").children.length, 0);
  assert.equal(w.$("replay").hidden, true);
});

test("New conversation is disabled while busy", async () => {
  const w = makeWorld();
  w.click("mic"); await flush();
  assert.equal(w.$("reset").disabled, true);
});