# -*- coding: utf-8 -*-
"""cs2map.config —— 工作区配置加载。

口径：
  ROOT = 本文件向上 3 级（shared/pipeline/cs2map/config.py -> 工作区根）
  机器级配置  config/game.json        （游戏安装、用户数据、E2C 缓存路径）
  地图级配置  maps/<map_id>/<map_id>.config.json
地图选择：--map <id> 参数 > 环境变量 CS2MAP_MAP > 默认 harbin。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def game() -> dict:
    return json.loads((ROOT / "config" / "game.json").read_text(encoding="utf-8"))


def map_id_from_argv(argv: list[str] | None = None) -> str:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--map" in argv:
        return argv[argv.index("--map") + 1]
    for a in argv:
        if a.startswith("--map="):
            return a.split("=", 1)[1]
    import os
    return os.environ.get("CS2MAP_MAP", "harbin")


class MapCfg:
    """地图配置的薄封装；路径一律解析为工作区绝对路径。"""

    def __init__(self, map_id: str):
        self.map_id = map_id
        p = ROOT / "maps" / map_id / f"{map_id}.config.json"
        if not p.exists():
            raise SystemExit(f"[config] 找不到地图配置: {p}")
        self.raw = json.loads(p.read_text(encoding="utf-8"))

    def path(self, key: str) -> Path:
        return ROOT / self.raw["files"][key]

    # ---- 常用字段 ----
    @property
    def display_name(self) -> str:
        return self.raw["display_name"]

    @property
    def center(self) -> tuple[float, float]:
        c = self.raw["center"]
        return (float(c["lon"]), float(c["lat"]))

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(LEFT, RIGHT, BOTTOM, TOP) —— 与 E2C 线性铺路一致的四至。"""
        b = self.raw["bounds"]
        return (float(b["left"]), float(b["right"]), float(b["bottom"]), float(b["top"]))

    @property
    def playable(self) -> tuple[int, float]:
        p = self.raw["playable"]
        return (int(p["px"]), float(p["m_per_px"]))

    @property
    def world(self) -> tuple[int, float]:
        w = self.raw["world"]
        return (int(w["px"]), float(w["m_per_px"]))

    @property
    def playable_m(self) -> float:
        px, mpp = self.playable
        return px * mpp

    @property
    def world_m(self) -> float:
        px, mpp = self.world
        return px * mpp

    @property
    def epsg(self) -> str:
        return self.raw["epsg"]

    @property
    def height_scale(self) -> float:
        return float(self.raw["height_scale_m"])

    @property
    def sea_level(self) -> float | None:
        """海平面；未测定时为 None（依赖它的脚本应显式失败，不得用占位值）。"""
        v = self.raw.get("sea_level_m")
        return None if v is None else float(v)

    @property
    def water(self) -> dict:
        return self.raw["water"]

    @property
    def region_shp(self) -> Path:
        return ROOT / self.raw["region_shp"]

    def load(self) -> dict:
        return self.raw


def load(map_id: str | None = None) -> MapCfg:
    return MapCfg(map_id or map_id_from_argv())
