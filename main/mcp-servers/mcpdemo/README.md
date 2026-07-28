# mcpdemo — 圆形 & 正方形面积计算 MCP 服务

最小可用的 MCP server 示例,提供两个工具:

| 工具 | 公式 | 参数 |
|------|------|------|
| `calculate_circle_area` | π × r² | `radius: float` (>0) |
| `calculate_square_area` | side × side | `side: float` (>0) |

## 文件

- `mcpdemo.py` — FastMCP server 实现,定义两个工具
- `mcp_pipe.py` — stdio ↔ WebSocket 桥接器(从 `78/mcp-calculator` 复制)
- `requirements.txt` — Python 依赖

## 安装

```bash
cd main/mcp-servers/mcpdemo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
# 1. 设置 mcp-endpoint 地址
#从 mcp-endpoint-server 容器日志拿到 ws 地址
docker logs mcp-endpoint-server | grep "ws://"
# 完整安装方案要从智控台web界面获取ws地址

# 2. 设置环境变量并启动(后台)
export MCP_ENDPOINT="ws://192.168.1.107:8004/mcp_endpoint/mcp/?token=<从日志拿到的 token>"
nohup python mcp_pipe.py mcpdemo.py > mcpdemo.log 2>&1 &
```

## 验证

启动后:
1. `tail -f mcpdemo.log` 应该看到 `Successfully connected to WebSocket server`
2. 在 xiaozhi 智控台 → 智能体配置 → 点击"刷新 MCP 的接入状态"
3. 应能看到 `calculate_circle_area` 与 `calculate_square_area` 两个工具

## 工具返回示例

```json
// calculate_circle_area(2)
{"success": true, "shape": "circle", "radius": 2.0, "area": 12.566371}

// calculate_square_area(3)
{"success": true, "shape": "square", "side": 3.0, "area": 9.0}

// calculate_circle_area(-1)
{"success": false, "error": "半径(radius) 必须大于 0,实际收到 -1"}
```