"""唱跳的音乐：从 music 文件夹挑一首主人自己的歌，分析节拍（BPM + 第一拍的位置），播放出来让银月踩着拍子跳。

只用主人自己放进来的音频文件，不联网下载。解码用 ffmpeg（已装在系统里）。
"""
import random
import subprocess
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

MUSIC_DIR = Path(__file__).parent / "music"
EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
SR = 22050


def pick_song():
    MUSIC_DIR.mkdir(exist_ok=True)
    songs = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in EXTS]
    return random.choice(songs) if songs else None


def load(path: Path, max_seconds=180) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-t", str(max_seconds), "-i", str(path),
                          "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"],
                         capture_output=True, timeout=60, creationflags=0x08000000).stdout
    return np.frombuffer(out, np.float32).copy()


def estimate_beat(x: np.ndarray):
    """估计 BPM 和第一拍的时间：能量起伏（onset）曲线做自相关找周期，再找最对得上的相位。"""
    hop = 512
    frames = len(x) // hop
    if frames < 200:
        return 118.0, 0.0
    spec = np.abs(np.fft.rfft(x[:frames * hop].reshape(frames, hop) * np.hanning(hop), axis=1))
    flux = np.maximum(np.diff(np.log1p(spec), axis=0), 0).sum(axis=1)     # 频谱突增 = 鼓点、重音
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    fps = SR / hop
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lags = np.arange(len(ac))
    bpm_of = lambda lag: 60 * fps / lag
    lo, hi = int(60 * fps / 170), int(60 * fps / 75)                        # 只在 75~170 BPM 里找
    w = np.exp(-0.5 * (np.log2(bpm_of(lags[lo:hi]) / 118) / 0.6) ** 2)      # 偏向常见的流行歌速度
    lag = lo + int(np.argmax(ac[lo:hi] * w))
    bpm = bpm_of(lag)
    phase = max(range(lag), key=lambda ph: flux[ph::lag].sum())             # 每隔一拍都落在重音上的起点
    return float(bpm), float(phase / fps)


class Player:
    def __init__(self):
        self.playing_until = 0.0
        self.lock = threading.Lock()

    def start(self, can_play=lambda: True):
        """挑一首歌，分析节拍并开始播放。返回 (BPM, 第一拍时间, 时长) ；没有歌返回 None。"""
        song = pick_song()
        if not song:
            return None
        x = load(song)
        if len(x) < SR * 5:
            return None
        bpm, offset = estimate_beat(x)
        peak = np.abs(x).max() or 1.0
        with self.lock:
            if not can_play():
                return None
            sd.play(x / peak * 0.8, SR)
        return bpm, offset, len(x) / SR, song.stem

    def stop(self):
        with self.lock:
            sd.stop()
