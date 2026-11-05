# EchoAI

A real-time AI voice assistant that runs entirely on your own Windows PC. There are no API keys and no
paid services: speech recognition, the AI model and the voice all run locally. You can talk over it at
any time.

```
Browser mic -> AudioWorklet -> WebSocket -> Silero VAD -> faster-whisper -> Qwen (Ollama)
                                                                                |
Browser speaker <- Web Audio <- WebSocket <- Piper or Kokoro <- sentences ------+
```

## Quick start

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Open http://localhost:3000, create an account, press **Start conversation** and allow the microphone.
Stop everything with `scripts\stop.ps1`. Conversations are kept.

`setup.ps1` detects your hardware, chooses models to suit, asks before installing anything, and can be
run again safely. If C: is nearly full, keep Ollama elsewhere: `scripts\setup.ps1 -InstallDir E:\`

## Measured performance

Measured on the development PC: Intel i5-5300U (2 cores, 2015), 8 GB RAM, no usable GPU, with Chrome
and other apps running and under 1 GB of RAM free. Reproduce with `backend\scripts\e2e_voice_test.py`
and `verify_models.py`. **Expect different numbers on your machine.** The app shows real measurements
for every turn.

| Measurement | Result |
|---|---|
| Speech end to first audio, test client without a browser on the same PC | 2.0 to 3.6 s |
| Speech end to first audio, real Chrome on the same PC, under 1 GB RAM free | 4 to 8 s |
| Transcription of a short sentence (Whisper tiny) | 0.9 to 2.4 s, up to 5.3 s with Chrome running |
| AI first token, warm (qwen2.5:0.5b-instruct) | about 0.5 s, sometimes several seconds when memory is short |
| AI generation speed | about 14 tokens/s |
| Piper speed | 9 times faster than real time |
| Kokoro speed | 0.7 times real time (slower than real time) |
| Barge-in: first speech frame to stop signal | 305 to 900 ms (test client) |
| VAD per 32 ms frame | 0.37 ms |

The first-audio target of under 1 second is **not met on this hardware**, and under 500 ms is not close.
Whisper alone takes longer than that here. A modern CPU or a GPU should improve things a lot, but that
has not been measured.

To speed things up on an 8 GB machine, close other apps. The worst latency appeared when free RAM was
lowest, and with Chrome on the same PC, microphone audio sometimes reached the server seconds late in
a burst. The exact cause of those bursts has not been pinned down. The server log reports them, and using
a second device for the browser (`start.ps1 -Https`) would take that load off the PC.

## Known limitations

- **Answer quality.** `qwen2.5:0.5b-instruct` was the only size that stays responsive on the development
  PC. It is very small: it answers simple questions, but it can invent facts and ignore instructions such
  as "explain it simply". `qwen2.5:1.5b-instruct` should answer better, but it will be slower and needs about
  1 GB of RAM (not measured here). To try it: `ollama pull qwen2.5:1.5b-instruct`, then choose it in Settings.
- **Urdu is experimental.** Whisper understands it, and Urdu text is spoken by Kokoro with a Hindi voice
  and approximate pronunciation. But the 0.5b model gave wrong answers in mixed languages when tested, so
  Urdu needs a larger model (not tested). Replying in Urdu also uses Kokoro, which is slower than real time
  on small CPUs.
- **Live transcript comes in bursts.** Whisper is not a streaming recogniser. The growing utterance is
  re-transcribed about every 1.2 s, and these updates pause while a reply is being generated.
- **Pausing cannot resume mid-word.** Saying "continue" asks the model to carry on in new words.
- **Echo handling.** Browser echo cancellation does most of the work. The server raises its speech
  threshold during playback and discards transcripts that match what it just said. This has **not been
  tested with real speakers and a real room**: use headphones if the assistant interrupts itself.
- **Word-level accuracy of saved text.** On interruption, the saved words are estimated in proportion to
  how much of that sentence's audio played, so they may be a word or two off. Text from later sentences is
  never saved as spoken.
- **Barge-in clips the first word sometimes.** "Wait, explain it" was once heard as "Explain it".
- **Only one server process.** Live sessions are held in memory, so this is not set up to scale out.

## Using it from another device

Browsers only allow the microphone on `localhost` or HTTPS, so another device needs HTTPS:

```powershell
scripts\start.ps1 -Https
```

Then open `https://YOUR_PC_IP:8000` on the other device, on the same Wi-Fi, and accept the certificate
warning once. It is a self-signed certificate created on your PC. In this mode the backend serves the UI,
API and WebSocket from one port. `start.ps1` prints the address, or set `ECHOAI_LAN_IP` in `.env` if it
picks the wrong network.

