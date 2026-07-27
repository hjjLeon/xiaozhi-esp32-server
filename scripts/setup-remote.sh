#!/usr/bin/env bash
# scripts/setup-remote.sh
# 首次部署初始化：在远程服务器已手动 git clone 的前提下运行。
set -euo pipefail

# 确保从项目根目录运行，否则相对路径 LOCAL_CONFIG 无法找到
[[ -f Dockerfile-server ]] || { echo "错误：请在项目根目录运行此脚本"; exit 1; }

REMOTE="pve-ubuntu"
REMOTE_DIR="~/Projects/xiaozhi-esp32-server"
# 运行时数据目录与 docker-compose_all.yml 同级，volume 路径才能正确解析
RUNTIME_BASE="$REMOTE_DIR/main/xiaozhi-server"
LOCAL_CONFIG="main/xiaozhi-server/config_from_api.yaml"

echo "==> 检查远程目录..."
if ! ssh "$REMOTE" "test -d $REMOTE_DIR"; then
  echo "错误：远程 $REMOTE_DIR 不存在。"
  echo "请先在服务器上执行："
  echo "  git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git $REMOTE_DIR"
  exit 1
fi

echo "==> 创建运行时目录（与 docker-compose_all.yml 同级）..."
ssh "$REMOTE" "mkdir -p $RUNTIME_BASE/data $RUNTIME_BASE/models/SenseVoiceSmall $RUNTIME_BASE/mysql $RUNTIME_BASE/uploadfile"

echo "==> 传送配置模板（若远程尚无 .config.yaml）..."
if ! ssh "$REMOTE" "test -f $RUNTIME_BASE/data/.config.yaml"; then
  scp "$LOCAL_CONFIG" "$REMOTE:$RUNTIME_BASE/data/.config.yaml"
  echo "    已传送 .config.yaml，请编辑填写 manager-api.secret 等参数。"
else
  echo "    远程已有 .config.yaml，跳过。"
fi

echo ""
echo "==> 初始化完成。后续步骤："
echo "    1. 下载语音模型（若尚未下载）："
echo "       服务器上执行：wget -O $RUNTIME_BASE/models/SenseVoiceSmall/model.pt <ModelScope链接>"
echo "    2. 运行 ./scripts/deploy.sh 完成首次构建和启动"
echo "    3. 访问 http://<server-ip>:8002 注册超级管理员"
