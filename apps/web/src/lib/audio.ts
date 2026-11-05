/** Browser audio: microphone capture through an AudioWorklet, and gapless agent playback. */
import { AUDIO_HEADER_BYTES } from "@echoai/shared-types";

export class MicCapture {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  analyser: AnalyserNode | null = null;
  private enabled = true;

  constructor(
    private onFrame: (pcm: ArrayBuffer) => void,
    private onLevel: (peak: number) => void,
  ) {}

  async start(deviceId?: string): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(
        "This browser blocks the microphone on insecure pages. Use http://localhost:3000 on this PC, or the HTTPS address from another device (see README).",
      );
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.context = new AudioContext({ latencyHint: "interactive" });
    await this.context.resume();
    await this.context.audioWorklet.addModule("/worklets/mic-processor.js");
    const source = this.context.createMediaStreamSource(this.stream);
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 512;
    this.node = new AudioWorkletNode(this.context, "mic-processor", { numberOfOutputs: 0 });
    this.node.port.onmessage = ({ data }) => {
      if (data.pcm) this.onFrame(data.pcm);
      this.onLevel(data.peak ?? 0);
    };
    this.node.port.postMessage({ enabled: this.enabled });
    source.connect(this.analyser);
    source.connect(this.node);
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    this.node?.port.postMessage({ enabled });
    this.stream?.getAudioTracks().forEach((track) => (track.enabled = enabled));
  }

  async switchDevice(deviceId: string) {
    this.stop();
    await this.start(deviceId);
  }

  stop() {
    this.node?.port.close();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    void this.context?.close();
    this.node = null;
    this.stream = null;
    this.context = null;
    this.analyser = null;
  }
}

interface ChunkMeta { responseId: string; text: string; durationMs: number }
interface Scheduled extends ChunkMeta {
  chunkSeq: number;
  responseSeq: number;
  source: AudioBufferSourceNode;
  startAt: number;
  started: boolean;
  reported: number;
}

/**
 * Schedules agent audio and reports what has really played. Those reports decide which
 * words are saved as spoken, so progress is based on the audio clock, not on receipt.
 */
export class AudioPlayer {
  readonly context: AudioContext;
  readonly analyser: AnalyserNode;
  private gain: GainNode;
  private nextStart = 0;
  private pending = new Map<string, ChunkMeta>();
  private scheduled: Scheduled[] = [];
  private minSeq = 0;
  private timer: ReturnType<typeof setInterval>;

  constructor(
    private onProgress: (responseId: string, chunkSeq: number, playedMs: number) => void,
    private onChunkStart: (responseId: string, text: string) => void,
  ) {
    this.context = new AudioContext({ latencyHint: "interactive" });
    this.gain = this.context.createGain();
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 512;
    this.gain.connect(this.analyser);
    this.analyser.connect(this.context.destination);
    this.timer = setInterval(() => this.tick(), 80);
  }

  resume() {
    return this.context.resume();
  }

  setVolume(volume: number) {
    this.gain.gain.value = volume;
  }

  describeChunk(responseSeq: number, chunkSeq: number, meta: ChunkMeta) {
    if (responseSeq >= this.minSeq) this.pending.set(`${responseSeq}:${chunkSeq}`, meta);
  }

  enqueue(packet: ArrayBuffer) {
    const header = new DataView(packet, 0, AUDIO_HEADER_BYTES);
    const responseSeq = header.getUint32(0, true);
    const chunkSeq = header.getUint32(4, true);
    const rate = header.getUint32(8, true);
    const key = `${responseSeq}:${chunkSeq}`;
    const meta = this.pending.get(key);
    this.pending.delete(key);
    if (!meta || responseSeq < this.minSeq) return; // Stale audio from an interrupted response.

    const pcm = new Int16Array(packet, AUDIO_HEADER_BYTES);
    const buffer = this.context.createBuffer(1, pcm.length, rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain);
    const startAt = Math.max(this.context.currentTime + 0.03, this.nextStart);
    source.start(startAt);
    this.nextStart = startAt + buffer.duration;
    this.scheduled.push({ ...meta, chunkSeq, responseSeq, source, startAt, started: false, reported: 0 });
  }

  /** Silence everything at once and refuse any audio from this response or earlier. */
  stop(upToSeq?: number) {
    this.tick();
    if (upToSeq !== undefined) this.minSeq = Math.max(this.minSeq, upToSeq + 1);
    for (const item of this.scheduled) {
      try {
        item.source.stop();
      } catch {
        /* already finished */
      }
      item.source.disconnect();
    }
    this.scheduled = [];
    this.pending.clear();
    this.nextStart = 0;
  }

  get playing() {
    return this.scheduled.length > 0;
  }

  private tick() {
    const latency = this.context.outputLatency || 0;
    const now = this.context.currentTime - latency;
    this.scheduled = this.scheduled.filter((item) => {
      const played = Math.min((now - item.startAt) * 1000, item.durationMs);
      if (played <= 0) return true;
      if (!item.started) {
        item.started = true;
        this.onChunkStart(item.responseId, item.text);
      }
      if (played > item.reported) {
        item.reported = played;
        this.onProgress(item.responseId, item.chunkSeq, played);
      }
      return played < item.durationMs;
    });
  }

  close() {
    this.stop();
    clearInterval(this.timer);
    void this.context.close();
  }
}
