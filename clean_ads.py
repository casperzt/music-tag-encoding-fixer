#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理音乐标签里的广告/网址文字。

默认只预演：打印将删除/修改的内容，不写文件。
加 --apply 才真正写回（ID3v2.4 + UTF-8，APEv2 UTF-8）。
"""
import os
import re
import json
import argparse
import collections
from mutagen.id3 import ID3, Encoding, ID3NoHeaderError
from mutagen.apev2 import APEv2, APEBinaryValue

URL_RE = re.compile(r'(?:https?:/{1,2}|ftp://)\S*', re.I)
WWW_RE = re.compile(r'www\.[^\s"\'<>）)】\]]+', re.I)
DOMAIN_RE = re.compile(
    r'(?:[a-z0-9][a-z0-9-]{1,30}\.)+(?:com|net|org|cn|top|xyz|info|vip|tw|hk|biz|ru|jp|kr|io|co)(?![a-z0-9-])',
    re.I)
CJK_DOMAIN_RE = re.compile(r'[\u4e00-\u9fff]{2,}\.(?:com|net|org|cn|top|xyz|info|vip|tw|hk|biz|ru|jp|kr|io|co)(?![a-z0-9-])', re.I)
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
QQ_RE = re.compile(r'(?:QQ|qq|Q群|qq群)\s*[:：]?\s*\d{5,}', re.I)
WECHAT_RE = re.compile(r'(?:微信|公众号|WeChat)\s*[:：]?\s*[A-Za-z0-9_-]{3,}', re.I)

# 明确的站点/推广短语（只放不会被当成正常歌名/歌手名的）
SITE_PHRASES = [
    '音域派', 'UKoO音乐论坛', 'UKoO', '搜啦EP', '全球华语Demo音乐网',
    '晓风下载论坛', '晓风下载', '天堂制作', 'YOU主流', '品茶制作', '品茶制',
    '小老虎制作', '六月联盟', '小豬音樂網', '豬豬音樂網', '小猪音乐网',
    '星皓音乐网', '音乐风云网', '捌零音乐论坛', '极品音乐论坛', '93MV音乐网',
    '365佳音网', '新华音乐', '天籁回寰', '优卡娱乐', '傲世堂', '杰伦皇城',
    '开心家园', '酷猪音乐', '酷猪', '至爱音乐论坛', '炫音音乐论坛tgj9604推荐',
    '炫音音乐论坛', '绝对音乐影视论坛', '绝对音乐', '港音湾岸', '联盟@星星',
    '联盟 星星', 'lure收集整理', '烟缸金鱼制作发行', '河神收藏', '年轻人收藏',
    '果子收藏', '欢迎光临pighome.org', 'pighome.org', 'pighome', '欢迎光临',
    '欢迎大家来JRSITE.COM玩哦', 'JRSITE.COM', 'EE23forum欢迎大家',
    'Ee23 Forum欢迎大家', '专业DJ到论坛下载最新压缩包',
    '本曲由musiclover提供，特此致谢', '本曲由musiclover提供', '特此致谢',
    '本曲由北唐提供', '偶爱 mp3', 'oimp3.com', 'oimp3',
    '国际在线/音乐空间', 'Allenkoby@港音湾岸@原创论坛', 'TConline网络电台频道',
    '想了解更多的作品请上', '精品发烧尽在六月联盟', '精品发烧尽在',
    '完美下载', '本站是以推荐音乐为主', '163音樂網', '特此致',
    'Buy it if you like it', 'buy it if you like it',
]
SITE_RE = re.compile('|'.join(re.escape(x) for x in sorted(SITE_PHRASES, key=len, reverse=True)), re.I)

BRACKET_RE = re.compile(r'[\[【(（<][^\]】)）>]{0,120}[\]】)）>]')
EDGE_RE = re.compile(r'^[\s\-–—|/\\\\.:：,，;；~～@#*+=_&]+|[\s\-–—|/\\\\.:：,，;；~～@#*+=_&]+$')
JUNK_TOKENS = {'http', 'https', 'ftp', 'www', 'bbs', 'htm', 'html', 'com', 'cn',
               'net', 'org', 'ru', 'co', 'ne', 'c', 'w', 'htt', 'type', 'providedby',
               'provided', 'by', 'music', 'mp3', 'wma', 'flac', 'lrc'}

PROMO_WORDS = ['本站', '推荐', '收录', '下载', '论坛', '咨询', '欢迎', '提供',
               '整理', '制作', '首发', '分享', '访问', '网址', '网站', '歌迷',
               '后援', '联盟', '社区']
NAME_PROMO = ['咨询', '下载', '论坛', '推荐', '首发', '网址', '网站', '社区']
LRC_RE = re.compile(r'^\s*\[[A-Za-z]{2,4}:')
HAS_CJK = re.compile(r'[\u4e00-\u9fff]')


def looks_fragment(s):
    if not s:
        return True
    if len(s.strip()) <= 1:
        return True
    if '.' in s and ' ' not in s and re.search(r'[A-Za-z]', s):
        return True
    if any(0x80 <= ord(c) <= 0xFF for c in s) and not re.search(r'[\u4e00-\u9fff]', s):
        return True
    return False

# 名称类字段：清理后只要还有内容就保留，不因推广词整行删除
NAME_FIELDS = {
    'TIT1', 'TIT2', 'TIT3', 'TPE1', 'TPE2', 'TPE3', 'TPE4', 'TALB', 'TOAL',
    'Album', 'Artist', 'Album Artist', 'Title', 'BAND',
}

JUNK_FIELDS = {'WMFSDKNeeded', 'ee23', 'ee23forum', '論壇網址', '新潮娛樂論壇',
               'BYAYU', 'CONTACT US', 'Related'}


def has_ad_signal(s):
    return bool(URL_RE.search(s) or WWW_RE.search(s) or DOMAIN_RE.search(s)
                or CJK_DOMAIN_RE.search(s)
                or EMAIL_RE.search(s) or QQ_RE.search(s) or WECHAT_RE.search(s)
                or SITE_RE.search(s))


def meaningful(s):
    return bool(re.search(r'[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', s))


def clean_line(line):
    def bracket(m):
        seg = m.group(0)
        return ' ' if has_ad_signal(seg) else seg
    x = BRACKET_RE.sub(bracket, line)
    x = URL_RE.sub(' ', x)
    x = WWW_RE.sub(' ', x)
    x = DOMAIN_RE.sub(' ', x)
    x = CJK_DOMAIN_RE.sub(' ', x)
    x = EMAIL_RE.sub(' ', x)
    x = QQ_RE.sub(' ', x)
    x = WECHAT_RE.sub(' ', x)
    x = SITE_RE.sub(' ', x)
    parts = [p for p in re.split(r'[ \t]+', x) if p]
    parts = [p for p in parts if p.lower().strip('.,:;!?()[]{}') not in JUNK_TOKENS]
    x = ' '.join(parts).strip()
    x = EDGE_RE.sub('', x)
    x = re.sub(r' {2,}', ' ', x).strip()
    # 截断后残留的半个方括号，如 "[mp3wma.ne"
    x = re.sub(r'[\[【(（<][^\]】)）>]*$', ' ', x).strip()
    x = EDGE_RE.sub('', x)
    return x


def is_name_field(key):
    k = str(key)
    if k.upper().startswith('TXXX:'):
        k = k.split(':', 1)[1]
    return k in NAME_FIELDS


WHITELIST = {'Souljaboytellem.com'}


def clean_value(s, key):
    """返回 (new, changed, deleted)。只有含广告信号才处理。"""
    if not s or not has_ad_signal(s) or s.strip() in WHITELIST:
        return s, False, False

    name_field = is_name_field(key)
    lines = s.splitlines(keepends=True)
    out = []
    dropped = False
    had_url = bool(URL_RE.search(s) or WWW_RE.search(s) or DOMAIN_RE.search(s))

    for line in lines:
        body = line.rstrip('\r\n')
        ending = line[len(body):]
        if not body:
            out.append(line)
            continue
        if not has_ad_signal(body):
            # 广告行被拆散后可能留下 "http"、域名碎片之类的残片
            if body.strip().lower() in JUNK_TOKENS or not meaningful(body):
                dropped = True
                continue
            if (not name_field and looks_fragment(body)
                    and not LRC_RE.match(body) and not HAS_CJK.search(body)):
                dropped = True
                continue
            out.append(line)
            continue
        cleaned = clean_line(body)
        if not meaningful(cleaned):
            dropped = True
            continue
        if cleaned.lower() in JUNK_TOKENS or cleaned in PROMO_WORDS:
            dropped = True
            continue
        # 注释类字段：整行都是推广话术就删掉
        if not name_field:
            if had_url and any(w in cleaned for w in PROMO_WORDS):
                dropped = True
                continue
            if looks_fragment(cleaned):
                dropped = True
                continue
        else:
            if had_url and any(w in cleaned for w in NAME_PROMO):
                dropped = True
                continue
            if any(0x80 <= ord(c) <= 0xFF for c in cleaned) and not re.search(r'[\u4e00-\u9fff]', cleaned):
                dropped = True
                continue
        out.append(cleaned + ending)

    # 去掉开头/结尾空行
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    new = ''.join(out)
    new = re.sub(r'\n{3,}', '\n\n', new)
    if not new.strip():
        return '', True, True
    if new != s:
        return new, True, False
    return s, False, False


def txxx_bad(key):
    if not str(key).upper().startswith('TXXX:'):
        return False
    desc = str(key).split(':', 1)[1]
    return has_ad_signal(desc) or desc in JUNK_FIELDS


def iter_files(root):
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith(('.mp3', '.mpga', '.ape')):
                yield os.path.join(dp, f)


def process(path, apply):
    changes = []
    low = path.lower()
    if low.endswith(('.mp3', '.mpga')):
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            return changes, None
        except Exception as e:
            return changes, repr(e)
        for key in list(t.keys()):
            if txxx_bad(key):
                changes.append((key, str(t[key]), '<删除字段>'))
                if apply:
                    del t[key]
                continue
            fr = t[key]
            texts = getattr(fr, 'text', None)
            if not isinstance(texts, list):
                continue
            new_texts = []
            changed = False
            for s in texts:
                if not isinstance(s, str):
                    new_texts.append(s)
                    continue
                new, ch, dele = clean_value(s, key)
                if ch:
                    changed = True
                    changes.append((key, s, '<删除>' if dele else new))
                    if not dele:
                        new_texts.append(new)
                else:
                    new_texts.append(s)
            if changed and apply:
                if not new_texts or all(not str(x).strip() for x in new_texts):
                    del t[key]
                else:
                    fr.text = new_texts
        if changes and apply:
            try:
                t.update_to_v24()
                for fr in t.values():
                    if hasattr(fr, 'encoding'):
                        fr.encoding = Encoding.UTF8
                t.save(v2_version=4)
            except Exception as e:
                return changes, repr(e)
    else:
        try:
            a = APEv2(path)
        except Exception:
            return changes, None
        for key in list(a.keys()):
            v = a[key]
            if isinstance(v, APEBinaryValue):
                continue
            s = str(v)
            new, ch, dele = clean_value(s, key)
            if ch:
                changes.append((key, s, '<删除>' if dele else new))
                if apply:
                    if dele:
                        del a[key]
                    else:
                        v.value = new
        if changes and apply:
            try:
                a.save()
            except Exception as e:
                return changes, repr(e)
    return changes, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--log', default=None)
    ap.add_argument('--sample', type=int, default=40)
    args = ap.parse_args()

    total_files = 0
    hit_files = 0
    deleted = 0
    modified = 0
    rows = []
    errors = []
    field_count = collections.Counter()
    for p in iter_files(args.root):
        total_files += 1
        ch, err = process(p, args.apply)
        if err:
            errors.append((p, err))
        if ch:
            hit_files += 1
            for key, old, new in ch:
                rows.append({'file': p, 'field': str(key), 'old': old, 'new': new})
                field_count[str(key)] += 1
                if new.startswith('<删除'):
                    deleted += 1
                else:
                    modified += 1

    print(f'扫描 {total_files} 个文件，命中 {hit_files} 个')
    print(f'删除 {deleted} 处，修改 {modified} 处')
    print('按字段:', field_count.most_common(15))
    print(f'错误 {len(errors)}')
    for e in errors[:10]:
        print('  ERR', e)

    if args.log:
        with open(args.log, 'w', encoding='utf-8') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        print('日志:', args.log)

    print('\n样例:')
    for r in rows[:args.sample]:
        old = r['old'] if len(r['old']) < 120 else r['old'][:117] + '...'
        new = r['new'] if len(r['new']) < 120 else r['new'][:117] + '...'
        print(f"  [{r['field']}] {old!r}")
        print(f"      -> {new!r}")


if __name__ == '__main__':
    main()
