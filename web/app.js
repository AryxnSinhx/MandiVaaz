/* web/app.js - Unit 6: the Mandi Vaaz mic page. Plain JS, no build step.
 *
 * One conversation turn:
 *   tap mic -> record (MediaRecorder) -> tap again (or 10 s) -> POST /ask
 *   -> show transcript + Punjabi reply -> play the mp3 the server sent back.
 *
 * All browser pieces (fetch, MediaRecorder, Audio, timers...) are passed into
 * createApp(env), so the tests can run the whole page with fakes.
 */
(function (root) {
  "use strict";

  var MAX_RECORD_MS = 10000;   // auto-stop; Gnani STT has a 30 s hard limit
  var MIN_RECORD_MS = 500;     // shorter than this is almost certainly a mis-tap
  var MAX_TURNS_SHOWN = 6;
  var MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

  var BUTTON_TEXT = { idle: "Speak", requesting: "\u2026", recording: "Send", thinking: "\u2026", speaking: "Stop" };
  var STATE_LABEL = {
    idle: "Tap to speak",
    requesting: "Allow the microphone when the browser asks",
    recording: "Listening\u2026 tap again to send (sends by itself after 10 s)",
    thinking: "Working on your question\u2026",
    speaking: "Speaking\u2026 tap to stop"
  };
  // statuses where the agent asked the user something and waits for the answer
  var QUESTION_STATUSES = { clarify_crop: 1, clarify_mandi: 1, confirm: 1 };

  /* ---------- small pure helpers (tested on their own) ---------- */

  function pickMimeType(MR) {
    if (!MR || typeof MR.isTypeSupported !== "function") { return ""; }
    for (var i = 0; i < MIME_CANDIDATES.length; i++) {
      if (MR.isTypeSupported(MIME_CANDIDATES[i])) { return MIME_CANDIDATES[i]; }
    }
    return "";   // let the browser choose its default
  }

  function extensionFor(mime) {
    var base = (mime || "").split(";")[0].toLowerCase();
    if (base === "audio/mp4") { return "mp4"; }
    if (base === "audio/ogg") { return "ogg"; }
    return "webm";
  }

  function b64ToBytes(b64) {
    var bin = atob(b64);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) { out[i] = bin.charCodeAt(i); }
    return out;
  }

  // The server accepts 8-64 chars of A-Z a-z 0-9 _ -  (pipeline.clean_session_id)
  function newSessionId(cryptoObj) {
    if (cryptoObj && typeof cryptoObj.randomUUID === "function") { return cryptoObj.randomUUID(); }
    var s = "";
    for (var i = 0; i < 32; i++) { s += Math.floor(Math.random() * 16).toString(16); }
    return s;
  }

  function micErrorMessage(err) {
    var name = err && err.name;
    if (name === "NotAllowedError" || name === "SecurityError") {
      return "Microphone is blocked. Allow it from the lock icon in the address bar, then try again.";
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      return "No microphone was found on this device.";
    }
    if (name === "NotReadableError") {
      return "The microphone is in use by another app. Close it and try again.";
    }
    return "Could not open the microphone (" + (name || "unknown error") + ").";
  }

  function formatTimings(t) {
    if (!t) { return "No timings."; }
    function f(label, v) { return (typeof v === "number") ? label + " " + v.toFixed(2) + " s" : null; }
    var parts = [f("speech-to-text", t.stt_wall), f("agent", t.agent), f("voice", t.tts_wall), f("total", t.total)];
    return parts.filter(function (x) { return x; }).join(" \u00b7 ") || "No timings.";
  }

  /* ---------- the page controller ---------- */

  function createApp(env) {
    var doc = env.document;
    var el = {
      mic: doc.getElementById("mic"),
      label: doc.getElementById("state-label"),
      error: doc.getElementById("error"),
      hint: doc.getElementById("hint"),
      notes: doc.getElementById("notes"),
      log: doc.getElementById("log"),
      replay: doc.getElementById("replay"),
      reset: doc.getElementById("reset"),
      timings: doc.getElementById("timings")
    };

    var state = "idle";
    var sessionId = newSessionId(env.crypto);
    var stream = null, recorder = null, chunks = [], recMime = "";
    var startedAt = 0, stopTimer = null;
    var currentAudio = null, currentUrl = null, lastBlob = null;

    function setState(s) {
      state = s;
      doc.body.setAttribute("data-state", s);
      el.mic.textContent = BUTTON_TEXT[s];
      el.label.textContent = STATE_LABEL[s];
      el.mic.disabled = (s === "requesting" || s === "thinking");
      el.reset.disabled = (s !== "idle");
      el.replay.hidden = !(lastBlob && s === "idle");
    }

    function showError(msg) { el.error.textContent = msg; el.error.hidden = false; }
    function clearMessages() {
      el.error.hidden = true; el.error.textContent = "";
      el.notes.hidden = true; el.notes.textContent = "";
    }
    function setAsking(on) {
      if (on) { doc.body.setAttribute("data-asking", "1"); } else { doc.body.removeAttribute("data-asking"); }
      el.hint.hidden = !on;
      el.hint.textContent = on ? "The assistant asked you a question. Tap the mic and answer it." : "";
    }

    function releaseStream() {
      if (stream && typeof stream.getTracks === "function") {
        stream.getTracks().forEach(function (t) { t.stop(); });   // turns the browser's mic light off
      }
      stream = null;
    }

    /* ----- recording ----- */

    async function startRecording() {
      if (!env.mediaDevices || !env.MediaRecorder) {
        showError("This page needs a microphone and a browser that can record. Open it at " +
                  "http://localhost:8000 in current Chrome, Edge or Safari (microphones do not work " +
                  "on plain http addresses other than localhost).");
        return;
      }
      clearMessages();
      stopPlayback();
      setState("requesting");
      try {
        stream = await env.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        setState("idle");
        showError(micErrorMessage(err));
        return;
      }
      recMime = pickMimeType(env.MediaRecorder);
      try {
        recorder = recMime ? new env.MediaRecorder(stream, { mimeType: recMime }) : new env.MediaRecorder(stream);
      } catch (err) {
        releaseStream();
        setState("idle");
        showError("Could not start recording (" + (err && err.message ? err.message : "unknown") + ").");
        return;
      }
      chunks = [];
      recorder.ondataavailable = function (ev) { if (ev.data && ev.data.size > 0) { chunks.push(ev.data); } };
      recorder.onstop = onRecorderStop;
      recorder.start();
      startedAt = env.now();
      setState("recording");
      stopTimer = env.setTimeout(stopRecording, MAX_RECORD_MS);
    }

    function stopRecording() {
      if (state !== "recording") { return; }
      if (stopTimer !== null) { env.clearTimeout(stopTimer); stopTimer = null; }
      try { recorder.stop(); } catch (err) { releaseStream(); setState("idle"); showError("Recording failed to stop."); }
    }

    function onRecorderStop() {
      releaseStream();
      var duration = env.now() - startedAt;
      var type = recMime || (recorder && recorder.mimeType) || "audio/webm";
      var blob = new env.Blob(chunks, { type: type });
      if (duration < MIN_RECORD_MS || blob.size === 0) {
        setState("idle");
        showError("That was too short. Tap, speak your question, then tap again.");
        return;
      }
      send(blob, type);
    }

    /* ----- talking to the server ----- */

    async function send(blob, type) {
      setState("thinking");
      var form = new env.FormData();
      form.append("audio", blob, "clip." + extensionFor(type));
      form.append("session_id", sessionId);
      var data;
      try {
        var res = await env.fetch("/ask", { method: "POST", body: form });
        if (!res.ok) { throw new Error("server answered " + res.status); }
        data = await res.json();
      } catch (err) {
        setState("idle");
        showError("Could not get an answer (" + (err && err.message ? err.message : "network error") + "). Is the server running?");
        return;
      }
      handleReply(data);
    }

    function handleReply(data) {
      if (data.session_id) { sessionId = data.session_id; }
      appendTurn(data.transcript, data.reply_text, data.status);
      setAsking(!!QUESTION_STATUSES[data.status]);
      el.timings.textContent = formatTimings(data.timings);

      var notes = [];
      (data.failed_stages || []).forEach(function (f) {
        if (f.stage === "tts") { notes.push("Voice reply was unavailable, so this answer is text only."); }
        else if (f.stage === "stt") { notes.push("The recording could not be processed."); }
        else if (f.stage === "agent") { notes.push("Something went wrong while answering."); }
      });
      if (notes.length) { el.notes.textContent = notes.join(" "); el.notes.hidden = false; }

      if (data.audio_b64) {
        lastBlob = new env.Blob([b64ToBytes(data.audio_b64)], { type: data.audio_mime || "audio/mpeg" });
        playBlob(lastBlob);
      } else {
        lastBlob = null;          // never leave an older answer behind the Replay button
        setState("idle");
      }
    }

    function appendTurn(transcript, reply, status) {
      var turn = doc.createElement("div");
      turn.className = "turn";
      turn.setAttribute("data-status", status || "");
      var you = doc.createElement("p");
      you.className = "you";
      you.textContent = "You said: " + (transcript ? transcript : "(nothing was heard)");   // textContent keeps server text from being read as HTML
      var rep = doc.createElement("p");
      rep.className = "reply";
      rep.textContent = reply || "";
      turn.appendChild(you);
      turn.appendChild(rep);
      el.log.appendChild(turn);
      while (el.log.children.length > MAX_TURNS_SHOWN) { el.log.removeChild(el.log.firstChild); }
    }

    /* ----- playback ----- */

    function cleanupAudio() {
      if (currentUrl) { env.URL.revokeObjectURL(currentUrl); currentUrl = null; }
      currentAudio = null;
    }

    function stopPlayback() {
      if (currentAudio) { try { currentAudio.pause(); } catch (e) { /* ignore */ } }
      cleanupAudio();
    }

    function playBlob(blob) {
      cleanupAudio();
      currentUrl = env.URL.createObjectURL(blob);
      var audio = new env.Audio(currentUrl);
      currentAudio = audio;
      audio.onended = function () { if (currentAudio === audio) { cleanupAudio(); setState("idle"); } };
      audio.onerror = function () {
        if (currentAudio === audio) {
          cleanupAudio(); setState("idle");
          showError("The answer audio could not be played. The text answer is above.");
        }
      };
      setState("speaking");
      var p = audio.play();
      if (p && typeof p.catch === "function") {
        p.catch(function () {
          // browsers may block autoplay; the Replay button is a user gesture
          if (currentAudio === audio) {
            cleanupAudio(); setState("idle");
            el.notes.textContent = "Your browser blocked automatic playback. Tap Replay answer to listen.";
            el.notes.hidden = false;
          }
        });
      }
    }

    /* ----- buttons ----- */

    el.mic.addEventListener("click", function () {
      if (state === "idle") { startRecording(); }
      else if (state === "recording") { stopRecording(); }
      else if (state === "speaking") { stopPlayback(); setState("idle"); }
      // requesting / thinking: ignore (the button is disabled anyway)
    });

    el.replay.addEventListener("click", function () {
      if (state === "idle" && lastBlob) { clearMessages(); playBlob(lastBlob); }
    });

    el.reset.addEventListener("click", async function () {
      if (state !== "idle") { return; }
      var old = sessionId;
      sessionId = newSessionId(env.crypto);
      lastBlob = null;
      el.log.textContent = "";
      el.timings.textContent = "No request yet.";
      clearMessages();
      setAsking(false);
      setState("idle");
      try {
        var form = new env.FormData();
        form.append("session_id", old);
        await env.fetch("/reset", { method: "POST", body: form });
      } catch (err) { /* the old session expires on its own after 15 minutes */ }
    });

    // first paint
    setState("idle");
    if (!env.mediaDevices || !env.MediaRecorder) {
      showError("This page needs a microphone and a browser that can record. Open it at " +
                "http://localhost:8000 in current Chrome, Edge or Safari.");
      el.mic.disabled = true;
    }

    return {
      getState: function () { return state; },
      getSessionId: function () { return sessionId; }
    };
  }

  /* ---------- exports and auto-start ---------- */

  var api = {
    createApp: createApp, pickMimeType: pickMimeType, extensionFor: extensionFor,
    b64ToBytes: b64ToBytes, newSessionId: newSessionId, micErrorMessage: micErrorMessage,
    formatTimings: formatTimings, MAX_RECORD_MS: MAX_RECORD_MS, MIN_RECORD_MS: MIN_RECORD_MS,
    MAX_TURNS_SHOWN: MAX_TURNS_SHOWN
  };
  root.MandiVaaz = api;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }

  if (typeof window !== "undefined" && window.document && !window.__MANDI_TEST__) {
    var boot = function () {
      createApp({
        document: window.document,
        fetch: window.fetch.bind(window),
        mediaDevices: window.navigator.mediaDevices,
        MediaRecorder: window.MediaRecorder,
        Audio: window.Audio,
        Blob: window.Blob,
        FormData: window.FormData,
        URL: window.URL,
        crypto: window.crypto,
        setTimeout: window.setTimeout.bind(window),
        clearTimeout: window.clearTimeout.bind(window),
        now: function () { return window.performance.now(); }
      });
    };
    if (window.document.readyState === "loading") { window.document.addEventListener("DOMContentLoaded", boot); }
    else { boot(); }
  }
})(typeof window !== "undefined" ? window : globalThis);