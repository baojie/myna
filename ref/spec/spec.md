# myna 设计规格

版本：v1（2026-08-14）

## 0. 一句话

按一次快捷键开始说话，再按一次停止，识别出的文字直接落在当前焦点的输入框里。

## 1. 目标与非目标

### 目标

- 全局可用：任何应用的任何文本输入框，不要求应用配合。
- 一个快捷键唤醒，再按一次结束。
- 中文优先（简体），兼顾英文与中英混说。
- 从松开快捷键到文字出现，**1 秒以内**。
- 完全本地，不联网。

### 非目标（v1 不做）

- 实时流式上屏（说一半就往外冒字）。v1 是「说完再出」。
- 语音命令控制（"删除上一句"、"换行"之类）。
- 输入法框架集成（IBus/Fcitx engine）。走通用注入路线，见 §3.4。
- 多用户、多机器同步、云端。
- X11。本机是 Wayland，v1 只保证 Wayland + GNOME，X11 走兜底路径但不承诺。

## 2. 事实基础

设计不从零猜测，依据 `ref/summary/existing-setup.md` 记录的**本机已跑通的方案**
和 2026-08-14 的实测数据：

| 实测项 | 结果 |
|---|---|
| GPU | RTX 4060 Laptop 8G，ctranslate2 CUDA 可用，支持 float16 |
| large-v3 / float16 / GPU | 模型加载 **12.7s**，转写 3.5s 音频耗时 **0.7s** |
| medium / float16 / GPU | 加载 9.7s，转写 0.7s |
| small / int8 / CPU | 加载 1.7s，转写 1.6s，且把「散步」听成「三步」 |
| `initial_prompt="以下是简体中文的句子。"` | **一并解决了繁体输出和误识**，输出「今天天气很好,我们一起去公园散步吧。」 |

三条结论直接决定架构：

1. **模型加载 10~13s，转写只要 0.7s** → 必须常驻进程预加载模型。每次现加载
   在交互上不可接受。这是 v1 相对现有 `whisper-dictate` 脚本最大的改进。
2. **GPU 上 large-v3 和 medium 一样快** → 默认直接用 `large-v3`，没有理由将就
   精度。CPU 回退档才用 `small`。
3. **`initial_prompt` 是必需参数**，不是可选优化。

## 3. 架构

```
   ┌──────────────┐   按键     ┌──────────────────────────────┐
   │ GNOME 快捷键 ├──────────► │ myna toggle  (瘦客户端, <50ms)│
   └──────────────┘            └───────────┬──────────────────┘
                                           │ Unix socket 一行 JSON
                                           ▼
   ┌───────────────────────────────────────────────────────────┐
   │ myna daemon   (systemd --user 常驻，启动时预加载模型)      │
   │                                                           │
   │  录音 audio.py ──► 转写 asr.py ──► 后处理 postprocess.py   │
   │   ffmpeg/pulse      faster-whisper     简繁·热词·标点      │
   │   16k 单声道 wav    cuda float16                          │
   │                              │                            │
   │                              ▼                            │
   │                      注入 inject.py ──► notify.py 提示     │
   │                      剪贴板 + 模拟 Ctrl+V                  │
   └───────────────────────────────────────────────────────────┘
```

分成 daemon + 瘦客户端，是因为快捷键必须**立刻**返回（否则桌面卡顿），而模型
必须常驻（否则每次 12s）。两个约束只有这一种解法。

### 3.1 状态机

daemon 内只有三个状态，任何时刻只有一次录音在进行：

```
IDLE ──toggle──► RECORDING ──toggle──► TRANSCRIBING ──完成/失败──► IDLE
                     │                       │
                     └──── cancel ───────────┴──► IDLE（丢弃）
```

- `TRANSCRIBING` 期间再按 toggle：忽略（并提示"正在识别"），不排队。
- 录音超过 `max_seconds`（默认 120s）自动停止并转写，防止忘记关。
- 音频短于 `min_seconds`（默认 0.3s）或 wav 小于 1KB：判为误触，静默丢弃。

### 3.2 录音 audio.py

沿用已验证的参数：

