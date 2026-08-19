# myna 八哥

Linux 桌面语音输入法：在任何应用的文本输入框里，按一个快捷键开始说话，再按一次
结束，识别出的文字直接落在光标处。

名字取自八哥（myna），会学舌的鸟。

全程本地，不联网——模型只从本地缓存加载，启动时不会去连 HuggingFace。

## 它和一段脚本的区别

Whisper 模型加载要 10~13 秒，而转写只要 0.7 秒。所以模型必须常驻内存，
不能每次现加载；而快捷键又必须立刻返回，不能卡住桌面。myna 因此是
**守护进程 + 瘦客户端**：快捷键只往 Unix socket 发一行 JSON。

## 安装

需要 `ffmpeg`、`ydotool`（含已启用的 `ydotoold`）、`wl-clipboard`、`libnotify-bin`，
以及 Python 包 `faster-whisper`（可选 `opencc-python-reimplemented` 用于繁转简）。
默认档位 `qwen3` 另需 `onnxruntime`、`librosa`、`tokenizers`（GPU 机器用
`onnxruntime-gpu` + cuDNN，见 [ref/spec/spec.md](ref/spec/spec.md) 第 15 节）。

```bash
./run.sh doctor    # 逐项自检，缺什么会告诉你怎么装
./run.sh install   # 绑定快捷键 + 装并启动 systemd 用户服务 + 把图标放进 dock，之后开机自启
```

`run.sh` 会把本 checkout 的 `src/` 加进 `PYTHONPATH`，所以不装也能跑；
想把 `myna` 命令装到 PATH 里则用 `pip install --user -e .`。

常用：

```
./run.sh            前台启动守护进程，日志直接打在终端（调试用）
./run.sh restart    改完代码重启后台服务
./run.sh log        跟踪后台服务日志
./run.sh test       跑单元测试
./run.sh <其他>     原样透传给 myna，如 ./run.sh status
```

默认快捷键是 **Super+Z**。`myna install --key '<Super>space'` 可换别的键。

绑定是幂等的，且做了两道检查：

- **写入后回读校验**——现有的 whisper-dictate 方案就是断在这一环：GNOME 里那条
  快捷键的 name 和 command 都对，binding 却是空字符串，等于没绑。
- **绑定前扫描 GNOME 全部 5 个快捷键 schema 查冲突**，撞了就拒绝并告诉你是谁
  占着（`--force` 可强绑）。

> 别用 `<Super>D`——GNOME 自带的「显示桌面」占着它。绑上去按了只会最小化窗口，
> 而配置、回读、`doctor` 全都显示正常，极难查。这曾是本项目的默认值。

## 使用

按 **Super+Z**（默认激活快捷键，`myna install --key` 可换）→ 说话（通知栏
显示 🎙 录音中）→ 再按一次 → 一秒内文字出现在输入框。

```
myna toggle     开始/停止（快捷键绑的就是它）
myna cancel     放弃本次录音，不识别
myna model      切换识别模型（如 `myna model medium`，也可托盘里点）
myna paste-key  设置粘贴键（终端用 ctrl+shift+v，见下）
myna status     查看状态、当前模型、设备、粘贴键
myna history    查看识别历史（见下，批量纠错用）
myna doctor     依赖自检
myna daemon     前台运行守护进程（调试用）
myna uninstall  移除快捷键、服务和图标
```

## 顶栏图标

装好后顶栏常驻一个话筒图标，是这个没有窗口的程序唯一的「我还活着」的出口：

| 图标 | 状态 |
|---|---|
| 🎤 话筒 | 待机 |
| ⏺ 红点 + 「● 录音中」 | 正在录音 |
| ⟳ 转圈 | 识别中 |

点开有菜单：开始/停止录音、放弃本次录音、复制上次识别结果、**识别模型**
（子菜单切换档位，当前打勾）、**粘贴方式**、关于、退出。

切换模型不用重启 daemon：点菜单项或 `myna model medium`，加载在后台进行，
几秒后弹出通知确认；加载期间旧模型继续用，切换失败也会明确告诉你并留在旧档。

### 粘贴键默认用 Shift+Insert

**终端的粘贴键是 Ctrl+Shift+V，普通输入框是 Ctrl+V，而 Wayland 下分辨不了
当前是哪种。** 不是没做，是做不到：GNOME Shell 的 Introspect 接口返回
AccessDenied，xdotool 也看不见原生 Wayland 窗口——客户端无权知道别的窗口是谁，
这是 Wayland 的安全模型。

