"""放声音 + 回声消除。

原理：银月自己放出去的每一段声音，都同时交一份给 WebRTC 的回声消除器（视频会议软件用的同一套算法）。
麦克风收到声音后，消除器会把“银月自己的声音”从里面减掉，剩下的才是主人的声音。
所以扬声器外放时，主人也能随时插话打断她。
"""
import collections
import logging
import subprocess
import threading
import time

import numpy as np
import sounddevice as sd
from livekit import rtc

MIC_SR = 16000
OUT_SR = 48000
FRAME = 160  # WebRTC 每次处理 10 毫秒（16kHz 下 160 个采样）


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if len(x) == 0:
        return np.zeros(0, np.float32)
    if sr_from == sr_to:
        return x.astype(np.float32)
    n = int(len(x) * sr_to / sr_from)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def adjust_tempo(samples: np.ndarray, sr: int, speed: float) -> np.ndarray:
    """Change playback speed while preserving pitch."""
    if speed == 1.0 or not len(samples):
        return samples
    pcm = samples.astype("<f4", copy=False).tobytes()
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
             "-af", f"atempo={speed:.6g}", "-f", "f32le", "pipe:1"],
            input=pcm, capture_output=True, timeout=3, check=True, creationflags=0x08000000)
        if result.stdout:
            return np.frombuffer(result.stdout, dtype="<f4")
    except (OSError, subprocess.SubprocessError) as exc:
        logging.warning("备用朗读变速失败：%s", exc)
    return samples


