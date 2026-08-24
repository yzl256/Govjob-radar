#!/usr/bin/env bash
# 岗位雷达 govjob-radar 一键启动（macOS / Linux）
# chmod +x start.sh && ./start.sh
set -e
cd "$(dirname "$0")/backend"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3，请先安装 Python 3.12+"
  exit 1
fi

if ! python3 -c "import pydantic" >/dev/null 2>&1; then
  echo "[初始化] 首次运行，安装依赖 pydantic ..."
  python3 -m pip install -r requirements.txt
fi

echo "============================================================"
echo "  岗位雷达启动中 → http://127.0.0.1:8420"
echo "  首次使用: 页面顶部「LLM 设置」填 API Key；「我的档案」填学历专业"
echo "  放入真实职位表: data/inbox/ 目录（xlsx）"
echo "============================================================"
python3 -m app.cli serve --port=8420
