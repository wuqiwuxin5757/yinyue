# 在 Windows 运行银月

从仓库根目录运行下面的命令。先完成 README 中的 Python 3.12 环境安装。把 FFmpeg 加到 PATH，并下载适合本机的 Windows 版 llama.cpp，将解压目录中的 `llama-server.exe` 配置给银月。

```powershell
$env:YINYUE_LLAMA_SERVER = 'C:\Tools\llama.cpp\llama-server.exe'
ffmpeg -version
```

下载语音识别和对话权重时可从 Git Bash 运行 `bash download_models.sh`。脚本把 SenseVoice、Silero VAD、Qwen3-4B 的 GGUF 和可选的 Kokoro 文件放到 `models/`；默认的朗读音色见下一节。运行前确认这些路径存在。

- `models/silero_vad.onnx`
- `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/model.int8.onnx`
- 同一 SenseVoice 目录里的 `tokens.txt`
- `models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`

llama.cpp 的 Windows 压缩包可以从其上游发布页下载。后端版本需要支持 `--jinja` 和工具调用。 [llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases)

## 安装 CosyVoice 内置音色

CosyVoice 用单独的 Python 3.10 环境。以下版本与项目当前的部署对应。若依赖安装时 `pyworld` 需要在本机编译，请先安装 Microsoft C++ Build Tools。

```powershell
git clone https://github.com/FunAudioLLM/CosyVoice.git cosyvoice/repo
git -C cosyvoice/repo checkout 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
git -C cosyvoice/repo submodule update --init --recursive
uv venv cosyvoice/.venv --python 3.10
$voicePython = ".\cosyvoice\.venv\Scripts\python.exe"
uv pip install --python $voicePython -r cosyvoice/repo/requirements.txt
& $voicePython -c "from huggingface_hub import snapshot_download; snapshot_download('FunAudioLLM/CosyVoice-300M-SFT', local_dir='cosyvoice/CosyVoice-300M-SFT')"
& $voicePython -c "from modelscope import snapshot_download; snapshot_download('pengzhendong/wetext', local_dir='cosyvoice/modelscope_cache/hub/pengzhendong/wetext')"
```

验证音色时，第一条命令会启动本机模型并读出测试句。第二条测试文本问答。第三条启用麦克风和桌面形象，启动后可以说「银月，在屏幕上跑一圈」。

```powershell
.\.venv\Scripts\python.exe assistant.py --say "你好，我是银月。"
.\.venv\Scripts\python.exe assistant.py --text "一加二等于几？"
.\.venv\Scripts\python.exe assistant.py
```

查看 `logs/assistant.log`、`logs/llama.log` 和 `logs/cosyvoice.log` 可定位模型启动错误。截图视觉定位使用独立的可选 Qwen3-VL 权重，路径在 `config.json` 的 `vision_model` 和 `vision_mmproj`。建议先完成语音与桌面角色验证，再配置视觉或手机遥控。

第三方模型和素材按上游条款使用。 [CosyVoice-300M-SFT](https://huggingface.co/FunAudioLLM/CosyVoice-300M-SFT) · [WeText](https://modelscope.cn/models/pengzhendong/wetext)
