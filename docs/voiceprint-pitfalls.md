# 声纹识别部署踩坑记录

本文档记录把 [voiceprint-api](https://github.com/xinnan-tech/voiceprint-api) 整合进 `docker-compose_all.yml` 全模块部署时遇到的坑。配合 `voiceprint-integration.md`（官方教程）使用——官方教程讲"怎么做"，本文讲"哪些地方会让你卡住"。

> 部署时间：2026-07-28，远程服务器 pve-ubuntu（192.168.1.107）。

## 整合方案概览

把 voiceprint-api 作为 `xiaozhi-esp32-server-voiceprint` 服务加入现有 compose，复用 `xiaozhi-esp32-server-db` MySQL 和 `xiaozhi-server_default` 网络。

```yaml
# main/xiaozhi-server/docker-compose_all.yml 新增片段
xiaozhi-esp32-server-voiceprint:
  image: ghcr.nju.edu.cn/xinnan-tech/voiceprint-api:latest
  container_name: xiaozhi-esp32-server-voiceprint
  restart: always
  depends_on:
    - xiaozhi-esp32-server-db
  networks:
    - default
  ports:
    - "8005:8005"
  security_opt:
    - seccomp:unconfined
  environment:
    - TZ=Asia/Shanghai
  volumes:
    - ./data/voiceprint:/app/data
```

## 踩坑清单

### 1. voiceprint-api 不会自动建库建表

上游代码 `app/database/connection.py` 用 pymysql 直连，**没有任何 DDL**（没有 `Base.metadata.create_all`、没有 `CREATE TABLE`、没有 init script）。启动时如果 `voiceprint_db.voiceprints` 不存在，第一次注册声纹就报错。

**解决**：在 `xiaozhi-esp32-server-db` 容器里手动建库建表：

```sql
CREATE DATABASE IF NOT EXISTS voiceprint_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE voiceprint_db;
CREATE TABLE IF NOT EXISTS voiceprints (
    id INT AUTO_INCREMENT PRIMARY KEY,
    speaker_id VARCHAR(255) NOT NULL UNIQUE,
    feature_vector LONGBLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_speaker_id (speaker_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 2. auth key 必须是 UUID 格式

`.voiceprint.yaml` 里的 `server.authorization` 字段，**只接受 UUID 字符串**。非 UUID（如随机 hex、字母数字）会被静默替换为自动生成的 UUID，**配置不生效但也不报错**。

容器启动时它会打印一行：

```
声纹接口地址: http://<ip>:8005/voiceprint/health?key=<实际生效的key>
```

**用实际打印的 key，不要相信你写在配置文件里的**。

要固定 key：

```bash
python3 -c "import uuid; print(uuid.uuid4())"
```

### 3. Python server 的声纹配置从 API 拉取，不是从 .config.yaml

`main/xiaozhi-server/config/config_loader.py` 里，只要 `manager-api.url` 配了，配置就走 Java API（`get_config_from_api_async`），本地 `data/.config.yaml` 只用于 `server` 段（ip/port/http_port）和 `prompt_template` 回退。

声纹配置更细粒度——在 `ConfigServiceImpl.buildVoiceprintConfig` 里按 **agent 维度**返回（不是全局 getConfig），每次设备连接时由 Python server 调用 `/admin/agent/models` 拉取。

**结论**：在 `data/.config.yaml` 加 `voiceprint` 段在同 compose 部署下是**无效的**。要让声纹生效：
- `sys_params.server.voice_print` 必须有值（API 用这个拼 identify URL）
- 每个 agent 单独管理自己的 speakers（在智控台 → 智能体 → 声纹识别里注册）

### 4. 容器间用服务名，不要用 LAN IP

因为 voiceprint-api 和 xiaozhi server 都在 `xiaozhi-server_default` bridge 网络里，互相通过 Docker DNS 解析。

**推荐**：`http://xiaozhi-esp32-server-voiceprint:8005/...`（容器内直连）
**不推荐**：`http://192.168.1.107:8005/...`（要走 bridge NAT，万一 LAN IP 变了要改两处）

`server.voice_print` 用服务名时，**manager-api 容器和 Python server 容器都能用**，不用为不同消费者维护不同 URL。

### 5. 集成后不用改 MySQL 端口

官方教程的"step 2"建议把 `xiaozhi-esp32-server-db` 的 `expose: 3306` 改成 `ports: 3306:3306`，那是因为官方假设 voiceprint-api 是**独立 compose** 部署的，需要从宿主机访问 MySQL。

**集成到同 compose 后没必要改**——voiceprint 容器直接走 Docker 网络 `xiaozhi-esp32-server-db:3306` 连 MySQL，不走宿主机。改 `ports` 反而会把 MySQL 暴露到 LAN，安全上没好处。

## 部署后验证清单

```bash
# 1. 容器在跑
docker ps --filter name=voiceprint
# 期望：xiaozhi-esp32-server-voiceprint Up

# 2. health 端点通
curl -s 'http://127.0.0.1:8005/voiceprint/health?key=<实际key>'
# 期望：{"total_voiceprints":0,"status":"healthy"}

# 3. Python server 容器能访问 voiceprint
docker exec xiaozhi-esp32-server sh -c \
  'python3 -c "import urllib.request; \
print(urllib.request.urlopen(\"http://xiaozhi-esp32-server-voiceprint:8005/voiceprint/health?key=<实际key>\", timeout=3).read())"'
# 期望：b'{"total_voiceprints":0,"status":"healthy"}'

# 4. identify 鉴权
curl -s -X POST -H "Authorization: Bearer <实际key>" \
  -F "speaker_ids=test1" -F "file=@/etc/hostname" \
  http://127.0.0.1:8005/voiceprint/identify
# 期望：{"detail":"只支持WAV格式音频文件"}（鉴权通过，只是文件格式不对）
# 不期望：{"detail":"密钥验证失败"}（鉴权失败）
```

## 智控台开启步骤

1. **参数字典 → 系统功能配置** → 勾选"声纹识别" → 保存
2. **智能体管理** → 选智能体 → "声纹识别"按钮 → 注册说话人（录入音频 + 描述）
3. **智能体配置** → 记忆设置成"本地短期记忆"，开启"上报文字+语音"

## 相关文档

- [voiceprint-integration.md](voiceprint-integration.md) — 官方部署教程
- [Deployment_all.md](Deployment_all.md) — 全模块部署总览
- 上游：[xinnan-tech/voiceprint-api](https://github.com/xinnan-tech/voiceprint-api)
