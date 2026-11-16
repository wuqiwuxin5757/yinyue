#!/usr/bin/env bash
# 下载 CosyVoice 3 推理所需文件（跳过 llm.rl.pt / TensorRT 用的 onnx / 批量版 tokenizer）
cd "$(dirname "$0")"
HF=https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/resolve/main
D=Fun-CosyVoice3-0.5B
mkdir -p $D/CosyVoice-BlankEN
for f in config.json configuration.json cosyvoice3.yaml campplus.onnx hift.pt flow.pt llm.pt speech_tokenizer_v3.onnx \
         CosyVoice-BlankEN/config.json CosyVoice-BlankEN/generation_config.json CosyVoice-BlankEN/merges.txt \
         CosyVoice-BlankEN/model.safetensors CosyVoice-BlankEN/tokenizer_config.json CosyVoice-BlankEN/vocab.json; do
  curl -L -C - --retry 5 --retry-delay 5 -s -S -o "$D/$f" "$HF/$f" && echo "OK $f" || echo "FAIL $f"
done
echo ALL DONE
