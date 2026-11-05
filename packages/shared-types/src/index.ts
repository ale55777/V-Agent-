/** Contracts shared by the web app and the future Expo app. Mirrors the FastAPI backend. */

export type SessionState =
  | "IDLE" | "CONNECTING" | "LISTENING" | "PROCESSING" | "SPEAKING"
  | "INTERRUPTED" | "PAUSED" | "DISCONNECTED" | "ENDED";

export type AttentionMode = "always" | "wake_word" | "manual";
export type Language = "en" | "ur" | "auto";

export interface Message {
  id: string;
  session_id: string;
  speaker: "user" | "assistant";
  content: string;
  timestamp: string;
  sequence_number: number;
  status: "completed" | "interrupted";
  interrupted: boolean;
  response_id: string | null;
}

export interface LiveMetrics {
  active_seconds: number;
  paused_seconds: number;
  user_speaking_seconds: number;
  ai_speaking_seconds: number;
  interruption_count: number;
  turn_count: number;
  average_latency_ms: number | null;
  minimum_latency_ms: number | null;
  maximum_latency_ms: number | null;
  average_transcription_ms: number | null;
  average_llm_first_token_ms: number | null;
  average_interruption_ms: number | null;
}

export interface ModelNames { llm: string; stt: string; tts: string }

/** Server events on /ws/v1/voice (protocol v1). Agent PCM arrives as separate binary frames. */
export type ServerEvent =
  | { type: "session.started"; session_id: string; resumed: boolean; models: ModelNames; attention_mode: AttentionMode; external_api_cost: number; metrics: LiveMetrics }
  | { type: "session.loading"; models: unknown }
  | { type: "session.state"; state: SessionState; listening_for_resume?: boolean }
  | { type: "session.paused"; listening_for_resume: boolean }
  | { type: "session.resumed" }
  | { type: "session.ended"; session_id: string }
  | { type: "session.end_requested" }
  | { type: "session.handoff"; to_device: string }
  | { type: "session.configured"; changes: string[]; models: ModelNames }
  | { type: "speech.started" }
  | { type: "speech.ended"; duration_ms: number }
  | { type: "transcript.partial"; text: string }
  | { type: "transcript.final"; text: string; message?: Message; command?: string; stt_ms: number }
  | { type: "transcript.discarded"; reason: "no_speech" | "echo" | "paused" | "no_wake_word"; text?: string }
  | { type: "attention.awake"; until_seconds: number }
  | { type: "agent.response.started"; response_id: string; response_seq: number }
  | { type: "agent.text.delta"; response_id: string; text: string }
  | { type: "agent.audio.chunk"; response_id: string; response_seq: number; chunk_seq: number; text: string; duration_ms: number; sample_rate: number }
  | { type: "agent.response.completed"; response_id: string; interrupted: boolean; spoken_text: string; message: Message | null }
  | { type: "agent.interrupted"; response_id: string | null; response_seq: number }
  | { type: "metrics.updated"; metrics: LiveMetrics }
  | { type: "metrics.turn"; response_id: string; stt_ms: number | null; llm_first_token_ms: number | null; first_audio_ms: number | null }
  | { type: "mic.stats"; peak: number; speech: number; threshold: number }
  | { type: "pong"; t: number }
  | { type: "error"; code: string; message: string; recoverable: boolean };

export type ClientMessage =
  | { type: "session.start"; token: string; session_id: string; device_id: string }
  | { type: "playback.progress"; response_id: string; chunk_seq: number; played_ms: number }
  | { type: "session.pause"; listen_for_resume?: boolean }
  | { type: "session.resume" }
  | { type: "session.end" }
  | { type: "session.configure"; language?: Language; voice?: string; llm_model?: string; personality?: string; attention_mode?: AttentionMode; wake_word?: string }
  | { type: "ping"; t: number };

/** WebSocket close codes the client must treat as final rather than reconnecting. */
export const CloseCode = { UNAUTHORIZED: 4401, FORBIDDEN: 4403, REPLACED: 4408, DEVICE_CONFLICT: 4409, HANDOFF: 4410, NOT_READY: 4503 } as const;

/** Agent audio frame: 12-byte header (uint32le responseSeq, chunkSeq, sampleRate) + PCM16 mono. */
export const AUDIO_HEADER_BYTES = 12;
export const MIC_SAMPLE_RATE = 16000;

export interface AgentSettings {
  selected_llm: string | null;
  selected_stt: string | null;
  selected_tts: "kokoro" | "piper" | null;
  selected_voice: string | null;
  language: Language;
  vad_sensitivity: number;
  attention_mode: AttentionMode;
  personality: string;
  wake_word: string;
  retention_days: number | null;
}

export interface Voice { id: string; label: string; engine: string; language: string }

export interface SessionSummary {
  id: string; title: string; status: string; start_time: string; end_time: string | null;
  duration: number; active_duration: number; paused_duration: number;
  estimated_cost: number; owner_device_id: string | null; message_count: number;
}

export interface SessionReport {
  session: {
    id: string; title: string; status: string; start_time: string; end_time: string | null;
    duration_seconds: number; active_seconds: number; paused_seconds: number;
    user_messages: number; ai_responses: number; interruptions: number;
  };
  performance: {
    average_response_latency_ms: number | null; fastest_response_ms: number | null; slowest_response_ms: number | null;
    average_transcription_latency_ms: number | null; average_llm_first_token_ms: number | null;
    average_interruption_latency_ms: number | null; user_speaking_seconds: number; ai_speaking_seconds: number;
  };
  cost: {
    external_api_cost_usd: number; llm_model: string | null; stt_model: string | null; tts_model: string | null;
    electricity_estimate: { kwh: number; cost: number; currency: string; assumed_watts: number } | null; note: string;
  };
  transcript: Message[];
}

export type ComponentState = { state: "pending" | "ready" | "failed"; detail?: string; load_ms?: number };
export interface Health {
  status: "ok" | "degraded";
  loading: boolean;
  database: ComponentState;
  ollama: { state: string; models?: string[] };
  models: Record<"vad" | "stt" | "tts" | "llm", ComponentState>;
}
