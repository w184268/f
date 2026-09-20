#!/usr/bin/env bash
# 一键把本项目推到 GitHub，推完 GitHub 会自动帮你编 APK。
# 用法： bash tools/push_github.sh https://github.com/你的用户名/你的仓库.git
set -e

REPO="${1:-}"
if [ -z "$REPO" ]; then
  echo "用法： bash tools/push_github.sh https://github.com/用户名/仓库.git"
  exit 1
fi

cd "$(dirname "$0")/.."

# 1. 初始化（已初始化则跳过）
if [ ! -d .git ]; then
  git init -q -b main
  echo "✓ 已初始化 git 仓库"
fi

# 2. 身份（没有就给个默认，避免 commit 失败）
git config user.name  >/dev/null 2>&1 || git config user.name  "watchtube"
git config user.email >/dev/null 2>&1 || git config user.email "watchtube@local"

# 3. 提交
git add -A
if git diff --cached --quiet; then
  echo "· 没有新改动，跳过 commit"
else
  git commit -q -m "WatchTube: 手表提问自动投递科普视频"
  echo "✓ 已提交"
fi

# 4. 关联远端并推
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO"
else
  git remote add origin "$REPO"
fi
git branch -M main

echo "→ 正在推送到 $REPO"
echo "  提示输入密码时：不要填登录密码，要填 Personal Access Token（github.com/settings/tokens，勾 repo）"
git push -u origin main
echo "✓ 推送完成。现在打开 $REPO 的 Actions 页面，等 5–10 分钟下载 APK。"
