@echo off
rem 岗位雷达 govjob-radar 一键启动（Windows）
rem 双击本文件：检查 Python → 装依赖 → 起服务（首次自动播种样例职位表）→ 开浏览器
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0backend"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python，请先安装 Python 3.12+ 并勾选 "Add to PATH"
  echo 下载: https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import pydantic" >nul 2>nul
if errorlevel 1 (
  echo [初始化] 首次运行，安装依赖 pydantic ...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
  )
)

echo.
echo ============================================================
echo   岗位雷达启动中 → http://127.0.0.1:8420  （浏览器将自动打开）
echo   首次使用: 页面顶部「LLM 设置」填 API Key；「我的档案」填学历专业
echo   放入真实职位表: data\inbox\ 目录（xlsx）
echo   停止服务: 关闭本窗口或按 Ctrl+C
echo ============================================================
echo.
python -m app.cli serve --port=8420
pause
