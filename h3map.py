#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 大肥鱼
# SPDX-License-Identifier: GPL-3.0-or-later
# 本程序是自由软件，按 GNU 通用公共许可证 v3 或更新版本发布；详见 LICENSE。
"""h3map.py —— H3 地图文件（.h3m）静态解析器

只读文件，不碰进程、不写任何东西。格式依据 h3mtxt 的 h3m_specification：
https://github.com/alexanderbelous/h3mtxt/blob/master/doc/h3m_specification.MD

用法：
    python h3map.py info  <地图.h3m>      头部 + 地形统计 + 对象统计（人读）
    python h3map.py check <地图.h3m>      自检：解析到底、padding 全 0、对象坐标合法
    python h3map.py objs  <地图.h3m> [N]  前 N 个对象的明细（调试用）
    python h3map.py json  <地图.h3m> [out.json]

设计原则：读错就报错，不猜、不补默认值。
"""

import argparse
import gzip
import json
import struct
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------------------
# 基础读取
# ---------------------------------------------------------------------------
class Reader:
    __slots__ = ("b", "p")

    def __init__(self, buf):
        self.b = buf
        self.p = 0

    def left(self):
        return len(self.b) - self.p

    def take(self, n):
        if n < 0:
            raise ValueError("负长度 %d" % n)
        if self.p + n > len(self.b):
            raise EOFError("越界：偏移 0x%X 要读 %d 字节，只剩 %d" % (self.p, n, len(self.b) - self.p))
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def u8(self):
        return self.take(1)[0]

    def i8(self):
        return struct.unpack("<b", self.take(1))[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def i16(self):
        return struct.unpack("<h", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def s32(self):
        """String32：u32 长度 + N 字节（中文图是 GBK）"""
        n = self.u32()
        raw = self.take(n)
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("gbk", errors="replace")

    def skip(self, n):
        self.take(n)


# ---------------------------------------------------------------------------
# 子结构
# ---------------------------------------------------------------------------
def read_creature_stack(r):
    return {"type": r.i16(), "quantity": r.i16()}


def read_creature_stacks(r, n=7):
    return [read_creature_stack(r) for _ in range(n)]


def read_guardians(r):
    g = {"message": r.s32()}
    if r.u8():                      # has_creatures
        g["creatures"] = read_creature_stacks(r)
    r.skip(4)
    return g


def skip_artifact_props(r):
    if r.u8():                      # has_guardians
        read_guardians(r)


def skip_message_and_treasure(r):
    r.s32()                         # message
    r.skip(28)                      # resources int32[7]
    r.skip(2)                       # artifact int16


def skip_event_base(r):
    if r.u8():                      # has_guardians
        read_guardians(r)
    r.skip(4 + 4 + 1 + 1)           # exp / spell / morale / luck
    r.skip(28)                      # resources int32[7]
    r.skip(4)                       # primary int8[4]
    n = r.u8(); r.skip(2 * n)       # secondary skills
    n = r.u8(); r.skip(2 * n)       # artifacts
    n = r.u8(); r.skip(n)           # spells
    n = r.u8(); r.skip(4 * n)       # creatures
    r.skip(EVENT_EXTRA)


def skip_hero_props(r, sod, ab):
    r.skip(4)                       # absod_id
    r.skip(1)                       # owner
    r.skip(1)                       # type
    if r.u8():
        r.s32()                     # has_name + name
    if sod:
        if r.u8():
            r.skip(4)               # has_experience + experience
    elif ab:
        r.skip(4)                   # experience（AB 总是有）
    if r.u8():
        r.skip(1)                   # has_portrait + portrait
    if r.u8():
        n = r.u32(); r.skip(2 * n)  # secondary skills
    if r.u8():
        r.skip(28)                  # creatures
    r.skip(1)                       # formation
    if r.u8():                      # artifacts
        r.skip(2 * (19 if sod else 18))
        n = r.u16(); r.skip(2 * n)
    r.skip(1)                       # patrol_radius
    if r.u8():
        r.s32()                     # biography
    r.skip(1)                       # gender
    if sod:
        if r.u8():
            r.skip(9)               # spells
    elif ab:
        r.skip(1)                   # spell
    if sod:
        if r.u8():
            r.skip(4)               # primary skills
    r.skip(16)                      # reserved


def skip_quest(r):
    t = r.u8()
    if t == 1: r.skip(4)
    elif t == 2: r.skip(4)
    elif t == 3: r.skip(4)
    elif t == 4: r.skip(4)
    elif t == 5:
        n = r.u8(); r.skip(2 * n)
    elif t == 6:
        n = r.u8(); r.skip(4 * n)
    elif t == 7: r.skip(28)
    elif t == 8: r.skip(1)
    elif t == 9: r.skip(1)
    if t != 0:
        r.skip(4)                   # deadline
        r.s32()                     # proposal
        r.s32()                     # progress
        r.s32()                     # completion


def skip_timed_event(r, sod):
    r.s32()                         # name
    r.s32()                         # message
    r.skip(28)                      # resources
    r.skip(1)                       # affected players
    if sod:
        r.skip(1)                   # applies to human
    r.skip(1)                       # applies to computer
    r.skip(2)                       # day of first occurrence
    r.skip(2)                       # repeat
    r.skip(16)                      # reserved


def skip_town_event(r, sod):
    skip_timed_event(r, sod)
    r.skip(6)                       # buildings
    r.skip(14)                      # creatures uint16[7]
    r.skip(4)                       # reserved


def skip_town_props(r, sod, o=None):
    r.skip(4)                       # absod_id
    r.skip(1)                       # owner
    if r.u8():
        r.s32()                     # name
    if r.u8():
        g = read_creature_stacks(r)  # garrison（7 槽 × 4 字节）
        if o is not None:
            o["garrison"] = [(c["type"], c["quantity"]) for c in g]
    r.skip(1)                       # formation
    if r.u8():
        r.skip(12)                  # buildings
    else:
        r.skip(1)                   # has_fort
    r.skip(18)                      # must/may not have spell
    n = r.u32()
    for _ in range(n):
        skip_town_event(r, sod)
    if sod:
        r.skip(1)                   # alignment
    r.skip(3)


def object_tiles(o, tpl):
    """由锚点 + 模板矩阵算出这个对象"占了哪些格"和"能点哪格"。

    H3M 里 Object.coordinates 是对象的**右下角锚点**；模板自带 6 行 × 8 列的
    passability / actionability 位图，矩阵右下角 (r5,c7) 对准锚点。

    实测对照（Unholy Quest 另存版，2026-11-04）：
        水晶矿锚点(74,59) → 可访问格 (73,59) ✓
        金矿  (67,65) → (66,65) ✓
        硫磺矿(59,68) → (58,68) ✓
    """
    pas = bytes.fromhex(tpl["passability"])
    act = bytes.fromhex(tpl["actionability"])
    blocked, access = [], []
    for rr in range(6):
        for cc in range(8):
            ax = o["x"] - (7 - cc)
            ay = o["y"] - (5 - rr)
            if ax < 0 or ay < 0:
                continue
            if ((pas[rr] >> cc) & 1) == 0:
                blocked.append((ax, ay))
            if (act[rr] >> cc) & 1:
                access.append((ax, ay))
    return blocked, access


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
FORMAT_NAME = {0x0E: "RoE", 0x15: "AB", 0x1C: "SoD"}

RM71_LEN = 12         # class 71（随机怪物）属性固定长度（实测确定）
EVENT_EXTRA = 8       # EventBase 末尾还有 8 字节（实测确定，规范里没写）
OBJ_RESERVED = 5      # 对象头里 reserved 的字节数，实验确定


class MapFile:
    def __init__(self, path):
        self.path = path
        raw = open(path, "rb").read()
        self.raw_size = len(raw)
        if raw[:2] == b"\x1f\x8b":
            self.data = gzip.decompress(raw)
            self.compressed = True
        else:
            self.data = raw
            self.compressed = False

    def parse(self):
        r = Reader(self.data)
        d = {"issues": [], "stage": "header"}
        try:
            return self._parse(r, d)
        except Exception as e:
            raise RuntimeError("[阶段 %s，偏移 0x%X] %s" % (d["stage"], r.p, e)) from None

    def _parse(self, r, d):
        d["stage"] = "header"
        d["format"] = r.u32()
        fmt = d["format"]
        self.sod = (fmt == 0x1C)
        self.ab = (fmt == 0x15)
        self.roe = (fmt == 0x0E)
        d["format_name"] = FORMAT_NAME.get(fmt, "其他(0x%X)" % fmt)

        d["is_playable"] = r.u8()
        d["map_size"] = r.u32()
        d["has_two_levels"] = r.u8()
        d["name"] = r.s32()
        d["description"] = r.s32()
        d["difficulty"] = r.u8()
        d["max_hero_level"] = r.u8()

        d["stage"] = "players"
        if self.roe:
            self._parse_roe_after_header(r, d)
        else:
            self._read_players(r, d)
            d["stage"] = "additional_info"
            d["_off_players_end"] = r.p
            self._skip_additional_info(r)
            d["stage"] = "tiles"
            d["_off_additional_end"] = r.p
            self._read_tiles(r, d)
            d["stage"] = "object_templates"
            self._read_templates(r, d)
            d["stage"] = "objects"
            self._read_objects(r, d)
            d["stage"] = "timed_events"
        n_te = r.u32()
        d["num_timed_events"] = n_te
        for _ in range(n_te):
            skip_timed_event(r, self.sod)
        padding = r.take(124)
        d["padding_zeros"] = all(b == 0 for b in padding)
        d["left_bytes"] = r.left()
        return d

    # -- RoE：玩家/附加信息结构与 SoD 不同，直接定位地形区 -----------------
    def _parse_roe_after_header(self, r, d):
        """RoE(0x0E) 的 PlayerSpecs 与 SoD 不同，没找到规范。

        做法：从玩家区起点向后扫描"地形区起点"——判据是：从该点 + 7×N 处
        能完整解析模板（首模板 def 以 .def 结尾）、对象、事件，且末尾 124 字节
        padding 全 0、一个字节不剩。这个判据极强，实际唯一命中。
        """
        start = r.p
        n_tiles = (2 if d["has_two_levels"] else 1) * d["map_size"] ** 2
        d["stage"] = "roe_locate_tiles"
        for off in range(start, start + 4000):
            if off + 7 * n_tiles + 8 > len(self.data):
                break
            rr = Reader(self.data)
            rr.p = off + 7 * n_tiles
            dd = {"map_size": d["map_size"], "has_two_levels": d["has_two_levels"],
                  "issues": [], "_templates": []}
            try:
                self._read_templates(rr, dd)
                if not dd["_templates"] or not dd["_templates"][0]["def"].endswith(".def"):
                    continue
                self._read_objects(rr, dd)
                k = rr.u32()
                for _ in range(k):
                    skip_timed_event(rr, False)
                pad = rr.take(124)
                if rr.left() != 0 or not all(b == 0 for b in pad):
                    continue
            except Exception:
                continue
            d["_off_tiles"] = off
            d["_roe_players_skipped"] = off - start
            r.p = off
            self._read_tiles(r, d)
            d["stage"] = "object_templates"
            self._read_templates(r, d)
            d["stage"] = "objects"
            self._read_objects(r, d)
            d["stage"] = "timed_events"
            d["players"] = []
            d["players_note"] = "RoE 格式，玩家设置未解析（跳过 %d 字节）" % (off - start)
            return
        raise RuntimeError("RoE：在地形区起点扫描范围内没找到完整可信的解析起点"
                           "（玩家区起点 0x%X）" % start)

    # -- players ---------------------------------------------------------
    def _read_players(self, r, d):
        players = []
        for color in range(8):
            p = {"color": color}
            p["can_be_human"] = r.u8()
            p["can_be_computer"] = r.u8()
            p["behavior"] = r.u8()
            if self.sod:
                r.skip(1)           # has_customized_alignments (SoD only)
            r.skip(2)               # allowed alignments
            r.skip(1)               # allow random alignment
            if r.u8():              # has_main_town
                r.skip(1)           # generate hero
                r.skip(1)           # town type
                p["town_coords"] = tuple(r.take(3))
            r.skip(1)               # has_random_heroes
            hero_type = r.u8()
            if hero_type != 0xFF:
                r.skip(1)           # portrait
                r.s32()             # name
            r.skip(1)               # num nonspecific placeholder heroes
            n = r.u32()
            heroes = []
            for _ in range(n):
                heroes.append({"type": r.u8(), "name": r.s32()})
            p["num_heroes"] = n
            p["heroes"] = heroes
            players.append(p)
        d["players"] = players

    # -- additional info（只跳过）----------------------------------------
    def _skip_additional_info(self, r):
        vc = r.u8()
        if vc != 0xFF:
            r.skip(1 + 1)
            if vc == 0: r.skip(2)
            elif vc == 1: r.skip(6)
            elif vc == 2: r.skip(5)
            elif vc == 3: r.skip(5)
            elif vc == 4: r.skip(3)
            elif vc == 5: r.skip(3)
            elif vc == 6: r.skip(3)
            elif vc == 7: r.skip(3)
            elif vc == 10: r.skip(4)
            elif vc == 12: r.skip(4)
        lc = r.u8()
        if lc == 0: r.skip(3)
        elif lc == 1: r.skip(3)
        elif lc == 2: r.skip(2)
        if r.u8():                  # num teams
            r.skip(8)
        r.skip(20)                  # heroes availability
        n = r.u32(); r.skip(n)      # placeholder heroes
        if self.sod:
            n = r.u8()
            for _ in range(n):
                r.skip(2)
                r.s32()
                r.skip(1)
        r.skip(31)                  # reserved
        r.skip(18 if self.sod else 17)   # disabled artifacts
        if self.sod:
            r.skip(9)               # disabled spells
            r.skip(4)               # disabled skills
        n = r.u32()                 # rumors
        for _ in range(n):
            r.s32(); r.s32()
        if self.sod:
            for _ in range(156):
                if r.u8():
                    self._skip_hero_settings(r)

    def _skip_hero_settings(self, r):
        if r.u8(): r.skip(4)        # experience
        if r.u8():
            n = r.u32(); r.skip(2 * n)
        if r.u8():
            r.skip(2 * 19)
            n = r.u16(); r.skip(2 * n)
        if r.u8(): r.s32()          # biography
        r.skip(1)                   # gender
        if r.u8(): r.skip(9)        # spells
        if r.u8(): r.skip(4)        # primary skills

    # -- tiles -----------------------------------------------------------
    def _read_tiles(self, r, d):
        size = d["map_size"]
        levels = 2 if d["has_two_levels"] else 1
        n = levels * size * size
        start = r.p
        raw = r.take(7 * n)
        d["num_tiles"] = n
        d["tiles_bytes"] = r.p - start
        grids = []
        roadgrids = []
        for lv in range(levels):
            base = 7 * lv * size * size
            g = [[raw[base + 7 * (y * size + x)] for x in range(size)] for y in range(size)]
            grids.append(g)
            rg = [[raw[base + 7 * (y * size + x) + 4] for x in range(size)] for y in range(size)]
            roadgrids.append(rg)
        d["terrain"] = grids
        d["roads"] = roadgrids
        d["_tiles_raw"] = raw          # 完整 7 字节/格，内存指纹搜索用

    # -- templates -------------------------------------------------------
    def _read_templates(self, r, d):
        n = r.u32()
        d["num_object_templates"] = n
        tpl = []
        for _ in range(n):
            t = {"def": r.s32()}
            t["passability"] = bytes(r.take(6)).hex()
            t["actionability"] = bytes(r.take(6)).hex()
            t["allowed_landscapes"] = bytes(r.take(2)).hex()
            t["landscape_group"] = bytes(r.take(2)).hex()
            t["object_class"] = r.u32()
            t["object_subclass"] = r.u32()
            t["object_group"] = r.u8()
            t["is_ground"] = r.u8()
            r.skip(16)
            tpl.append(t)
        d["_templates"] = tpl

    # -- objects ---------------------------------------------------------
    def _read_objects(self, r, d):
        tpl = d["_templates"]
        size = d["map_size"]
        n = r.u32()
        d["num_objects"] = n
        objs = []
        for i in range(n):
            o = {"x": r.u8(), "y": r.u8(), "z": r.u8(), "template_idx": r.u32()}
            if OBJ_RESERVED:
                o["_resv"] = bytes(r.take(OBJ_RESERVED))
                if any(o["_resv"]):
                    d["issues"].append("对象 #%d reserved 非 0：%s" % (i, o["_resv"].hex()))
            if o["template_idx"] >= len(tpl):
                prev = objs[-1] if objs else None
                raise RuntimeError("对象 #%d 模板索引 %d 超范围（共 %d）；上一个对象 class=%s sub=%s def=%s"
                                   % (i, o["template_idx"], len(tpl),
                                      prev and prev["class"], prev and prev["subclass"],
                                      prev and prev["def"]))
            t = tpl[o["template_idx"]]
            cls, sub = t["object_class"], t["object_subclass"]
            o["class"] = cls
            o["subclass"] = sub
            o["def"] = t["def"]
            if o["x"] > size + 16 or o["y"] > size + 16:
                d["issues"].append("对象 #%d 坐标 (%d,%d) 远离地图 %d，偏移 0x%X"
                                   % (i, o["x"], o["y"], size, r.p))
            try:
                self._read_object_props(r, o, cls, sub)
            except Exception as e:
                raise RuntimeError("对象 #%d/%d（class=%d sub=%d def=%r）在偏移 0x%X 解析失败：%s"
                                   % (i, n, cls, sub, t["def"], r.p, e))
            objs.append(o)
            o["blocked"], o["access"] = object_tiles(o, t)
        d["objects"] = objs

    def _read_object_props(self, r, o, cls, sub):
        if cls in (53, 220) and sub == 7:
            r.skip(4)
        elif cls in (5, 65, 66, 67, 68, 69):
            skip_artifact_props(r)
        elif cls == 26:
            skip_event_base(r)
            r.skip(1 + 1 + 1 + 4)
        elif cls in (17, 20, 42, 87) or (cls in (53, 220) and sub != 7):
            o["owner"] = r.u8(); r.skip(3)
        elif cls in (33, 219):
            o["owner"] = r.u8(); r.skip(3)
            o["garrison"] = read_creature_stacks(r)
            r.skip(1 + 8)
        elif cls == 36:
            r.skip(4)
        elif cls in (34, 62, 70):
            skip_hero_props(r, self.sod, self.ab)
        elif cls == 214:
            r.skip(1)
            if r.u8() == 0xFF:
                r.skip(1)
        elif cls == 71:
            # 随机怪物（AVWmrnd*.def）：固定 12 字节，没有 message/treasure 分支
            o["absod_id"] = r.u32()
            o["quantity"] = r.u16()
            r.skip(RM71_LEN - 6)
        elif cls in (54, 72, 73, 74, 75, 162, 163, 164):
            o["absod_id"] = r.u32()
            o["quantity"] = r.u16()
            r.skip(1)                       # disposition
            if r.u8():                      # has message and treasure
                skip_message_and_treasure(r)
            r.skip(1 + 1 + 2)               # never flees / does not grow / reserved
        elif cls == 6:
            skip_event_base(r)
        elif cls == 215:
            skip_quest(r)
        elif cls == 216:
            r.skip(4)                       # owner + reserved
            if r.u32() == 0:
                r.skip(2)                   # alignment
            r.skip(2)                       # min/max level
        elif cls == 218:
            r.skip(6)
        elif cls == 217:
            r.skip(4)
            if r.u32() == 0:
                r.skip(2)
        elif cls in (76, 79):
            if r.u8():
                read_guardians(r)
            o["quantity"] = r.u32()
            r.skip(4)
        elif cls == 81:
            r.skip(8)
        elif cls == 83:
            skip_quest(r)
            rt = r.u8()
            if rt in (1, 2): r.skip(4)
            elif rt in (3, 4): r.skip(1)
            elif rt == 5: r.skip(5)
            elif rt == 6: r.skip(2)
            elif rt == 7: r.skip(2)
            elif rt == 8: r.skip(2)
            elif rt == 9: r.skip(1)
            elif rt == 10: r.skip(4)
            r.skip(2)
        elif cls in (88, 89, 90):
            r.skip(4)
        elif cls in (59, 91):
            r.s32(); r.skip(4)
        elif cls == 93:
            if r.u8():
                read_guardians(r)
            r.skip(1 + 3)
        elif cls in (77, 98):
            skip_town_props(r, self.sod, o)
        elif cls == 113:
            r.skip(4)
        # 其余 class 没有任何附加属性（装饰物 / 不可交互对象），跳过 0 字节


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def load(path):
    m = MapFile(path)
    return m, m.parse()


def cmd_info(path):
    m, d = load(path)
    print("文件：%s" % path)
    print("  磁盘 %d → 解压 %d 字节（%s）" % (m.raw_size, len(m.data),
                                          "gzip" if m.compressed else "未压缩"))
    print("格式：0x%X（%s）  可玩：%d" % (d["format"], d["format_name"], d["is_playable"]))
    print("尺寸：%d×%d   地下层：%s" % (d["map_size"], d["map_size"],
                                     "有" if d["has_two_levels"] else "无"))
    print("名字：%s" % d["name"])
    print("说明：%s" % (d["description"][:50] + ("…" if len(d["description"]) > 50 else "")))
    print("难度：%d  等级上限：%d" % (d["difficulty"], d["max_hero_level"]))
    print("玩家：人类可玩 %s ｜ 电脑可用 %s"
          % ([p["color"] for p in d["players"] if p["can_be_human"]],
             [p["color"] for p in d["players"] if p["can_be_computer"]]))
    for p in d["players"]:
        if p["can_be_human"] or p["can_be_computer"]:
            print("  颜色 %d：英雄 %d 个 %s" % (p["color"], p["num_heroes"],
                                            [h["name"] for h in p["heroes"]]))
    print("地形：%d 格 = %d 字节（%d 层）" % (d["num_tiles"], d["tiles_bytes"],
                                         2 if d["has_two_levels"] else 1))
    from collections import Counter
    for lv, g in enumerate(d["terrain"]):
        c = Counter(v for row in g for v in row)
        print("  第 %d 层地形：%s" % (lv, dict(sorted(c.items()))))
    print("对象模板：%d" % d["num_object_templates"])
    print("对象：%d 个" % d["num_objects"])
    c = Counter((o["class"], o["subclass"]) for o in d["objects"])
    print("  按 (class,sub) 统计前 12：%s" % c.most_common(12))
    print("结尾：padding 全 0=%s，剩余 %d 字节" % (d["padding_zeros"], d["left_bytes"]))
    return d


def cmd_check(path):
    try:
        m, d = load(path)
    except Exception as e:
        print("❌ 解析失败：%s" % e)
        return 1
    ok = True
    print("[1] 解析到底：剩余 %d 字节" % d["left_bytes"]);            ok &= d["left_bytes"] == 0
    print("[2] padding 全 0：%s" % d["padding_zeros"]);              ok &= d["padding_zeros"]
    bad = [o for o in d["objects"] if o["x"] > d["map_size"] or o["y"] > d["map_size"]]
    print("[3] 对象坐标合法：%s" % ("是" if not bad else "否（%d 个）" % len(bad))); ok &= not bad
    print("结论：%s" % ("通" if ok else "没通"))
    return 0 if ok else 1


def cmd_objs(path, n=20):
    m, d = load(path)
    print("共 %d 个对象，前 %d 个：" % (d["num_objects"], min(n, d["num_objects"])))
    for i, o in enumerate(d["objects"][:n]):
        extra = ""
        if "quantity" in o:
            extra = " 数量=%d" % o["quantity"]
        print("  #%-3d (%3d,%3d,%d) class=%-3d sub=%-3d %-16s%s"
              % (i, o["x"], o["y"], o["z"], o["class"], o["subclass"], o["def"], extra))
    return d


def main():
    ap = argparse.ArgumentParser(description="H3 .h3m 静态解析（只读）")
    ap.add_argument("cmd", choices=["info", "check", "objs", "json"])
    ap.add_argument("path")
    ap.add_argument("out", nargs="?")
    a = ap.parse_args()
    try:
        if a.cmd == "info":
            cmd_info(a.path)
        elif a.cmd == "check":
            sys.exit(cmd_check(a.path))
        elif a.cmd == "objs":
            cmd_objs(a.path, int(a.out) if a.out else 20)
        else:
            d = load(a.path)[1]
            d.pop("_templates", None)
            txt = json.dumps(d, ensure_ascii=False)
            if a.out:
                open(a.out, "w", encoding="utf-8").write(txt)
                print("已写 %s（%d 字节）" % (a.out, len(txt)))
            else:
                print(txt)
    except Exception as e:
        print("❌ %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
