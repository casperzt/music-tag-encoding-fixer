#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 APEv2 标签里直接存 GBK/Big5 原始字节（不是 UTF-8）的文件。

Mp3tag / foobar 读这种标签会显示乱码，因为 APEv2 规范要求 UTF-8。
本脚本按字节重新解码，再把整个 APE 标签用 UTF-8 重写。

  python3 fix_ape_raw.py /path/to/Music            # 预演
  python3 fix_ape_raw.py /path/to/Music --apply    # 写入
"""
import os
import sys
import json
import struct
import argparse
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fix_music_encoding import fix_text, KEY_MAP  # noqa: E402

CJK = lambda c: "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf"
KANA = lambda c: "\u3040" <= c <= "\u30ff"
HANGUL = lambda c: "\uac00" <= c <= "\ud7af"
FULLW = lambda c: "\uff01" <= c <= "\uff5e" or "\u3000" <= c <= "\u303f"
PUA = lambda c: "\ue000" <= c <= "\uf8ff"


def find_tag(d):
    n = len(d)
    end = n - 128 if (n >= 128 and d[-128:-125] == b"TAG") else n
    if end >= 32 and d[end - 32:end - 24] == b"APETAGEX":
        version, size, count, flags = struct.unpack("<4I", d[end - 24:end - 8])
        start = end - 32 - size
        if start >= 0 and d[start:start + 8] == b"APETAGEX":
            return start, end
        start = end - size
        if start >= 0:
            return start, end
    if d[:8] == b"APETAGEX":
        size = struct.unpack("<I", d[12:16])[0]
        return 0, min(n, size)
    return None


def parse_items(d, start, end):
    p = start
    if d[p:p + 8] == b"APETAGEX":
        p += 32
    items = []
    while p + 8 <= end:
        vsize, flags = struct.unpack("<II", d[p:p + 8])
        p += 8
        z = d.find(b"\x00", p, end)
        if z < 0:
            break
        key = d[p:z]
        p = z + 1
        val = d[p:p + vsize]
        p += vsize
        if len(val) < vsize:
            break
        items.append((key, flags, val))
    return items


def build_corpus(root):
    freq = collections.Counter()
    for dp, dn, fn in os.walk(root):
        for name in dn + fn:
            for c in name:
                if CJK(c) or KANA(c) or HANGUL(c):
                    freq[c] += 1
    try:
        from mutagen.id3 import ID3
        from mutagen.apev2 import APEv2
        for dp, dn, fn in os.walk(root):
            for f in fn:
                p = os.path.join(dp, f)
                low = f.lower()
                vals = []
                if low.endswith((".mp3", ".mpga")):
                    try:
                        t = ID3(p)
                    except Exception:
                        continue
                    for k in t:
                        for s in getattr(t[k], "text", []) or []:
                            if isinstance(s, str):
                                vals.append(s)
                elif low.endswith(".ape"):
                    try:
                        a = APEv2(p)
                    except Exception:
                        continue
                    for k, v in a.items():
                        if v.__class__.__name__ != "APEBinaryValue":
                            vals.append(str(v))
                for s in vals:
                    for c in s:
                        if CJK(c) or KANA(c) or HANGUL(c):
                            freq[c] += 1
    except Exception:
        pass
    return {c for c, n in freq.items() if n >= 2}


TOP = set()


def score_text(s):
    if not s or any(PUA(c) for c in s):
        return -9
    n_cjk = sum(1 for c in s if CJK(c))
    n_kana = sum(1 for c in s if KANA(c))
    n_hang = sum(1 for c in s if HANGUL(c))
    n_full = sum(1 for c in s if FULLW(c))
    if n_cjk == 0 and n_kana == 0 and n_hang == 0 and n_full == 0:
        return -5
    if n_cjk:
        cov = sum(1 for c in s if CJK(c) and c in TOP) / n_cjk
    else:
        cov = 1.0
    return cov + min(n_kana + n_hang + n_full, 5) * 0.02


def smart_decode(raw):
    raw = raw.rstrip(b"\x00")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best = None
    for enc in ("gb18030", "big5", "shift_jis"):
        try:
            s = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        sc = score_text(s)
        if best is None or sc > best[0]:
            best = (sc, s, enc)
    if best is None:
        return raw.decode("latin-1")
    return best[1]


def patch_mutagen():
    import mutagen.apev2 as ape
    from mutagen.apev2 import APEBadItemError, _get_value_type
    from mutagen._util import cdata
    import io

    def _parse_value(self, data):
        self.value = smart_decode(data)

    ape._APEUtf8Value._parse = _parse_value
    ape.is_valid_apev2_key = lambda k: isinstance(k, str) and bool(k) and "\x00" not in k

    def _parse_tag(self, tag, count):
        f = io.BytesIO(tag)
        for _ in range(count):
            head = f.read(8)
            if not head:
                break
            if len(head) != 8:
                raise APEBadItemError("bad item header")
            size = cdata.uint32_le(head[:4])
            flags = cdata.uint32_le(head[4:8])
            kind = (flags & 6) >> 1
            if kind == 3:
                raise APEBadItemError("bad value type")
            key = f.read(1)
            if not key:
                raise APEBadItemError
            while key[-1:] != b"\x00":
                nxt = f.read(1)
                if not nxt:
                    raise APEBadItemError
                key += nxt
            key = key[:-1]
            try:
                k = key.decode("ascii")
            except UnicodeDecodeError:
                try:
                    k = key.decode("utf-8")
                except UnicodeDecodeError:
                    k = key.decode("gb18030", "replace")
            val = f.read(size)
            if len(val) != size:
                raise APEBadItemError
            self[k] = _get_value_type(kind)._new(val)

    ape.APEv2._APEv2__parse_tag = _parse_tag


AD_KEYS = ("绝对音乐影视论坛", "绝对音乐", "绝对音乐发布组",
           "論壇網址", "新潮娛樂論壇", "论坛网址")


def is_ad_key(k):
    return k in AD_KEYS or any(p in k for p in ("绝对音乐", "論壇網址", "新潮娛樂論壇"))


def process(root, apply, log):
    global TOP
    TOP = build_corpus(root)
    print("语料字种", len(TOP))
    patch_mutagen()
    from mutagen.apev2 import APEv2

    rows = []
    stats = collections.Counter()
    checked = 0
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if not f.lower().endswith((".ape", ".mp3", ".mpga")):
                continue
            p = os.path.join(dp, f)
            try:
                d = open(p, "rb").read()
            except Exception:
                continue
            loc = find_tag(d)
            if not loc:
                continue
            items = parse_items(d, *loc)
            need = False
            for key, flags, val in items:
                if flags & 6:
                    continue
                try:
                    key.decode("ascii")
                except UnicodeDecodeError:
                    need = True
                try:
                    val.rstrip(b"\x00").decode("utf-8")
                except UnicodeDecodeError:
                    need = True
            if not need:
                continue
            checked += 1
            try:
                tag = APEv2(p)
            except Exception as e:
                stats["parse_error"] += 1
                rows.append({"file": p, "error": repr(e)})
                continue

            new_items = {}
            for key, value in list(tag.items()):
                if is_ad_key(key):
                    rows.append({"file": p, "action": "delete_key", "key": key})
                    continue
                nk = KEY_MAP.get(key, key)
                if key == "版权所有":
                    nk = "Copyright"
                s = str(value).strip("\x00")
                fixed = fix_text(s)
                if fixed is not None:
                    rows.append({"file": p, "action": "fix_text", "key": key,
                                 "old": s, "new": fixed})
                    s = fixed
                elif s != str(value):
                    rows.append({"file": p, "action": "strip_nul", "key": key,
                                 "old": str(value), "new": s})
                if nk != key:
                    rows.append({"file": p, "action": "rename_key", "old": key, "new": nk})
                new_items[nk] = s

            if apply:
                try:
                    tag.clear()
                    for k, v in new_items.items():
                        tag[k] = v
                    tag.save()
                    stats["rewritten"] += 1
                except Exception as e:
                    stats["save_error"] += 1
                    rows.append({"file": p, "error": repr(e)})
            else:
                stats["would_rewrite"] += 1

    print(stats)
    if log:
        with open(log, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("日志:", log)
    if not apply:
        print("预演完成，加 --apply 写入")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    process(a.root, a.apply, a.log)
