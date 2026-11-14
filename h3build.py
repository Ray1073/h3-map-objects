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
  python h3build.py <存档.GM1> <地图.h3m>           # 人看的
  python h3build.py <存档.GM1> <地图.h3m> --json    # 给合并层的节点（语义字段）

--json 每座城一个节点：
  id（记录偏移，稳定、非坐标）/ kind / x,y,z（城中心，地图坐标）/
  visit（门口互动格）/ owner / race /
  built（已建中文名）/ garrison[{id,name,count}] /
  recruit[{id,name,capacity}]
recruit.available 是**当前可招数量**（本周还剩几个）。
   来源：名字后 14 个 u16，对应位图槽 30..43（7 基础 + 7 升级）。
   证据：111.GM1 读到各族的周产量值，112.GM1 起同样位置全 0（兵被招走了）——
   产量是常数不会变 0，所以这组数是「剩多少」。
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
GROWTH_CANDIDATES = (
    os.path.join(HERE, "..", "refs", "兵种周产量.json"),
    os.path.join(HERE, "data", "兵种周产量.json"),
)
CREATURE_CANDIDATES = (
    os.path.join(HERE, "..", "refs", "兵种ID表.json"),
    os.path.join(HERE, "data", "兵种ID表.json"),
)


def _load_json(paths, what):
    for path in paths:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    raise RuntimeError("找不到 %s（refs/ 或 data/ 下）" % what)


def load_table():
    return _load_json(CANDIDATES, "城镇建筑表.json")


TABLE = load_table()
TOWN_TYPES = {int(k): v for k, v in TABLE["城镇类型"].items()}
NEUT = [TABLE["通用"].get(str(i), "位%d" % i) for i in range(17)]
SPEC = {int(k): v for k, v in TABLE["特有建筑"].items()}
DWELL = {int(k): v for k, v in TABLE["巢穴"].items()}

ANCHOR = b"\xff\x0f\x00\x00"
BITMAP_BYTES = 6

# 每周基础产量（每族 7 级：基础名 / 升级名 / 产量）。不是「当前还剩几个能招」。
GROWTH = _load_json(GROWTH_CANDIDATES, "兵种周产量.json")["产量"]
_CREATURE_CN = _load_json(CREATURE_CANDIDATES, "兵种ID表.json")
CREATURE_ID = {}
for _k, _v in _CREATURE_CN.items():
    if isinstance(_v, dict) and "cn" in _v:
        CREATURE_ID.setdefault(_v["cn"], int(_k))


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
            if 10 in bs:
                # 每座城必有议事堂（位 10），这条比「必有 1 级巢穴」可靠：
                #   - 空壳城（地图编辑器摆的）没有巢穴，但有议事堂；
                #   - 升级成内政厅/议会/国会后，位 10 仍然是 1——
                #     位图记的是「建过」，不是「现在是哪一形态」。
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


def read_towns(data, h3m):
    """城镇节点（给合并层用）：已经翻好的语义字段，不出位图、不出字节。

    字段：
        id       记录在存档里的偏移（稳定、不是坐标；合并层要换就换）
        kind     "城镇"
        x, y, z  城中心，地图坐标（和英雄同一套）
        visit    [x, y] 门口互动格（.h3m 的 access）；拿不到是 None
        owner    0-7 / 255
        race     城镇类型中文名
        built    已建建筑，中文名
        garrison [{"id", "name", "count"}]
        recruit  [{"id", "name", "available"}]  available = **当前可招数量**
    """
    import h3map

    access = {}
    m = h3map.MapFile(h3m).parse()
    for o in m["objects"]:
        if o["class"] != 98:
            continue
        acc = o.get("access") or []
        access[(o["x"], o["y"], o["z"])] = list(acc[0]) if acc else None

    heads = sorted(locate_towns(data, h3m))
    nodes = []
    for k, head in enumerate(heads):
        if k + 1 < len(heads):
            n = heads[k + 1] - head
        elif len(heads) > 1:
            n = heads[-1] - heads[-2]
        else:
            n = 388

        t = data[head + 3]
        race = TOWN_TYPES.get(t, "类型%d" % t)
        x, y, z = data[head + 4] + 2, data[head + 5], data[head + 6]
        res = read_town_buildings(data, head, n)

        built = []
        if res:
            for b in res["built"]:
                nm = name_of(b, t)
                if nm:
                    built.append(nm)

        garrison = []
        for i in range(7):
            cid = int.from_bytes(data[head + 0x09 + i * 4:head + 0x0D + i * 4], "little")
            cnt = int.from_bytes(data[head + 0x25 + i * 4:head + 0x29 + i * 4], "little")
            if cid in (0, 0xFFFFFFFF) or not cnt:
                continue
            garrison.append({"id": cid, "name": h3objs.creature_name(cid), "count": cnt})

        # 名字后面紧跟着 14 个 u16，一一对应位图槽 30..43：
        #   [0..6]  = 7 个基础巢穴的**当前可招数量**
        #   [7..13] = 7 个升级巢穴的当前可招数量
        # 建了升级巢穴，招的就是升级兵，数量在 [7..13]；基础槽那位会归 0。
        after = head + 0x47 + data[head + 0x45]
        avail = [int.from_bytes(data[after + i * 2:after + i * 2 + 2], "little")
                 for i in range(14)]

        recruit = []
        levels = GROWTH.get(race) or []
        if res and levels:
            bs = set(res["built"])
            for i in range(7):
                if 37 + i in bs:
                    slot, up = 37 + i, True
                elif 30 + i in bs:
                    slot, up = 30 + i, False
                else:
                    continue
                lv = levels[i]
                nm = lv["升级"] if up else lv["基础"]
                recruit.append({"id": CREATURE_ID.get(nm), "name": nm,
                                "available": avail[slot - 30]})

        nodes.append({
            "id": head, "kind": "城镇",
            "x": x, "y": y, "z": z,
            "visit": access.get((x, y, z)),
            "owner": data[head],
            "race": race,
            "built": built,
            "garrison": garrison,
            "recruit": recruit,
        })
    return nodes


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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return
    save, h3m = args[0], args[1]
    data = h3objs.load_save(save)

    if "--json" in sys.argv:
        print(json.dumps(read_towns(data, h3m), ensure_ascii=False, indent=1))
        return

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
