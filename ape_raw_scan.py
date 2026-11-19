#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 APEv2 标签里直接存 GBK/非 UTF-8 字节的文件。只出报告。"""
import os
import sys
import json
import struct
import collections

root = sys.argv[1]
out = sys.argv[2]


def find_tag(d):
    n = len(d)
    end = n - 128 if (n >= 128 and d[-128:-125] == b'TAG') else n
    if end < 32 or d[end - 32:end - 24] != b'APETAGEX':
        if d[:8] == b'APETAGEX':
            size = struct.unpack('<I', d[12:16])[0]
            return 0, min(n, size)
        return None
    version, size, count, flags = struct.unpack('<4I', d[end - 24:end - 8])
    start = end - 32 - size
    if start >= 0 and d[start:start + 8] == b'APETAGEX':
        return start, end
    start = end - size
    if start >= 0:
        return start, end
    return None


def parse(d, start, end):
    p = start
    has_header = d[p:p + 8] == b'APETAGEX'
    if has_header:
        p += 32
    items = []
    while p + 8 <= end:
        vsize, flags = struct.unpack('<II', d[p:p + 8])
        p += 8
        z = d.find(b'\x00', p, end)
        if z < 0:
            break
        key = d[p:z]
        p = z + 1
        val = d[p:p + vsize]
        p += vsize
        if len(val) < vsize:
            break
        items.append((key, flags, val))
    return items, has_header


def pua(s):
    return sum(1 for c in s if 0xE000 <= ord(c) <= 0xF8FF)


def decode_bytes(b):
    b = b.rstrip(b'\x00')
    if not b:
        return '', 'empty'
    try:
        return b.decode('utf-8'), 'utf8'
    except UnicodeDecodeError:
        pass
    cands = []
    for enc in ('gb18030', 'big5', 'shift_jis'):
        try:
            s = b.decode(enc)
        except UnicodeDecodeError:
            continue
        cjk = sum(1 for c in s if '\u4e00' <= c <= '\u9fff')
        bad = pua(s)
        cands.append((bad, -cjk, enc, s))
    if not cands:
        return b.decode('latin-1'), 'latin1'
    cands.sort()
    return cands[0][3], cands[0][2]


rows = []
stats = collections.Counter()
for dp, dn, fn in os.walk(root):
    for f in fn:
        if not f.lower().endswith(('.ape', '.mp3', '.mpga')):
            continue
        p = os.path.join(dp, f)
        try:
            d = open(p, 'rb').read()
        except Exception:
            continue
        loc = find_tag(d)
        if not loc:
            continue
        items, hdr = parse(d, *loc)
        badkeys = []
        badvals = []
        for key, flags, val in items:
            if flags & 6:
                continue
            try:
                ks = key.decode('ascii')
            except UnicodeDecodeError:
                ks, ke = decode_bytes(key + b'\x00')
                ke = 'key:' + ke
                badkeys.append((ke, ks))
            try:
                val.rstrip(b'\x00').decode('utf-8')
            except UnicodeDecodeError:
                s, enc = decode_bytes(val)
                badvals.append((enc, s))
        if badkeys or badvals:
            stats['files'] += 1
            stats['badkeys'] += len(badkeys)
            stats['badvals'] += len(badvals)
            for enc, s in badvals:
                stats['enc:' + enc] += 1
            rows.append({'file': p, 'header': hdr,
                         'badkeys': badkeys, 'badvals': badvals})

print(stats)
json.dump(rows, open(out, 'w'), ensure_ascii=False, indent=1)
print('样例:')
for r in rows[:12]:
    print('\n', r['file'])
    for e, s in r['badkeys'][:3]:
        print('   KEY', e, repr(s)[:80])
    for e, s in r['badvals'][:5]:
        print('   VAL', e, repr(s)[:80])
