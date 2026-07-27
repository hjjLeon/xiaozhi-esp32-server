# 部署设计：远程服务器全模块安装 + 双向同步

**日期**：2026-07-27  
**作者**：LeonHe  
**状态**：待实施

---

## 背景与目标

将 xiaozhi-esp32-server 全模块（Python Server + Java/Vue Web 管理台 + MySQL + Redis）部署到远程服务器（SSH 别名 `pve-ubuntu`，Ubuntu 24.04，x86_64，20GB RAM，Docker 29.1.3 + Compose v2.24.0）。

后续会对源码持续修改，需要：
1. 快速将本地改动推送到远程并重新部署
2. 在服务器直接改动时能同步回本地

SSH 连接走按量计费的 frp，需控制流量。

---

## 架构

```
本地开发机                              远程服务器 (pve-ubuntu)
────────────────────                   ──────────────────────────────
 源码（当前工作目录）                    ~/xiaozhi-esp32-server/
  │                                      ├── main/              ← 源码（rsync 同步）
  │──── deploy.sh ──────────────────>    ├── Dockerfile-server
  │<─── sync-down.sh ───────────────     ├── Dockerfile-web
                                         ├── docker-compose_all.yml
                                         ├── data/              ← 配置文件、运行数据（不同步）
                                         ├── models/            ← 语音模型（不同步）
                                         ├── mysql/             ← 数据库数据（不同步）
                                         └── uploadfile/        ← 上传文件（不同步）
```

---

## 初始化流程（首次部署）

为节省 frp 流量，源码通过 GitHub 直接 clone 到远程，不走 rsync。

### 步骤一：在远程服务器手动执行（不消耗 frp 流量）

```bash
cd ~
git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git xiaozhi-esp32-server
cd xiaozhi-esp32-server
mkdir -p data models/SenseVoiceSmall mysql uploadfile
```

然后下载 SenseVoiceSmall 模型文件到 `models/SenseVoiceSmall/model.pt`（从 ModelScope 或百度网盘下载）。

### 步骤二：在本地执行初始化脚本

```bash
./scripts/setup-remote.sh
```

该脚本负责：
- 确认远程目录已存在（若无则提示手动 clone）
- 将 `main/xiaozhi-server/config_from_api.yaml` 复制到远程 `data/.config.yaml`（若远程尚无配置）
- 打印后续配置提示（填写 API Key、数据库密码等）

### 步骤三：在远程智控台完成配置

首次启动后访问 `http://<server-ip>:8002` 注册超级管理员，配置模型 API Key。

---

## 日常部署（本地 → 远程）

```bash
./scripts/deploy.sh          # 构建全部模块
./scripts/deploy.sh --server # 只重新构建 Python server
./scripts/deploy.sh --web    # 只重新构建 Java+Vue web
```

### 执行流程

1. **rsync 源码到远程**（只传有变化的文件）

   ```
   rsync -avz --delete \
     --exclude='.git/' \
     --exclude='__pycache__/' \
     --exclude='*.pyc' \
     --exclude='node_modules/' \
     --exclude='data/' \
     --exclude='models/' \
     --exclude='mysql/' \
     --exclude='uploadfile/' \
     ./ pve-ubuntu:~/xiaozhi-esp32-server/
   ```

2. **SSH 到远程：构建镜像**

   ```bash
   # 全部构建（或按参数选择）
   docker build -t ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:server_latest \
     -f Dockerfile-server .
   docker build -t ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:web_latest \
     -f Dockerfile-web .
   ```

   Docker layer cache 保证：只有实际改动的层才重新构建，速度快。

3. **SSH 到远程：重启服务**

   ```bash
   docker compose -f docker-compose_all.yml up -d
   ```

   只重启镜像有变化的容器，MySQL 和 Redis 不受影响。

---

## 反向同步（远程 → 本地）

在服务器上直接修改代码后，拉回本地：

```bash
./scripts/sync-down.sh
```

排除规则与 deploy.sh 相同，运行时数据（data/、models/ 等）不会污染本地。

**重要**：sync-down.sh **不使用 `--delete`**。反向拉取只补差异，不删除本地已有而远程没有的文件（比如本地新建但尚未 deploy 的文件）。两端都改了同一文件时，sync-down 会覆盖本地版本，操作前应确认本地无未 deploy 的改动。

**关于远程 git 仓库**：首次 clone 仅用于节省 frp 流量，此后不在远程执行任何 git 操作。rsync 覆盖工作树后，远程 `git status` 会显示大量修改——这是预期状态，忽略即可。

---

## 镜像构建策略

不新建 docker-compose 文件。deploy.sh 在远程使用 `docker build` 打上与 `docker-compose_all.yml` 中相同的镜像 tag，`docker compose up -d` 发现本地已有同名镜像时直接使用，不从注册表拉取。

| 组件 | Dockerfile | 镜像 tag |
|------|-----------|---------|
| Python Server | `Dockerfile-server` | `ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:server_latest` |
| Java+Vue Web | `Dockerfile-web` | `ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:web_latest` |
| MySQL | 官方镜像，无需构建 | `mysql:8.4`（通过 override 固定版本） |
| Redis | 官方镜像，无需构建 | `redis:8.0` |

---

## 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `scripts/setup-remote.sh` | 新建 | 首次初始化 |
| `scripts/deploy.sh` | 新建 | 推送源码 + 构建 + 重启 |
| `scripts/sync-down.sh` | 新建 | 从远程拉回改动（无 --delete） |
| `docker-compose.override.yml` | 新建 | 固定 MySQL 版本至 8.4 |
| `.dockerignore` | 修改 | 补充 models/ mysql/ uploadfile/ node_modules/ |
| `docker-compose_all.yml` | 不改动 | 原样复用 |
| `Dockerfile-server` | 不改动 | 原样复用 |
| `Dockerfile-web` | 不改动 | 原样复用 |

---

## 端口说明

| 端口 | 服务 |
|------|------|
| 8000 | WebSocket 服务（ESP32 连接） |
| 8002 | 智控台 Web 界面 |
| 8003 | HTTP 视觉分析接口 |
