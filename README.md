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

```bash
./run.sh doctor    # 逐项自检，缺什么会告诉你怎么装
./run.sh install   # 绑定快捷键 + 安装并启动 systemd 用户服务，之后开机自启
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

按快捷键 → 说话（通知栏显示 🎙 录音中）→ 再按一次 → 一秒内文字出现在输入框。

```
myna toggle     开始/停止（快捷键绑的就是它）
myna cancel     放弃本次录音，不识别
myna model      切换识别模型（如 `myna model medium`，也可托盘里点）
myna paste-key  设置粘贴键（终端用 ctrl+shift+v，见下）
myna status     查看状态、当前模型、设备、粘贴键
myna doctor     依赖自检
myna daemon     前台运行守护进程（调试用）
myna uninstall  移除快捷键与服务
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

### 粘贴方式要手动切

**终端的粘贴键是 Ctrl+Shift+V，普通输入框是 Ctrl+V，而 Wayland 下分辨不了
当前是哪种。** 不是没做，是做不到：GNOME Shell 的 Introspect 接口返回
AccessDenied，xdotool 也看不见原生 Wayland 窗口——客户端无权知道别的窗口是谁，
这是 Wayland 的安全模型。

所以托盘菜单里给了「粘贴方式」子菜单，进终端前切一下即可，选择会写回配置文件。
也可以用命令：

```bash
myna paste-key ctrl+shift+v   # 终端
myna paste-key ctrl+v         # 普通输入框
myna paste-key                # 看当前是哪个
```

同理，配置里的 `[inject.paste_key_by_app]`（按窗口类自动切）在 Wayland 上
基本是死配置，只对 XWayland 应用偶尔生效。

需要 `python3-gi` 和 `gir1.2-ayatanaappindicator3-0.1`（Ubuntu 自带），
以及 GNOME 的 AppIndicator 扩展（Ubuntu 默认启用）。缺了也不影响主功能，
只是没图标；也可以在配置里 `[tray] enabled = false` 主动关掉。

托盘初始化失败时会**发桌面通知**告诉你原因，不会让你只看到「图标没了」却
无从查起——这条兜底本身曾经就是最难查的那个坑。

## 配置

`~/.config/myna/config.toml`，全部可选。见 [config.example.toml](config.example.toml)。

### 换识别模型

`[asr] model` 填档位名即可，自动匹配设备与精度：

| 档位 | 完整模型 | 定位 |
|---|---|---|
| `turbo` | faster-whisper-large-v3-turbo | 最快，精度略降 |
| `large-v3` | faster-whisper-large-v3 | 默认，最准（GPU 上与 medium 同速） |
| `large-v2` | faster-whisper-large-v2 | 次准 |
| `medium` | faster-whisper-medium | 均衡 |
| `small` | faster-whisper-small | 快，中文偶有误识 |
| `base` / `tiny` | faster-whisper-base / tiny | 最轻 |

也可以直接写任意 HuggingFace 模型名（如 `"Systran/faster-whisper-medium"`）。
GPU 不可用时会自动降级到 `[asr] fallback_model`（默认 `small`），并明确通知你。
`myna status` 会显示当前解析后的完整模型名、设备与计算类型。

最常改的是 `[hotwords]` —— 把反复听错的人名、术语强制改回来。

## 实测

RTX 4060 Laptop 8G，3.5 秒中文音频：

| 配置 | 模型加载 | 转写 |
|---|---|---|
| large-v3 / cuda float16 | 12.7s（冷）/ 2.4s（热） | 0.7s |
| medium / cuda float16 | 9.7s | 0.7s |
| small / cpu int8 | 1.7s | 1.6s，且把「散步」听成「三步」 |

GPU 上 large-v3 和 medium 一样快，所以默认直接用 large-v3；没有 GPU 才回退 small。

配置里的 `initial_prompt`（默认「以下是简体中文的句子。」）**不是可选优化**：
实测它一举解决了 Whisper 中文输出繁体、以及小模型的误识两个问题。

## 已知限制

- **只保证 Wayland + GNOME**。X11 有兜底路径（xdotool/xclip）但不承诺。
- **只能 toggle，不能按住说话**。GNOME 快捷键只有按下事件、没有释放事件。
- **说完才出字**，不做流式上屏。
- **daemon 常驻占约 4GB 显存**（large-v3）。跑别的 GPU 任务时可能挤不下，
  届时 myna 会自动降级到 small/CPU 并**明确通知**你。识别突然变差时先看
  `myna status` 的「已降级」字段——`small` 会把「散步」听成「三步」。
  换个小档位可以让出显存：`myna model medium`。
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
./run.sh test       # 85 项测试
./run.sh restart    # 改完代码重启服务
./run.sh log        # 跟日志
```

模型权重一律走 HuggingFace 缓存（本机 `~/.cache/huggingface` 是指向 `/data` 的
符号链接），不在仓库或主盘另存——主盘已用 98%。

## 许可

MIT