def level_speech(audio: np.ndarray, sr: int) -> np.ndarray:
    """Level active speech across phrases and gently compress syllable-level peaks."""
    if not len(audio):
        return audio
    hop = max(1, sr // 100)
    centers = np.arange(0, len(audio), hop)
    radius = max(1, int(sr * 0.055))
    start = np.maximum(0, centers - radius)
    end = np.minimum(len(audio), centers + radius)
    power = np.concatenate(([0.0], np.cumsum(np.square(audio), dtype=np.float64)))
    rms = np.sqrt(np.maximum(0, (power[end] - power[start]) / (end - start)))
    active = rms[rms > max(0.012, 0.22 * rms.max())]
    if not len(active):
        return audio

    typical = np.percentile(active, 45)
    level = np.clip(0.105 / typical, 0.55, 1.65)
    # Leave quieter syllables and pauses below the compression threshold.
    envelope = np.maximum(rms, typical)
    compression = np.clip((typical / envelope) ** 0.38, 0.55, 1.0)
    gain = np.interp(np.arange(len(audio)), centers, level * compression)
    result = audio * gain
    result = (result * min(1.0, 0.89 / np.max(np.abs(result)))).astype(np.float32)
    # Remove only the middle of very long quiet gaps, leaving ordinary breaths alone.
    quiet = rms < max(0.004, typical * 0.095)
    boundaries = np.flatnonzero(np.diff(np.r_[False, quiet, False])) * hop
    parts = []
    cursor = 0
    fade = max(1, sr // 100)
    blend = np.linspace(0, 1, fade, dtype=np.float32)
    for start, end in boundaries.reshape(-1, 2):
        if end - start < 0.5 * sr:
            continue
        left = start + int(0.15 * sr)
        right = end - int(0.15 * sr)
        parts.append(result[cursor:left - fade])
        parts.append(result[left - fade:left] * (1 - blend) + result[right:right + fade] * blend)
        cursor = right + fade
    if parts:
        return np.concatenate([*parts, result[cursor:]])
    return result


def _to_frame(x: np.ndarray) -> rtc.AudioFrame:
    data = (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()
    return rtc.AudioFrame(data, MIC_SR, 1, len(x))


class EchoCanceller:
    def __init__(self, enabled=True, delay_ms=150):
        self.enabled = enabled
        self.apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=True,
                                             high_pass_filter=True) if enabled else None
        self.delay_ms = delay_ms
        self.far = collections.deque()    # 刚放出去的声音（16kHz），等着和麦克风对齐
        self.far_lock = threading.Lock()
        self.near_buf = np.zeros(0, np.float32)

    def push_far(self, x16: np.ndarray):
        with self.far_lock:
            self.far.extend(x16.tolist())
            while len(self.far) > MIC_SR:  # 最多攒 1 秒，防止麦克风卡顿时越积越多
                self.far.popleft()

    def _pop_far(self) -> np.ndarray:
        with self.far_lock:
            n = min(FRAME, len(self.far))
            out = [self.far.popleft() for _ in range(n)]
        return np.pad(np.asarray(out, np.float32), (0, FRAME - n))

    def process(self, mic: np.ndarray, *, use_processed_audio: bool = True) -> np.ndarray:
        """每 10 毫秒更新 AEC。没有播放回声时可保留原始麦克风信号。"""
        if not self.enabled:
            return mic
        self.near_buf = np.concatenate([self.near_buf, mic])
        out = []
        while len(self.near_buf) >= FRAME:
            near, self.near_buf = self.near_buf[:FRAME], self.near_buf[FRAME:]
            self.apm.process_reverse_stream(_to_frame(self._pop_far()))
            self.apm.set_stream_delay_ms(self.delay_ms)
            frame = _to_frame(near)
            self.apm.process_stream(frame)
            if use_processed_audio:
                out.append(np.frombuffer(bytes(frame.data), np.int16).astype(np.float32) / 32768)
            else:
                out.append(near)
        return np.concatenate(out) if out else np.zeros(0, np.float32)


class Player:
    """一直开着的输出流：要说的话放进缓冲区，声卡要多少取多少；取出去的同时交给回声消除器做参照。"""

    def __init__(self, aec: EchoCanceller, device=None):
        self.aec = aec
        self.device = device
        self.buf = collections.deque()
        self.lock = threading.Lock()
        self.last_audio_end = 0.0  # 已交给声卡的最后一个非空播放缓冲的结束时间
        self.stream = None
        self._open()

    def _open(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        try:
            self.stream = sd.OutputStream(samplerate=OUT_SR, channels=1, dtype="float32", blocksize=480,
                                          device=self.device, callback=self._cb)
            self.stream.start()
        except Exception as e:
            logging.warning("打开扬声器失败：%s", e)
            if self.stream is not None:
                self.stream.close()
            self.stream = None

    def _cb(self, outdata, frames, t, status):
        with self.lock:
            n = min(frames, len(self.buf))
            chunk = np.asarray([self.buf.popleft() for _ in range(n)], np.float32)
            if n:
                delay = max(0.0, t.outputBufferDacTime - t.currentTime)
                self.last_audio_end = time.time() + delay + n / OUT_SR
        chunk = np.pad(chunk, (0, frames - n))
        outdata[:, 0] = chunk
        # 48kHz → 16kHz：每 3 个采样取平均，顺便滤掉高频
        self.aec.push_far(chunk[: frames // 3 * 3].reshape(-1, 3).mean(axis=1))

    def play(self, audio: np.ndarray, sr: int):
        if self.stream is None or not self.stream.active:
            self._open()
        if self.stream is None or not self.stream.active:
            raise RuntimeError("扬声器不可用，请检查输出设备")
        with self.lock:
            self.buf.extend(resample(audio, sr, OUT_SR).tolist())

    @property
    def busy(self) -> bool:
        if self.buf and (self.stream is None or not self.stream.active):
            raise RuntimeError("播放设备已断开")
        return len(self.buf) > 0

    def stop(self):
        with self.lock:
            self.buf.clear()
        if self.stream is not None and self.stream.active:
            try:
                self.stream.abort()  # 同时丢掉已经交给 PortAudio 但尚未播放的缓冲
            except sd.PortAudioError as e:
                logging.warning("中止扬声器播放失败：%s", e)
