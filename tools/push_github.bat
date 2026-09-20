@echo off
chcp 65001 >nul
REM 一键把本项目推到 GitHub，推完 GitHub 会自动帮你编 APK。
REM 用法： tools\push_github.bat https://github.com/你的用户名/你的仓库.git

set "REPO=%~1"
if "%REPO%"=="" (
  echo 用法： tools\push_github.bat https://github.com/用户名/仓库.git
  exit /b 1
)

cd /d "%~dp0.."

if not exist ".git" (
  git init -q -b main
  echo [OK] 已初始化 git 仓库
)

git config user.name  >nul 2>&1 || git config user.name  watchtube
git config user.email >nul 2>&1 || git config user.email watchtube@local

git add -A
git diff --cached --quiet && (
  echo [..] 没有新改动，跳过 commit
) || (
  git commit -q -m "WatchTube: 手表提问自动投递科普视频"
  echo [OK] 已提交
)

git remote get-url origin >nul 2>&1 && (
  git remote set-url origin "%REPO%"
) || (
  git remote add origin "%REPO%"
)
git branch -M main

echo.
echo == 正在推送到 %REPO%
echo == 提示输入密码时：不要填登录密码，要填 Personal Access Token
echo == 生成地址： github.com/settings/tokens  ^(勾选 repo^)
echo.
git push -u origin main
if errorlevel 1 (
  echo.
  echo [X] 推送失败。最常见两个原因：
  echo     1^) 用了登录密码 —— GitHub 早已停用，必须用 Token
  echo     2^) 仓库不存在 —— 先在 github.com 网页上 New repository 建一个
  exit /b 1
)
echo.
echo [OK] 推送完成！打开仓库的 Actions 页面，等 5-10 分钟下载 APK。
