#!/usr/bin/env bash
# scripts/deploy.sh
# 用法：
#   ./scripts/deploy.sh          # 构建 server + web
#   ./scripts/deploy.sh --server # 只构建 Python server
#   ./scripts/deploy.sh --web    # 只构建 Java+Vue web
set -euo pipefail

# 确保从项目根目录运行，否则 rsync ./ --delete 会同步错误目录
[[ -f Dockerfile-server ]] || { echo "错误：请在项目根目录运行此脚本"; exit 1; }

REMOTE="pve-ubuntu"
REMOTE_DIR="~/Projects/xiaozhi-esp32-server"
SERVER_IMAGE="ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:server_latest"
WEB_IMAGE="ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:web_latest"

BUILD_SERVER=true
BUILD_WEB=true

for arg in "$@"; do
  case "$arg" in
    --server) BUILD_SERVER=true;  BUILD_WEB=false ;;
    --web)    BUILD_SERVER=false; BUILD_WEB=true  ;;
    *) echo "未知参数：$arg"; echo "用法：$0 [--server|--web]"; exit 1 ;;
  esac
done

echo "==> rsync 源码到远程（只传变化文件）..."
rsync -avz --delete --temp-dir=/tmp \
  --exclude='.git/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='main/manager-web/node_modules/' \
  --exclude='main/manager-api/node_modules/' \
  --exclude='main/manager-mobile/node_modules/' \
  --exclude='main/digital-human/node_modules/' \
  --exclude='main/xiaozhi-server/node_modules/' \
  --exclude='data/' \
  --exclude='models/' \
  --exclude='mysql/' \
  --exclude='uploadfile/' \
  ./ "$REMOTE:$REMOTE_DIR/"

echo "==> 远程构建镜像..."
ssh "$REMOTE" bash -s << ENDSSH
  set -euo pipefail
  cd $REMOTE_DIR

  if [[ "$BUILD_SERVER" == "true" ]]; then
    echo "-- 构建 server 镜像..."
    docker build -t $SERVER_IMAGE -f Dockerfile-server .
  fi

  if [[ "$BUILD_WEB" == "true" ]]; then
    echo "-- 构建 web 镜像..."
    docker build --network=host -t $WEB_IMAGE -f Dockerfile-web .
  fi

  echo "-- 启动/更新容器..."
  docker-compose -f main/xiaozhi-server/docker-compose_all.yml -f docker-compose.override.yml up -d

  echo "-- 当前容器状态："
  docker-compose -f main/xiaozhi-server/docker-compose_all.yml -f docker-compose.override.yml ps
ENDSSH

echo ""
echo "==> 部署完成。服务地址："
echo "    智控台：http://<server-ip>:8002"
echo "    WebSocket：ws://<server-ip>:8000"
