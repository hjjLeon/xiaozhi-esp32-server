"""mcpdemo — 一个最小的 MCP 演示服务,提供圆形和正方形面积计算工具。

参考实现: https://github.com/78/mcp-calculator/blob/main/calculator.py
运行方式: 由 mcp_pipe.py 启动 stdio 模式的 FastMCP server。
"""

import logging
import math
import sys

from fastmcp import FastMCP

logger = logging.getLogger("mcpdemo")

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

mcp = FastMCP("mcpdemo")


def _validate_positive_number(value, field_name: str) -> float | dict:
    """统一校验:必须是数字,且 > 0。失败时返回错误 dict(供工具 return)。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return {"success": False, "error": f"{field_name} 必须是数字,实际收到 {type(value).__name__}"}
    if isinstance(value, float) and not math.isfinite(value):
        return {"success": False, "error": f"{field_name} 必须是有限数字,实际收到 {value!r}"}
    if value <= 0:
        return {"success": False, "error": f"{field_name} 必须大于 0,实际收到 {value}"}
    return float(value)


@mcp.tool()
def calculate_circle_area(radius: float) -> dict:
    """计算圆形面积。

    当用户询问"圆的面积"、"半径是多少的圆面积"、"一个圆形有多大"等问题时,
    调用此工具。公式:面积 = π × 半径²。

    Args:
        radius: 圆的半径,必须为大于 0 的数字(单位自定,例如 cm、m)。

    Returns:
        成功时: {"success": True, "shape": "circle", "radius": r, "area": 数值}
        失败时: {"success": False, "error": "原因"}
    """
    checked = _validate_positive_number(radius, "半径(radius)")
    if isinstance(checked, dict):
        return checked
    area = math.pi * checked * checked
    logger.info("calculate_circle_area: r=%s, area=%s", checked, area)
    return {
        "success": True,
        "shape": "circle",
        "radius": checked,
        "area": round(area, 6),
    }


@mcp.tool()
def calculate_square_area(side: float) -> dict:
    """计算正方形面积。

    当用户询问"正方形的面积"、"边长是多少的方形面积"、"一个正方形有多大"等问题时,
    调用此工具。公式:面积 = 边长 × 边长。

    Args:
        side: 正方形的边长,必须为大于 0 的数字(单位自定,例如 cm、m)。

    Returns:
        成功时: {"success": True, "shape": "square", "side": s, "area": 数值}
        失败时: {"success": False, "error": "原因"}
    """
    checked = _validate_positive_number(side, "边长(side)")
    if isinstance(checked, dict):
        return checked
    area = checked * checked
    logger.info("calculate_square_area: side=%s, area=%s", checked, area)
    return {
        "success": True,
        "shape": "square",
        "side": checked,
        "area": round(area, 6),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")