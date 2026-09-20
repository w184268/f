@echo off
REM 启动 WatchTube 服务端（Windows）
cd /d "%~dp0"

if not exist config.yaml (
    copy config.example.yaml config.yaml >nul
    echo 已生成 config.yaml，请先修改后重新运行。
    pause
    exit /b 1
)

python --version >nul 2>&1
if errorlevel 1 (
    echo 未检测到 Python，请先安装 https://www.python.org/downloads/
    pause
    exit /b 1
)

python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖...
    python -m pip install -r requirements.txt
)

python main.py %*
pause
