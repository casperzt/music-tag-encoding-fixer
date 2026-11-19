#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三遍：剩余少量 Big5 全角/GBK 重音字母（é è í ó）等。只输出候选。"""
import os
import sys
import json
import collections
from mutagen.id3 import ID3
from mutagen.apev2 import APEv2

root = sys.argv[1]
out = sys.argv[2]

CJK = lambda c: "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf"
KANA = lambda c: "\u3040" <= c <= "\u30ff"
HANGUL = lambda c: "\uac00" <= c <= "\ud7af"
FULLW = lambda c: "\uff01" <= c <= "\uff5e" or "\u3000" <= c <= "\u303f"
HALFKANA = lambda c: "\uff61" <= c <= "\uff9f"
ACC = lambda c: 0xC0 <= ord(c) <= 0xFF


def values():
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            low = f.lower()
            if low.endswith((".mp3", ".mpga")):
                try:
                    t = ID3(p)
                except Exception:
                    continue
                for k in t:
                    for s in getattr(t[k], "text", []) or []:
                        if isinstance(s, str):
                            yield p, "id3", k, s
            elif low.endswith(".ape"):
                try:
                    a = APEv2(p)
                except Exception:
                    continue
                for k, v in a.items():
                    if v.__class__.__name__ == "APEBinaryValue":
                        continue
                    for s in str(v).split("\x00"):
                        yield p, "ape", k, s


allrows = list(values())
freq = collections.Counter()
for p, kind, k, s in allrows:
    for c in s:
        if CJK(c) or KANA(c) or HANGUL(c):
            freq[c] += 1
TOP = {c for c, n in freq.items() if n >= 2}

ENCODINGS = ["gbk", "big5", "shift_jis", "euc_kr"]


def score(s):
    if any(HALFKANA(c) for c in s):
        return None
    n_cjk = sum(1 for c in s if CJK(c))
    n_kana = sum(1 for c in s if KANA(c))
    n_hang = sum(1 for c in s if HANGUL(c))
    n_full = sum(1 for c in s if FULLW(c))
    n_acc = sum(1 for c in s if ACC(c))
    n_bad = sum(1 for c in s if ord(c) >= 0x80 and not (CJK(c) or KANA(c) or HANGUL(c) or FULLW(c) or ACC(c)))
    if n_bad:
        return None
    if n_cjk:
        cov = sum(1 for c in s if CJK(c) and c in TOP) / n_cjk
        if cov < 0.85:
            return None
        if n_cjk + n_kana + n_hang < 2:
            return None
        return (0.9 + cov * 0.1, "cjk")
    if n_kana + n_hang >= 2:
        return (0.85, "kana")
    if n_kana + n_hang == 0 and n_full >= 2:
        return (0.85, "fullwidth")
    if n_acc and n_cjk == 0 and n_kana == 0 and n_full == 0:
        return (0.7, "accent")
    return None


rows = []
for p, kind, k, s in allrows:
    if not s or any(ord(c) > 0xFF for c in s):
        continue
    if "?" in s or not any(0x80 <= ord(c) <= 0xFF for c in s):
        continue
    raw = s.encode("latin-1")
    cands = []
    for enc in ENCODINGS:
        try:
            d = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        sc = score(d)
        if sc:
            cands.append((sc[0], enc, d, sc[1]))
    if not cands:
        continue
    cands.sort(key=lambda x: (-x[0], ENCODINGS.index(x[1])))
    best = cands[0]
    alt = [{"enc": e, "text": d} for _, e, d, _ in cands[1:3]]
    rows.append({"file": p, "kind": kind, "field": k, "old": s,
                 "new": best[2], "enc": best[1], "type": best[3], "alt": alt})

print("候选", len(rows), collections.Counter(r["type"] for r in rows), file=sys.stderr)
json.dump(rows, open(out, "w"), ensure_ascii=False, indent=1)
for r in rows:
    print(r["enc"], r["type"], "|", repr(r["old"]), "->", r["new"], "|", r["file"].rsplit("/", 1)[-1])
