# myna 八哥

Linux 桌面语音输入法：在任何应用的文本输入框里，按一个快捷键开始说话，再按一次
结束，识别出的文字直接落在光标处。

名字取自八哥（myna），会学舌的鸟。

全程本地，不联网。

## 它和一段脚本的区别

Whisper 模型加载要 10~13 秒，而转写只要 0.7 秒。所以模型必须常驻内存，
不能每次现加载；而快捷键又必须立刻返回，不能卡住桌面。myna 因此是
**守护进程 + 瘦客户端**：快捷键只往 Unix socket 发一行 JSON。

## 安装

需要 `ffmpeg`、`ydotool`（含已启用的 `ydotoold`）、`wl-clipboard`、`libnotify-bin`，
以及 Python 包 `faster-whisper`（可选 `opencc-python-reimplemented` 用于繁转简）。

```bash
pip install --user -e .
myna doctor      # 逐项自检，缺什么会告诉你怎么装
myna install     # 绑定快捷键（默认 <Super>d）+ 安装并启动 systemd 用户服务
```

`myna install --key '<Super>space'` 可指定别的快捷键。它是幂等的，写入后会回读
校验——现有的 whisper-dictate 方案就是断在这一环：GNOME 里那条快捷键的 name 和
command 都对，binding 却是空字符串，等于没绑。

## 使用

按快捷键 → 说话（通知栏显示 🎙 录音中）→ 再按一次 → 一秒内文字出现在输入框。

```
myna toggle     开始/停止（快捷键绑的就是它）
myna cancel     放弃本次录音，不识别
myna status     查看状态、当前模型与设备
myna doctor     依赖自检
myna daemon     前台运行守护进程（调试用）
myna uninstall  移除快捷键与服务
```

## 配置

`~/.config/myna/config.toml`，全部可选。见 [config.example.toml](config.example.toml)。

最常改的是 `[hotwords]` —— 把反复听错的人名、术语强制改回来。

## 实测

RTX 4060 Laptop 8G，3.5 秒中文音频：

| 配置 | 模型加载 | 转写 |
|---|---|---|
| large-v3 / cuda float16 | 12.7s（冷）/ 4.2s（热） | 0.7s |
| medium / cuda float16 | 9.7s | 0.7s |
| small / cpu int8 | 1.7s | 1.6s，且把「散步」听成「三步」 |

GPU 上 large-v3 和 medium 一样快，所以默认直接用 large-v3；没有 GPU 才回退 small。

## 已知限制

- **只保证 Wayland + GNOME**。X11 有兜底路径（xdotool/xclip）但不承诺。
- **只能 toggle，不能按住说话**。GNOME 快捷键只有按下事件、没有释放事件。
- **说完才出字**，不做流式上屏。
- **daemon 常驻占约 3GB 显存**。跑别的 GPU 任务时可能挤不下，届时 myna 会
  自动降级到 small/CPU 并**明确通知**你（精度会掉，别以为它本来就这样）。
- 终端等应用 Ctrl+V 语义不同，用 `[inject.paste_key_by_app]` 单独配；但
  GNOME 45+ 下窗口类常常取不到。
- 按完快捷键就切换窗口的话，文字会粘到新窗口去。v1 不处理。

任何注入手段都失败时，**文本一定还在剪贴板里**，并会提示你手动粘贴——
不会让你白说一句。

## 文档

- [docs/spec.md](docs/spec.md) —— 设计规格
- [ref/summary/existing-setup.md](ref/summary/existing-setup.md) —— 本机原有方案的
  勘察记录与实测数据，本项目的事实基础

## 许可

MIT
