# -*- coding: utf-8 -*-
"""scan_dsh_sessions.py —— 解压 DSH 会话文件并按关键词扫描（只读）。

用法：
  python tools/scan_dsh_sessions.py head <zstd文件> [行数=3]     # 看头部元数据
  python tools/scan_dsh_sessions.py find <关键词> [目录根]        # 全库搜关键词，列出命中会话与次数
"""
import sys
import zstandard as zstd


def read_session(path):
    with open(path, "rb") as f:
        raw = f.read()
    return zstd.ZstdDecompressor().decompressobj().decompress(raw).decode("utf-8", "replace")


def main():
    mode = sys.argv[1]
    if mode == "head":
        text = read_session(sys.argv[2])
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        for line in text.splitlines()[:n]:
            print(line[:600])
    elif mode == "find":
        kw = sys.argv[2]
        root = sys.argv[3] if len(sys.argv) > 3 else r"%USERPROFILE%\.dsh\sessions"
        import os
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".jsonl.zstd"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    text = read_session(p)
                except Exception as ex:
                    print(f"!! 解压失败 {p}: {ex}")
                    continue
                c = text.count(kw)
                if c:
                    print(f"{c:6d}  {p}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
