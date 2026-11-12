#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解包后体检 HedEx 文档包：结构 / 编码 / 目录树 / 重复主题。
用法: inspect_hdx.py <解压目录>
"""
import sys, os, re, glob, collections
import xml.etree.ElementTree as ET

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/hdx_extract"
RES = os.path.join(SRC, "resources")

prof = ET.parse(os.path.join(SRC, "profile.xml")).getroot()
print("== profile ==")
for t in ("libName", "productType", "productVersion", "libVersion", "issueDate", "libId", "topicNumber"):
    e = prof.find(t)
    print(f"  {t:16s} {(e.text or '').strip() if e is not None else '-'}")

files = sorted(glob.glob(os.path.join(RES, "zh-cn_topic_*.html")))
print(f"\n== 正文 ==\n  topic 文件 {len(files)} 个")

kinds = collections.Counter()
bad_enc = 0
for f in files[:400]:
    raw = open(f, "rb").read()
    try:
        t = raw.decode("gb18030")
    except UnicodeDecodeError:
        bad_enc += 1
        t = raw.decode("gb18030", "replace")
    b = re.search(r"<body[^>]*>(.*)</body>", t, re.S)
    inner = b.group(1) if b else ""
    m = re.search(r'<div class="([^"]*)"', inner)
    kinds[m.group(1) if m else "NONE"] += 1
print("  抽样 400 篇首层 div:", dict(kinds.most_common(5)))
print("  非 gb18030 篇数:", bad_enc)
print("  含 footerNavBar 的篇数:",
      sum(1 for f in files[:300] if b"footerNavBar" in open(f, "rb").read()), "/300")
print("  官方打印样式:", "有" if os.path.exists(
    os.path.join(RES, "public_sys-resources", "commonltr_print.css")) else "缺失")

navi = os.path.join(RES, "navi.xml")
if not os.path.exists(navi):
    print("\n[!] 没有 navi.xml —— 该包结构不同，需要另找目录来源（hhc / hhp）")
    sys.exit(0)

rows = []
def walk(n, d):
    for c in n.findall("topic"):
        rows.append((c.get("txt"), c.get("url"), d))
        walk(c, d + 1)
walk(ET.parse(navi).getroot(), 1)

dep = collections.Counter(d for _, _, d in rows)
urls = collections.Counter(u for _, u, _ in rows)
dup = {u: n for u, n in urls.items() if n > 1}
missing = [u for u in urls if not os.path.exists(os.path.join(RES, u or ""))]

print(f"\n== 目录树 navi.xml ==")
print(f"  节点 {len(rows)}  唯一 url {len(urls)}  深度 {max(dep)} 级")
print(f"  深度分布 {dict(sorted(dep.items()))}")
print(f"  重复引用 {len(dup)} 个 url（正文会去重，目录照列）")
for u, n in list(dup.items())[:5]:
    print(f"    x{n}  {u}")
print(f"  缺失文件 {len(missing)}")
if missing:
    for u in missing[:5]:
        print("    !", u)

# PDF 附件型主题：绝不能当 HTML 解码，否则 WeasyPrint 会栈溢出
att = []
for txt, u, d in rows:
    if not u:
        continue
    fp = os.path.join(RES, u)
    if os.path.exists(fp) and open(fp, "rb").read(4) == b"%PDF":
        att.append((txt, u, d, os.path.getsize(fp)))
if att:
    print(f"\n[!] PDF 附件型主题 {len(att)} 个（正文需放说明页，原件合并到文末附录）:")
    for txt, u, d, sz in att:
        print(f"    [{d}] {txt}  {sz/1024/1024:.1f}MB  {u}")
else:
    print("\n  PDF 附件型主题: 无")

# 长图预检：这些图会按页宽缩放后超出纸面，渲染完必须逐张翻页核对
# 注意 navi 里存的是 .html，图片得从 topic 正文里解析 <img src>
try:
    import re as _re
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    _img = _re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', _re.I)
    longs, _seen = [], set()
    for txt, u, d in rows:
        if not u or not u.lower().endswith(".html"):
            continue
        fp = os.path.join(RES, u)
        if not os.path.exists(fp):
            continue
        try:
            body = open(fp, "rb").read().decode("gb18030", "replace")
        except Exception:
            continue
        for src in _img.findall(body):
            if src.startswith(("http", "data:")) or src in _seen:
                continue
            _seen.add(src)
            ip = os.path.join(RES, src)
            if not os.path.exists(ip):
                continue
            try:
                w, h = Image.open(ip).size
            except Exception:
                continue
            if w < 1200:
                continue
            if h / w > 2.0:                      # 会被切块
                longs.append((h / w, w, h, txt, src, "切块"))
            elif h / w > 690 / 523.6875:         # 竖长图，靠 max-height 约束
                longs.append((h / w, w, h, txt, src, "限高"))
    if longs:
        longs.sort(reverse=True)
        print(f"\n[!] 长图 {len(longs)} 张（渲染后务必逐页核对，别只看总页数）:")
        for r, w, h, txt, src, act in longs:
            print(f"    1:{r:4.2f}  {w}x{h}  [{act}]  {txt}")
            print(f"              {src}")
    else:
        print("\n  长图: 无")
except Exception as e:
    print(f"\n  [长图预检跳过] {e}")

print("\n[提示] 深度超过脚本已定义的 lvN 时，需补 `.lvN h1.topicTitle-h1{bookmark-level:N}`")
