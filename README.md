# music-tag-encoding-fixer

修复音乐库中因历史编码问题导致的中文/日文标签乱码，统一写回 UTF-8。

A small toolkit that repairs mojibake in music tags and `.cue` sheets and rewrites them as UTF-8. It was built for a large Chinese music library that displayed garbled text in foobar2000.

## 问题现象

```
Ê®Ò»ÔÂµÄÏô°î   ->  十一月的萧邦
³Â°ÙÇ¿         ->  陈百强
Í¯»°           ->  童话
```

这类乱码通常不是文件“不是 UTF-8”，而是**老工具把 GBK 字节当成 Latin-1 解码后，再存成 Unicode**（双重编码）。只看字节无法判断，必须按“连续高位字节段”还原，并排除正常的西文重音（`Hélène`、`Björk`、`Jörgen`）。

## 功能

- **第一遍 `fix_music_encoding.py`**：处理最常见的 GBK 双重编码
  - MP3 ID3v2：还原文本后升级为 **ID3v2.4 + UTF-8**
  - APEv2：字段值转 UTF-8，中文键名（标题/歌手/专辑…）映射为标准键
  - `.cue` / `.m3u`：GBK 转 UTF-8
- **第二遍 `scan_legacy.py` + `apply_legacy.py`**：处理 Big5 / Shift-JIS / GBK 全角等老编码
- **第三遍 `scan_accent.py` + `apply_legacy.py`**：处理 `é è í ó` 等被双重编码的重音字母

所有扫描默认只输出报告，只有 `--apply` 或显式执行 `apply_legacy.py` 才会写文件。

## 依赖

```bash
python3 -m pip install --user -r requirements.txt
```

- Python 3.8+
- [mutagen](https://mutagen.readthedocs.io/) >= 1.45

## 使用

> 建议先备份标签（或整个目录），并在小范围目录上试跑。

### 1. GBK 双重编码

```bash
# 预演，只生成报告
python3 fix_music_encoding.py /path/to/Music --log dryrun.jsonl

# 确认无误后写入
python3 fix_music_encoding.py /path/to/Music --apply --log applied.jsonl
```

### 2. Big5 / Shift-JIS / 全角

```bash
python3 scan_legacy.py /path/to/Music legacy.json
# 先看 legacy.json 里的候选，再执行
python3 apply_legacy.py legacy.json
```

### 3. 重音字母

```bash
python3 scan_accent.py /path/to/Music accent.json
python3 apply_legacy.py accent.json
```

最后重新跑一遍三组扫描，应该都输出 `0` 个候选。

## 输出

- `dryrun.jsonl` / `applied.jsonl`：每条变更的 `文件 / 类型 / 字段 / 旧值 / 新值`
- `legacy.json` / `accent.json`：候选及置信度、备选编码
- 日志为 UTF-8，可直接转成表格查看或用于回滚

## 在 foobar2000 中刷新

foobar 会缓存标签，修改后需要：

1. 全选歌曲
2. 右键 → **Tagging → Reload info from file(s)**
3. 或从媒体库移除目录后重新添加

## 说明与限制

- 音频数据不会被改写，只修改标签区和 `.cue` 文本
- 无法还原字节已丢失的字段（例如已经变成 `?` 的内容），这类会留在报告里
- 升级到 ID3v2.4 后，极老的硬件播放器可能不识别；如需兼容可改回 v2.3 + UTF-16
- 脚本按“宁可漏修，不可错改”设计：正常西文重音会被保留

## License

MIT
