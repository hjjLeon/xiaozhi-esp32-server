# 开发者部署指南（Fork 仓库 + 自定义构建）

适用场景：你 fork 了本项目并做了自定义修改，需要将代码持续部署到远端服务器。

与普通用户部署（直接拉取官方镜像）的区别：
- 本地改代码 → rsync 同步 → 远端构建镜像 → 重启容器
- 使用 `scripts/deploy.sh` 脚本自动化全流程

---

## 环境要求

| 项目 | 要求 |
|------|------|
| 本地 | 已安装 rsync、SSH |
| 远端服务器 | Ubuntu，Docker，docker-compose，Node.js（≥18） |
| 网络 | 本地可 SSH 到远端服务器 |

---

## 首次部署

### 第一步：在远端 clone 仓库

```bash
ssh <your-server> "mkdir -p ~/Projects && cd ~/Projects && \
  nohup git clone https://github.com/<your-fork>/xiaozhi-esp32-server.git xiaozhi-esp32-server \
  > /tmp/git-clone.log 2>&1 &"

# 等待完成（约 1-3 分钟）
ssh <your-server> "tail -f /tmp/git-clone.log"
```

> 使用 `nohup` 后台运行，避免 SSH 断开中断 clone。

### 第二步：创建运行时目录

```bash
ssh <your-server> "
  BASE=~/Projects/xiaozhi-esp32-server/main/xiaozhi-server
  mkdir -p \$BASE/data \$BASE/models/SenseVoiceSmall \$BASE/mysql \$BASE/uploadfile
"
```

### 第三步：下载语音识别模型

SenseVoiceSmall 模型（约 1.6GB）需要手动下载，放到 `models/SenseVoiceSmall/model.pt`。

**方式一：阿里魔搭（国内推荐）**
```bash
ssh <your-server> "
  wget -O ~/Projects/xiaozhi-esp32-server/main/xiaozhi-server/models/SenseVoiceSmall/model.pt \
    https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt
"
```

**方式二：百度网盘**

下载链接：https://pan.baidu.com/share/init?surl=QlgM58FHhYv1tFnUT_A8Sg 提取码：`qvna`

下载后上传到服务器：
```bash
scp /path/to/model.pt <your-server>:~/Projects/xiaozhi-esp32-server/main/xiaozhi-server/models/SenseVoiceSmall/model.pt
```

> **注意**：`models/SenseVoiceSmall/` 必须是文件 `model.pt`，不能是目录。

### 第四步：在远端安装前端依赖

由于 Docker 容器内的网络限制，`npm install` 需要在远端宿主机上执行：

```bash
ssh <your-server> "cd ~/Projects/xiaozhi-esp32-server/main/manager-web && npm install --include=dev"
```

> 原因：Docker 容器内可能无法访问 npm 镜像源（取决于网络环境）。宿主机直接执行可走系统代理。

### 第五步：传送初始配置文件

```bash
scp main/xiaozhi-server/config_from_api.yaml \
  <your-server>:~/Projects/xiaozhi-esp32-server/main/xiaozhi-server/data/.config.yaml
```

### 第六步：配置 SSH 别名

编辑本地 `~/.ssh/config`，确保远端服务器别名已配置：

```
Host your-server
    HostName <ip-or-domain>
    User <username>
    Port <port>
```

`scripts/deploy.sh` 默认使用 `pve-ubuntu` 别名，可在脚本顶部修改 `REMOTE` 变量：

```bash
# scripts/deploy.sh
REMOTE="your-server"          # 改成你的 SSH 别名
REMOTE_DIR="~/Projects/xiaozhi-esp32-server"
```

### 第七步：执行首次部署

```bash
./scripts/deploy.sh
```

脚本会自动完成：
1. rsync 同步本地代码到远端（增量，跳过模型/数据库等大文件）
2. 远端构建 server 镜像（基于 `Dockerfile-server`）
3. 远端构建 web 镜像（基于 `Dockerfile-web`，含 Vue 前端 + Java 后端）
4. `docker-compose up -d` 启动全部容器

