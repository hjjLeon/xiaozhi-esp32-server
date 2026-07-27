#!/usr/bin/env bash
# scripts/sync-down.sh
# 将服务器上的代码改动同步回本地开发机。
# 注意：
#   - 不使用 --delete，不删除本地独有文件
#   - 若本地和远程同时改了同一文件，远程版本会覆盖本地版本
#   - 运行前请确认本地无未 deploy 的重要改动
set -euo pipefail

REMOTE="pve-ubuntu"
REMOTE_DIR="~/xiaozhi-esp32-server"

echo "==> 从远程同步代码到本地（不删除本地独有文件）..."
rsync -avz \
  --exclude='.git/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='node_modules/' \
  --exclude='data/' \
  --exclude='models/' \
  --exclude='mysql/' \
  --exclude='uploadfile/' \
  "$REMOTE:$REMOTE_DIR/" ./

echo "==> 同步完成。建议 git diff 查看变更后再提交。"
