"""Registered visual evidence tool; final research conclusions stay in the Skill."""
from src.services.stock_kline_visual import inspect_daily_chart


def run(args):
    return {"tool": "stock_kline_visual_analysis", **inspect_daily_chart(**args)}
