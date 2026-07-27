# 远程服务器全模块部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `pve-ubuntu` 远程服务器上部署 xiaozhi-esp32-server 全模块，并提供三个脚本支持日常开发迭代和双向同步。

**Architecture:** rsync 增量同步源码到远程 → 远程 `docker build` 构建镜像（复用 layer cache）→ `docker compose up -d` 滚动重启。双向同步通过两个显式命令实现，不做自动合并。

**Tech Stack:** bash, rsync, docker, docker compose v2, ssh (pve-ubuntu)

**参考文档：** `docs/superpowers/specs/2026-07-27-deployment-design.md`

---

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `docker-compose.override.yml` | 新建 | 固定 MySQL 版本至 8.4 |
| `scripts/setup-remote.sh` | 新建 | 首次初始化远程环境 |
| `scripts/deploy.sh` | 新建 | 推送源码 + 远程构建 + 重启 |
| `scripts/sync-down.sh` | 新建 | 从远程拉回改动（无 --delete） |
| `.dockerignore` | 已修改 | 已补全，无需再动 |

---

## Task 1：创建 docker-compose.override.yml

**Files:**
- Create: `docker-compose.override.yml`

Docker Compose 自动合并同目录下的 override 文件，但我们部署时显式指定 `-f`，所以需要同时指定 override 文件。该文件只覆盖 MySQL 镜像版本，其余继承主 compose 配置。

**路径说明：** `docker-compose_all.yml` 位于 `main/xiaozhi-server/`，volume 路径（`./data`、`./models` 等）按 Compose 规范相对于该文件目录解析，即 `~/xiaozhi-esp32-server/main/xiaozhi-server/data/` 等。`docker-compose.override.yml` 放在项目根目录，与 Dockerfile 同级。

- [ ] **Step 1: 创建文件**

```yaml
# docker-compose.override.yml
# 覆盖 docker-compose_all.yml 中未固定的镜像版本
services:
  xiaozhi-esp32-server-db:
    image: mysql:8.4
```

- [ ] **Step 2: 验证语法（本地）**

```bash
docker compose -f main/xiaozhi-server/docker-compose_all.yml -f docker-compose.override.yml config | grep "image: mysql"
```

预期输出：`image: mysql:8.4`（不是 `mysql:latest`）。

- [ ] **Step 3: Commit**

```bash
git add docker-compose.override.yml
git commit -m "chore: pin mysql to 8.4 via compose override"
```

---

## Task 2：创建 scripts/setup-remote.sh

**Files:**
- Create: `scripts/setup-remote.sh`

首次部署时运行一次。检查远程目录是否已 clone，创建运行时目录，传送配置模板。

- [ ] **Step 1: 创建脚本**

```bash
#!/usr/bin/env bash
# scripts/setup-remote.sh
# 首次部署初始化：在远程服务器已手动 git clone 的前提下运行。
set -euo pipefail

REMOTE="pve-ubuntu"
REMOTE_DIR="~/xiaozhi-esp32-server"
# 运行时数据目录与 docker-compose_all.yml 同级，volume 路径才能正确解析
RUNTIME_BASE="$REMOTE_DIR/main/xiaozhi-server"
LOCAL_CONFIG="main/xiaozhi-server/config_from_api.yaml"

echo "==> 检查远程目录..."
if ! ssh "$REMOTE" "test -d $REMOTE_DIR"; then
  echo "错误：远程 $REMOTE_DIR 不存在。"
  echo "请先在服务器上执行："
  echo "  git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git ~/xiaozhi-esp32-server"
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
echo "       服务器上执行：wget -O ~/xiaozhi-esp32-server/main/xiaozhi-server/models/SenseVoiceSmall/model.pt <ModelScope链接>"
echo "    2. 运行 ./scripts/deploy.sh 完成首次构建和启动"
echo "    3. 访问 http://<server-ip>:8002 注册超级管理员"
```

- [ ] **Step 2: 赋予执行权限**

```bash
chmod +x scripts/setup-remote.sh
```

- [ ] **Step 3: 验证脚本（在远程已 clone 的前提下运行）**

```bash
./scripts/setup-remote.sh
```

预期：输出各步骤 `==>` 提示，无报错退出。若远程目录不存在则打印错误并退出码非零。

- [ ] **Step 4: Commit**

```bash
git add scripts/setup-remote.sh
git commit -m "feat: add setup-remote.sh for first-time remote initialization"
```

---

## Task 3：创建 scripts/deploy.sh

**Files:**
- Create: `scripts/deploy.sh`

日常部署脚本。支持 `--server`、`--web` 参数选择性构建，默认构建全部。

- [ ] **Step 1: 创建脚本**

