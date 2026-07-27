# 首次部署踩坑记录

> 环境：本地开发机（dexmate-soc）通过 FRP 隧道部署到内网服务器（pve-ubuntu，192.168.1.107）

---

## 1. FRP 隧道频繁断开

**现象**：每次 SSH 执行耗时命令（rsync、docker build）都会在中途断开，导致 `deploy.sh` 以 exit 255 退出。

**原因**：FRP 隧道对长连接有超时限制，SSH 空闲或高负载时会被服务端关闭。

**解决**：
- 耗时命令（git clone、npm install）用 `nohup ... &` 在远端后台运行，避免依赖 SSH 连接存活
- `deploy.sh` 里远端 docker build 通过 `ssh remote bash -s << ENDSSH` 执行，SSH 断开会导致本地脚本失败，但**远端 docker build 进程仍在继续**，可直接轮询结果

---

## 2. git clone 被 SSH 断开中断

**现象**：`git clone` 途中 SSH 断连，远端仓库目录存在但内容不完整（`.git` 目录损坏）。

**解决**：
```bash
# 远端删除不完整的克隆
ssh pve-ubuntu "rm -rf ~/Projects/xiaozhi-esp32-server"

# 用 nohup 后台克隆，避免依赖 SSH 连接
ssh pve-ubuntu "cd ~/Projects && nohup git clone https://github.com/<fork>/xiaozhi-esp32-server.git xiaozhi-esp32-server > /tmp/git-clone.log 2>&1 &"

# 轮询进度
ssh pve-ubuntu "cat /tmp/git-clone.log"
```

**注意**：clone 应使用与本地一致的 fork 仓库地址，而非上游仓库。

---

## 3. rsync mkstemp 错误（目标目录缺失）

**现象**：
```
rsync: [receiver] mkstemp "/home/leon/.../runtime/motion/.file.json.XXXXXX" failed: No such file or directory (2)
```

**原因**：rsync 写文件前先在目标目录创建临时文件，如果目标目录不存在则失败。远端 git clone 后某些子目录（`digital-human/resources/`、`manager-mobile/` 等）不存在（在 `.gitignore` 中或属于 LFS 对象）。

**解决**：在 rsync 命令加 `--temp-dir=/tmp`，让临时文件写到 `/tmp` 而不是目标目录：
```bash
rsync -avz --delete --temp-dir=/tmp ...
```

---

## 4. 远端没有 `docker compose`（插件形式）

**现象**：
```
docker: unknown command: docker compose
```

**原因**：远端服务器安装的是独立二进制 `docker-compose`（`/usr/local/bin/docker-compose`），不是 Docker 插件形式的 `docker compose`。

**解决**：`deploy.sh` 和 `setup-remote.sh` 中统一改用 `docker-compose`：
```bash
docker-compose -f main/xiaozhi-server/docker-compose_all.yml -f docker-compose.override.yml up -d
```

---

## 5. Docker 容器内无法访问外网（npm/apt 失败）

**现象**：
- `npm install` 内所有请求 `ECONNREFUSED`，退出码 0 但 `node_modules` 为空
- `apt-get update` 报 `Unable to connect to 192.168.1.125:1070`

**原因**：
- 服务器有本地 HTTP 代理（`192.168.1.125:1070`），宿主机通过代理上网
- Docker bridge 网络内容器无法访问局域网代理，导致所有 HTTPS 请求失败
- npm 10.x 的 bug：网络全部失败时仍以退出码 0 退出，但不安装任何包

**npm 解决方案**：在宿主机（非容器内）安装依赖，再通过 rsync/COPY 带入镜像：
```bash
# 1. 在远端宿主机（有代理可上网）安装 node_modules
ssh pve-ubuntu "cd ~/Projects/xiaozhi-esp32-server/main/manager-web && npm install --include=dev"

# 2. rsync 时排除 manager-web/node_modules（避免 --delete 删除它）
rsync ... --exclude='main/manager-web/node_modules/' ...

# 3. Dockerfile 去掉 npm install 步骤，直接 COPY
COPY main/manager-web .   # 包含宿主机安装好的 node_modules
RUN npm run build
```

**注意**：`.dockerignore` 中不要加 `main/manager-web/node_modules/`，否则 COPY 时会被排除。