首次构建耗时约 20-40 分钟（主要是 Maven 下载依赖）。

### 第八步：配置 manager-api.secret

1. 访问 `http://<server-ip>:8002`，注册超级管理员账号（第一个注册的账号自动成为超管）
2. 进入【参数管理】，找到 `server.secret`，复制其值
3. 编辑远端配置：
   ```bash
   ssh <your-server> "cat > ~/Projects/xiaozhi-esp32-server/main/xiaozhi-server/data/.config.yaml << 'EOF'
   server:
     ip: 0.0.0.0
     port: 8000

   manager-api:
     url: http://xiaozhi-esp32-server-web:8002/xiaozhi
     secret: <粘贴你的 server.secret>

   prompt_template: agent-base-prompt.txt
   EOF"
   ```
4. 重启 server 容器：
   ```bash
   ssh <your-server> "docker restart xiaozhi-esp32-server"
   ```

---

## 日常更新部署

本地修改代码后，一条命令完成部署：

```bash
# 全量构建（server + web）
./scripts/deploy.sh

# 只更新 Python server（速度快，有 Docker 层缓存）
./scripts/deploy.sh --server

# 只更新 Web（Vue 前端 + Java 后端）
./scripts/deploy.sh --web
```

> **提示**：若只改了 `main/xiaozhi-server/` 下的 Python 代码，用 `--server` 约 10 秒即可完成（命中缓存）。

---

## 从远端拉取变更

如果直接在远端修改了文件（如配置文件），同步回本地：

```bash
./scripts/sync-down.sh
```

---

## 网络受限环境的额外注意事项

若服务器 Docker 容器内无法访问外网（常见于有内网代理的环境）：

**npm 依赖**：始终在宿主机安装，不在容器内安装（见第四步）。

**Docker 基础镜像**：`docker-compose.override.yml` 中使用国内镜像源：
```yaml
services:
  xiaozhi-esp32-server-db:
    image: docker.m.daocloud.io/library/mysql:8.4
  xiaozhi-esp32-server-redis:
    image: docker.m.daocloud.io/library/redis:8.0
```

**apt-get**：`Dockerfile-web` 中使用 Aliyun Ubuntu 镜像并绕过代理（已在 Dockerfile 中配置）。

**maven**：`maven.aliyun.com` 在大多数国内网络环境可直连，无需特殊处理（已在 `pom.xml` 中配置）。

---

## 常见问题

**Q：server 容器一直 Restarting**

检查日志：
```bash
ssh <your-server> "docker logs xiaozhi-esp32-server --tail 30"
```
- `请先配置manager-api的secret` → 按第八步配置
- `Is a directory: 'models/SenseVoiceSmall/model.pt'` → 按第三步下载模型文件

**Q：web 容器启动后访问 8002 无响应**

```bash
ssh <your-server> "docker logs xiaozhi-esp32-server-web --tail 30"
```
检查 nginx 配置是否正确，Java 进程是否启动。

**Q：deploy.sh 中途报 exit 255（SSH 断开）**

远端构建可能仍在运行，等待后检查镜像是否构建完成：
```bash
ssh <your-server> "docker images | grep xiaozhi"
```
若镜像已存在，可直接启动容器：
```bash
ssh <your-server> "cd ~/Projects/xiaozhi-esp32-server && docker-compose -f main/xiaozhi-server/docker-compose_all.yml -f docker-compose.override.yml up -d"
```

---

## 目录结构说明

```
xiaozhi-esp32-server/
├── scripts/
│   ├── deploy.sh        # 主部署脚本（rsync + build + start）
│   ├── setup-remote.sh  # 首次初始化脚本（已被本文档取代）
│   └── sync-down.sh     # 从远端同步变更到本地
├── Dockerfile-server    # Python server 镜像
├── Dockerfile-web       # Vue + Java web 镜像
├── docker-compose.override.yml  # 本地覆盖配置（镜像源等）
└── main/
    └── xiaozhi-server/
        └── data/
            └── .config.yaml  # 运行时配置（含 secret，勿提交 git）
```
