#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 大肥鱼
# SPDX-License-Identifier: GPL-3.0-or-later
# 本程序是自由软件，按 GNU 通用公共许可证 v3 或更新版本发布；详见 LICENSE。
"""h3objs.py —— 读《英雄无敌3》存档里各类对象的归属

七类对象，布局各不相同，所以配置是分开写的：

    对象    每条      坐标在记录里    owner      坐标偏移
    矿       62       +0x3B          +0x00       x-1
    灯塔     62       +0x3B          +0x00       x-1（在矿数组里，type=100）
    巢穴     75       +0x0D          +0x49       按模板
    驻军     61       +0x3A          +0x01       横 x-1 / 纵 y-1
    城镇    变长~388  +0x04          +0x00       x-2
    船坞     26×3     散在格子表      +0x08       —
    船       28       +0x08          无归属      x-1

用法：
    python h3objs.py <存档.GM1> <对应.h3m>
    python h3objs.py <存档.GM1> <对应.h3m> --only 城镇
"""

import argparse
import collections
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

COLORS = ["红", "蓝", "棕", "绿", "橙", "紫", "青", "粉"]
MINE_TYPES = ["木材", "水银", "矿石", "硫磺", "水晶", "宝石", "金币"]

KINDS = {
    "矿": {
        # 53/220 = 矿，42 = 灯塔。矿和灯塔在同一个数组里（H3API 的 minesLighthouses）
        "classes": (53, 220, 42),
        "stride": 62,
        "xy_off": 0x3B,
        "owner_off": 0x00,
        "type_off": 0x01,
        "time_off": 0x02,
        "shifts": [(-1, 0)],
        "group_by": "none",
        "verified": True,
    },
    "巢穴": {
        # 17 = 普通巢穴（招 1 种），20 = 招 4 种的巢穴（石人工场/元素汇合点）
        # 18/19 是空号，未实现，放了游戏起不来
        # 随机巢穴是 216/217/218，游戏开始就解析成 17 或 20，存档里不会有
        "classes": (17, 20),
        "stride": 75,
        # 坐标不在记录开头：记录真起点比坐标位置早 13 字节
        "lead": 0x0D,
        "xy_off": 0x0D,
        "sub_off": 0x00,
        "owner_off": 0x49,
        "locator": "dwell",
        "verified": True,
    },
    "驻军": {
        "classes": (33, 219),
        "stride": 61,
        "xy_off": 0x3A,
        "owner_off": 0x01,
        "count_off": 0x1E,
        # 横向（class 33）差在 x，纵向（class 219）差在 y
        "shift_by_class": {33: (-1, 0), 219: (0, -1)},
        "group_by": "class",
        "verified": True,
    },
    "城镇": {
        "classes": (98,),
        "variable": True,
        "xy_off": 0x04,
        "owner_off": 0x00,
        "shift": (-2, 0),
        "verified": True,
    },
    "船坞": {
        # 87 = 船坞（AVXshyd0）。不在对象数组里，散在格子表中，
        # 每条 26 字节、每座占 3 格，见 README「船坞」一节
        "classes": (87,),
        "locator": "ship",
        "verified": True,
    },
}


def load_save(path):
    raw = open(path, "rb").read()
    if raw[:2] == b"\x1f\x8b":
        try:
            return zlib.decompressobj(-15).decompress(raw[10:])
        except zlib.error:
            import gzip
            return gzip.decompress(raw)
    return raw


def map_objects(h3m, classes):
    import h3map
    _, d = h3map.load(h3m)
    return [(o["x"], o["y"], o["z"], o["subclass"], o["class"])
            for o in d["objects"] if o["class"] in classes]


def positions_of(d, pat):
    out = []
    i = d.find(pat)
    while i >= 0:
        out.append(i)
        i = d.find(pat, i + 1)
    return out


def locate_fixed(d, coords, cfg, shifts):
    """固定 stride 的对象：找能连成 stride 链的偏移。"""
    stride = cfg["stride"]
    best = None
    for (dx, dy) in shifts:
        pos = {}
        for c in coords:
            nx, ny = c[0] + dx, c[1] + dy
            if not (0 <= nx < 256 and 0 <= ny < 256):
                continue
            for p in positions_of(d, bytes((nx, ny, c[2]))):
                pos[p] = c
        if not pos:
            continue
        for p in pos:
            ln = 1
            q = p + stride
            while q in pos:
                ln += 1
                q += stride
            if best is None or ln > best[2]:
                best = (p - cfg["xy_off"], (dx, dy), ln)
    return best


