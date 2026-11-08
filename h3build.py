#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""h3build.py — 城镇建筑读取

表在 refs/城镇建筑表.json（开源包里是 data/城镇建筑表.json）。

位图结构（实测）：
  城镇记录里有两份 6 字节（48 位）位图，中间隔 2 个 0：
    位图 A = 已建造
    位图 B = 当前可建造
  位图 B 结尾固定是 ff 0f 00 00；位图 A 在它前 8 或 16 字节（城堡差 16）。
  位 0-16 通用 / 17-27 城镇特有 / 28-29 空 / 30-43 巢穴 / 44-47 空
  城镇类型字节在记录 +0x03。

用法：
  python h3build.py <存档.GM1> <地图.h3m>
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import h3objs

CANDIDATES = (
    os.path.join(HERE, "..", "refs", "城镇建筑表.json"),
    os.path.join(HERE, "data", "城镇建筑表.json"),
)


def load_table():
    for path in CANDIDATES:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    raise RuntimeError("找不到 城镇建筑表.json（refs/ 或 data/ 下）")


TABLE = load_table()
TOWN_TYPES = {int(k): v for k, v in TABLE["城镇类型"].items()}
NEUT = [TABLE["通用"].get(str(i), "位%d" % i) for i in range(17)]
SPEC = {int(k): v for k, v in TABLE["特有建筑"].items()}
DWELL = {int(k): v for k, v in TABLE["巢穴"].items()}

ANCHOR = b"\xff\x0f\x00\x00"
BITMAP_BYTES = 6


def bits(bm):
    return [i for i in range(len(bm) * 8) if bm[i // 8] >> (i % 8) & 1]


def name_of(bit, ttype):
    if 0 <= bit < len(NEUT):
        return NEUT[bit]
    if 17 <= bit <= 27:
        table = SPEC.get(ttype)
        if table:
            return table[bit - 17]
        return None
    if 30 <= bit <= 43:
        table = DWELL.get(ttype)
        if table and bit - 30 < len(table):
            return table[bit - 30]
        return "巢穴#%d" % (bit - 30)
    return None


def locate_towns(data, h3m):
    objs = h3objs.map_objects(h3m, (98,))
    heads = []
    for (x, y, z, sub, cls) in objs:
        pat = bytes((x - 2, y, z))
        i = data.find(pat)
        found = None
        while i >= 0:
            head = i - 4
            if (head >= 0 and head + 8 < len(data)
                    and (data[head] <= 7 or data[head] == 0xFF)
                    and data[head + 3] <= 8
                    and data.find(ANCHOR, head, min(head + 400, len(data))) >= 0):
                found = head
                break
            i = data.find(pat, i + 1)
        if found is not None:
            heads.append(found)
    return heads


def find_bitmap2(data, addr, length):
    end = min(addr + length, len(data))
    # 用 rfind：满城的「已建」位图巢穴段也全亮，结尾同样是 ff 0f 00 00
    i = data.rfind(ANCHOR, addr, end)
    if i < 0 or i - 4 < addr:
        return -1
    return i - 4


def read_town_buildings(data, addr, length):
    b2 = find_bitmap2(data, addr, length)
    if b2 < 0:
        return None
    bm2 = data[b2:b2 + BITMAP_BYTES]

    cands = []
    for back in (8, 16):
        a = b2 - back
        if a >= addr:
            bs = bits(data[a:a + BITMAP_BYTES])
            if 30 in bs:                 # 每座城必有 1 级兵巢穴
                cands.append((len(bs), a, bs))
    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    _, a, bs = cands[0]

    return {
        "built": bs,
        "buildable": bits(bm2),
        "off_built": a - addr,
        "off_buildable": b2 - addr,
    }


def render(index, r, t, res):
    lines = []
    lines.append("=== 城镇 %d  (%d,%d,%d)  %s  [记录 +0x%x, 位图 +0x%x/+0x%x] ===" % (
        index, r["x"], r["y"], r["z"], TOWN_TYPES.get(t, "类型%d" % t),
        r["addr"], res["off_built"], res["off_buildable"]))
    for label, key in (("已建", "built"), ("可建", "buildable")):
        bl = [b for b in res[key] if name_of(b, t)]
        lines.append("  %s %d 个：" % (label, len(bl)))
        lines.append("    " + "、".join(name_of(b, t) for b in bl))
        lines.append("    位: " + " ".join(str(b) for b in bl))
    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    save, h3m = sys.argv[1], sys.argv[2]
    data = h3objs.load_save(save)

    heads = locate_towns(data, h3m)
    if not heads:
        print("没定位到城镇")
        return

    heads = sorted(heads)
    default_len = (heads[-1] - heads[-2]) if len(heads) > 1 else 388
    print("城镇 %d 座" % len(heads))

    for k, head in enumerate(heads):
        n = (heads[k + 1] - head) if k + 1 < len(heads) else default_len
        t = data[head + 3]
        r = {"addr": head, "x": data[head + 4] + 2, "y": data[head + 5], "z": data[head + 6]}
        res = read_town_buildings(data, head, n)
        if res is None:
            print("=== 城镇 %d  (0x%x)  没找到位图 ===" % (k, head))
            continue
        print(render(k, r, t, res))


if __name__ == "__main__":
    main()
