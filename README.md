# hdx-to-pdf

把华为 HedEx 文档包（`.hdx`）转成**带书签、带目录页码、图片表格不被裁**的单本 PDF。

华为官方阅读器 HedEx Lite 只有 Windows 版，Linux 上打开 `.hdx` 没有官方方案。这个脚本绕开阅读器，直接从文档包内部结构重建一本书。

## 效果

两本真实产品文档的转换结果：

| 文档包 | 话题数 | 素材 | 输出 | 书签 | 用时 |
|---|---|---|---|---|---|
| FusionCompute 8.9.0 | 1857 | 95 MB | 3727 页 / 69 MB | 1857 | ~11 分钟 |
| FusionCube 8.3.1 | 2328（+1653 页附件） | 342 MB | 6289 页 / 261 MB | 2336 | ~21 分钟 |

都不只是「能看」：书签数精确等于 navi 目录节点数，目录页码与正文实际页码自洽，排版接近华为官方打印版式，并且通过了全书级别的溢出体检（0 页内容被裁到纸外）。

## 原理

`.hdx` 就是个 ZIP，里面是 DITA 导出的 HTML 集合：

| 文件 | 作用 |
|---|---|
| `profile.xml` | 产品名 / 版本 / 发布版本，用于生成封面 |
| `resources/navi.xml` | **权威目录树**（递归 `<topic txt url>`），章节顺序全靠它 |
| `resources/zh-cn_topic_*.html` | 正文，**GB18030** 编码 |
| `resources/public_sys-resources/commonltr_print.css` | 华为官方打印样式表，直接复用 |
| `resources/zh-cn_bookmap_*.hhk` | HTML Help 索引，**通常是空壳**，不要依赖 |

流程：解析 `navi.xml` → 按目录顺序拼接全部话题（按 url 去重）→ 套官方打印 CSS + 一份覆盖补丁 → WeasyPrint 渲染 → pikepdf 合并 PDF 型附件、写入书签与元数据。

## 依赖

```bash
# 系统库：Pango / cairo / poppler
#   Debian/Ubuntu: sudo apt install libpango-1.0-0 libpangoft2-1.0-0 poppler-utils
#   Arch/Manjaro:  sudo pacman -S pango poppler
pip install weasyprint pikepdf

# 字体：官方 CSS 硬编码 "宋体" / "黑体" / "Times New Roman"，缺了会变形
fc-match 宋体   # 期望 simsun.ttc
fc-match 黑体   # 期望 simhei.ttf
```

`poppler-utils` 只在跑溢出体检（`scan_overflow.py`）时需要。

## 用法

### 1. 解包并摸清结构

```bash
file xxx.hdx                  # "Zip archive data" 即可
mkdir -p /tmp/hdx_extract && cd /tmp/hdx_extract
unzip -q -o /path/to/xxx.hdx

python3 scripts/inspect_hdx.py /tmp/hdx_extract
```

`inspect_hdx.py` 会报出话题数、navi 节点数与深度、有无重复节点、超长图清单、**以及有没有 PDF 型附件**。最后这一项很关键，见 [docs/PITFALLS.md](docs/PITFALLS.md) 第 12 条。

### 2. 先探路，再全量

```bash
python3 scripts/build_hdx_pdf.py /tmp/hdx_extract /tmp/probe.pdf --limit 40
```

几秒出 40 篇，检查封面、目录、表格、图片是否正常。没问题再跑全量：

```bash
python3 scripts/build_hdx_pdf.py /tmp/hdx_extract out.pdf
```

全量大约每 1000 页 2.5 分钟，中途没有进度输出。图片又大又多时会更慢（单张上亿像素的图渲染开销远超页数线性外推）。

其他参数：

```bash
--offset 2340 --limit 11   # 定点抽检某一段
--reuse-html               # 复用已生成的 HTML，只重跑渲染阶段
```

`--reuse-html` 用于渲染阶段崩溃或中断后重来。拼装（3~7 分钟）和渲染（10 分钟左右）是两段独立耗时，HTML 一旦落盘就有效，加这个开关会跳过拼装、直接从 HTML 渲染。

### 3. 出 PDF 后必做：全书溢出体检

```bash
python3 scripts/scan_overflow.py out.pdf --fast            # 快检：只查文字，6300 页约 50 秒
python3 scripts/scan_overflow.py out.pdf --pages 3019-3124 # 定向复检某页段
python3 scripts/scan_overflow.py out.pdf                   # 全检：连图片一起，约 4.5 分钟
```

两种模式的盲区正好互补，**别只信一种**：

| 模式 | 抓得到 | 抓不到 | 6310 页耗时 |
|---|---|---|---|
| `--fast`（`pdftotext -bbox`） | 表格列被裁、代码块被裁、正文长串冲出纸外 | **图片被裁**（图片没有文字层） | ~50 秒 |
| 默认（低分辨率光栅看边缘着墨） | 上述 + 图片 | 内容**整块**跑到 MediaBox 外时纸边反而干净 | ~4.5 分钟 |

判断标准：`--fast` 报出界 > 8pt（约 2.8mm）基本是肉眼可见的裁切；3~8pt 属于「少半个字」，仍建议修。

退出码 0 = 干净，1 = 有嫌疑页，方便做回归。完整清单落盘到 `/tmp/hdx_overflow_pages.txt`。

### 4. 验收

```bash
pdfinfo out.pdf | grep -E "Pages|File size"
pdftoppm -png -r 88 -f 420 -l 420 out.pdf /tmp/chk
```

必查六项：① 书签数 = navi 节点数 + 附件数 + 1 个附录父节点；② 目录页码与正文实际页一致；③ 抽 3~4 页看有没有两端对齐拉爆、表格溢出；④ 有附件时抽最后一页确认原样合并、书签跳转正确；⑤ 逐张看超长图所在页，确认没被切成两页也没留半屏空白；⑥ 全书溢出体检两种模式都过。

## 它解决的那些坑

排版类问题全部集中在 [docs/PITFALLS.md](docs/PITFALLS.md)，共 21 条，每条都是实测踩出来的，附症状、根因和修法。几个代表性的：

- 官方 `commonltr_print.css` 的 `pre.screen{white-space:pre}` 优先级压过模板的 `pre{pre-wrap}`，命令行的 `#注释` 整段被推出纸外
- 表格没有 `table-layout`，多列表必然溢出；而只加 `fixed` 会把长中文压成一字一行的竖条
- 超长「一页纸」信息图按页宽缩放后高度是页面的两三倍，必须切块
- navi 里的「主题」可能是**完整 PDF 附件**，按 HTML 解码会让 WeasyPrint 爆栈，而且只有跑到全量才炸

## 已知限制

- 目录页码依赖 WeasyPrint 的 target-counter，只对正文有效；合并进来的 PDF 附件自带原页码，所以**页脚总页数会小于 PDF 实际页数**，这是设计使然而非缺陷。
- 超长图切块采用等分，可能出现「章节标题页只有标题和一段引言、下半页空白」的取舍。若让首块追随标题页剩余高度，切块就不再等分、无法满铺页宽，可读性反而下降。
- 只处理中文包（`zh-cn_topic_*`）。英文包的文件命名规则不同，未验证。

## 许可证

MIT
