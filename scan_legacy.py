#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二遍：处理 GBK 以外的老编码（Big5 / Shift-JIS / GBK 全角）。
用整个曲库已正确的标签建立字频表，给候选解码打分，避免误伤 Hélène 之类西文。
只输出候选，不改文件。
"""
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
                    fr = t[k]
                    for s in getattr(fr, "text", []) or []:
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


freq = collections.Counter()
allrows = list(values())
for p, kind, k, s in allrows:
    for c in s:
        if CJK(c) or KANA(c) or HANGUL(c):
            freq[c] += 1
TOP = {c for c, n in freq.items() if n >= 2}
print("语料字种", len(TOP), file=sys.stderr)

ENCODINGS = ["gbk", "big5", "shift_jis", "euc_kr"]


def has_bigram_run(s):
    run = 0
    for c in s:
        if 0x80 <= ord(c) <= 0xFF:
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
    return False


def score(s, enc):
    if any(HALFKANA(c) for c in s):
        return None
    if any(ord(c) < 0x20 and c not in "\r\n\t" for c in s):
        return None
    n_cjk = sum(1 for c in s if CJK(c))
    n_kana = sum(1 for c in s if KANA(c))
    n_hang = sum(1 for c in s if HANGUL(c))
    n_full = sum(1 for c in s if FULLW(c))
    n_acc = sum(1 for c in s if 0xC0 <= ord(c) <= 0xFF)
    n_bad = sum(1 for c in s if ord(c) >= 0x80 and not (CJK(c) or KANA(c) or HANGUL(c) or FULLW(c) or 0xC0 <= ord(c) <= 0xFF))
    if n_bad:
        return None
    n_text = n_cjk + n_kana + n_hang
    if n_text == 0:
        if n_full < 1:
            return None
    elif n_text < 2:
        return None
    if n_cjk:
        hit = sum(1 for c in s if CJK(c) and c in TOP)
        cov = hit / n_cjk
        if cov < 0.75:
            return None
        base = 0.9 + cov * 0.1
    else:
        base = 0.6
    base += min(n_kana + n_hang + n_full, 5) * 0.02
    return (base, "cjk")


rows = []
for p, kind, k, s in allrows:
    if not s or any(ord(c) > 0xFF for c in s):
        continue
    if "?" in s or not has_bigram_run(s):
        continue
    raw = s.encode("latin-1")
    cands = []
    for enc in ENCODINGS:
        try:
            d = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        sc = score(d, enc)
        if sc:
            cands.append((sc[0], enc, d, sc[1]))
    if not cands:
        continue
    # 同一结果去重，优先 gbk/big5
    seen = {}
    for sc, enc, d, typ in sorted(cands, key=lambda x: (-x[0], ENCODINGS.index(x[1]))):
        if d not in seen:
            seen[d] = (sc, enc, typ)
    ranked = [(v[0], k2, v[1], v[2]) for k2, v in seen.items()]
    ranked.sort(key=lambda x: (-x[0], ENCODINGS.index(x[2])))
    best = ranked[0]
    conf = "high"
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.06 and ranked[0][1] != ranked[1][1]:
        conf = "low"
    rows.append({"file": p, "kind": kind, "field": k, "old": s,
                 "new": best[1], "enc": best[2], "conf": conf,
                 "alt": [{"enc": e, "text": d} for _, d, e, _ in ranked[1:4]]})

print("候选", len(rows), collections.Counter(r["conf"] for r in rows),
      collections.Counter(r["enc"] for r in rows), file=sys.stderr)
json.dump(rows, open(out, "w"), ensure_ascii=False, indent=1)
