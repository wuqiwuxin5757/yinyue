"""CosyVoice 3 本地朗读服务（在 cosyvoice\\.venv 里运行，由 assistant.py 按需启动）。

  GET  /health               模型加载好返回 200
  POST /tts {"text": "..."}  返回 16 位单声道 WAV

音色：D:\\YinYue\\voice\\reference.wav（配 reference.txt 文字稿）存在就用它，
否则用 CosyVoice 仓库自带的示例音色。启动时把音色登记一次，之后每句都复用，不再重复提取特征。
"""
import io
import json
import logging
import os
import re
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
if len(sys.argv) > 2 and sys.argv[2] == "cpu":
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ.setdefault("MODELSCOPE_CACHE", str(HERE / "modelscope_cache"))  # 文本规整小模型也放 D 盘，别占 C 盘
sys.path[:0] = [str(HERE / "repo"), str(HERE / "repo" / "third_party" / "Matcha-TTS")]

import numpy as np  # noqa: E402
import torch  # noqa: E402
from cosyvoice.cli.cosyvoice import AutoModel, CosyVoice  # noqa: E402
from wetext import wetext  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
SPK = "yinyue"
PRESET = sys.argv[3] if len(sys.argv) > 3 else ""
SYNTH_LOCK = threading.Lock()
PREFIX = "You are a helpful assistant.<|endofprompt|>"

ref_wav = HERE.parent / "voice" / "reference.wav"
if ref_wav.exists() and ref_wav.with_suffix(".txt").exists():
    prompt_wav, prompt_text = str(ref_wav), ref_wav.with_suffix(".txt").read_text(encoding="utf-8").strip()
else:
    prompt_wav, prompt_text = str(HERE / "repo" / "asset" / "zero_shot_prompt.wav"), "希望你以后能够做的比我还好呦。"

if not torch.cuda.is_available():
    torch.set_num_threads(8)
    torch.set_num_interop_threads(2)


def local_text_models(_repo):
    root = Path(os.environ["MODELSCOPE_CACHE"]) / "hub" / "pengzhendong" / "wetext"
    if all((root / lang / "tn" / name).is_file()
           for lang in ("zh", "en") for name in ("tagger.fst", "verbalizer.fst")):
        return str(root)
    raise FileNotFoundError("Local WeText models are incomplete")


wetext.snapshot_download = local_text_models
if PRESET:
    model = CosyVoice(str(HERE / "CosyVoice-300M-SFT"), fp16=torch.cuda.is_available())
    if PRESET not in model.list_available_spks():
        raise ValueError(f"没有这个 CosyVoice 预置音色：{PRESET}")
    logging.info("已加载 CosyVoice-300M-SFT 内置音色：%s", PRESET)
else:
    model = AutoModel(model_dir=str(HERE / "Fun-CosyVoice3-0.5B"), fp16=torch.cuda.is_available())
    model.add_zero_shot_spk(PREFIX + prompt_text, prompt_wav, SPK)
    logging.info("已加载 CosyVoice 参考音色：%s", prompt_wav)


def english_speech_text(text):
    text = text.strip().translate(str.maketrans("。！？；", ".!?;"))
    # The SFT model reads short English headings more reliably in sentence case.
    if re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+[.!?;]?", text):
        text = text[0] + text[1:].lower()
    # Read the separator in identifiers such as T-55/62 instead of losing it during normalization.
    if re.fullmatch(r"[A-Z]+-\d+(?:/\d+)+[.!?;]?", text):
        text = text.replace("-", " ", 1).replace("/", " slash ")
    return text if text[-1:] in (".", "!", "?", ";") else text + "."


def infer(text, speed=1.0):
    if PRESET:
        speaker = PRESET
        if PRESET == "中文女" and re.search(r"[A-Za-z]", text) and not re.search(r"[\u3400-\u9fff]", text):
            speaker = "英文女"
            text = english_speech_text(text)
        return model.inference_sft(text, speaker, stream=False, speed=speed)
    return model.inference_zero_shot(text, "", "", zero_shot_spk_id=SPK, stream=False, speed=speed)


for _ in infer("你好。"):
    pass
logging.info("CosyVoice 已就绪")


def synth(text: str, speed: float = 1.0) -> bytes:
    chunks = [out["tts_speech"].squeeze(0).float().cpu().numpy()
              for out in infer(text, speed)]
    audio = np.concatenate(chunks) if chunks else np.zeros(1, np.float32)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(model.sample_rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/tts":
            self.send_response(404)
            self.end_headers()
            return
        if not SYNTH_LOCK.acquire(blocking=False):
            self.send_error(503, "CosyVoice is already synthesizing")
            return
        try:
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = synth(req["text"], float(req.get("speed", 1.0)))
        except Exception as e:
            logging.exception("合成失败")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))
            return
        finally:
            SYNTH_LOCK.release()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