**Windows firewall.** The first start may prompt you to allow Python. To allow it manually, from an
administrator PowerShell, for private networks only:

```powershell
New-NetFirewallRule -DisplayName "EchoAI" -Direction Inbound -Protocol TCP -LocalPort 3000,8000 -Action Allow -Profile Private
```

**CORS.** Requests from private network addresses are allowed automatically. Add others with
`ECHOAI_CORS_ORIGINS` in `.env`.

**Remote access.** Do not forward ports to the internet. Use a private overlay network such as Tailscale
or WireGuard. PostgreSQL is bound to `127.0.0.1` by `docker-compose.yml`, and Ollama listens on loopback by
default. Keep both that way. If you share your network, set `ECHOAI_ALLOW_REGISTRATION=false` after making
your account, or anyone who can reach the port can create one.

## Controls

**Voice commands** must be the whole sentence, so "how do I stop a leaky tap" is treated as a question.

| Say | What happens |
|---|---|
| Pause | Pauses. It keeps listening only for a resume or end command, and nothing is saved |
| Stop | Stops the current reply. With nothing to stop, pauses |
| Continue, I'm back, Listen to me | Resumes |
| Repeat that | Speaks the last reply again |
| End conversation | Ends the session and opens the report |

**Attention modes** (in Settings): Always listening, Wake word (default "echo", awake for 20 seconds),
and Manual pause. The **Pause button** ignores the microphone completely until you press Resume.

## Privacy

Microphone audio is held in memory only while an utterance is being processed. It is never written to disk:
there is no recording feature. Transcripts are saved in your local PostgreSQL database. Delete any session
from History, or set automatic deletion in Settings. Nothing is sent to the internet during conversations.
The internet is only used to download models and packages during setup.

## Cost

External AI API cost is $0.00 because no paid API exists in the system. Electricity and hardware still cost
money. For a rough electricity figure in reports, set `ECHOAI_ELECTRICITY_PRICE_PER_KWH` and
`ECHOAI_POWER_WATTS`. It is an estimate from an assumed wattage, not a measurement, and is hidden until you
set a price.

## Project layout

```
apps/web/            Next.js UI (static export), AudioWorklet in public/worklets/
apps/mobile/         Future Expo app: scaffolding and audio notes, not a working app
backend/app/         FastAPI: api/, websocket/, services/{vad,asr,llm,tts,conversation,history,metrics}
backend/alembic/     Database migrations
backend/tests/       Automated tests
backend/scripts/     Model download, benchmarks, end-to-end tests, certificates
packages/            shared-types, api-client, agent-config (shared with mobile)
infrastructure/      docker-compose.yml (PostgreSQL)
scripts/             setup.ps1, start.ps1, stop.ps1
```

The web UI is served as a static export on port 3000 rather than by a Node server, because that frees
150+ MB for the AI models. For development with hot reload, stop the `web` process and run `npm run dev:web`.

## Testing

```powershell
cd backend
.venv\Scripts\python -m pytest tests                 # logic, API, auth, real models if downloaded
# With EchoAI running:
.venv\Scripts\python scripts\e2e_voice_test.py       # real models and PostgreSQL over a real WebSocket
.venv\Scripts\python scripts\e2e_browser_test.py --out shots   # real Chrome with a fake microphone
```