绕法是**换一个两边都认的键**：Shift+Insert。终端（kitty/VTE/alacritty）和
普通输入框都响应它，于是根本不需要知道焦点在谁身上。唯一的讲究是两边读的
不是同一份剪贴板——终端粘的是 PRIMARY（鼠标选中区），输入框粘的是 CLIPBOARD——
所以 myna 会把识别结果**同时写进这两份**，`restore_clipboard` 也会把两份
一起还原。

要固定成某一个键也可以，托盘菜单「粘贴方式」里选，会写回配置文件。也可以用命令：

```bash
myna paste-key shift+insert   # 通用（默认）
myna paste-key ctrl+shift+v   # 固定终端
myna paste-key ctrl+v         # 固定普通输入框
myna paste-key                # 看当前是哪个
```

同理，配置里的 `[inject.paste_key_by_app]`（按窗口类自动切）在 Wayland 上
基本是死配置，只对 XWayland 应用偶尔生效。

需要 `python3-gi` 和 `gir1.2-ayatanaappindicator3-0.1`（Ubuntu 自带），
以及 GNOME 的 AppIndicator 扩展（Ubuntu 默认启用）。缺了也不影响主功能，
只是没图标；也可以在配置里 `[tray] enabled = false` 主动关掉。

托盘初始化失败时会**发桌面通知**告诉你原因，不会让你只看到「图标没了」却
无从查起——这条兜底本身曾经就是最难查的那个坑。

托盘是配角：**它中途没了，语音输入照常工作**，只是转为无图标运行并通知你一声。
只有点「退出 myna」才会真的关掉服务。（曾经不是这样——GTK 主循环因为任何原因
返回都会把 daemon 一起带走，而且退出码是 0，systemd 的 `Restart=on-failure`
也不来救，于是「点了下托盘图标，服务就永久躺平了」。）

## 配置

`~/.config/myna/config.toml`，全部可选。见 [config.example.toml](config.example.toml)。

### 换识别模型

`[asr] model` 填档位名即可，自动匹配设备与精度：

| 档位 | 完整模型 | 定位（实测见下） |
|---|---|---|
| `qwen3` | cvxhull/qwen3-asr-0.6b-onnx-fp16 | **默认**。Qwen+ONNX，中文最准（CER 8.8%），GPU 上最快（RTF 0.067） |
| `large-v3` | Systran/faster-whisper-large-v3 | whisper 里 GPU 最快，CER 19.2%* |
| `turbo` | deepdml/faster-whisper-large-v3-turbo-ct2 | 省显存（2.2G）且最快，但精度没赢过 medium |
| `medium` | Systran/faster-whisper-medium | 均衡（CER 14.7%，2.0G 显存） |
| `large-v2` | Systran/faster-whisper-large-v2 | 次准，未实测 |
| `small` | Systran/faster-whisper-small | GPU 不可用时的回退档，中文误识明显（CER 21.5%） |
| `base` / `tiny` | Systran/faster-whisper-base / tiny | 最轻，未实测 |

> `turbo` 用的是 `deepdml` 的社区转换版——**Systran 没有出 turbo 的
> CTranslate2 版本**，写成 `Systran/faster-whisper-large-v3-turbo` 会 401。

也可以直接写任意 HuggingFace 模型名（如 `"Systran/faster-whisper-medium"`）。
GPU 不可用时会自动降级到 `[asr] fallback_model`（默认 `small`），并明确通知你。
`myna status` 会显示当前解析后的完整模型名、设备与计算类型。

> `qwen3` 和其余档位不是一回事：它不走 faster-whisper，是 Qwen 架构 + ONNX，
> 由 `onnxruntime` 推理。需要额外装 `onnxruntime`（GPU 机器用
> `onnxruntime-gpu` + cuDNN）、`librosa`、`tokenizers`，未装时选它会明确提示
> 缺什么。实测（见下）它**中文最准**（CER 8.8%，比 whisper 各档低一半以上），
> GPU 上 RTF 0.067 全场最快（比 large-v3 快 3 倍），无 GPU 自动回 CPU
> （RTF 0.64–0.80）。模型 2.9G（fp16），重依赖一律装 `/data`。

最常改的是 `[hotwords]` —— 把反复听错的人名、术语强制改回来。

### 识别历史

每次识别落一行 JSONL 存下来，供日后批量纠错。默认位置：

```
~/.local/share/myna/
├── history/2026-08.jsonl        文本历史，按月一个文件
└── audio/2026-08/<id>.wav       音频（默认关，开了才有）
```