def read_fixed(d, h3m, name, cfg):
    objs = map_objects(h3m, cfg["classes"])
    if not objs:
        return None, "地图里没有这类对象"

    groups = {}
    if cfg["group_by"] == "none":
        groups[None] = objs
        shifts = cfg["shifts"]
    elif cfg["group_by"] == "class":
        for o in objs:
            groups.setdefault(o[4], []).append(o)
        shifts = None
    else:  # sub
        for o in objs:
            groups.setdefault(o[3], []).append(o)
        shifts = cfg["shifts"]

    found = []
    for key, coords in groups.items():
        if cfg["group_by"] == "class":
            sh = [cfg["shift_by_class"][key]]
        else:
            sh = shifts
        r = locate_fixed(d, coords, cfg, sh)
        if r:
            found.append((key, r))

    if not found:
        return None, "坐标链没接上"

    base = min(r[0] for _, r in found)
    if any((b - base) % cfg["stride"] for _, (b, s, ln) in found):
        return None, "各组起点对不齐，定位不可靠"

    rows = []
    cset = {(o[0], o[1], o[2]) for o in objs}
    for k in range(len(objs)):
        q = base + k * cfg["stride"]
        if q + cfg["stride"] > len(d):
            break
        xo = q + cfg["xy_off"]
        row = {
            "i": k,
            "owner": d[q + cfg["owner_off"]],
            "x": d[xo] + (1 if cfg["xy_off"] == 0x3B else 0),
            "y": d[xo + 1],
            "z": d[xo + 2],
        }
        # 驻军的坐标偏移是横 x-1 / 纵 y-1，用 .h3m 的坐标集合还原
        if cfg["group_by"] == "class":
            if (row["x"] + 1, row["y"], row["z"]) in cset:
                row["x"] += 1
            elif (row["x"], row["y"] + 1, row["z"]) in cset:
                row["y"] += 1
        if "sub_off" in cfg:
            row["sub"] = d[q + cfg["sub_off"]]
        if "type_off" in cfg:
            row["type"] = d[q + cfg["type_off"]]
        if "time_off" in cfg:
            row["abandoned"] = d[q + cfg["time_off"]]
        if "count_off" in cfg:
            # 驻军：兵种在 +0x02 + k*4（u16），数量在 +0x1E + k*4（u32），各 7 槽
            troops = []
            for k in range(7):
                t = d[q + 0x02 + k * 4] | (d[q + 0x03 + k * 4] << 8)
                c = struct.unpack("<I", d[q + 0x1E + k * 4:q + 0x22 + k * 4])[0]
                if c and t != 0xFFFF:
                    troops.append((t, c))
            row["troops"] = troops
            row["count"] = sum(c for _, c in troops)
        rows.append(row)

    shifts_used = {k: v[1] for k, v in found}
    return rows, {"base": base, "n": len(objs), "shifts": shifts_used}


def read_town(d, h3m, cfg):
    """城镇是变长记录，不能按 stride 扫：坐标位置往前退 4 字节就是记录头。"""
    objs = map_objects(h3m, cfg["classes"])
    if not objs:
        return None, "地图里没有城镇"

    dx, dy = cfg["shift"]
    pos = []
    for c in objs:
        for p in positions_of(d, bytes((c[0] + dx, c[1] + dy, c[2]))):
            pos.append(p)
    pos = sorted(set(pos))
    # 候选位置：往前 4 字节那个 owner 必须合法（0-7 或 FF），否则是误报
    cand = [p for p in pos
            if d[p - cfg["xy_off"]] <= 7 or d[p - cfg["xy_off"]] == 0xFF]
    if len(cand) >= len(objs):
        pos = cand
    if len(pos) < len(objs):
        return None, f"只找到 {len(pos)}/{len(objs)} 个坐标"

    # 变长记录，相邻间距应该落在 200~600 之间。命中多的时候，挑最长的那串
    if len(pos) > len(objs):
        best = []
        for i in range(len(pos)):
            chain = [pos[i]]
            for p in pos[i + 1:]:
                if 200 <= p - chain[-1] <= 600:
                    chain.append(p)
                if len(chain) >= len(objs):
                    break
            if len(chain) > len(best):
                best = chain
        if len(best) >= len(objs):
            pos = best

    rows = []
    for k, p in enumerate(pos[:len(objs)]):
        b = p - cfg["xy_off"]
        # 守军也是两段式，但这里是 u32：+0x09 兵种 ×7、+0x25 数量 ×7
        troops = []
        for s in range(7):
            t = struct.unpack("<I", d[b + 0x09 + s * 4:b + 0x0D + s * 4])[0]
            c = struct.unpack("<I", d[b + 0x25 + s * 4:b + 0x29 + s * 4])[0]
            if c and t != 0xFFFFFFFF:
                troops.append((t, c))
        rows.append({
            "i": k,
            "owner": d[b + cfg["owner_off"]],
            "index": d[b + 3],
            "x": d[b + 4] + 2,
            "y": d[b + 5],
            "z": d[b + 6],
            "troops": troops,
            "addr": b,
        })
    gaps = [pos[i + 1] - pos[i] for i in range(len(pos) - 1)]
    return rows, {"n": len(objs), "gaps": gaps}


