#!/usr/bin/env bash
# 下载银月所需模型（可重复运行，已下载的会跳过/续传）
set -u
mkdir -p "$(dirname "$0")/models"
cd "$(dirname "$0")/models" || exit 1
GH=https://github.com/k2-fsa/sherpa-onnx/releases/download
get() { # url out
  curl -L -C - --retry 5 --retry-delay 5 -s -S -o "$2" "$1" && echo "OK  $2" || echo "FAIL $2"
}
get "$GH/asr-models/silero_vad.onnx" silero_vad.onnx
for t in asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09 tts-models/kokoro-int8-multi-lang-v1_1; do
  name=$(basename "$t")
  if [ ! -d "$name" ]; then
    get "$GH/$t.tar.bz2" "$name.tar.bz2" && tar xjf "$name.tar.bz2" && rm -f "$name.tar.bz2" && echo "EXTRACTED $name"
  else echo "SKIP $name"; fi
done
get "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" Qwen3-4B-Instruct-2507-Q4_K_M.gguf
ls -la
echo ALL DONE
