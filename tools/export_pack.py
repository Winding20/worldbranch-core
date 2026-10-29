"""把 Python 播种器里的内容导出成世界包的 content.json。

用途：让老世界（tang_750 / tang_755）也走数据路径，从此新增世界只需要写 JSON。

    python tools/export_pack.py            # 导出全部已登记的老世界
    python tools/export_pack.py tang_755   # 只导一个

导出的文件是**可读的 JSON**，人工可以继续改；不会覆盖已有的手写内容，
除非显式加 --force。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.seed import WORLD_SEEDERS  # noqa: E402
from app.seed.json_dump import dump_world_content  # noqa: E402
from app.worldpack import pack_dir  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="导出世界包的 content.json")
    parser.add_argument("keys", nargs="*", help="世界 key，缺省导出全部")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 content.json")
    args = parser.parse_args(argv[1:])

    keys = args.keys or sorted(WORLD_SEEDERS)
    rc = 0
    for key in keys:
        if key not in WORLD_SEEDERS:
            print(f"✗ {key}：没有对应的 Python 播种器")
            rc = 1
            continue
        target = pack_dir(key) / "content.json"
        if target.exists() and not args.force:
            print(f"· {key}：已存在 content.json，跳过（--force 覆盖）")
            continue
        data = dump_world_content(key)
        geo = data.pop("_geo", {})
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if geo:
            (pack_dir(key) / "geo.json").write_text(
                json.dumps(geo, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        size = target.stat().st_size
        print(
            f"✓ {key}：{len(data['regions'])} 地区、{len(data['factions'])} 派系、"
            f"{len(data['characters'])} 人物、{len(data['armies'])} 军队、"
            f"{len(data['events'])} 史实（{size // 1024} KB）"
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))