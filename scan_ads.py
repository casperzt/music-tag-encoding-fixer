#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描标签里的广告/网址文字，只出报告，不改文件。"""
import os
import re
import sys
import json
import collections
from mutagen.id3 import ID3
from mutagen.apev2 import APEv2

root = sys.argv[1]
out = sys.argv[2]

URL_RE = re.compile(r'(?:https?://|ftp://|www\.)[^\s"\'<>）)】\]]+', re.I)
DOMAIN_RE = re.compile(r'\b(?:[a-z0-9][a-z0-9-]{1,30}\.)+(?:com|net|org|cn|cc|tv|top|xyz|info|me|vip|la|tw|hk|biz|site|club|pw)\b', re.I)
QQ_RE = re.compile(r'(?:QQ|qq|Q群|qq群)\s*[:：]?\s*\d{5,}', re.I)
WECHAT_RE = re.compile(r'(?:微信|公众号|WeChat)\s*[:：]?\s*[A-Za-z0-9_-]{3,}', re.I)
KEYWORDS = [
    '音乐网', '音乐论坛', '论坛', '网站', '网址', '主页', '空间', '博客', '微博', '贴吧',
    '首发', '制作', '压制', '转载', '下载', '试听', '本站', '更多', '点击', '收藏',
    '分享', '资源', '整理', '提供', '欢迎', '访问', '歌迷', '后援', '联盟', '社区',
    '同步', '更新', '发布', '独家', '高清', '无损', '高品质', '百度云', '网盘',
    '音悦台', '酷狗', '酷我', '网易云', '虾米', '千千静听', '多米', '一听', '九酷',
    'btgod', 'needpop', 'sosolala', 'topzj', 'ting163', 'kupig', '91ting',
    '品茶', '六月联盟', '小猪音乐', '豬豬音樂', '小豬音樂', '爱音乐', '爱听',
]
KW_RE = re.compile('|'.join(re.escape(k) for k in KEYWORDS), re.I)


def hit(s):
    tags = []
    for name, rx in (('url', URL_RE), ('domain', DOMAIN_RE), ('qq', QQ_RE),
                     ('wechat', WECHAT_RE), ('keyword', KW_RE)):
        m = rx.search(s)
        if m:
            tags.append((name, m.group(0)))
    return tags


def values():
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            low = f.lower()
            if low.endswith(('.mp3', '.mpga')):
                try:
                    t = ID3(p)
                except Exception:
                    continue
                for k in t:
                    fr = t[k]
                    for s in getattr(fr, 'text', []) or []:
                        if isinstance(s, str):
                            yield p, 'id3', k, s
            elif low.endswith('.ape'):
                try:
                    a = APEv2(p)
                except Exception:
                    continue
                for k, v in a.items():
                    if v.__class__.__name__ == 'APEBinaryValue':
                        continue
                    for s in str(v).split('\x00'):
                        yield p, 'ape', k, s


rows = []
stat = collections.Counter()
for p, kind, k, s in values():
    h = hit(s)
    if h:
        stat[k] += 1
        stat['__kinds:' + ','.join(sorted(set(x[0] for x in h)))] += 1
        rows.append({'file': p, 'kind': kind, 'field': k, 'value': s,
                     'hits': h})
print('命中字段', len(rows))
for k, v in stat.most_common(40):
    print(f'  {k}: {v}')
json.dump(rows, open(out, 'w'), ensure_ascii=False, indent=1)

print('\n=== 唯一值样例（前 60）')
seen = set()
n = 0
for r in rows:
    v = r['value'][:160]
    if v in seen:
        continue
    seen.add(v)
    print(f"  [{r['field']}] {v!r}")
    n += 1
    if n >= 60:
        break
