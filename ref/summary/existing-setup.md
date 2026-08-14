# 本机已验证可行的语音输入配置

记录时间：2026-08-14
机器：Ubuntu 26.04 LTS，GNOME + Wayland，Python 3.14.4

这份文档记录的是**在本机实际跑通过的**语音输入方案。myna 的实现应当以此为
事实基础，而不是从零猜测技术选型。

---

## 1. 已跑通的方案

核心脚本：`~/.local/bin/whisper-dictate`（Python，149 行）。

整条链路：

```
快捷键 → whisper-dictate toggle
         ├─ 未录音 → ffmpeg 从 PulseAudio 录 16kHz 单声道 wav，pid 存 /tmp
         └─ 录音中 → SIGTERM 停止 ffmpeg，fork 一个 transcribe 子进程后立即返回
                      └─ faster-whisper 转写 → wl-copy 写剪贴板 → ydotool 模拟 Ctrl+V 粘贴
```

关键设计（都是踩过坑之后的产物，值得继承）：

- **停止即返回**：`stop()` 把转写扔进独立子进程，快捷键不会卡住桌面。
- **补环境变量**：GNOME 快捷键守护进程启动的子进程没有完整会话环境，脚本开头
  显式 `setdefault` 了 `XDG_RUNTIME_DIR` / `PULSE_SERVER` / `WAYLAND_DISPLAY` /
  `DISPLAY`。缺这段在快捷键触发时会静默失败，手动在终端跑却正常——非常难查。
- **剪贴板 + 模拟粘贴**，而非逐字符输入。Wayland 下没有 XTEST，逐字符 type 中文
  不可靠且慢。
- **双通道兜底**：剪贴板 `wl-copy` → `xclip`；粘贴 `ydotool` → `xdotool`。
- **ydotool 的按键用 keycode**：`ydotool key 29:1 47:1 47:0 29:0`
  （29=LEFTCTRL，47=V，`:1` 按下 `:0` 抬起），不是 `ctrl+v` 字符串。
- **过短音频直接丢弃**：wav < 1000 字节视为误触，不送识别。

### 依赖现状（均已安装并验证）

| 组件 | 位置 / 版本 | 用途 |
|---|---|---|
| faster-whisper | 1.2.1 (pip) | 识别主力 |
| ctranslate2 | 4.7.1 | faster-whisper 后端 |
| openai-whisper | pipx venv，`~/.local/bin/whisper` | 备用 CLI |
| ffmpeg | /usr/bin/ffmpeg | 录音（`-f pulse -i default`） |
| parecord | /usr/bin/parecord | 录音备选 |
| ydotool + ydotoold | /usr/bin/ydotool，user service **已 enabled 且 running** | Wayland 下模拟按键 |
| xdotool / xclip | /usr/bin | XWayland 兜底 |
| wl-copy | /usr/bin | Wayland 剪贴板 |
| notify-send | /usr/bin | 状态提示 |
| onnxruntime-gpu | 1.27.0 | 备选推理后端 |

ydotool 的 socket：`/run/user/1000/.ydotool_socket`，由
`~/.config/systemd/user/ydotool.service` 拉起，**已 enabled**（开机自启）。
调用时需设 `YDOTOOL_SOCKET` 环境变量指向它。

### 快捷键绑定

GNOME 自定义快捷键 `custom0` 已存在：

- name: `Whisper Dictation`
- command: `/home/baojie/.local/bin/whisper-dictate toggle`
- binding: **空字符串** ← 注意，实际按键没绑上

也就是说脚本本身跑通了，但快捷键这一环当前是断的。myna 需要自己解决唤醒键，
不能假设 GNOME 那条绑定可用。

---

## 2. 实测数据（2026-08-14 迁移后复验）

用 piper TTS 合成一句中文，再送 faster-whisper `small` 转写：

- 输入：`今天天气很好，我们一起去公园散步吧。`
- 输出：`今天天氣很好,我們一起去公園三步吧!`
- 模型加载 1.7s（冷启动），转写 1.6s（CPU int8，约 3.5s 音频）