```bash
#!/usr/bin/env bash
# scripts/deploy.sh
# 用法：
#   ./scripts/deploy.sh          # 构建 server + web
#   ./scripts/deploy.sh --server # 只构建 Python server
#   ./scripts/deploy.sh --web    # 只构建 Java+Vue web
set -euo pipefail

REMOTE="pve-ubuntu"
REMOTE_DIR="~/xiaozhi-esp32-server"
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
rsync -avz --delete \
  --exclude='.git/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='node_modules/' \
  --exclude='data/' \
  --exclude='models/' \
  --exclude='mysql/' \
  --exclude='uploadfile/' \
  ./ "$REMOTE:$REMOTE_DIR/"

echo "==> 远程构建镜像..."
ssh "$REMOTE" bash -s << ENDSSH
  set -e
  cd $REMOTE_DIR

  if $BUILD_SERVER; then
    echo "-- 构建 server 镜像..."
    docker build -t $SERVER_IMAGE -f Dockerfile-server .
  fi

  if $BUILD_WEB; then
    echo "-- 构建 web 镜像..."
    docker build -t $WEB_IMAGE -f Dockerfile-web .
  fi

  echo "-- 启动/更新容器..."
  docker compose -f main/xiaozhi-server/docker-compose_all.yml -f docker-compose.override.yml up -d

  echo "-- 当前容器状态："
  docker compose -f main/xiaozhi-server/docker-compose_all.yml ps
ENDSSH

echo ""
echo "==> 部署完成。服务地址："
echo "    智控台：http://<server-ip>:8002"
echo "    WebSocket：ws://<server-ip>:8000"
```

- [ ] **Step 2: 赋予执行权限**

```bash
chmod +x scripts/deploy.sh
```

- [ ] **Step 3: 验证参数解析（dry-run，不实际连接远程）**

```bash
bash -n scripts/deploy.sh
```

预期：无语法错误输出，退出码 0。

- [ ] **Step 4: 实际运行首次部署（需远程已完成 setup-remote.sh）**

```bash
./scripts/deploy.sh
```

预期：
- rsync 输出文件列表（首次因与 clone 相同，传输量极小）
- 远程构建输出 `Successfully built ...`
- `docker compose ps` 显示所有容器 `running`

- [ ] **Step 5: 验证 --server 参数**

```bash
# 修改一个 Python 文件后运行
./scripts/deploy.sh --server
```

预期：跳过 web 构建（无 `构建 web 镜像` 输出），只重启 server 容器。

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy.sh
git commit -m "feat: add deploy.sh for rsync + remote build + restart"
```

---

## Task 4：创建 scripts/sync-down.sh

**Files:**
- Create: `scripts/sync-down.sh`

从远程拉回改动。不使用 `--delete`，只补差异，不删本地文件。

- [ ] **Step 1: 创建脚本**

```bash
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
```

- [ ] **Step 2: 赋予执行权限**

```bash
chmod +x scripts/sync-down.sh
```

- [ ] **Step 3: 验证语法**

```bash
bash -n scripts/sync-down.sh
```

预期：无输出，退出码 0。

- [ ] **Step 4: 验证实际行为**

在远程随意修改一个源码文件（如在注释中加一行），然后：

```bash
./scripts/sync-down.sh
git diff
```

预期：`git diff` 显示该改动已同步到本地，本地其他文件不受影响。

- [ ] **Step 5: Commit**

```bash
git add scripts/sync-down.sh
git commit -m "feat: add sync-down.sh for pulling remote code changes"
```

---

## Task 5：端到端验证

验证完整部署流程和双向同步均正常。

- [ ] **Step 1: 验证服务可访问**

```bash
# 检查端口连通性
ssh pve-ubuntu "curl -s -o /dev/null -w '%{http_code}' http://localhost:8002/xiaozhi/doc.html"
```

预期：返回 `200`。

- [ ] **Step 2: 验证 deploy.sh 增量更新速度**

在本地任意改动一行 Python 注释，记录时间：

```bash
time ./scripts/deploy.sh --server
```

预期：rsync 只传改动文件（几KB），server 镜像只重建最后一层（< 30 秒）。

- [ ] **Step 3: 验证 sync-down.sh 不删本地文件**

本地新建一个临时文件，然后运行：

```bash
touch /tmp/test-local-file.txt
cp /tmp/test-local-file.txt scripts/_local_only_test.txt
./scripts/sync-down.sh
ls scripts/_local_only_test.txt
```

预期：文件仍存在，未被删除。清理：

```bash
rm scripts/_local_only_test.txt
```

- [ ] **Step 4: 验证 MySQL 版本固定**

```bash
ssh pve-ubuntu "docker exec xiaozhi-esp32-server-db mysql --version"
```

预期：输出包含 `8.4`。

- [ ] **Step 5: 最终 Commit**

```bash
git add docs/superpowers/plans/2026-07-27-remote-deployment.md
git commit -m "docs: add remote deployment implementation plan"
```

---

## 已知注意事项

- **首次 server 镜像构建**：`Dockerfile-server` 的 FROM 使用 `ghcr.io`（非 NJU 镜像），在国内网络首次拉取基础镜像可能较慢，后续有 layer cache 则极快。
- **web 镜像首次构建**：Maven 下载依赖约需 5-15 分钟，有 layer cache 后约 1-3 分钟。
- **远程 git 状态**：rsync 同步后远程 `git status` 会显示大量修改，属预期状态，不影响部署，忽略即可。