```
ffmpeg -f pulse -i default -ar 16000 -ac 1 -y <tmpfile>
```

- 停止用 `SIGTERM`（不是 SIGKILL），让 ffmpeg 写完 wav 头，否则文件损坏。
- 录音写到 `$XDG_RUNTIME_DIR/myna/rec-<n>.wav`（tmpfs，不落盘）。
- daemon 由 systemd 启动，环境完整；但仍保留 `PULSE_SERVER` 等
  `setdefault` 兜底（见 §3.6），代价为零。

### 3.3 识别 asr.py

```python
WhisperModel(model_size, device="cuda", compute_type="float16")
model.transcribe(wav, language="zh", beam_size=5,
                 initial_prompt="以下是简体中文的句子。",
                 vad_filter=True)
```

- 默认 `large-v3` on cuda/float16。
- **启动时探测**：`ctranslate2.get_cuda_device_count()`，或加载失败（缺 cuDNN、
  显存不足），自动回退 `small` + cpu/int8，并通知用户已降级。降级必须可见，
  不能让用户以为精度就这样。
- `vad_filter=True` 去掉首尾静音和空段，减少幻听（whisper 在静音上会输出
  「谢谢观看」之类的训练集残留）。
- 模型走 HuggingFace 缓存，即 `/data/cache/huggingface`（`~/.cache/huggingface`
  是指向它的符号链接）。**不在仓库或主盘另存权重**——主盘已用 98%。

### 3.4 注入 inject.py

Wayland 下没有 XTEST，逐字符 type 中文不可靠。已验证可行的是**剪贴板 + 模拟
Ctrl+V**：

1. 备份当前剪贴板（`wl-paste`）。
2. `wl-copy` 写入识别文本。
3. `ydotool key 29:1 47:1 47:0 29:0`（29=LEFTCTRL，47=V，`:1` 按下 `:0` 抬起）。
   必须设 `YDOTOOL_SOCKET=/run/user/<uid>/.ydotool_socket`。
4. 短暂延迟后恢复原剪贴板——不能默默吞掉用户原本复制的东西。

兜底链：`wl-copy` → `xclip -selection clipboard`；`ydotool` → `xdotool key
--clearmodifiers ctrl+v`。全部失败时：文本留在剪贴板，通知用户"已复制，请手动
粘贴"——**永不丢失识别结果**。

已知局限，如实记录：某些应用（终端、Vim）Ctrl+V 语义不同。v1 提供配置项
`paste_key`，允许按窗口类覆盖（如终端用 Ctrl+Shift+V）。窗口类通过
`gdbus` 查询 GNOME Shell，取不到就用默认键。

### 3.5 后处理 postprocess.py

按顺序：

1. **简繁转换**：`initial_prompt` 已能大幅缓解，但不保证。用 `opencc` 做
   `t2s` 兜底（可配置关闭）。opencc 不可用时跳过，不报错。
2. **热词替换**：用户词表，`词典.toml` 里配 `错误 = 正确`，直接字符串替换。
   解决人名、术语、项目名反复识错的问题。
3. **首尾空白与多余标点**清理。空结果直接不注入，只通知。

### 3.6 唤醒快捷键

**现有的 GNOME `custom0` 绑定 name/command 都对，但 `binding` 是空字符串**——
这正是现有方案最后一环断掉的地方。myna 必须自己把它配上，不能假设可用。

`myna install` 负责：

- 用 `gsettings` 写入自定义快捷键，command 为 `myna toggle`，
  binding 默认 `<Super>d`（可 `--key` 指定）。
- 幂等：已存在同名 `myna` 的绑定则更新，不重复追加。
- 安装并 `enable --now` 一个 `myna.service`（systemd user unit）。
- 检查 `ydotool.service` 是否 enabled，未启用则提示（本机已 enabled）。
- 打印一份自检结果：ffmpeg / ydotool socket / GPU / 模型缓存是否就位。

GNOME 快捷键只有「按下」事件、没有「释放」事件，所以 v1 用 **toggle** 语义而非
按住说话。这是平台限制，不是偏好。

### 3.7 反馈 notify.py

