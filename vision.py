"""银月的眼睛：截屏交给 Qwen3-VL 看，能回答“屏幕上是什么”，也能找到某个按钮的位置去点它。

视觉模型很大，只在用到时才启动（单独一个 llama-server），闲置几分钟自动关掉。
不指定 -ngl，让 llama.cpp 按当前剩余显存自动决定放多少层到显卡，显存不够时其余放内存。
"""
import base64
import ctypes
import io
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from ctypes import wintypes

import requests
from PIL import ImageGrab

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
LLAMA_SERVER = Path(os.environ.get("YINYUE_LLAMA_SERVER", r"D:\llama.cpp\bin\llama-server.exe"))


class VisionLLM:
    def __init__(self):
        self.proc = None
        self.last_used = 0.0
        self.url = f"http://127.0.0.1:{CFG['vision_port']}"
        self.lock = threading.Lock()
        threading.Thread(target=self._idle_watch, daemon=True).start()

    def _healthy(self):
        try:
            return requests.get(self.url + "/health", timeout=1).status_code == 200
        except requests.RequestException:
            return False

    def ensure_started(self):
        with self.lock:
            self.last_used = time.time()
            if self.proc and self.proc.poll() is None or self._healthy():
                pass
            else:
                logging.info("启动视觉模型 …")
                self.proc = subprocess.Popen(
                    [str(LLAMA_SERVER), "-m", CFG["vision_model"], "--mmproj", CFG["vision_mmproj"],
                     "--host", "127.0.0.1", "--port", str(CFG["vision_port"]), "-c", "8192", "-np", "1",
                     "-t", "12", "--jinja"],
                    stdout=subprocess.DEVNULL,
                    stderr=open(ROOT / "logs" / "vision.log", "w", encoding="utf-8"),
                    creationflags=0x08000000)
            for _ in range(480):
                if self._healthy():
                    return
                time.sleep(0.25)
            raise RuntimeError("视觉模型启动超时，详见 logs/vision.log")

    def stop(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                logging.info("视觉模型已卸载")
            self.proc = None

    def _idle_watch(self):
        while True:
            time.sleep(30)
            if self.proc and time.time() - self.last_used > CFG["vision_unload_after_minutes"] * 60:
                self.stop()

    def ask(self, image_b64: str, prompt: str, max_tokens=200) -> str:
        self.ensure_started()
        r = requests.post(self.url + "/v1/chat/completions", timeout=180, json={
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt}]}],
            "temperature": 0.1, "max_tokens": max_tokens})
        r.raise_for_status()
        self.last_used = time.time()
        return r.json()["choices"][0]["message"]["content"].strip()


_vlm = VisionLLM()


def stop():
    _vlm.stop()


def _encode(img, max_width=1600) -> str:
    w, h = img.size
    if w > max_width:
        img = img.resize((max_width, round(h * max_width / w)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def describe(question: str) -> str:
    return _vlm.ask(_encode(ImageGrab.grab()), f"这是用户电脑屏幕的截图。请用简短的中文回答（不超过80字）：{question}")


def capture_window(hwnd):
    """截取前台窗口在桌面上实际显示的像素，适用于硬件加速的浏览器。"""
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    rect = wintypes.RECT()
    if user32.GetForegroundWindow() != hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("浏览器窗口已经切走")
    img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
    if user32.GetForegroundWindow() != hwnd or img.convert("L").getextrema()[1] < 12:
        raise RuntimeError("没有截取到可见的浏览器内容")
    return img


def read_page_image(img, prompt: str) -> str:
    return _vlm.ask(_encode(img), prompt, max_tokens=220)


def _point(img, target: str):
    """在图里找 target，返回 0~1000 的相对坐标，找不到返回 None。"""
    out = _vlm.ask(_encode(img), f"在这张截图中找到：{target}。\n"
                                 '只输出一个 JSON，格式为 {"x": 横坐标, "y": 纵坐标}，坐标是 0 到 1000 的相对坐标（左上角为 0,0）。'
                                 '如果找不到，输出 {"x": -1, "y": -1}。不要输出其他文字。', max_tokens=60)
    logging.info("视觉定位 %s -> %s", target, out)
    m = re.search(r'"x"\s*:\s*(-?\d+(?:\.\d+)?)\s*,\s*"y"\s*:\s*(-?\d+(?:\.\d+)?)', out)
    if not m:
        m = re.search(r"\[?\s*(\d+)\s*,\s*(\d+)", out)  # 兼容 [x, y] 或 bbox 写法
    if not m or float(m.group(1)) < 0 or float(m.group(2)) < 0:
        return None
    return float(m.group(1)), float(m.group(2))


def locate(target: str):
    """找到屏幕上某个东西的位置，返回屏幕像素坐标 (x, y)；找不到返回 None。
    先在整屏上粗找，再把那一块放大细找一次（4K 屏缩小后细节不够，一次找会偏几十像素）。"""
    img = ImageGrab.grab()
    w, h = img.size
    p = _point(img, target)
    if not p:
        return None
    x, y = p[0] / 1000 * w, p[1] / 1000 * h
    cw, ch = w // 3, h // 3
    left = int(min(max(x - cw / 2, 0), w - cw))
    top = int(min(max(y - ch / 2, 0), h - ch))
    fine = _point(img.crop((left, top, left + cw, top + ch)), target)
    if fine:
        x, y = left + fine[0] / 1000 * cw, top + fine[1] / 1000 * ch
    return round(x), round(y)
