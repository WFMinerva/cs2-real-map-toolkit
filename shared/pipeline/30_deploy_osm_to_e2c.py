# -*- coding: utf-8 -*-
"""30 把裁剪好的路网 OSM 部署到 Earth2Cities 的缓存位置（它在游戏内导入时读这里）。

新脚本（迁移时新增）：把「复制到游戏目录」这一步显式化、可校验。
安全：目标已有且内容相同时不动；不同时先把旧文件备份为 .bak 再覆盖。
"""
import hashlib
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> int:
    mp = config.load()
    src = mp.path("roads_trimmed")
    dst = Path(config.game()["e2c"]["cache_osm"])

    if not src.exists():
        print(f"[MISS] {src}（先跑 26_trim_roads.py）")
        return 1
    if not dst.parent.exists():
        print(f"[MISS] Earth2Cities 缓存目录不存在: {dst.parent}")
        print("       （mod 未安装或未运行过一次导入？）")
        return 1

    if dst.exists() and sha(dst) == sha(src):
        print(f"[same] 目标已是同一文件，不动。  {dst}")
        return 0

    if dst.exists():
        bak = dst.with_suffix(".osm.bak")
        shutil.copy2(dst, bak)
        print(f"[bak ] 旧文件已备份 -> {bak.name}  sha256:{sha(dst)}")
    shutil.copy2(src, dst)
    same = sha(dst) == sha(src)
    print(f"[over] {dst}")
    print(f"       sha256:{sha(dst)}  字节一致={same}  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("下一步：进游戏 -> Earth2Cities -> 导入（详见 shared/docs/Earth2Cities-操作卡.md）")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
