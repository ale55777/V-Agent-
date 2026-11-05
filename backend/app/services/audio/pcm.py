"""PCM helpers and the binary wire format for agent audio.

Mic frames (client -> server) are bare little-endian PCM16 mono at 16 kHz.
Agent audio (server -> client) has a 12-byte header followed by PCM16 mono:

    uint32 response_seq | uint32 chunk_seq | uint32 sample_rate

The response sequence lets clients drop stale audio after an interruption.
"""
from __future__ import annotations

import struct

import numpy as np

MIC_SAMPLE_RATE = 16000
VAD_FRAME_SAMPLES = 512
VAD_FRAME_MS = VAD_FRAME_SAMPLES * 1000 / MIC_SAMPLE_RATE
_HEADER = struct.Struct("<III")
HEADER_SIZE = _HEADER.size


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def pack_audio(response_seq: int, chunk_seq: int, sample_rate: int, pcm: bytes) -> bytes:
    return _HEADER.pack(response_seq, chunk_seq, sample_rate) + pcm


def unpack_audio(packet: bytes) -> tuple[int, int, int, bytes]:
    response_seq, chunk_seq, sample_rate = _HEADER.unpack_from(packet)
    return response_seq, chunk_seq, sample_rate, packet[HEADER_SIZE:]


class FrameAssembler:
    """Re-frames arbitrary mic packets into the exact frame size the VAD needs."""

    def __init__(self, frame_bytes: int = VAD_FRAME_SAMPLES * 2, max_buffer: int = MIC_SAMPLE_RATE * 2) -> None:
        self.frame_bytes = frame_bytes
        self.max_buffer = max_buffer
        self._buffer = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        if len(data) % 2:
            del self._buffer[-1]
        if len(self._buffer) > self.max_buffer:
            # Keep the most recent audio if the consumer has fallen behind.
            excess = len(self._buffer) - self.max_buffer
            del self._buffer[: excess + (excess % self.frame_bytes and self.frame_bytes - excess % self.frame_bytes)]
        frames = []
        while len(self._buffer) >= self.frame_bytes:
            frames.append(bytes(self._buffer[: self.frame_bytes]))
            del self._buffer[: self.frame_bytes]
        return frames

    def clear(self) -> None:
        self._buffer.clear()
