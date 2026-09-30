# -*- coding: utf-8 -*-
"""22 部署高度图到 CS2 地图编辑器 Heightmaps 目录，并校验字节一致。

来源：旧工作区 scripts/11_deploy.py（已验证），配置驱动改造。
官方口径（Modding Dev Diary #2）：
  Heightmaps 导出到用户数据目录的 Heightmaps\，也从这里导入
  （编辑器也支持 Select from Disk 直接选任意位置的文件，所以这个目录是可选的便利路径）。
安全：只新增/覆盖本工具自己的两个文件，不删除目录内任何既有文件。
"""
import datetime
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> int:
    mp = config.load()
    g = config.game()
    dest = Path(g["heightmaps_dir"])
    pairs = [(mp.path("playable_png"), mp.path("playable_png").name),
             (mp.path("world_png"), mp.path("world_png").name)]

    dest.mkdir(parents=True, exist_ok=True)
    print(f"目标目录: {dest}")
    print(f"部署前目录内已有 {len(list(dest.glob('*.png')))} 个 png\n")

    ok = True
    for src, name in pairs:
        if not src.exists():
            print(f"[MISS] {src} 不存在")
            ok = False
            continue
        dst = dest / name
        existed = dst.exists()
        data = src.read_bytes()
        dst.write_bytes(data)
        same = sha16(src) == sha16(dst) and len(data) == dst.stat().st_size
        print(f"[{'over' if existed else 'new '}] {name}  {dst.stat().st_size/1e6:.2f} MB  "
              f"sha256:{sha16(dst)}  字节一致={same}")
        ok &= same

    print("\n部署后目录内容:")
    for p in sorted(dest.iterdir()):
        ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {p.name:<28} {p.stat().st_size/1e6:8.2f} MB  {ts}")

    # 从部署位置回读，确认是合法 16-bit PNG
    import rasterio
    for _, name in pairs:
        try:
            with rasterio.open(dest / name) as d:
                print(f"  [回读] {name}: {d.width}x{d.height} bands={d.count} "
                      f"dtype={d.dtypes[0]} driver={d.driver}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [回读失败] {name}: {type(exc).__name__}: {exc}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
