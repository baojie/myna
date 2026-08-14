#!/usr/bin/env bash
# 把主盘（/）上的本地模型文件迁到 /data，原位置留符号链接，对应用透明。
# 起因：主盘 563G 已用 98%，只剩 14G；/data 尚有 129G。
# 幂等：已是符号链接的条目直接跳过。
set -uo pipefail

DEST_ROOT=/data/models
mkdir -p "$DEST_ROOT"

# 格式： 源路径|目标子目录名
ENTRIES=(
  "$HOME/.cache/whisper|openai-whisper"
  "$HOME/.local/share/piper-voices|piper-voices"
  "$HOME/.var/app/net.mkiol.SpeechNote/cache/net.mkiol/dsnote/speech-models|speechnote"
  "$HOME/.vscode/extensions/ms-vscode.vscode-speech-0.16.0-linux-x64/assets|vscode-speech-en"
  "$HOME/.vscode/extensions/ms-vscode.vscode-speech-language-pack-zh-cn-0.5.1/assets|vscode-speech-zh"
  "$HOME/.insightface/models|insightface"
)

for entry in "${ENTRIES[@]}"; do
  src="${entry%%|*}"
  dst="$DEST_ROOT/${entry##*|}"

  if [ -L "$src" ]; then
    echo "跳过（已是符号链接）: $src -> $(readlink "$src")"
    continue
  fi
  if [ ! -d "$src" ]; then
    echo "跳过（不存在）: $src"
    continue
  fi
  if [ -e "$dst" ]; then
    echo "跳过（目标已存在，需人工确认）: $dst"
    continue
  fi

  echo "迁移: $src  ->  $dst"
  # 先复制，校验通过后才删除源，避免中途失败丢数据
  if ! rsync -a --info=progress2 "$src/" "$dst/"; then
    echo "  ！rsync 失败，保留源目录，跳过" >&2
    continue
  fi
  src_n=$(find "$src" -type f | wc -l)
  dst_n=$(find "$dst" -type f | wc -l)
  src_b=$(du -sb "$src" | cut -f1)
  dst_b=$(du -sb "$dst" | cut -f1)
  if [ "$src_n" != "$dst_n" ] || [ "$src_b" != "$dst_b" ]; then
    echo "  ！校验不一致 (文件数 $src_n/$dst_n, 字节 $src_b/$dst_b)，保留源目录" >&2
    continue
  fi
  rm -rf "$src"
  ln -s "$dst" "$src"
  echo "  ✓ 完成，已建符号链接（$src_n 个文件，$(numfmt --to=iec "$src_b")）"
done

# SpeechNote 是 flatpak，沙箱默认看不到 /data，须显式授权，
# 否则符号链接在沙箱内是断链。
if command -v flatpak >/dev/null && flatpak info net.mkiol.SpeechNote >/dev/null 2>&1; then
  flatpak override --user --filesystem="$DEST_ROOT" net.mkiol.SpeechNote
  echo "已授权 SpeechNote 访问 $DEST_ROOT"
fi

echo
echo "=== 结果 ==="
df -h / /data