准确说是 `$XDG_DATA_HOME/myna`，没设这个环境变量才落到 `~/.local/share`。
`myna history --path` 打印当前实际路径。

```
myna history            最近 20 条
myna history -n 100     最近 100 条
myna history --raw      同时显示后处理前的原始输出
myna history --json     JSONL 输出，接管道用
myna history --path     只打印存档路径
```

一条记录长这样：

```json
{"id":"3f1c…","ts":"2026-08-14T22:41:07.213+08:00","raw":"去公园三步",
 "text":"去公园散步","duration":2.1,"latency":0.7,"rtf":0.333,
 "model":"Systran/faster-whisper-large-v3","device":"cuda","injected":"ok",
 "hotwords_hit":["三步"],"audio":null}
```

**`raw` 和 `text` 两份都存**是刻意的：只有对照着看，才分得清一处错是模型听错的，
还是热词表/繁转简自己改坏的——这两种错的修法完全相反。`hotwords_hit` 则回答
「加的词到底有没有在用」。

时间戳带毫秒和时区偏移，是为了能和 Claude Code 的 transcript
（`~/.claude/projects/*/*.jsonl`，UTC）按时间对齐：myna 注入的文本 vs 你按回车前
手改过的文本，差异就是现成的纠错标注。这是日后做批量纠错的原料。

全部配置项：

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 关掉就完全不记 |
| `save_audio` | `false` | 连 wav 一起存，日后能拿新模型对老录音重跑做 A/B |
| `max_audio_mb` | `200` | 音频总量上限，超了自动删最旧的（文本历史不受影响） |
| `dir` | `""` | 存档根目录；留空即 `$XDG_DATA_HOME/myna`。`history/` 和 `audio/` 一起跟着走 |

体积：文本一条约 300 字节，一天说一百句也才 30KB/月，放哪都无所谓。音频是
16kHz 单声道 wav，**约 32KB/秒**，默认 200MB 上限约合 1.8 小时语音——这个量就得
挑盘放了：

```toml
[history]
save_audio = true
dir = "/data/myna"        # 主盘紧张时把整个存档挪走
```

> 历史文件里是你说过的每一句话。目录和文件都是 0700/0600，只有本人可读；
> 不需要就 `enabled = false` 关掉。

（模型缓存是另一套路径，`myna models` 会打印，跟这个存档互不相干。）

## 实测

RTX 4060 Laptop 8G，20 句中文（piper 合成），float16：

| 档位 | 平均字错率 | RTF | 显存 | 加载 |
|---|---|---|---|---|
| **qwen3**（默认，GPU/fp16） | **8.8%** | **0.067** | ~3.9G | 6.5s |
| **large-v3** | 19.2%* | 0.217 | 3838 MB | 17.6s |
| turbo | 20.8% | 0.134 | 2174 MB | 3.4s |
| medium | 14.7% | 0.142 | 2014 MB | 4.0s |
| small（CPU/int8） | 21.5% | 0.485 | — | 3.4s |
| qwen3（无 GPU 时 CPU） | 8.8% | 0.64–0.80 | — | ~17s |

\* large-v3 的 19.2% 被**单句异常**拉高：第 14 句大数字「一千二百三十四万五千六百七十八」它 100% 全错（medium/qwen3 全对），一句就贡献约 5 个百分点。去掉这 1 句它约 14.9%，与 medium 同级——**whisper 内部这几档谁更准，20 句样本不足以定论**，见下。

两个指标的含义：

- **字错率（CER, Character Error Rate）**＝ 识别结果与正确答案的字符编辑距离
  ÷ 正确答案字数，**越低越好**。例：正确「推理速度」被听成「推理素肚」，
  18 字里错 2 个 → CER 11%。标点和空格不计——Whisper 的标点基本靠猜，
  算进去会淹没真正的识别错误。
- **实时率（RTF, Real Time Factor）**＝ 转写耗时 ÷ 音频时长，**越低越快**。
  RTF 0.244 表示 10 秒录音要转 2.44 秒。**1.0 是分界线**：超过 1 意味着说完
  还得等更久，交互上不可接受（表里最慢的 qwen3 CPU 回退 0.64–0.80，勉强可用）。
  用比值而非秒数，是为了不受音频长短影响、可横向比较。