def read_dwell(d, h3m, cfg):
    """巢穴：存档顺序 = 地图顺序，但坐标可能差一格（按模板的访客格）。

    定位靠投票：真记录的坐标都写在记录 +0x00，所以「坐标位置 mod stride」
    全都相等；误命中会散到别的余数上。挑票数最高的余数，再在里面找最长的
    stride 等差链，链首就是数组起点。
    """
    objs = map_objects(h3m, cfg["classes"])
    if not objs:
        return None, "地图里没有巢穴"

    stride = cfg["stride"]
    n = len(objs)

    pos = set()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for o in objs:
                nx, ny = o[0] + dx, o[1] + dy
                if not (0 <= nx < 256 and 0 <= ny < 256):
                    continue
                pat = bytes((nx, ny, o[2]))
                i = d.find(pat)
                while i >= 0:
                    pos.add(i)
                    i = d.find(pat, i + 1)
    if not pos:
        return None, "一个坐标都没找到"

    votes = collections.Counter(p % stride for p in pos)
    rem = votes.most_common(1)[0][0]
    cand = sorted(p for p in pos if p % stride == rem)

    # 地图坐标 +/-1 的邻域：不同模板的访客格和锚点差多少不一定
    hit = set()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for o in objs:
                hit.add((o[0] + dx, o[1] + dy, o[2]))

    # 同一个余数下的位置互差都是 stride 倍数，光看间距分不出真假，
    # 直接拿「按顺序排的 n 条坐标能命中多少」打分
    score, base = -1, None
    for c in cand:
        if c + n * stride > len(d):
            continue
        s = 0
        for k in range(n):
            q = c + k * stride
            if (d[q], d[q + 1], d[q + 2]) in hit:
                s += 1
        if s > score:
            score, base = s, c
    if base is None:
        return None, "候选位置都不够长"

    side = struct.unpack("<I", d[0x35:0x39])[0]
    if not (0 < side <= 256):
        side = 256

    def plausible(p):
        if p < 0 or p + stride > len(d):
            return False
        xo = p + cfg["xy_off"]
        own = d[p + cfg["owner_off"]]
        return (d[xo] < side and d[xo + 1] < side and d[xo + 2] <= 1
                and (own < 8 or own == 0xFF))

    # 打分得到的 base 是「坐标位置」，记录真起点还要往前 lead 字节
    base -= cfg.get("lead", 0)

    # 打分用的起点不一定是第 0 条，往前退到记录不像巢穴为止。
    # 判据要同时满足：坐标落在 .h3m 邻域里 + 字段合法，光靠字段合法会多退一条
    while base - stride >= 0:
        q = base - stride + cfg["xy_off"]
        if (d[q], d[q + 1], d[q + 2]) in hit and plausible(base - stride):
            base -= stride
        else:
            break

    rows = []
    for k in range(n):
        p = base + k * stride
        if p + stride > len(d):
            break
        xo = p + cfg["xy_off"]
        rows.append({
            "i": k,
            "owner": d[p + cfg["owner_off"]],
            "sub": d[p + cfg["sub_off"]],
            "x": d[xo],
            "y": d[xo + 1],
            "z": d[xo + 2],
        })
    return rows, {"base": base, "n": n, "score": score}