暴露出两个必须在 myna 中处理的问题：

1. **输出是繁体**。Whisper 中文默认倾向繁体。需要 `initial_prompt` 给简体示范
   句，或事后过一遍 opencc（t2s）。
2. **`small` 精度不够**：「散步」→「三步」。中文场景建议默认 `medium`，本机
   `medium` 与 `large-v3` 都已下载好，切换零成本。

另外脚本用 `device="cpu"` —— 机器上装了 `onnxruntime-gpu`，是否有可用 GPU
值得 myna 在选型阶段再确认一次，能上 GPU 的话 `large-v3` 也不贵。

---

## 3. 另一套废弃的实现（勿参考）

`~/.local/bin/dictate-start` / `dictate-stop` / `dictate-toggle`（bash，2026-04）
是更早的一版：parecord 录音 + `ydotool type` 逐字符输入。它依赖
`~/faster-whisper-dictation/venv`，**该目录在主盘上已不存在**（残留在
`/data/downloads/tmp/faster-whisper-dictation`），所以这套现在是坏的。
`ydotool type` 输中文也不可靠。留着只作为「此路不通」的记录。

同类成品应用 Speech Note（flatpak `net.mkiol.SpeechNote`）也装着，可作对照，
但它是独立 GUI 应用，不解决"任意输入框"的问题。

---

## 4. 模型文件位置（已全部迁至 /data）

主盘 `/` 563G 已用 98%，只剩 14G，因此本机所有本地模型已迁到 `/data`，
**原位置一律留符号链接，对应用透明**。迁移脚本：
`scripts/migrate-models-to-data.sh`（幂等，可重复执行）。

| 内容 | 现位置 | 原位置（现为符号链接） | 大小 |
|---|---|---|---|
| HuggingFace 缓存（含 faster-whisper small / medium / large-v3） | `/data/cache/huggingface` | `~/.cache/huggingface` | 20G |
| openai-whisper `base.pt` | `/data/models/openai-whisper` | `~/.cache/whisper` | 139M |
| piper 中文语音 `zh_CN-huayan-medium.onnx` | `/data/models/piper-voices` | `~/.local/share/piper-voices` | 61M |
| Speech Note 模型 | `/data/models/speechnote` | `~/.var/app/net.mkiol.SpeechNote/.../speech-models` | 1.5G |
| VS Code speech 扩展（en / zh） | `/data/models/vscode-speech-{en,zh}` | 两个扩展的 `assets/` | 455M |
| insightface buffalo_l | `/data/models/insightface` | `~/.insightface/models` | 184M |

本轮从主盘释放约 2.3G（`~/.cache/huggingface` 早在 2026-05-12 就已经是指向
`/data` 的符号链接，不在本轮内）。

注意事项：

- **Speech Note 是 flatpak**，沙箱默认看不到 `/data`，符号链接会是断链。迁移
  脚本已执行 `flatpak override --user --filesystem=/data/models
  net.mkiol.SpeechNote` 补上授权。
- **VS Code 两个 speech 扩展**的 `assets/` 被换成了符号链接。扩展升级会安装到
  带新版本号的新目录并重新下载模型，届时 `/data/models/vscode-speech-*` 会变成
  无人引用的残留，需要手工清理。
- Chrome 的 `OptGuideOnDevice*/weights.bin` **没有迁移**：路径含版本号、由浏览器
  自行管理和更新，动它得不偿失。

myna 自己的模型请一律走 HuggingFace 缓存（即 `/data/cache/huggingface`），
不要在仓库或主盘另存权重。

---

## 5. 给 myna 的结论

可以直接继承的：ffmpeg/pulse 录音参数、faster-whisper 调用方式、
剪贴板+ydotool 粘贴的注入策略、GNOME 快捷键子进程的环境变量修补、
转写异步化。

必须自己解决的：唤醒快捷键的可靠绑定（现有绑定是空的）、简繁转换、
默认模型档位（`small` 不够用）、常驻进程避免每次重新加载模型
（当前每次转写都要 1.7s 冷启动）。