Unit tests use scripted stand-ins for the models so they can check timing and cancellation precisely. The
end-to-end scripts use the real thing, with a synthesised voice as the "user". No test has yet used a human
voice in a real room, so try that first.

## WebSocket protocol (`/ws/v1/voice`, version 1)

The first frame from the client must be text:
`{"type":"session.start","token":"<jwt>","session_id":"...","device_id":"..."}`

**Binary frames.** Client to server: PCM16 mono 16 kHz (`audio.input`). Server to client: a 12-byte header
(uint32 little-endian `response_seq`, `chunk_seq`, `sample_rate`) and then PCM16 mono.

**Client control messages:** `playback.progress`, `session.pause`, `session.resume`, `session.configure`,
`session.end`, `ping`.

**Server events:** `session.started`, `session.state`, `session.paused`, `session.resumed`, `session.ended`,
`session.handoff`, `speech.started`, `speech.ended`, `transcript.partial`, `transcript.final`,
`transcript.discarded`, `agent.response.started`, `agent.text.delta`, `agent.audio.chunk`,
`agent.response.completed`, `agent.interrupted`, `metrics.updated`, `metrics.turn`, `error`.

Full types are in `packages/shared-types/src/index.ts`. Close codes: 4401 unauthorized, 4403 forbidden,
4408 replaced by a newer connection, 4409 active on another device, 4410 handed off, 4503 models unavailable.

REST endpoints are documented at `http://localhost:8000/docs` while running.

## Troubleshooting

| Problem | Try |
|---|---|
| "Models unavailable" | Run `setup.ps1` again. See `.data\logs\backend.log` |
| AI model shows as unavailable | Is Ollama running? `ollama list` should include the model |
| Long pauses, words cut off | Close other apps. The log warns `ms of audio queued` when the PC cannot keep up |
| It interrupts itself | Use headphones, or lower the microphone sensitivity in Settings |
| It cuts you off mid-sentence | Raise `ECHOAI_VAD_SILENCE_MS` in `.env` (default 750) |
| Microphone blocked on another device | That needs HTTPS: `start.ps1 -Https` |
| "No sound is reaching EchoAI from …" | Choose another microphone in Settings, and check it is not muted in Windows |
| "… does not sound like speech" | Speak closer to the microphone, or choose another one in Settings |

## Progress log

### 2026-11-06: first real-use fixes

The first push worked in automated tests, but failed when tried for real by voice in Chrome. Fixed:

- **Replies cut off after the first sentence.** If the AI finished writing before the next sentence's
  audio was ready, the reply was treated as finished once the first sentence played, and the rest was
  dropped. Now a reply only finishes once every sentence has been synthesised, sent and played. A
  regression test covers this case. Full replies are now spoken and kept in the conversation text.
- **Replies taking 40 to 130 s.** The main cause was the page itself: one Chrome tab used over two CPU
  threads, starving speech recognition, which took 14 to 34 s per sentence. Changes:
  - The orb and waveforms redraw at about 12 fps on a timer instead of 60 fps, and stop while the tab is hidden.
  - Blur effects (`backdrop-filter`) removed.
  - Kokoro is now loaded only when first used. On CPUs with 4 threads or fewer, Piper is the default
    without benchmarking Kokoro. Backend memory went from 561 MB to 308 MB.
  - Whisper uses greedy decoding (beam size 1).
  - Live partial transcripts back off automatically when transcription is slow.
  - Long sentences are split at about 90 characters, so the first audio starts sooner.
- **No feedback when the mic heard nothing.** The server now reports microphone level and speech
  probability. If no speech is detected, the page names the microphone in use.

Measured afterwards with the test client and no browser: first audio 2.3 to 3.4 s, barge-in 456 ms, 82
tests passing. **Not yet measured:** speed in real Chrome after the page changes.

Still to improve: speed in real Chrome, answer quality (the 0.5b model is very small), Urdu, and echo with
real speakers.