`notify-send` 三个节点：🎙 录音中 → ⏳ 识别中 → ✅ 结果前 40 字（或 ❌ 原因）。
录音中的通知用同一个 replace-id 更新，不堆叠一屏。

## 4. 接口

### CLI

| 命令 | 作用 |
|---|---|
| `myna daemon` | 前台运行守护进程（systemd 调这个） |
| `myna toggle` | 开始/停止（快捷键绑这个） |
| `myna start` / `myna stop` | 显式控制 |
| `myna cancel` | 放弃本次录音，不识别 |
| `myna status` | 打印状态、模型、设备、socket |
| `myna model <档位>` | 热切换识别模型（托盘菜单同一个动作） |
| `myna install` / `myna uninstall` | 配置/移除快捷键与 systemd 服务 |
| `myna doctor` | 依赖自检，逐项给出修复建议 |

客户端在 daemon 未运行时给出明确提示（`systemctl --user start myna`），
不静默失败。

### 控制协议

`$XDG_RUNTIME_DIR/myna/control.sock`，每次连接一行 JSON 请求、一行 JSON 响应：

```json
→ {"cmd": "toggle"}
← {"ok": true, "state": "recording"}

→ {"cmd": "switch", "model": "turbo"}
← {"ok": true, "state": "idle", "switching": true}   // 后台加载，完成/失败会通知
```

socket 权限 0600。仅本机本用户。

### 配置

`~/.config/myna/config.toml`，全部可选，缺省即默认：

```toml
[asr]
model = "large-v3"        # 档位 turbo|large-v3|large-v2|medium|small|base|tiny，
                          # 或任意 HF 模型名；回退档 fallback_model（默认 small）
device = "auto"           # auto | cuda | cpu
language = "zh"
initial_prompt = "以下是简体中文的句子。"
beam_size = 5
vad_filter = true

[audio]
max_seconds = 120
min_seconds = 0.3

[inject]
method = "clipboard"      # clipboard | type
paste_key = "ctrl+v"
restore_clipboard = true

[postprocess]
to_simplified = true

[hotwords]
"三步" = "散步"
```

## 5. 模块与文件

```
src/myna/
  __init__.py
  cli.py           命令分发
  config.py        配置加载与默认值
  daemon.py        状态机 + socket 服务
  client.py        socket 客户端
  audio.py         ffmpeg 录音
  asr.py           faster-whisper 封装与设备探测
  postprocess.py   简繁、热词、清理
  inject.py        剪贴板 + 模拟按键，含兜底链
  notify.py        notify-send 封装
  install.py       gsettings 快捷键 + systemd unit + doctor
```

测试：`audio`/`inject`/`notify` 依赖外部程序，用 `subprocess` 打桩；
`postprocess`、`config`、状态机是纯逻辑，直接单测。ASR 不做单测（重、慢），
留 `myna doctor` 做真实链路自检。

## 6. 里程碑

- **M1 能用**：daemon + toggle + 录音 + 转写 + 注入 + 通知，配置走默认值。
  验收：任意输入框里按快捷键说一句中文，1 秒内出简体文字。
- **M2 装得上**：`install` / `doctor` / systemd unit / 快捷键自动绑定。
  验收：新装一遍，只跑 `myna install` 就可用。
- **M3 好用**：配置文件、热词、简繁、剪贴板恢复、按窗口类换粘贴键、GPU 降级提示。

## 7. 风险

| 风险 | 应对 |
|---|---|
| 快捷键触发的进程环境不全（历史上真的踩过） | daemon 由 systemd 拉起，环境完整；仍保留 setdefault 兜底 |
| ydotool socket 权限/未启动 | doctor 明确检查；注入失败时文本仍留剪贴板 |
| 终端等应用 Ctrl+V 语义不同 | 按窗口类配置 paste_key |
| 显存被别的程序占满 | 加载失败自动降级 CPU 并**显式通知** |
| whisper 在静音上幻听 | vad_filter + 最短时长门槛 |
| 主盘空间（已用 98%） | 权重只走 /data 的 HF 缓存；录音写 tmpfs |
| 粘贴时焦点已变（用户按完键切了窗口） | v1 不处理，如实记录为已知限制 |

---

