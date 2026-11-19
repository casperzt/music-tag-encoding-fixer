#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 scan_legacy.py 产生的候选写回文件（ID3 -> v2.4/UTF-8，APEv2 -> UTF-8）。"""
import json
import sys
import collections
from mutagen.id3 import ID3, Encoding
from mutagen.apev2 import APEv2

rows = json.load(open(sys.argv[1]))
byfile = collections.defaultdict(list)
for r in rows:
    if r.get("conf","high") == "high":
        byfile[r["file"]].append(r)

print("待处理文件", len(byfile))
ok = err = 0
for path, items in byfile.items():
    id3items = [x for x in items if x["kind"] == "id3"]
    apeitems = [x for x in items if x["kind"] == "ape"]
    try:
        if id3items:
            t = ID3(path)
            for x in id3items:
                fr = t[x["field"]]
                fr.text = [x["new"] if s == x["old"] else s
                           for s in getattr(fr, "text", [])]
            t.update_to_v24()
            for fr in t.values():
                if hasattr(fr, "encoding"):
                    fr.encoding = Encoding.UTF8
            t.save(v2_version=4)
        if apeitems:
            a = APEv2(path)
            for x in apeitems:
                key = x["field"]
                val = a[key]
                parts = str(val).split("\x00")
                parts = [x["new"] if p == x["old"] else p for p in parts]
                val.value = "\x00".join(parts)
            a.save()
        ok += 1
        for x in items:
            print(f"  {path.rsplit('/',1)[-1]} | {x['field']}: {x['old']!r} -> {x['new']!r}")
    except Exception as e:
        err += 1
        print("  ERR", path, repr(e))
print(f"完成 文件 {ok}，错误 {err}")
