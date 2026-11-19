#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 foobar2000 中音乐标签/索引文件乱码。

原理：大量老文件里的中文是 GBK 字节被当成 Latin-1 解码后，
再以 Unicode(UTF-16) / UTF-8 存进标签，属于"双重编码"。
本脚本按"连续高位字节串"识别这种模式，还原成正确中文，
并统一写回 UTF-8（ID3 升级到 v2.4）。

用法:
  python3 fix_music_encoding.py /path/to/Music          # 预演，只出报告
  python3 fix_music_encoding.py /path/to/Music --apply  # 实际写入
选项:
  --only id3,ape,cue,m3u    只处理某几类
  --workers N               并发数（默认 8）
  --sample N                预演时打印多少条样例（默认 30）
"""

import os
import sys
import json
import time
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor

# ---------- 乱码识别 ----------

CP1252_EXTRA = {
    "\u20ac": 0x80, "\u201a": 0x82, "\u0192": 0x83, "\u201e": 0x84,
    "\u2026": 0x85, "\u2020": 0x86, "\u2021": 0x87, "\u02c6": 0x88,
    "\u2030": 0x89, "\u0160": 0x8a, "\u2039": 0x8b, "\u0152": 0x8c,
    "\u017d": 0x8e, "\u2018": 0x91, "\u2019": 0x92, "\u201c": 0x93,
    "\u201d": 0x94, "\u2022": 0x95, "\u2013": 0x96, "\u2014": 0x97,
    "\u02dc": 0x98, "\u2122": 0x99, "\u0161": 0x9a, "\u203a": 0x9b,
    "\u0153": 0x9c, "\u017e": 0x9e, "\u0178": 0x9f,
}


def _is_high(ch):
    # 只把 U+0080..U+00FF 当作“被当成 Latin-1 的字节”。
    # 不能把 “—”“……” 等 CP1252 标点算进来，否则会误伤正常中文标点。
    return 0x80 <= ord(ch) <= 0xFF


def _to_bytes(chars):
    b = bytearray()
    for ch in chars:
        o = ord(ch)
        if o < 0x80:
            return None
        if o <= 0xFF:
            b.append(o)
        else:
            return None
    return bytes(b)


def _looks_text(s):
    for ch in s:
        o = ord(ch)
        if o < 0x20 and ch not in "\r\n\t":
            return False
    return True


def _fix_run(raw):
    """一段连续高位字节 -> 还原文本，失败返回 None"""
    if not raw:
        return None
    # 1) UTF-8 字节被当成 Latin-1：还原成真正的 UTF-8 文本
    try:
        s = raw.decode("utf-8")
        if s and _looks_text(s):
            has_cjk = any("\u4e00" <= c <= "\u9fff" for c in s)
            has_latin = any(0x80 <= ord(c) <= 0x24F for c in s)
            if has_cjk or has_latin:
                return s
    except UnicodeDecodeError:
        pass
    # 2) GBK 双字节
    if len(raw) >= 2 and len(raw) % 2 == 0:
        try:
            s = raw.decode("gbk")
        except UnicodeDecodeError:
            return None
        if not _looks_text(s):
            return None
        cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
        if cjk >= 1 and cjk * 2 >= len(raw) * 0.5:
            return s
    return None


def _is_allowed_result_char(ch):
    o = ord(ch)
    if o < 0x80:
        return True
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return True
    if 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF:
        return True
    # 中文标点 / 全角字符
    if 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
        return True
    # 只放行常见的通用标点，不能整段放行（‚ ˜ Œ 之类是乱码残渣）
    if ch in "—–…‘’“”•‥‰′″‹›※⁄":
        return True
    if ch in "°℃€£¥©®™№±×÷":
        return True
    return False


def fix_text(s):
    """整串修复；只有所有高位字节段都可还原时才改，避免误伤西文重音"""
    if not isinstance(s, str) or not s:
        return None
    runs = []
    cur = []
    for ch in s:
        if _is_high(ch):
            cur.append(ch)
        else:
            if cur:
                runs.append("".join(cur))
                cur = []
    if cur:
        runs.append("".join(cur))
    if not runs:
        return None

    fixed_runs = []
    for run in runs:
        raw = _to_bytes(run)
        if raw is None:
            return None
        fixed = _fix_run(raw)
        if fixed is None:
            return None
        fixed_runs.append(fixed)
    if not fixed_runs:
        return None

    # 按出现顺序替换各段
    out = []
    pos = 0
    i = 0
    n = len(s)
    while i < n:
        if _is_high(s[i]):
            j = i
            while j < n and _is_high(s[j]):
                j += 1
            out.append(fixed_runs[pos])
            pos += 1
            i = j
        else:
            out.append(s[i])
            i += 1
    result = "".join(out)
    if result == s:
        return None
    # 结果里若还残留无法解释的 Latin-1/扩展拉丁字符，说明是残缺乱码，放弃
    for ch in result:
        if not _is_allowed_result_char(ch):
            return None
    return result


# ---------- APEv2 宽容解析补丁 ----------

def patch_mutagen_ape():
    import mutagen.apev2 as ape
    from mutagen.apev2 import APEBadItemError, _get_value_type
    from mutagen._util import cdata
    import io
    import sys as _sys

    def _utf8_lenient(self, data):
        try:
            self.value = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                self.value = data.decode("gb18030")
            except UnicodeDecodeError:
                self.value = data.decode("utf-8", "replace")

    ape._APEUtf8Value._parse = _utf8_lenient

    # 允许非 ASCII 的键（老工具写的中文键），保存时由 mutagen 写成 UTF-8
    ape.is_valid_apev2_key = lambda k: isinstance(k, str) and bool(k) and "\x00" not in k

    def _parse_tag(self, tag, count):
        fileobj = io.BytesIO(tag)
        for _ in range(count):
            head = fileobj.read(8)
            if not head:
                break
            if len(head) != 8:
                raise APEBadItemError("truncated item header")
            size = cdata.uint32_le(head[:4])
            flags = cdata.uint32_le(head[4:8])
            kind = (flags & 6) >> 1
            if kind == 3:
                raise APEBadItemError("value type must be 0, 1, or 2")
            key = fileobj.read(1)
            if not key:
                raise APEBadItemError
            while key[-1:] != b"\x00":
                nxt = fileobj.read(1)
                if not nxt:
                    raise APEBadItemError
                key += nxt
            key = key[:-1]
            try:
                k = key.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    k = key.decode("gb18030")
                except UnicodeDecodeError:
                    k = key.decode("latin-1")
            value = fileobj.read(size)
            if len(value) != size:
                raise APEBadItemError
            value = _get_value_type(kind)._new(value)
            self[k] = value

    ape.APEv2._APEv2__parse_tag = _parse_tag


KEY_MAP = {
    "标题": "Title", "歌名": "Title", "曲名": "Title", "名称": "Title",
    "歌手": "Artist", "艺术家": "Artist", "演唱": "Artist", "演唱者": "Artist",
    "专辑": "Album", "专集": "Album", "唱片": "Album", "专辑名": "Album",
    "专辑艺术家": "Album Artist", "专辑歌手": "Album Artist",
    "音轨": "Track", "轨道": "Track", "曲目": "Track", "音轨号": "Track",
    "碟片": "Disc", "光盘": "Disc", "碟号": "Disc",
    "年份": "Year", "年代": "Year", "发行年份": "Year",
    "日期": "Date", "发行日期": "Date",
    "流派": "Genre", "风格": "Genre",
    "注释": "Comment", "备注": "Comment", "说明": "Comment",
    "版权": "Copyright", "作词": "Lyricist", "作曲": "Composer",
    "编曲": "Arranger", "制作人": "Producer", "指挥": "Conductor",
    "唱片公司": "Label", "厂牌": "Label", "介质": "Media", "编码": "Encoder",
}


def normalize_ape_key(k):
    # 键本身也可能是 UTF-8 被当成 GBK 的乱码
    if k and not all(0x20 <= ord(c) < 0x7F for c in k):
        try:
            cand = k.encode("gb18030").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            cand = None
        if cand and any("\u4e00" <= c <= "\u9fff" for c in cand):
            k = cand
    if k in KEY_MAP:
        return KEY_MAP[k]
    return k


# ---------- 处理函数 ----------

def process_id3(path, apply):
    from mutagen.id3 import ID3, Encoding, ID3NoHeaderError

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return {"path": path, "kind": "id3", "changes": []}
    except Exception as e:
        return {"path": path, "kind": "id3", "error": repr(e), "changes": []}
    changes = []
    for key in list(tags.keys()):
        frame = tags[key]
        # 文本值
        text = getattr(frame, "text", None)
        if isinstance(text, list):
            new_text = []
            for item in text:
                if isinstance(item, str):
                    fixed = fix_text(item)
                    if fixed is not None:
                        changes.append({"file": path, "kind": "id3", "field": key,
                                        "old": item, "new": fixed})
                        new_text.append(fixed)
                        continue
                new_text.append(item)
            frame.text = new_text
        # 描述字段
        desc = getattr(frame, "desc", None)
        if isinstance(desc, str):
            fixed = fix_text(desc)
            if fixed is not None:
                changes.append({"file": path, "kind": "id3", "field": key + ".desc",
                                "old": desc, "new": fixed})
                frame.desc = fixed

    if not changes:
        return {"path": path, "kind": "id3", "changes": []}

    if apply:
        try:
            tags.update_to_v24()
            for frame in tags.values():
                if hasattr(frame, "encoding"):
                    frame.encoding = Encoding.UTF8
            tags.save(v2_version=4)
        except Exception as e:
            return {"path": path, "kind": "id3", "changes": changes, "error": repr(e)}
    return {"path": path, "kind": "id3", "changes": changes}


def process_ape(path, apply):
    from mutagen.apev2 import APEv2, APENoHeaderError, APEBinaryValue

    try:
        tag = APEv2(path)
    except APENoHeaderError:
        return {"path": path, "kind": "ape", "changes": []}
    except Exception as e:
        return {"path": path, "kind": "ape", "error": repr(e), "changes": []}

    changes = []
    new_items = {}
    for key, value in list(tag.items()):
        nk = normalize_ape_key(key)
        if nk != key:
            changes.append({"file": path, "kind": "apekey", "field": key,
                            "old": key, "new": nk})
        # 只处理文本类
        if isinstance(value, APEBinaryValue):
            new_items[nk] = value
            continue
        try:
            s = str(value)
        except Exception:
            new_items[nk] = value
            continue
        parts = s.split("\x00")
        fixed_parts = []
        for part in parts:
            fixed = fix_text(part)
            if fixed is not None:
                changes.append({"file": path, "kind": "ape", "field": key,
                                "old": part, "new": fixed})
                fixed_parts.append(fixed)
            else:
                fixed_parts.append(part)
        fixed_s = "\x00".join(fixed_parts).rstrip("\x00")
        if fixed_s != s:
            value.value = fixed_s
        new_items[nk] = value

    if not changes:
        return {"path": path, "kind": "ape", "changes": []}
    if apply:
        try:
            tag.clear()
            for k, v in new_items.items():
                tag[k] = v
            tag.save()
        except Exception as e:
            return {"path": path, "kind": "ape", "changes": changes, "error": repr(e)}
    return {"path": path, "kind": "ape", "changes": changes}


def decode_text_file(raw):
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "big5"):
        try:
            s = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
        if cjk >= 3:
            return s, enc
    return None, None


def process_textfile(path, apply):
    with open(path, "rb") as f:
        raw = f.read()
    s, enc = decode_text_file(raw)
    if s is None or enc == "utf-8":
        return {"path": path, "kind": "text", "changes": []}
    # 清理 UTF-8 BOM 之外的异常
    out = s.encode("utf-8")
    change = {"file": path, "kind": os.path.splitext(path)[1].lower().lstrip("."),
              "field": "encoding", "old": enc, "new": "utf-8"}
    if apply:
        try:
            with open(path, "wb") as f:
                f.write(out)
        except Exception as e:
            return {"path": path, "kind": "text", "changes": [change], "error": repr(e)}
    return {"path": path, "kind": "text", "changes": [change]}


def handle_file(path, apply, kinds):
    ext = os.path.splitext(path)[1].lower()
    results = []
    if ext in (".mp3", ".mpga") and "id3" in kinds:
        results.append(process_id3(path, apply))
        if "ape" in kinds:
            results.append(process_ape(path, apply))
    elif ext == ".ape" and "ape" in kinds:
        results.append(process_ape(path, apply))
    elif ext in (".cue", ".m3u", ".m3u8") and "text" in kinds:
        results.append(process_textfile(path, apply))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="id3,ape,text")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    kinds = set(x.strip().lower() for x in args.only.split(",") if x.strip())

    patch_mutagen_ape()

    files = []
    for dp, dn, fn in os.walk(args.root):
        for f in fn:
            ext = os.path.splitext(f)[1].lower()
            if ext in (".mp3", ".mpga", ".ape", ".cue", ".m3u", ".m3u8"):
                files.append(os.path.join(dp, f))
    files.sort()
    print(f"待扫描 {len(files)} 个文件，模式={'写入' if args.apply else '预演'}")

    lock = threading.Lock()
    done = [0]
    results = []

    def work(p):
        try:
            r = handle_file(p, args.apply, kinds)
        except Exception as e:
            r = [{"path": p, "kind": "fatal", "error": repr(e), "changes": []}]
        with lock:
            done[0] += 1
            if done[0] % 200 == 0:
                print(f"  ... {done[0]}/{len(files)}")
        return r

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(work, files):
            results.extend(r)

    changes = [c for r in results for c in r.get("changes", [])]
    errors = [r for r in results if r.get("error")]
    by_kind = {}
    for c in changes:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    affected = len({c["file"] for c in changes})

    print(f"\n完成，用时 {time.time()-t0:.0f}s")
    print(f"受影响文件 {affected}，变更 {len(changes)} 条 -> {by_kind}")
    print(f"错误 {len(errors)}")
    for e in errors[:20]:
        print("  ERR", e["path"], e.get("error"))

    if args.log:
        with open(args.log, "w", encoding="utf-8") as f:
            for c in changes:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print("变更日志:", args.log)

    print("\n样例:")
    seen = 0
    for c in changes:
        if seen >= args.sample:
            break
        print(f"  [{c['kind']}] {os.path.basename(c['file'])} | {c['field']}: "
              f"{c['old']!r} -> {c['new']!r}")
        seen += 1


if __name__ == "__main__":
    main()