## 8. 增补（v1.1）：顶栏状态图标

语音输入没有窗口，用户唯一能确认「它还活着、现在在干嘛」的地方就是顶栏。
通知只闪一下就没了，不足以替代常驻图标。

- `tray.py`，AppIndicator（KStatusNotifierItem）+ GTK3，用系统主题的 symbolic
  图标，随深浅色主题自动适配，不自带图标文件。
- **GTK 是可选依赖**：`available()` 探测不到就无图标运行，daemon 功能不受影响，
  也可在配置里 `[tray] enabled = false` 关掉。
- 线程模型：GTK 独占主线程跑 `Gtk.main()`，socket 循环挪到后台线程。
  daemon 只暴露 `on_state_change` 回调，不知道托盘存在；回调可能来自任意线程，
  一律 `GLib.idle_add` 转交主线程——跨线程直接碰 GTK 会随机崩。
- 菜单：开始/停止、放弃本次录音、复制上次识别结果、**识别模型**（radio 档位
  子菜单，当前打勾；切换中整组禁用）、关于、退出。
- **模型热切换**：托盘/`myna model` 发 `switch` 命令。加载要 10s 级，放后台
  线程不阻塞快捷键；**旧模型在加载期间保持可用**（`Transcriber.switch` 只在新
  模型加载成功后替换一次 `self.loaded`），失败回滚并明确通知。仅空闲态允许
  切换，录音/识别中拒绝。切换完成/失败都触发状态回调，托盘借此刷新勾选。

## 9. 增补（v1.1）：模型离线加载

原实现每次启动都会向 HuggingFace 发一次 revision 校验请求，与「全程本地」
的承诺不符，断网时还会拖慢启动。改为：先把所有候选按 `local_files_only=True`
试一遍，全都不行才允许联网下载。已缓存的模型不再产生任何网络请求
（实测启动后 HTTP 请求数为 0）。

## 10. 事故记录：GTK 的 locale 副作用打死了转写

加上托盘图标后，转写 100% 失败，报 `UnicodeDecodeError: 'ascii' codec can't
decode byte 0xe8`——错误栈指向音频解码，看上去像是 wav 文件坏了，实际完全无关。

真实因果链：

1. GTK 初始化调用 `setlocale(LC_ALL, "")`；
2. 此后 C 库 `strerror` 返回**本地化**字符串（中文系统上是「没有那个文件或目录」）；
3. PyAV 的错误处理路径拿 `strerror` 的结果时用 **ascii** 解码，遇中文即抛异常；
4. 它本来处理的只是重采样结束时一个**良性的 EOF 信号**——PyAV 内部会吞掉它，
   但异常在构造错误消息时就抛出了，直接逃逸出来打死整条流水线。

修复：`tray.py` 在 GTK 加载后立刻把 `LC_MESSAGES` 与 `LC_NUMERIC` 钉回 `C`
（界面文字走 LC_CTYPE，菜单仍是中文）。`tests/test_tray_locale.py` 断言
`os.strerror()` 必须是纯 ASCII。

教训：**UI 库会改进程级全局状态**。托盘和音频处理同在一个进程，GTK 的
`setlocale` 就足以打死核心功能，而症状出现在八竿子打不着的地方。往
daemon 里塞任何 GUI 组件都要考虑这层污染。

## 11. 增补（v1.2）：粘贴方式手动切换

原设计寄望于 `paste_key_by_app`——按窗口类自动切换粘贴键。**实测行不通**：
GNOME Shell 的 `Introspect.GetWindows` 返回 AccessDenied，xdotool 也拿不到
原生 Wayland 窗口。客户端无权知道别的窗口是谁，这是 Wayland 的安全模型，
不是配置或权限问题。

既然分辨不了，就不假装能自动判断（那只会时灵时不灵，比明确的手动更糟）：

- 托盘菜单加「粘贴方式」子菜单，Ctrl+V / Ctrl+Shift+V 一键切，当前项打勾。
- `myna paste-key [键]` 命令行等价物。
- 切换即写回 `~/.config/myna/config.toml`，重启后仍然有效。写入是**定点文本
  替换**而非重新序列化——用户的注释和排版比我们的整洁值钱。