def read_ships(d, h3m, cfg):
    """船坞：不在对象数组里，散在格子表中。

    每条 26 字节，每座占 3 格：
        +0x02  u16 class = 87
        +0x06  u16 序号（格子块编号，不是数组下标）
        +0x08  owner
        +0x0B  恒为 FF —— 筛掉误命中的关键判据
    """
    objs = map_objects(h3m, cfg["classes"])
    if not objs:
        return None, "地图里没有船坞"

    pat = bytes((0x57, 0x00, 0x00, 0x00))
    found = []
    last = None
    i = d.find(pat)
    while i >= 0:
        s = i - 2
        if s >= 0 and s + 26 <= len(d):
            own = d[s + 8]
            if d[s + 11] == 0xFF and (own < 8 or own == 0xFF):
                seq = d[s + 6] | (d[s + 7] << 8)
                if (seq, own) != last:
                    found.append((seq, own, s))
                    last = (seq, own)
        i = d.find(pat, i + 1)
    if not found:
        return None, "没找到船坞记录"

    found.sort()
    coords = sorted((o[0], o[1], o[2]) for o in objs)
    rows = []
    for k, (seq, own, addr) in enumerate(found[:len(coords)]):
        x, y, z = coords[k]
        rows.append({"i": k, "owner": own, "x": x, "y": y, "z": z,
                     "seq": seq, "addr": addr})
    return rows, {"n": len(coords), "found": len(found)}


_CRE = None


def creature_name(t):
    """兵种 ID -> 中文名（查 data/兵种ID表.json）"""
    global _CRE
    if _CRE is None:
        try:
            with open(os.path.join(DATA, "兵种ID表.json"), encoding="utf-8") as f:
                _CRE = json.load(f)
        except Exception:
            _CRE = {}
    r = _CRE.get(str(t))
    if not r:
        return f"?{t}"
    return r.get("cn") or r["en"]


def show(name, rows, info, cfg):
    flag = "" if cfg["verified"] else "  ⚠ 未验证"
    print(f"\n=== {name} ==={flag}")
    if rows is None:
        print(f"  {info}")
        return
    if "base" in info:
        print(f"  数组 @0x{info['base']:06X}   {info['n']} 条")
    elif "found" in info:
        print(f"  地图 {info['n']} 座，存档里找到 {info['found']} 条记录")
    else:
        gaps = info.get("gaps", [])
        print(f"  {info['n']} 条   间距：{gaps}")
    for r in rows:
        own = "无主" if r["owner"] == 0xFF else (
            COLORS[r["owner"]] if r["owner"] < 8 else f"owner={r['owner']}")
        extra = ""
        if r.get("type") is not None:
            if r["type"] == 100:
                extra += "  灯塔"
            elif r["type"] < 7:
                extra += f"  {MINE_TYPES[r['type']]}"
            else:
                extra += f"  ?{r['type']}"
        if r.get("sub") is not None:
            extra += f"  sub={r['sub']}"
        if r.get("troops"):
            extra += "  " + " + ".join(f"{creature_name(t)}×{c}"
                                       for t, c in r["troops"])
        elif r.get("count"):
            extra += f"  兵={r['count']}"
        if r.get("abandoned"):
            extra += "  [废弃]"
        print(f"    #{r['i']:<2} ({r['x']:>2},{r['y']:>2},{r['z']})  {own}{extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("save")
    ap.add_argument("h3m")
    ap.add_argument("--only", help="只读这一类（矿 / 巢穴 / 驻军 / 城镇）")
    a = ap.parse_args()

    d = load_save(a.save)
    names = [a.only] if a.only else list(KINDS)
    for name in names:
        if name not in KINDS:
            print(f"不认识的类型：{name}")
            continue
        cfg = KINDS[name]
        if cfg.get("variable"):
            rows, info = read_town(d, a.h3m, cfg)
        elif cfg.get("locator") == "dwell":
            rows, info = read_dwell(d, a.h3m, cfg)
        elif cfg.get("locator") == "ship":
            rows, info = read_ships(d, a.h3m, cfg)
        else:
            rows, info = read_fixed(d, a.h3m, name, cfg)
        show(name, rows, info, cfg)


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
