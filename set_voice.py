"""设置银月的音色：给一段 5~15 秒的清晰人声（音频或视频都行），之后朗读都会模仿这个声音。

用法：
  .venv\\Scripts\\python set_voice.py 某段录音.mp3
  .venv\\Scripts\\python set_voice.py 某个视频.mp4 --start 1:23 --duration 10
  .venv\\Scripts\\python set_voice.py --reset          恢复成自带音色

素材要求：只有一个人在说话、没有背景音乐、没有混响，10 秒左右效果最好。
"""
import argparse
import shutil
import subprocess
from pathlib import Path

VOICE_DIR = Path(__file__).parent / "voice"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", nargs="?")
    p.add_argument("--start", default="0", help="从第几秒开始截取，如 83 或 1:23")
    p.add_argument("--duration", default="12", help="截取多少秒（建议 5~15）")
    p.add_argument("--reset", action="store_true")
    args = p.parse_args()

    VOICE_DIR.mkdir(exist_ok=True)
    ref, txt = VOICE_DIR / "reference.wav", VOICE_DIR / "reference.txt"
    if args.reset:
        ref.unlink(missing_ok=True)
        txt.unlink(missing_ok=True)
        print("已恢复自带音色，重启银月后生效")
        return
    if not args.source:
        p.error("请给出音频或视频文件路径")
    if not shutil.which("ffmpeg"):
        raise SystemExit("找不到 ffmpeg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", args.start, "-t", args.duration,
                    "-i", args.source, "-ac", "1", "-ar", "24000", "-af", "loudnorm",
                    "-sample_fmt", "s16", str(ref)], check=True)
    txt.unlink(missing_ok=True)  # 文字稿会在下次启动时自动重新识别
    print(f"已设置音色：{ref}\n重启银月后生效。如果识别出的文字稿有错，可以手动改 {txt}")


if __name__ == "__main__":
    main()