- 非法键在写入配置前就被 `parse_key` 拒绝；写配置失败时本次仍然生效，
  并如实告知「本次有效，写入配置失败」。

`paste_key_by_app` 保留但已在代码与文档中标注：Wayland 上基本是死配置。

## 12. 事故记录：剪贴板写入被误判为失败

用户报告「在终端里没出字」。第一反应是终端的 Ctrl+V 语义问题，**判断错了**——
日志显示识别完全成功，剪贴板里也确实有内容，但注入报「写剪贴板失败」，
且识别到报错之间恰好隔 6 秒。

根因：**wl-copy 和 xclip 都不会退出**。Wayland 与 X11 的剪贴板都要求源进程
存活来提供内容，这是协议决定的。原实现用 `subprocess.run(timeout=3)` 等它结束，
必然超时（6 秒 = 两次 3 秒），异常被吞掉后判定失败并直接 return——于是
`press()` 压根没被调用，粘贴键从未按下。

修复：起进程、喂完 stdin 就撒手，再读回校验；留句柄回收已退出的持有进程。
写入耗时从「3 秒超时失败」变成 0.09 秒成功。

教训：**「进程退出」不等于「操作完成」**。对这类常驻型外部命令，用退出码判断
成败是错的；要么读回验证，要么根本不该等它。

## 13. 事故记录：默认快捷键撞上 GNOME 内置键

`install` 的默认绑定原本是 `<Super>d`，而 GNOME 自带的「显示桌面」正占着它
（`org.gnome.desktop.wm.keybindings show-desktop`）。写入成功、回读校验通过、
`doctor` 显示「已绑定」——一切看着都对，按下去却是在最小化窗口。

这类「配置全对、功能不工作」最难查，所以修在根上：

- 默认值改为实测空闲的 `<Super>z`，并加测试断言默认值不得是 `<Super>d`。
- `install` 绑定前扫描全部 5 个快捷键 schema 找冲突，占用则拒绝并列出占用者，
  `--force` 可覆盖。
- 自定义快捷键住在带路径的子 schema 里，`list-recursively` 扫不到，需顺着
  `custom-keybindings` 列表逐个查——否则两个自定义键撞在一起完全发现不了。
- 匹配用 `'<Super>d'` 带引号的整值，避免 `<Super>d` 误命中 `<Primary><Super>d`。
- 重绑自己已占的键要保持幂等：把自己那条从冲突里滤掉。

## 14. 事故记录：托盘图标静默消失

用户报告顶栏没有图标，而 `run.sh restart` 一切正常、`status` 全绿。三个 bug
叠在一起，每一个都足以让托盘整个起不来，而症状只有「图标没了」：

1. **`NameError: Gtk is not defined`**。GTK 是可选依赖，只能在 `__init__` 里
   延迟导入，于是每个方法都得记着补一句 `Gtk = self.Gtk`——新加的
   `_build_paste_menu` 漏了。修法不是补那一行，而是把 `Gtk`/`GLib` 提升为
   模块全局，从根上消掉这类错误。
2. **`TypeError: Must be sequence`**。PyGObject 的 `RadioMenuItem.new_with_label`
   要求 group 是 GSList（列表），不能传上一个 item。
3. **启动时自己点自己**。构建和刷新 radio 菜单时 `set_active()` 会触发
   `activate` 信号，被当成用户点击——实测每次启动都白切两次模型
   （small→large-v3），十几秒和一次显存搬运全浪费，粘贴键也被来回改写。
   用 `_suppress` 计数器在构建/刷新期间屏蔽。

放大伤害的是**兜底**：托盘初始化失败被 `except` 吞掉只写了一行日志，用户根本
不会想到去翻 journalctl。已改为同时发桌面通知。

测试上的教训：托盘代码几乎无法用真 GTK 单测（要显示服务器、要主循环），却又
最容易悄悄坏掉。做法是注入**假 gi 模块**把整条构造路径和所有回调走一遍，并且
让假对象照搬真实 GTK 的约束（如 group 必须是列表）——假对象只能抓住它模拟了的
那部分语义，漏掉的约束就是测试的盲区。
