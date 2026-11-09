# How EchoAI Works

EchoAI is a real-time voice assistant. You talk to it in a web browser, it answers out loud, and you can
cut in at any moment, the way you would with a person. Inside the app it introduces itself as **Jarvis**,
which is also its default wake word.

## The 30-second explanation

> EchoAI is a voice agent you talk to like a phone call. It listens through the browser, notices when you
> stop talking, turns your speech into text, asks an AI model for an answer, and speaks the answer back one
> sentence at a time. If you start talking while it is speaking, it stops straight away and answers from
> the point where you cut in. It remembers the conversation, and saves a history and a report of each one.

## What happens when you say something

```
 YOU speak
    |
    v
 1. BROWSER        captures the microphone and streams the sound live to the server
    |
    v
 2. VAD            "Is this speech? Has the person finished?"     (Silero)
    |
    v
 3. SPEECH-TO-TEXT turns your words into text                     (Whisper)
    |
    v
 4. COMMAND CHECK  was that a command like "stop" or "pause"?
    |
    v
 5. AI MODEL       writes the reply, word by word                 (Qwen)
    |
    v
 6. SENTENCE SPLIT sends each finished sentence on immediately
    |
    v
 7. TEXT-TO-SPEECH turns each sentence into voice                 (Piper or Kokoro)
    |
    v
 8. BROWSER        plays the voice and reports how much was heard
    |
    v
 YOU hear the answer
```

### Each step in plain words

1. **Listening.** The browser sends the microphone sound to the server in tiny pieces over a WebSocket, a
   connection that stays open in both directions.
2. **Knowing when you are done (VAD, Voice Activity Detection).** A small model scores every 32 milliseconds
   of sound for how much it sounds like speech. About half a second of silence means you have finished. If
   your sentence sounds unfinished, for example it ends in "and" or "because", the agent waits longer, up to
   1.8 seconds.
3. **Understanding (speech-to-text).** Whisper converts your finished sentence to text.
4. **Commands.** If the whole sentence is a command such as "stop", "pause", "continue", "repeat that" or
   "end conversation", the agent does that instead of answering. "How do I stop a leaky tap?" is still treated
   as a question, because a command must be the entire sentence.
5. **Thinking (the AI model).** The agent sends the model the recent conversation plus its rules: speak in
   plain short sentences, give the direct answer first, and say so if it does not know something. When a
   conversation gets long, the oldest messages are dropped first.
6. **Sentence by sentence.** The agent does not wait for the whole reply. Each sentence goes to the voice
   step as soon as it is written, so speaking starts much sooner.
7. **Speaking (text-to-speech).** Piper (very fast) or Kokoro (more natural but slow on this PC) creates the
   voice audio.
8. **Playing.** The browser plays the audio and tells the server how much has been played. Only what you
   actually heard is saved as the agent's reply.

## What makes it feel like a real conversation

**You can interrupt it (barge-in).** The microphone stays on while the agent speaks. When it detects you
talking, it silences the speaker, cancels the rest of the reply, and starts listening. The next reply knows
the last words you heard, so it responds from that point and does not start over.

**It tries not to answer itself.** While speaking, the agent needs stronger and longer speech before it
counts as an interruption, so room noise is less likely to stop it. If the microphone picks up the agent's
own voice, the text is compared with what it just said and thrown away if it matches. Headphones still work
best.

**It stays in step.** Every reply has a number. When a reply is interrupted, any late audio from it has an
old number and is ignored, so old speech never plays over new speech.

**It keeps going when something fails.** If the online AI hits a limit or the internet drops, the agent
switches to spare models, then to the models on the PC, usually within the same turn.

## Two engines

| | **Groq (online, fast)** | **Local (private)** |
|---|---|---|
| Speech-to-text | `whisper-large-v3-turbo` on Groq | Whisper tiny on the PC |
| AI model | `qwen3.8-27b`, with spare models | `qwen2.5:0.5b-instruct` through Ollama |
| Voice | Piper or Kokoro on the PC | Piper or Kokoro on the PC |
| Time from end of your speech to first audio | About 1 second in automated tests | 2 to 8 seconds on this PC |
| Answer quality | Much better, because the model is larger | Basic, because the model is very small |
| Privacy | Audio and text go to Groq | Audio and text stay on the PC |
| Cost | Free tier, limited per model per day | Free |

The engine is chosen in Settings and can be changed during a conversation.

## The parts of the system

| Part | What it does | Built with |
|---|---|---|
| Web app | Screen, microphone, playback, settings, history, reports | Next.js, React, Web Audio |
| Backend server | Runs the conversation, connects all the models | Python, FastAPI, WebSocket |
| Database | Accounts, settings, conversations, measurements | PostgreSQL, hosted on Supabase |
| Local AI host | Runs the AI model on the PC | Ollama |
| Public access (optional) | Lets other devices reach the agent running on this PC | Docker Compose, Cloudflare Tunnel, Netlify |

The website can be hosted online, but the "brain" still runs on this PC. If the PC or its backend is off, the
agent does not answer.

## Features

- Natural voice conversation that you can interrupt at any time
- Voice commands: pause, stop, continue, repeat that, end conversation
- Three listening modes: always listening, wake word ("Jarvis", awake for 20 seconds), and manual pause
- Choice of AI engine, voice, language and personality in Settings
- Conversation history, with a report at the end of each session
- Timing measurements for every reply, shown on screen
- Login, so each person has their own conversations

## Privacy

- Microphone audio is held in memory only while it is being processed. It is never saved to disk.
- Transcripts are saved in the database, which is currently hosted on Supabase. You can delete any
  conversation from History.
- With the **Groq** engine, your audio and messages are sent to Groq to be processed.
- With the **Local** engine and a local database, nothing leaves the PC.

## Current limits

- **Hardware.** It was built on a 2-core PC with 8 GB of RAM, so replies slow down when memory is low.
- **Free Groq limit.** Each model has a daily limit. When all the Groq models are used up, replies come from
  the small local model, which takes about 4 to 12 seconds and answers less well.
- **Local answer quality.** The small local model can get facts wrong or ignore instructions.
- **Urdu is experimental.** It understands Urdu better than it speaks it.
- **Echo.** Speakers in a room can still confuse it, so headphones are recommended.
- **One server.** Conversations run in one server process on one PC, so it is not built for many users at once.

## Running it

From PowerShell:

```powershell
cd "E:\tmp\V AGENT\echoai"
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Then open http://localhost:3000. To stop it, run `scripts\stop.ps1` the same way.
