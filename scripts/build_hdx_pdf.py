#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Huawei HedEx (.hdx) -> 带书签/目录页码的单本 PDF
用法: build_hdx_pdf.py <解压目录> <输出.pdf> [--limit N]
"""
import sys, os, re, glob, time, math, html as ihtml
import xml.etree.ElementTree as ET

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/hdx_extract"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/out.pdf"
LIMIT = 0
OFFSET = 0
REUSE_HTML = "--reuse-html" in sys.argv   # 复用已生成的 HTML，只重跑渲染阶段
if "--limit" in sys.argv:
    LIMIT = int(sys.argv[sys.argv.index("--limit") + 1])
if "--offset" in sys.argv:
    OFFSET = int(sys.argv[sys.argv.index("--offset") + 1])

RES = os.path.join(SRC, "resources")

# ---------- 1. profile ----------
prof = ET.parse(os.path.join(SRC, "profile.xml")).getroot()
def g(tag, d=""):
    e = prof.find(tag)
    return (e.text or d).strip() if e is not None else d
LIB_NAME = g("libName", "产品文档")
PRODUCT = g("productType", "Product")
PVERSION = g("productVersion", "")
LIBVER = g("libVersion", "")
ISSUE = g("issueDate", "")
LIBID = g("libId", "")

_m = re.match(r"^(.*?)\s*产品文档\s*(\([^)]*\))?\s*$", LIB_NAME)
if _m and _m.group(1).strip():
    COVER_MODEL = _m.group(1).strip()
    _note = (_m.group(2) or "").replace("(", "（").replace(")", "）")
    COVER_DOC = "产品文档" + _note
else:
    COVER_MODEL = LIB_NAME
    COVER_DOC = ""


# ---------- 2. 目录树 ----------
nodes = []  # (title, url, depth)
def walk(parent, depth):
    for c in parent.findall("topic"):
        nodes.append((c.get("txt") or "", c.get("url") or "", depth))
        walk(c, depth + 1)
walk(ET.parse(os.path.join(RES, "navi.xml")).getroot(), 1)
if OFFSET or LIMIT:
    nodes = nodes[OFFSET:(OFFSET + LIMIT) if LIMIT else None]
print(f"[i] 目录节点 {len(nodes)} 个, 深度 {max(d for _,_,d in nodes)}")

# ---------- 表格列宽注入 ----------
# 官方 CSS 只有 `table{width:100%}`，没有 table-layout。auto 布局按「内容最小宽度」
# 分配列宽，列一多、单元格里又是长中文词组/长命令，总宽就突破 100%，整表右侧被裁
# （实测 8.9 版 207-213 页「表1 VRM节点规格要求」5 列，第 5 列整列丢到纸外）。
#
# 但直接上 `table-layout:fixed` 也不行：fixed 下若无 col 宽度，WeasyPrint 会把
# 各列均分，长文字列被压成一条竖线，中文竖着排 —— 比溢出还难看。
#
# 正解：fixed 布局 + 按内容长度加权注入 <colgroup><col style="width:N%">。
# 每列权重 = 该列所有单元格文本长度（封顶 3 倍均值，避免某一列独吞宽度）。
TABLE_RE = re.compile(r"(<table\b[^>]*>)(.*?)</table>", re.S | re.I)
ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t([hd])\b[^>]*>(.*?)</t\1>", re.S | re.I)
COLS_RE = re.compile(r"<colgroup\b.*?</colgroup>", re.S | re.I)


def _textlen(fragment):
    return len(re.sub(r"<[^>]+>", "", fragment or "").strip())


SPAN_RE = re.compile(r'colspan\s*=\s*["\']?(\d+)', re.I)
WIDTH_RE = re.compile(r'\bwidth\s*=\s*["\']?\s*([\d.]+)\s*%', re.I)


def _cell_span(open_attrs):
    m = SPAN_RE.search(open_attrs or "")
    try:
        return max(1, int(m.group(1))) if m else 1
    except Exception:
        return 1


def _native_widths(body):
    """尝试从 td/th 自带的 width="N%" 属性取官方列宽。

    Huawei 的 DITA 导出**几乎每张表都自带列宽**（实测 1225 张表里 1224 张有），
    这是作者在文档工具里真正排过的比例，权威性远高于任何自动推断。
    只有取不到时才回退到「按内容长度加权」。
    返回 None 表示这个表没有可用宽度。
    """
    rows = ROW_RE.findall(body)
    for r in rows:
        cells = re.findall(r"<t([hd])\b([^>]*)>", r, re.I)
        if not cells:
            continue
        ws, ok = [], True
        for _tag, attrs in cells:
            m = WIDTH_RE.search(attrs)
            if not m:
                ok = False
                break
            span = _cell_span(attrs)
            w = float(m.group(1))
            # colspan 的单元格：宽度摊给每格
            ws.extend([w / span] * span)
        if ok and ws and 90 <= sum(ws) <= 110:
            return ws
    return None


def _inject_colgroup(m):
    """给表格注入 <colgroup>，让 table-layout:fixed 有列宽可依。

    优先用作者自带的 width 属性；没有才按内容长度加权推算。

    两个坑（都实测踩过）：
    1. **必须处理 colspan**：参数表用合并单元格做分组标题（该行只有 1 个 td 但
       colspan=2）。按固定下标算列会让后面每行错位一格，权重全垮。
    2. **别用自己推算的宽度去覆盖原生 width**：8.9 版「POD标签和注解」那张表
       原生是 19.98/19.97/60.05，被加权算法改成 15.66/68.50/15.84，结果中间
       巨宽列没内容、两边窄列把中文压成一字一行。
    """
    open_tag = m.group(1)               # 形如 "<table class='x'>"
    body = m.group(2)
    if COLS_RE.search(m.group(0)):      # 已有 colgroup 的不动
        return m.group(0)
    rows = ROW_RE.findall(body)
    if not rows:
        return m.group(0)

    # ① 首选：作者原生 width
    pcts = _native_widths(body)
    if pcts and len(pcts) >= 2:
        total = sum(pcts) or 1
        pcts = [p / total * 100 for p in pcts]
        cols = "".join('<col style="width:%.2f%%">' % p for p in pcts)
        return '%s<colgroup>%s</colgroup>%s</table>' % (open_tag, cols, body)

    # ② 回退：按内容长度加权

    # 逐行展开成「列下标 -> 文本长度」，colspan 占多格
    grid = []
    for r in rows:
        cells = re.findall(r"(<t([hd])\b[^>]*>)(.*?)</t\2>", r, re.S | re.I)
        if not cells:
            continue
        line = {}
        col = 0
        for open_cell, _tag, inner in cells:
            span = _cell_span(open_cell)
            n = _textlen(inner)
            for k in range(span):
                # 合并单元格的内容摊到它占的每一格，避免它把某一列撑爆
                line[col + k] = max(line.get(col + k, 0), n // span if span > 1 else n)
            col += span
        if line:
            grid.append(line)

    if not grid:
        return m.group(0)
    ncol = max(max(g.keys()) + 1 for g in grid)
    if ncol < 2:                        # 单列表格不需要分配
        return m.group(0)

    # 每列权重 = 该列所有单元格里的最大文本长度
    weights = []
    for c in range(ncol):
        vals = [g[c] for g in grid if c in g]
        weights.append(max(vals) if vals else 1)

    # 封顶 / 垫底，防止某一列独吞或某列被压成一条线
    avg = sum(weights) / ncol or 1
    floor = avg * 0.28
    weights = [min(max(w, floor), avg * 2.0) for w in weights]
    total = sum(weights) or 1
    pcts = [w / total * 100 for w in weights]

    # 每列下限随列数变化：列越多，单列能分到的越少，下限也得跟着降。
    # 5 列时 12%% ≈ 53pt（一行 2~3 字，可读）；2 列时给到 25%%，保证参数名列不竖排。
    low = max(12.0, 50.0 / ncol)
    pcts = [max(p, low) for p in pcts]
    scale = 100.0 / sum(pcts)
    pcts = [p * scale for p in pcts]
    cols = "".join('<col style="width:%.2f%%">' % p for p in pcts)
    return '%s<colgroup>%s</colgroup>%s</table>' % (open_tag, cols, body)


def _fit_tables(inner):
    return TABLE_RE.sub(_inject_colgroup, inner)


# ---------- 3. 单篇清洗 ----------
SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.S | re.I)
# 图片热区 <map>（内含几十条 <area> 外链）：PDF 里热区无意义，
# 而每条 area 都会被当链接渲染成块级元素，把图挤走 / 压垮版面。整块剥掉。
MAP_RE = re.compile(r"<map\b[^>]*>.*?</map>", re.S | re.I)
FOOTER_RE = re.compile(r'<div class="footerNavBar.*\Z', re.S | re.I)
BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.S | re.I)
HEAD_RE = re.compile(r"<head[^>]*>.*?</head>", re.S | re.I)
IMG_TAG_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"[^>]*>', re.I)
LINK_RE = re.compile(r'(<a\b[^>]*?\bhref=")([^"#]*?\.html)(#[^"]*)?"', re.I)

def strip_tags_keep_href(m):
    href, frag = m.group(2), m.group(3) or ""
    return f'{m.group(1)}#{anchor_id(href)}{frag}"'

def anchor_id(url: str) -> str:
    return "t-" + os.path.splitext(os.path.basename(url))[0]

def clean(url):
    p = os.path.join(RES, url)
    if not os.path.exists(p):
        return None, ""
    blob = open(p, "rb").read()
    if blob[:4] == b"%PDF":          # 附件型主题：原样是独立 PDF，绝不能当 HTML 解码
        return None, "pdf"
    raw = blob.decode("gb18030", "replace")
    b = BODY_RE.search(raw)
    inner = b.group(1) if b else raw
    inner = SCRIPT_RE.sub("", inner)
    inner = MAP_RE.sub("", inner)
    inner = FOOTER_RE.sub("", inner)
    inner = LINK_RE.sub(strip_tags_keep_href, inner)
    inner = _rewrite_long_images(inner)
    inner = _fit_tables(inner)
    return inner, ""


# ---------- 超长图切分 ----------
# HedEx 里"一页纸"类的信息图动辄 8000x20000px，高度是页宽的十几倍。
# 官方 CSS 对 img 没有任何尺寸约束，imgResize 是 JS 按窗口宽度算出来的，
# 所以屏幕上看没问题（浏览器的 <img width=523 height=1349> 只有宽度能约束、
# 高度会被版面裁掉或撑爆），一进固定 A4 纸就必然溢出纸外。
# 处理：按图内"分节红色标签带"切成若干块，每块单独一页，宽度贴页宽。
# 阈值取 2.0：实测 FusionCube 包里三张"一页纸"信息图为 2.24/2.57/2.58，
# 其余所有大图最高只有 1.54，断层明显。2.6 会漏掉 2.575 那张，别往上调。
LONG_IMAGE_RATIO = 2.0
# 版心宽 156mm、可用高 244mm（A4 减页边距）→ 一块的像素高宽比上限约 244/156 = 1.564。
# 块高按 1.50 取，留一点余量，这样按页宽缩放后正好铺满一页且不溢出。
SLICE_FACTOR = 1.50
_split_cache = {}


def _rewrite_long_images(inner):
    """把超长 <img> 换成若干个分页容器，每块独占一页"""
    def repl(m):
        url = m.group(1)
        if url.startswith(("http", "data:")):
            return m.group(0)
        names = _split_long_image(url)
        if not names:
            return m.group(0)
        segs = []
        for i, n in enumerate(names):
            cls = "lslicebox first" if i == 0 else "lslicebox"
            segs.append('<div class="%s"><img src="%s"></div>' % (cls, n))
        return "".join(segs)
    return IMG_TAG_RE.sub(repl, inner)


def _split_long_image(path):
    """返回切块文件名列表（相对 RES）；无需切分则返回 None"""
    if path in _split_cache:
        return _split_cache[path]
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        full = os.path.join(RES, path)
        im = Image.open(full).convert("RGB")
        w, h = im.size
        # 只处理真正的大幅信息图：宽度够 + 纵向足够长。
        # 否则 172x777 这类窄长图标会被误切（小图标本来就能正常放进一页）。
        if w < 1200 or h <= w * LONG_IMAGE_RATIO:
            _split_cache[path] = None
            return None
        # 纯按页面比例等分：整图高度 / (宽 * 页面高宽比) = 需要的页数。
        # 红色标签带在本类图上间距远小于一页（每节 2900~5900px，而一页要 12000px），
        # 拿它当切点会让每页只放半屏。等分反而能让每块铺满一页；
        # 万一切点落在标签上（很罕见），最多是该页顶部有半行标题，不影响可读性。
        target = w * SLICE_FACTOR
        n = max(1, math.ceil(h / target))   # 必须向上取整：不足一整页也不能「不切」
        step = h / n
        segs = [(int(k * step), int((k + 1) * step)) for k in range(n)]
        outdir = os.path.join(RES, "_split")
        os.makedirs(outdir, exist_ok=True)
        base = os.path.splitext(os.path.basename(path))[0]
        names = []
        for i, (s0, e0) in enumerate(segs):
            nm = "_split/%s_s%02d.png" % (base, i + 1)
            im.crop((0, max(0, s0), w, min(h, e0))).save(os.path.join(RES, nm), optimize=True)
            names.append(nm)
        print("[i] 超长图切分 %s  %dx%d -> %d 块" % (os.path.basename(path), w, h, len(names)))
        _split_cache[path] = names
        return names
    except Exception as e:
        print("[!] 切分失败 %s: %s" % (path, e))
        _split_cache[path] = None
        return None

# ---------- 4. CSS ----------
print_css = open(os.path.join(RES, "public_sys-resources", "commonltr_print.css"),
                 encoding="utf-8", errors="replace").read()
print_css = re.sub(r"^\ufeff", "", print_css)

MY_CSS = r"""
@page {
  size: A4;
  margin: 22mm 17mm 18mm 22mm;
  @top-left  { content: "%(product)s %(pver)s 产品文档"; font: 8.5pt "宋体"; color:#8a8a8a;
               vertical-align:bottom; padding-bottom:2mm; }
  @top-right { content: string(chapter); font: 8.5pt "宋体"; color:#8a8a8a; text-align:right;
               vertical-align:bottom; padding-bottom:2mm; }
  @bottom-center { content: counter(page) " / " counter(pages); font: 8.5pt "宋体"; color:#7a7a7a; }
}
@page cover { margin: 0; @top-left{content:none} @top-right{content:none} @bottom-center{content:none} }
@page toc   { @top-right{content:"目录"} }

html { font-size: 10.5pt; }
body { font-family: "宋体","Noto Serif CJK SC",serif; font-size:10.5pt; line-height:1.62;
       margin:0; margin-left:0; color:#1a1a1a; text-align:left; }
p { margin:.45em 0; orphans:2; widows:2; }
/* 正文里的超长无空格串（注册表路径、URL、命令行、证书 DN）必须允许断行。
   只给 table td/th 和 pre/code 加过 overflow-wrap 是不够的 —— 列表项里的
   注册表路径照样一路冲出纸外。实测 8.3.1 版 2158 页
   `HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\W32Time\\TimeProviders\\N...`
   整串被纸边切掉，读者永远看不到后半截。
   `anywhere` 只在「整词放不下」时才断，不会影响正常英文单词的换行。 */
p, li, dd, dt, blockquote, .p, .p1, .p2 { overflow-wrap:anywhere; }
a { color:#0b4f9e; text-decoration:none; }

/* 先关掉所有标题的默认书签，只让 topic 标题产生书签 */
h1,h2,h3,h4,h5,h6 { bookmark-level: none; }

/* 章节标题 —— 同时产出 PDF 书签与页眉串 */
h1.topicTitle-h1 { break-after: avoid; page-break-after: avoid; string-set: chapter content();
                   font-family:"黑体","Noto Sans CJK SC",sans-serif; border-bottom:.6pt solid #c8c8c8;
                   padding-bottom:3mm; margin:0 0 6mm 0; margin-left:0; font-size:19pt; line-height:1.35; }
.lv1 h1.topicTitle-h1 { bookmark-level:1; bookmark-label:content(); }
.lv2 h1.topicTitle-h1 { bookmark-level:2; bookmark-label:content(); }
.lv3 h1.topicTitle-h1 { bookmark-level:3; bookmark-label:content(); }
.lv4 h1.topicTitle-h1 { bookmark-level:4; bookmark-label:content(); }
.lv5 h1.topicTitle-h1 { bookmark-level:5; bookmark-label:content(); }
.lv6 h1.topicTitle-h1 { bookmark-level:6; bookmark-label:content(); }
.lv7 h1.topicTitle-h1 { bookmark-level:7; bookmark-label:content(); }
.lv8 h1.topicTitle-h1 { bookmark-level:8; bookmark-label:content(); }

H1_SECTION_PLACEHOLDER

h4.sectiontitle,h5.sectiontitle,h6.sectiontitle { font-family:"黑体","Noto Sans CJK SC",sans-serif;
    break-after: avoid; page-break-after: avoid; margin:12pt 0 4pt 0; margin-left:0; }

/* 表格：官方 CSS 只有 `table{width:100%%}`，没有 table-layout。
   auto 布局下浏览器/WeasyPrint 按「内容最小宽度」分配列宽，一旦列多、单元格里
   是长中文词组或长命令，总宽就会突破 100%%，整表右侧被裁到纸外（实测 8.9 版
   207-213 页「表1 VRM节点规格要求」5 列，第 5 列整列丢失）。
   必须用 fixed 布局 + 100%% 宽，列宽才会被硬压进版心。 */
table { width:100%%; max-width:100%%; table-layout:fixed; border-collapse:collapse;
        margin:6pt 0; font-size:9.5pt; }
/* fixed 布局下长串不折断会顶破单元格，故 td/th 允许断行。
   两个属性要一起用，缺一不可：
   - `word-break:break-word` 管中文与常规断行；
   - `overflow-wrap:anywhere` 管「一整串没有空格的 Latin 串」（LDAP DN、路径、
     长命令）。只有 break-word 时，`cn=username,ou=people,dc=example,dc=com`
     这种串不会断，整行顶出纸外约 2mm（实测 8.9 版 2347 页）。
   **前提**：必须在 `table-layout:fixed` + 注入 `<colgroup>` 之后才能这么写。
   早先只在 auto 布局下加 `overflow-wrap:anywhere`，WeasyPrint 会按「最小宽度=
   一个字符」均分列，5 列全变竖排细条 —— 那时的问题不是这个属性错，是布局没定死。 */
table td, table th { border:.4pt solid #999; padding:3pt 4pt; vertical-align:top;
                     word-break:break-word; overflow-wrap:anywhere; }
table th { background:#f0f0f0; font-weight:bold; }
/* 单元格里的列表和注释框，缩进必须收窄。
   踩坑实录：FusionCube 8.3.1 有一张 6 列账户表，第 5 列（26.45%%）装的是
   `<ol>` + `<div class="note">` 的长列表，整行比一页还高。跨页拆分后，
   ol 的默认左缩进（约 40px）+ note 内边距把该列内部吃光，正文只剩一个字符宽，
   渲染出来是一列竖排单字，还顶出纸外 10.9pt；同时 WeasyPrint 在这条
   被撑爆的列里额外画出一条竖线，整表因此变成 7 列、宽出内容区 10%%。
   把这几个缩进压到 1.2em 后，6 列恢复正确比例、页数从 9 页降到 4 页。
   **只要列宽是硬约束，单元格内的任何固定缩进都是隐患** —— 它们不会被
   缩放到列宽里，只会把内容挤出去。 */
table td ol, table td ul, table th ol, table th ul {
  margin:0; padding-left:1.2em; }
table td li, table th li { margin:0; }
table td dl, table td dd, table th dl, table th dd { margin-left:0; }
thead { display:table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }

/* 图注与图必须同页：DITA 里结构是
       <div class="fignone"><span class="figcap">图1 …</span><p><img></p></div>
   图注和 <img> 之间没有共同的 break-inside 宿主，只有 figure/.fig 那两条规则管不到，
   于是「图注留在本页、图被推到下一页」（实测 8.9 版第 51 页只剩一行图注）。
   把 .fignone（以及 .figure/.figsides 等同类容器）整体设为不可拆。 */
figure, .fig, .fignone, .figure, .figborder, .figsides { break-inside: avoid; page-break-inside: avoid; }
/* 图片：只加「上限」，不覆盖标签自带尺寸。
   原先写 `img{max-width:100%%;height:auto}` 会把 height 属性一起抹掉，
   导致带宽高比的竖图（流程图等）按页宽撑开后高度超一页、被裁到纸外。 */
/* 图片缩放规则（实测踩过的坑都在这里）：
   1. `max-width:100%%` 是绝对必需的 —— 一旦把 width 写成 auto，WeasyPrint 会按
      图片固有像素尺寸绘制，直接超出纸面被裁。
   2. 再加 `max-height` 约束竖长图（流程图等），否则按页宽撑开后高度超一页。
   3. 不能只留 max-height 而丢掉 height:auto，否则比例会失真。 */
img { max-width:100%%; max-height:620pt; height:auto; }

/* 超长信息图切块：每块独占一页，宽度撑满版心、高度不超一页 */
div.lslicebox { break-before:page; page-break-before:always; height:243mm;
                display:flex; align-items:center; justify-content:center;
                break-inside:avoid; page-break-inside:avoid; }
div.lslicebox.first { break-before:auto; page-break-before:auto; }
div.lslicebox img { max-width:100%%; max-height:243mm; width:auto; height:auto;
                    object-fit:contain; }
/* 代码块：官方 CSS 是 `pre.screen{white-space:pre}`（选择器 0,1,1），
   优先级高于 `pre,code`（0,0,1），所以行内命令永不换行、右侧被裁。
   实测 8.9 版 155-157 页 `#注释` 全部丢失、2438 页 sha256 长路径被切。
   用同等或更高优先级的选择器覆盖，并允许任意位置断行。 */
pre, code, tt, pre.screen, pre.code, pre.screen code, .screen, .code
          { font-family:"Sarasa Mono SC","Noto Sans Mono CJK SC",monospace; font-size:9pt;
            white-space:pre-wrap !important; word-break:break-all; overflow-wrap:anywhere;
            max-width:100%%; line-height:1.45; }

/* 告警框 */
.note,.tip,.warning,.caution,.notice,.danger,.attention {
  break-inside: avoid; page-break-inside: avoid; padding:5pt 8pt; margin:8pt 0;
  border-left:3pt solid #999; background:#fafafa; }
.note p,.tip p,.warning p,.caution p,.notice p,.danger p,.attention p { text-indent:0; }

.attachbox { border:.6pt solid #d9d9d9; background:#fbfbfb; padding:8mm 9mm; margin:10mm 0; }
.attachbox .at { font-family:"黑体","Noto Sans CJK SC",sans-serif; font-size:13pt;
                 font-weight:bold; margin-bottom:4mm; }
.attachbox p { text-indent:0; color:#444; }

/* ---------- 封面 ---------- */
.cover { page: cover; height:297mm; width:100%%; position:relative; break-after: page; }
.cover .band { position:absolute; top:0; left:0; width:100%%; height:38mm; background:#c7000b; }
.cover .bandtxt { position:absolute; top:12mm; left:22mm; color:#fff;
                  font-family:"Noto Sans CJK SC","Source Han Sans SC","黑体",sans-serif; font-size:15pt; }
.cover .mid { position:absolute; top:82mm; left:22mm; right:22mm; }
.cover .ptype { font-family:"Noto Sans CJK SC","Source Han Sans SC","黑体",sans-serif; font-size:13pt;
                color:#8c0009; letter-spacing:2pt; }
.cover .title { font-family:"Noto Sans CJK SC","Source Han Sans SC","黑体",sans-serif; font-size:30pt;
                font-weight:bold; line-height:1.25; letter-spacing:0; margin-top:6mm; color:#111; }
.cover .doctype { font-family:"Noto Sans CJK SC","Source Han Sans SC","黑体",sans-serif; font-size:17pt;
                color:#333; margin-top:3mm; }
.cover .subtitle { font-family:"黑体",sans-serif; font-size:17pt; color:#444; margin-top:4mm; }
.cover .meta { position:absolute; bottom:34mm; left:22mm; right:22mm; font-size:11pt; color:#333;
               font-family:"宋体",serif; line-height:2.0; }
.cover .meta .k { display:inline-block; width:28mm; color:#777; }
.cover .rule { position:absolute; bottom:28mm; left:22mm; right:22mm; border-top:.8pt solid #c7000b; }
.cover .foot { position:absolute; bottom:14mm; left:22mm; right:22mm; font-size:9.5pt; color:#888;
               font-family:"宋体",serif; }

/* ---------- 目录 ---------- */
.toc { page: toc; break-after: page; }
.toc h1 { font-family:"黑体",sans-serif; font-size:22pt; text-align:center; border-bottom:none;
          margin:0 0 10mm 0; bookmark-level:none; }
.toc .e { display:block; position:relative; padding-right:12mm; margin:0;
          font-size:10pt; line-height:1.85; break-inside:avoid; }
.toc .e a { color:#1a1a1a; text-decoration:none; }
.toc .e a::after { content: target-counter(attr(href), page); position:absolute; right:0; color:#333; }
.toc .l1 { font-weight:bold; font-size:11pt; margin-top:5mm; font-family:"黑体",sans-serif; }
.toc .l2 { padding-left:7mm; }
.toc .l3 { padding-left:14mm; font-size:9.5pt; color:#333; }
""" % {"product": PRODUCT, "pver": PVERSION}

# 分页策略：一级/二级章节另起一页
MY_CSS = MY_CSS.replace("H1_SECTION_PLACEHOLDER", """
section.chapter { break-before: page; page-break-before: always; padding-top:4mm; }
""")

# ---------- 5. 组装 ----------
t0 = time.time()
parts = []
parts.append(f"""<!DOCTYPE html><html lang="zh-cn"><head><meta charset="utf-8">
<title>{ihtml.escape(LIB_NAME)} {ihtml.escape(PVERSION)}</title>
<style>{print_css}</style><style>{MY_CSS}</style></head><body>""")

parts.append(f"""
<div class="cover">
  <div class="band"><div class="bandtxt">{ihtml.escape(PRODUCT)}</div></div>
  <div class="mid">
    <div class="ptype">产品文档</div>
    <div class="title">{ihtml.escape(COVER_MODEL)}</div>
    <div class="doctype">{ihtml.escape(COVER_DOC)}</div>
    <div class="subtitle">版本 {ihtml.escape(PVERSION)}</div>
  </div>
  <div class="meta">
    <div><span class="k">产品名称</span>{ihtml.escape(PRODUCT)}</div>
    <div><span class="k">产品版本</span>{ihtml.escape(PVERSION)}</div>
    <div><span class="k">文档版本</span>{ihtml.escape(LIBVER)}</div>
    <div><span class="k">发布日期</span>{ihtml.escape(ISSUE)}</div>
    <div><span class="k">文档编号</span>{ihtml.escape(LIBID)}</div>
  </div>
  <div class="rule"></div>
  <div class="foot">华为技术有限公司</div>
</div>""")

# 目录
toc = ['<div class="toc"><h1>目　录</h1>']
for title, url, d in nodes:
    if d <= 3 and title:
        toc.append(f'<div class="e l{d}"><a href="#{anchor_id(url)}">{ihtml.escape(title)}</a></div>')
toc.append("</div>")
parts.append("\n".join(toc))

missing = []
attachments = []      # (标题, 绝对路径, 页数) —— 渲染后合并到文末附录
_html_path_early = OUT.replace(".pdf", ".html")
if REUSE_HTML and os.path.exists(_html_path_early):
    print(f"[i] 复用已有 HTML: {_html_path_early}")
    # 附件清单仍需重建（合并阶段要用）
    for title, url, d in nodes:
        if url and clean(url)[1] == "pdf":
            attachments.append((title, os.path.join(RES, url)))
    missing = ["(复用模式未校验)"]
seen = set()          # 同一 topic 被多个章节复用时，正文只渲染一次（目录条目照列）
dup_skipped = 0
for title, url, d in nodes:
    if url in seen:
        dup_skipped += 1
        continue
    seen.add(url)
    inner, kind = clean(url)
    if REUSE_HTML and os.path.exists(_html_path_early):
        continue
    if kind == "pdf":
        ap = os.path.join(RES, url)
        attachments.append((title, ap))
        inner = (f'<div class="attachbox"><div class="at">附件：{ihtml.escape(title)}</div>'
                 f'<p>本主题为一份独立文档，原始文件为 <code>{ihtml.escape(os.path.basename(url))}</code>，'
                 f'已合并至本文档末尾的“附录　独立文档”中，可通过书签直接跳转。</p></div>')
    elif inner is None:
        missing.append(url); continue
    cls = "chapter" if d <= 2 else ""
    parts.append(f'<section class="lv{d} {cls}" id="{anchor_id(url)}">{inner}</section>')

parts.append("</body></html>")
doc = "\n".join(parts)
html_path = OUT.replace(".pdf", ".html")
if not (REUSE_HTML and os.path.exists(html_path)):
    open(html_path, "w", encoding="utf-8").write(doc)
print(f"[i] HTML 生成完成 {time.time()-t0:.1f}s  {len(doc)/1024/1024:.1f}MB  缺失 {len(missing)} 去重跳过 {dup_skipped}")

# ---------- 6. 渲染 ----------
from weasyprint import HTML
t1 = time.time()
HTML(filename=html_path, base_url=RES + "/").write_pdf(OUT)
print(f"[✓] 主文档渲染完成 {time.time()-t1:.1f}s -> {OUT}  {os.path.getsize(OUT)/1024/1024:.1f}MB")

# ---------- 7. 合并附件（独立文档）到文末并挂书签 ----------
if attachments:
    import pikepdf
    marks = []
    tmp = OUT + ".tmp.pdf"
    with pikepdf.open(OUT) as main:
        base = len(main.pages)          # 附录起始页
        for title, ap in attachments:
            try:
                with pikepdf.open(ap) as src:
                    start = len(main.pages)
                    main.pages.extend(src.pages)
                    marks.append((f"附件：{title}", start))
                    print(f"    + 合并 {title}  {len(src.pages)} 页")
            except Exception as e:
                print(f"    ! 合并失败 {title}: {e}")
        if marks:                        # 页面就位后再挂书签，否则目标页越界
            with main.open_outline() as ol:
                appendix = pikepdf.OutlineItem("附录　独立文档", base)
                appendix.children = [pikepdf.OutlineItem(t, pg) for t, pg in marks]
                ol.root.append(appendix)
        main.save(tmp)
    os.replace(tmp, OUT)
    print(f"[✓] 合并 {len(marks)} 份附件完成 -> {os.path.getsize(OUT)/1024/1024:.1f}MB")