**qwen3 又快又准，是默认**：8.8% 全场最低，甩开 whisper 各档一半以上。数字句
（大数字、百分之 X）、成语古文、人名地名、书面长句它几乎全对——whisper 在
「百分之八十」上错 37%~42%，大数字句 large-v3 直接 100% 全错。它是**专门的中文
ASR**（Qwen3 架构），这个语料上优势很明显。速度同样拔尖：GPU 上 RTF 0.067，
20 句 75 秒音频只转 5 秒，比 large-v3 快 3 倍多，交互完全无感。没 GPU 时
onnxruntime 自动回 CPU（RTF 0.64–0.80，负载敏感），比任何 whisper 档都准，
代价是说完要等 0.6~0.8 倍时长——对无 GPU 机器它仍是准优先时最好的选择。

**为什么默认是 qwen3**：GPU 上它 RTF 0.067、CER 8.8%，速度与准确率全面压过
large-v3（0.217 / 19.2%*），显存量级相同。large-v3 的 19.2% 有单句异常成分
（第 14 句大数字 100% 全错，去掉后 ~14.9%），但即便按去噪后的数字也不及 qwen3。
whisper 内部这几档谁更准，20 句样本依然定不了论——但已不需要论了，qwen3
全面更好。若没有 GPU，选它要接受 0.64–0.80 的实时率；GPU 机器上它没有短板。

**turbo 依然不划算**：20.8% vs medium 14.7%，继续没赢。它唯一的价值还是省
显存——比 large-v3 少 1.7GB，要同时跑别的 GPU 任务时切它，远好过被挤到 small。

> 这是 20 句 piper 合成语音，**不是严谨评测**。合成语音比真人清晰；中英混说
> （`myna`/`Linux`/`Python`/`Java`/`HTTP`）仍是所有模型的共同短板——qwen3 相对
> 好一点（Python 句只错一个词），whisper 整句崩。方法局限：whisper 常把中文数字
> 写成「80%」这类缩写，语义对但被 CER 计为错。**5 句和 20 句的结论截然不同**——
> 样本构成对结论影响巨大，这套数字只够支撑「qwen3 中文更准、且 GPU 上更快」
> 这一个方向。

配置里的 `initial_prompt`（默认「以下是简体中文的句子。」）**不是可选优化**：
实测它一举解决了 Whisper 中文输出繁体、以及小模型的误识两个问题。

## 已知限制

- **只保证 Wayland + GNOME**。X11 有兜底路径（xdotool/xclip）但不承诺。
- **只能 toggle，不能按住说话**。GNOME 快捷键只有按下事件、没有释放事件。
- **说完才出字**，不做流式上屏。
- **daemon 常驻占约 4GB 显存**（qwen3 和 large-v3 都是这个量级）。跑别的 GPU
  任务时可能挤不下，届时 faster-whisper 档位会自动降级到 small/CPU 并**明确
  通知**你（qwen3 是独立 ONNX 后端，不参与这条降级链，自身自动回 CPU）。
  识别突然变差时先看 `myna status` 的「已降级」字段——`small` 会把「散步」听成
  「三步」。换个小档位可以让出显存：`myna model medium`。
- **粘贴键要手动切**（见上）。Wayland 下无法探测焦点窗口，这是安全模型决定的，
  不是没做。
- 按完快捷键就切换窗口的话，文字会粘到新窗口去。v1 不处理。

任何注入手段都失败时，**文本一定还在剪贴板里**，并会提示你手动粘贴——
不会让你白说一句。

## 文档

- [ref/spec/spec.md](ref/spec/spec.md) —— 设计规格。末尾有 4 则**事故记录**，
  每则都写清了因果链：GTK 的 locale 副作用打死转写、剪贴板写入被误判失败、
  默认快捷键撞 GNOME 内置键、托盘图标静默消失。共同点是「配置全对、日志正常、
  功能就是不工作」。
- [ref/summary/existing-setup.md](ref/summary/existing-setup.md) —— 本机原有方案的
  勘察记录与实测数据，本项目的事实基础

## 开发

```bash
./run.sh test       # 全部单元测试
./run.sh restart    # 改完代码重启服务
./run.sh log        # 跟日志
```

模型权重一律走 HuggingFace 缓存，不在仓库或主盘另存——主盘已用 98%。放哪由
配置决定：

```toml
[models]
cache_dir = "/data/cache/huggingface/hub"   # 留空则跟随 HF 默认
```

优先级是 `HF_HUB_CACHE` 环境变量 > `[models] cache_dir` > `HF_HOME/hub` >
`~/.cache/huggingface/hub`。daemon 和 CLI 读的是同一处，所以不必再分别给
shell 和 systemd 单元设环境变量；`myna models` 头一行会打印实际用的目录。

## 许可

MIT
