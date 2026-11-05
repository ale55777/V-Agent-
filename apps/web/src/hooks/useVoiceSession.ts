"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CloseCode } from "@echoai/shared-types";
import type { ClientMessage, LiveMetrics, Message, ModelNames, ServerEvent, SessionState } from "@echoai/shared-types";
import { AudioPlayer, MicCapture } from "@/lib/audio";
import { api, deviceId, getToken, setActiveSession } from "@/lib/api";

export interface LiveReply { responseId: string; generated: string; spokenChars: number }
export type Blocker = { kind: "conflict" | "handoff"; message: string } | null;

const MAX_RECONNECT_DELAY = 8000;

/** Owns one voice session: WebSocket, microphone, playback and live conversation state. */
export function useVoiceSession(onEnded: (sessionId: string) => void) {
  const [state, setState] = useState<SessionState>("IDLE");
  const [connected, setConnected] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [partial, setPartial] = useState("");
  const [reply, setReply] = useState<LiveReply | null>(null);
  const [metrics, setMetrics] = useState<LiveMetrics | null>(null);
  const [lastTurn, setLastTurn] = useState<{ stt_ms: number | null; llm_first_token_ms: number | null; first_audio_ms: number | null } | null>(null);
  const [models, setModels] = useState<ModelNames | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [blocker, setBlocker] = useState<Blocker>(null);
  const [timerBase, setTimerBase] = useState({ total: 0, active: 0, at: 0 });
  const [listeningForResume, setListeningForResume] = useState(false);

  const socket = useRef<WebSocket | null>(null);
  const mic = useRef<MicCapture | null>(null);
  const player = useRef<AudioPlayer | null>(null);
  const micLevel = useRef(0);
  const micOpen = useRef(false);
  const session = useRef<string | null>(null);
  const closing = useRef(false);
  const attempts = useRef(0);
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const volume = useRef(1);
  const device = useRef<string | undefined>(undefined);

  const send = useCallback((message: ClientMessage) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(message));
  }, []);

  const handleEvent = useCallback((event: ServerEvent) => {
    switch (event.type) {
      case "session.started":
        micOpen.current = true;
        attempts.current = 0;
        setConnected(true);
        setModels(event.models);
        setMetrics(event.metrics);
        setBlocker(null);
        setError(null);
        setNotice(null);
        setTimerBase({
          total: event.metrics.active_seconds + event.metrics.paused_seconds,
          active: event.metrics.active_seconds,
          at: performance.now(),
        });
        break;
      case "session.loading":
        setNotice("Loading the AI models. The first start can take a minute.");
        break;
      case "session.state":
        setState(event.state);
        if (event.state === "LISTENING") setListeningForResume(false);
        if (event.state === "PAUSED") setListeningForResume(Boolean(event.listening_for_resume));
        break;
      case "transcript.partial":
        setPartial(event.text);
        break;
      case "transcript.final":
        setPartial("");
        if (event.message) setMessages((m) => [...m, event.message!]);
        break;
      case "transcript.discarded":
        setPartial("");
        if (event.reason === "no_wake_word") setNotice("Say the wake word first.");
        break;
      case "attention.awake":
        setNotice(null);
        break;
      case "agent.response.started":
        setReply({ responseId: event.response_id, generated: "", spokenChars: 0 });
        break;
      case "agent.text.delta":
        setReply((r) => (r && r.responseId === event.response_id ? { ...r, generated: r.generated + event.text } : r));
        break;
      case "agent.audio.chunk":
        player.current?.describeChunk(event.response_seq, event.chunk_seq, {
          responseId: event.response_id,
          text: event.text,
          durationMs: event.duration_ms,
        });
        break;
      case "agent.interrupted":
        player.current?.stop(event.response_seq);
        break;
      case "agent.response.completed":
        setReply((r) => (r?.responseId === event.response_id ? null : r));
        if (event.message) setMessages((m) => [...m, event.message!]);
        break;
      case "metrics.updated":
        setMetrics(event.metrics);
        break;
      case "metrics.turn":
        setLastTurn(event);
        break;
      case "session.paused":
        player.current?.stop();
        break;
      case "session.ended":
        closing.current = true;
        break;
      case "session.handoff":
        closing.current = true;
        break;
      case "error":
        setError(event.message);
        break;
    }
  }, []);

  const teardownAudio = useCallback(() => {
    mic.current?.stop();
    mic.current = null;
    player.current?.close();
    player.current = null;
  }, []);

  const connect = useCallback(
    (id: string) => {
      const token = getToken();
      if (!token) return;
      setState("CONNECTING");
      const ws = new WebSocket(api().wsUrl);
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      ws.onopen = () =>
        ws.send(JSON.stringify({ type: "session.start", token, session_id: id, device_id: deviceId() } satisfies ClientMessage));
      ws.onmessage = ({ data }) => {
        if (typeof data === "string") {
          const event = JSON.parse(data) as ServerEvent;
          handleEvent(event);
        } else {
          player.current?.enqueue(data as ArrayBuffer);
        }
      };
      ws.onclose = ({ code }) => {
        if (socket.current !== ws) return;
        socket.current = null;
        micOpen.current = false;
        setConnected(false);
        player.current?.stop();
        setReply(null);
        setPartial("");

        if (code === CloseCode.DEVICE_CONFLICT) {
          setState("DISCONNECTED");
          setBlocker({ kind: "conflict", message: "This conversation is active on another device." });
          return;
        }
        if (code === CloseCode.HANDOFF || code === CloseCode.REPLACED) {
          teardownAudio();
          if (code === CloseCode.HANDOFF) setActiveSession(null);
          setState("IDLE");
          setBlocker({
            kind: "handoff",
            message: code === CloseCode.HANDOFF ? "This conversation moved to another device." : "This conversation was opened in another tab.",
          });
          return;
        }
        if (closing.current || code === 1000) {
          teardownAudio();
          setActiveSession(null);
          setState("ENDED");
          if (session.current) onEnded(session.current);
          return;
        }
        if (code === CloseCode.UNAUTHORIZED || code === CloseCode.FORBIDDEN || code === CloseCode.NOT_READY) {
          teardownAudio();
          setState("DISCONNECTED");
          setError(
            code === CloseCode.NOT_READY
              ? "The AI models are unavailable. Check the backend log and run scripts/setup.ps1."
              : "You no longer have access to this conversation. Sign in again.",
          );
          return;
        }
        // Unexpected drop: back off and rejoin the same session with its saved context.
        setState("DISCONNECTED");
        const delay = Math.min(500 * 2 ** attempts.current++, MAX_RECONNECT_DELAY);
        retry.current = setTimeout(() => connect(id), delay);
      };
    },
    [handleEvent, onEnded, teardownAudio],
  );

  const start = useCallback(
    async (existingId?: string) => {
      setError(null);
      setBlocker(null);
      setState("CONNECTING");
      closing.current = false;
      attempts.current = 0;
      try {
        // Created during the click, because browsers only allow audio after a user gesture.
        const output = new AudioPlayer(
          (responseId, chunkSeq, playedMs) =>
            send({ type: "playback.progress", response_id: responseId, chunk_seq: chunkSeq, played_ms: playedMs }),
          (responseId, text) =>
            setReply((r) => (r?.responseId === responseId ? { ...r, spokenChars: r.spokenChars + text.length + 1 } : r)),
        );
        output.setVolume(volume.current);
        await output.resume();
        player.current = output;

        const capture = new MicCapture(
          (pcm) => {
            // Nothing before session.started: the server is not reading audio during the
            // handshake, so it would all arrive later in one stale burst.
            if (micOpen.current && socket.current?.readyState === WebSocket.OPEN && socket.current.bufferedAmount < 64000) {
              socket.current.send(pcm);
            }
          },
          (peak) => (micLevel.current = peak),
        );
        await capture.start(device.current);
        capture.setEnabled(!muted);
        mic.current = capture;

        let id = existingId;
        if (id) {
          const previous = await api().session(id);
          setMessages(previous.messages);
        } else {
          id = (await api().createSession()).id;
          setMessages([]);
        }
        session.current = id;
        setSessionId(id);
        setActiveSession(id);
        connect(id);
      } catch (cause) {
        teardownAudio();
        setState("IDLE");
        const failure = cause as Error;
        setError(
          failure.name === "NotAllowedError"
            ? "Microphone access was denied. Allow it in the browser's address bar, then try again."
            : failure.message,
        );
      }
    },
    [connect, muted, send, teardownAudio],
  );

  const end = useCallback(() => {
    closing.current = true;
    if (socket.current?.readyState === WebSocket.OPEN) send({ type: "session.end" });
    else {
      teardownAudio();
      setActiveSession(null);
      setState("ENDED");
      if (session.current) onEnded(session.current);
    }
  }, [onEnded, send, teardownAudio]);

  const takeOver = useCallback(async () => {
    if (!session.current) return;
    await api().handoff(session.current, deviceId());
    setBlocker(null);
    connect(session.current);
  }, [connect]);

  const pause = useCallback(() => send({ type: "session.pause", listen_for_resume: false }), [send]);
  const resume = useCallback(() => send({ type: "session.resume" }), [send]);

  const toggleMute = useCallback(() => {
    setMuted((current) => {
      mic.current?.setEnabled(current);
      return !current;
    });
  }, []);

  const setVolume = useCallback((value: number) => {
    volume.current = value;
    player.current?.setVolume(value);
  }, []);

  const selectMicrophone = useCallback(async (id: string) => {
    device.current = id || undefined;
    if (mic.current) await mic.current.switchDevice(id).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(
    () => () => {
      if (retry.current) clearTimeout(retry.current);
      const ws = socket.current;
      socket.current = null;
      ws?.close();
      teardownAudio();
    },
    [teardownAudio],
  );

  return {
    state, connected, sessionId, messages, partial, reply, metrics, lastTurn, models, error, notice, muted,
    blocker, timerBase, listeningForResume, micLevel,
    micAnalyser: () => mic.current?.analyser ?? null,
    aiAnalyser: () => player.current?.analyser ?? null,
    start, end, pause, resume, toggleMute, setVolume, selectMicrophone, takeOver, send, clearError: () => setError(null),
  };
}