**apt-get 解决方案**：绕过代理 + 使用 Aliyun Ubuntu jammy 镜像：
```dockerfile
FROM docker.m.daocloud.io/library/eclipse-temurin:21-jre-jammy

RUN find /etc/apt -name "*.sources" -exec sed -i \
      's|http://archive.ubuntu.com|https://mirrors.aliyun.com|g; s|http://security.ubuntu.com|https://mirrors.aliyun.com|g' {} \; 2>/dev/null; \
    sed -i 's|http://archive.ubuntu.com|https://mirrors.aliyun.com|g; s|http://security.ubuntu.com|https://mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update && \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false install -y nginx ...
```

关键参数：`-o Acquire::http::Proxy=false -o Acquire::https::Proxy=false`，强制绕过代理直连。

---

## 6. Docker Hub 镜像不可访问

**现象**：
```
Get "https://registry-1.docker.io/v2/": EOF
```

**原因**：Docker Hub 对国内网络访问受限，即使通过代理也不稳定。

**解决**：所有基础镜像改用 DaoCloud 镜像前缀：
```yaml
# docker-compose.override.yml
services:
  xiaozhi-esp32-server-db:
    image: docker.m.daocloud.io/library/mysql:8.4
  xiaozhi-esp32-server-redis:
    image: docker.m.daocloud.io/library/redis:8.0
```

```dockerfile
# Dockerfile-server
FROM ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:server-base

# Dockerfile-web
FROM docker.m.daocloud.io/library/node:18 AS web-builder
FROM docker.m.daocloud.io/library/maven:3.9.4-eclipse-temurin-21 AS api-builder
FROM docker.m.daocloud.io/library/eclipse-temurin:21-jre-jammy
```

---

## 7. Liberica JRE 基础镜像包源不可访问

**现象**：
```
WARNING: updating and opening https://packages.bell-sw.com/alpaquita/...: Connection refused
```

**原因**：官方 `bellsoft/liberica-runtime-container:jre-21-glibc` 使用 Alpaquita Linux，其包源 `packages.bell-sw.com` 在本环境不可达。

**解决**：改用 `eclipse-temurin:21-jre-jammy`（Ubuntu 22.04 LTS，使用标准 Ubuntu 包源，可配置 Aliyun 镜像）。

---

## 8. SenseVoice 语音模型缺失

**现象**：
```
IsADirectoryError: [Errno 21] Is a directory: 'models/SenseVoiceSmall/model.pt'
```

**原因**：`models/SenseVoiceSmall/model.pt` 应为文件，但实际是空目录（`setup-remote.sh` 只创建了目录）。

**解决**：下载 SenseVoiceSmall 模型文件到正确路径：
```bash
# 在服务器上执行（需要访问 ModelScope 或 HuggingFace）
ssh pve-ubuntu "wget -O ~/Projects/xiaozhi-esp32-server/main/xiaozhi-server/models/SenseVoiceSmall/model.pt <ModelScope下载链接>"
```

模型下载后重启 server 容器：
```bash
ssh pve-ubuntu "docker restart xiaozhi-esp32-server"
```

---

## 9. server.secret 配置

**现象**：
```
Exception: 请先配置manager-api的secret
```

**流程**：
1. 先启动所有容器（`./scripts/deploy.sh`）
2. 访问 `http://<server-ip>:8002` 注册第一个账号（自动成为超管）
3. 进入【参数管理】→ 找到 `server.secret` → 复制值
4. 编辑远端 `.config.yaml`，注意 Docker 内 url 用容器名：
   ```yaml
   manager-api:
     url: http://xiaozhi-esp32-server-web:8002/xiaozhi
     secret: <从web界面复制的server.secret>
   ```
5. `docker restart xiaozhi-esp32-server`

---

## 快速部署检查清单

- [ ] 远端已 git clone fork 仓库
- [ ] 运行时目录已创建（`data/`、`models/SenseVoiceSmall/`、`mysql/`、`uploadfile/`）
- [ ] SenseVoice 模型文件已下载（`models/SenseVoiceSmall/model.pt` 是文件而非目录）
- [ ] `main/manager-web/node_modules/` 已在远端宿主机安装
- [ ] `docker-compose.override.yml` 使用 DaoCloud 镜像
- [ ] `.config.yaml` 已配置 `manager-api.secret`
- [ ] 日常更新：本地改代码 → `./scripts/deploy.sh`
