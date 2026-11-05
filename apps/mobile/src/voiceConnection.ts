/**
 * Platform-neutral voice socket for the future Expo app. Not yet run on a device.
 * It follows the same protocol as apps/web/src/hooks/useVoiceSession.ts.
 */
import { AUDIO_HEADER_BYTES, CloseCode } from "@echoai/shared-types";
import type { ClientMessage, ServerEvent } from "@echoai/shared-types";

/** Implement with a native module. Must deliver PCM16 mono 16 kHz, ideally 512-sample frames. */
export interface MicSource {
  start(onFrame: (pcm: ArrayBuffer) => void): Promise<void>;
  stop(): void;
}

/** Implement with a native module. Must report real playback progress, not receipt of data. */
export interface SpeechOutput {
  play(
    pcm: Int16Array,
    sampleRate: number,
    onProgress: (playedMs: number) => void,
  ): void;
  /** Must stop immediately and drop anything queued. */
  stopAll(): void;
}

interface ChunkMeta { responseId: string }

export class VoiceConnection {
  private socket: WebSocket | null = null;
  private chunks = new Map<string, ChunkMeta>();
  private staleBelow = 0;

  constructor(
    private wsUrl: string,
    private token: string,
    private deviceId: string,
    private mic: MicSource,
    private output: SpeechOutput,
    private onEvent: (event: ServerEvent) => void,
    private onClosed: (code: number) => void,
  ) {}

  connect(sessionId: string) {
    const socket = new WebSocket(this.wsUrl);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      this.send({ type: "session.start", token: this.token, session_id: sessionId, device_id: this.deviceId });
      void this.mic.start((pcm) => socket.readyState === WebSocket.OPEN && socket.send(pcm));
    };
    socket.onmessage = ({ data }) => (typeof data === "string" ? this.handle(JSON.parse(data)) : this.playPacket(data));
    socket.onclose = ({ code }) => {
      this.mic.stop();
      this.output.stopAll();
      this.onClosed(code);
    };
  }

  /** Final close codes mean: do not reconnect automatically. */
  static shouldReconnect(code: number) {
    const final: number[] = [
      1000, CloseCode.UNAUTHORIZED, CloseCode.FORBIDDEN, CloseCode.REPLACED,
      CloseCode.DEVICE_CONFLICT, CloseCode.HANDOFF, CloseCode.NOT_READY,
    ];
    return !final.includes(code);
  }

  send(message: ClientMessage) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
  }

  close() {
    this.socket?.close();
  }

  private handle(event: ServerEvent) {
    if (event.type === "agent.audio.chunk") {
      this.chunks.set(`${event.response_seq}:${event.chunk_seq}`, { responseId: event.response_id });
    } else if (event.type === "agent.interrupted") {
      this.staleBelow = event.response_seq + 1;
      this.chunks.clear();
      this.output.stopAll();
    }
    this.onEvent(event);
  }

  private playPacket(packet: ArrayBuffer) {
    const header = new DataView(packet, 0, AUDIO_HEADER_BYTES);
    const responseSeq = header.getUint32(0, true);
    const chunkSeq = header.getUint32(4, true);
    const sampleRate = header.getUint32(8, true);
    const meta = this.chunks.get(`${responseSeq}:${chunkSeq}`);
    if (!meta || responseSeq < this.staleBelow) return;
    this.output.play(new Int16Array(packet, AUDIO_HEADER_BYTES), sampleRate, (playedMs) =>
      this.send({ type: "playback.progress", response_id: meta.responseId, chunk_seq: chunkSeq, played_ms: playedMs }),
    );
  }
}
