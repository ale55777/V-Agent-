/**
 * Runs on the audio thread. Downsamples the mic to 16 kHz mono PCM16 and posts 512-sample
 * frames (32 ms), the exact size Silero VAD needs. Averaging the samples being merged
 * acts as a simple low-pass filter, which is enough for speech recognition.
 */
const TARGET_RATE = 16000;
const FRAME_SAMPLES = 512;

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.position = 0;
    this.sum = 0;
    this.count = 0;
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.peak = 0;
    this.enabled = true;
    this.port.onmessage = (event) => {
      if (typeof event.data?.enabled === "boolean") this.enabled = event.data.enabled;
    };
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      this.sum += channel[i];
      this.count++;
      this.position++;
      if (this.position >= this.ratio) {
        this.position -= this.ratio;
        const sample = Math.max(-1, Math.min(1, this.sum / this.count));
        this.sum = 0;
        this.count = 0;
        this.frame[this.filled++] = sample * 32767;
        this.peak = Math.max(this.peak, Math.abs(sample));
        if (this.filled === FRAME_SAMPLES) {
          if (this.enabled) {
            const copy = this.frame.slice();
            this.port.postMessage({ pcm: copy.buffer, peak: this.peak }, [copy.buffer]);
          } else {
            this.port.postMessage({ peak: 0 });
          }
          this.filled = 0;
          this.peak = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
