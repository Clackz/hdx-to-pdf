# 踩过的坑

这份清单是按「症状 → 根因 → 修法」记的，不是理论推导。每条都对应一次真实的返工，别跳过。

## 排版基础

1. **`text-align: justify` 必须改 `left`**。中文段落里夹长英文串（`Save Configuration`、`ResourceQuota`）时，justify 会把词间空格拉到几个汉字宽，整页像被撕开。这是本流程最显眼的排版缺陷。

2. **WeasyPrint 默认给 `h1..h6` 都生成书签**。华为正文有 6000+ 个 `h4.sectiontitle`，全进书签后目录树彻底糊掉。必须写 `h1,h2,h3,h4,h5,h6{bookmark-level:none}`，再用 `.lvN h1.topicTitle-h1{bookmark-level:N}` 按 navi 深度逐级打开。修前 7967 个书签，修后正好等于话题数。

3. **剥页脚 `footerNavBar` 的正则要用 `\Z`**。先抽出 `<body>` 内内容后，串里已无 `</body>`，用 `(?=</body>)` 的非贪婪正则永远匹配不上，「版权所有 © 华为技术有限公司 / 上一节 / 下一节」会残留在每页。

4. **页眉 `@top-left{width:100%}` 会把右侧挤成竖排**。margin box 不要设 100% 宽，左右各靠边即可。

5. **内部锚点转换**：`zh-cn_topic_xxx.html` → `#t-zh-cn_topic_xxx`，否则 PDF 内交叉引用全断。图片用 `base_url=resources/` 交给 WeasyPrint 解析，不必改写 `src`。

6. **跨页表头**：`thead{display:table-header-group}` + `tr{break-inside:avoid}` 保证表头重复；但**别给 `table` 整体加 `break-inside:avoid`**，大表会被整块推到下一页留下大片空白。

7. **分页策略**：只给 L1/L2 章节 `break-before:page`。每个话题都分页会让 1800 篇膨胀到 5000+ 页。分页后 `h1` 会紧贴页眉，给 `section.chapter` 加 `padding-top:4mm` 补回来。

8. **navi 里同一个话题会被多个章节复用**。实测某包里「配置 NTP 时钟源与时区」在 3 个部署场景下各列一次。目录条目照列（合法），**正文必须按 url 去重**，否则同一篇内容重复刷页、书签也重复。

9. **navi 深度不一定是 7 级**（实测有到 8 级的）。CSS 里 `lvN` 规则要按实际深度补，否则最深一级没有书签。

10. **`profile.xml` 的 `libName` 可能很长**（`FusionCube 1000H, 1000V 产品文档 (FusionCompute)`）。整串塞进封面大标题会拦腰折断，要按 `产品文档` 关键词拆成「产品型号」+「文档类型(平台)」两行 —— 这也正是官方封面样式。西文用 `Noto Sans CJK SC` 而不是 `黑体`，后者的拉丁字形接近等宽，大字号很难看。

11. **shell 细节**：`cp` 被 alias 成 `cp -i` 时脚本间复制会卡在覆盖确认上，用 `\cp -f`；zsh 下 `rm -f *.tmp` 在无匹配时直接报错中断整条命令（nomatch），批量清理要用 `rm -f *.tmp(N)` 或分开写。

## 结构陷阱

12. **navi 里的「主题」可能是 PDF 附件，不是 HTML** —— 这是最阴的坑。某些包末尾的 N 个 L1 主题（`zh-cn_attachment_*.pdf`）实际是**完整 PDF 二进制**。若按 HTML 解码塞进正文，会产生十几万个孤立 `<`、DOM 崩到上千层，WeasyPrint 抛 `RecursionError: maximum recursion depth exceeded`（cssselect2 遍历 ancestors 时爆栈），而且要**跑到全量才炸**（`--limit 20` 探路根本探不到，因为附件排在 navi 最末）。
    - 判据：解码前先看 `blob[:4] == b"%PDF"`，命中就分流。
    - 处理方式：正文放一页「附件说明」，原始 PDF 在渲染后用 pikepdf 合并到文末，挂成「附录　独立文档」的书签父节点。
    - 合并顺序：**先 `pages.extend()` 再建书签**，否则 `OutlineItem` 指向 `len(pages)` 会 `IndexError: Accessing nonexistent PDF page number`。
    - 以后拿到任何 hdx，先跑 `inspect_hdx.py` 看一眼有没有 `attachment` 型 url。

13. **合并附件后，正文页脚的总页数会小于 PDF 实际页数**（实测页脚 `3 / 4659`，实际 6289 页）。正文 1..4659 与目录页码自洽，附录是原样保留的独立文档、自带页码。不要试图改 —— 页脚是渲染期固化的文字，改不了。

## 图片：四类溢出

最容易漏的一类缺陷，而且只有翻到具体页才会发现。根因是官方 CSS 对 `img` 完全没有尺寸约束：`imgResize` 这个类在两套官方 CSS 里**根本没有定义**，它是 HedEx 前端 JS 按窗口宽度算出来写进内联样式的。屏幕能看是因为浏览器有滚动条，A4 纸没有。

14. **超长「一页纸」信息图**（`8000x20597` 这种，高宽比 2~3）：按页宽缩放后高度是页面的两倍以上，直接溢出纸外被裁。脚本内置 `_split_long_image()`：高宽比 > `LONG_IMAGE_RATIO=2.0` 且宽 ≥ 1200px 就切块，每块独占一页。
    - **阈值不能调高**。实测一个包里三张图是 2.24 / 2.57 / 2.58，其余所有大图最高只有 1.54，断层就在 2.0 附近。曾经设成 2.6，结果 2.575 那张刚好漏过去。
    - **页数必须 `math.ceil` 不能 `round`**。`17882/(8000*1.5)=1.49`，round 得 1 就等于「不切」，图照样溢出。
    - **别拿图内的分节色块当切点**。试过检测华为红 `(199,0,11)` 的编号标签带做边界，结果每节只有 2900~5900px，而一整页要 12000px，切出来每页只放半屏。**纯按页面比例等分**才铺得满。
    - `SLICE_FACTOR=1.50`：版心 156mm、可用高 244mm，比例上限 1.564，取 1.50 留余量。

15. **竖长流程图**（高宽比 1.3~1.6，如 1661×2561）：这类图标签上带 `width="399" height="624"`，但全局 `img{max-width:100%}` 会连带抹掉高度属性，撑满页宽后高度超一页。必须补 `max-height`。

16. **图片 `<map>` 热区**：有些图后面跟着 `<map>`，内含几十条指向外网的 `<area>`。PDF 里热区毫无意义，但每条 area 都会被当链接渲染成块级元素，把图挤走。`clean()` 里整块剥掉。

17. **`width:auto` 是个陷阱**：一旦把 img 的 `width` 写成 `auto`（哪怕同时有 `max-width:100%`），WeasyPrint 会按**固有像素尺寸**绘制 —— 1661px 的图直接铺出纸面 3 倍被裁。正确写法只有一条：

    ```css
    img { max-width:100%; max-height:620pt; height:auto; }
    ```

    `max-height` 取 620pt 而不是 690pt（正文可用高度）：688pt 时图虽不溢出，但和标题挤不进同一页，标题会**单独占一页、图跑到下一页**，看上去像「图丢了」。这两个数字是实测出来的，改之前先 `--offset` 抽查那两张图。

## 表格与代码块：官方 CSS 优先级压不住

图片修完不等于版面就干净了。**有一版文档全包竖图最高高宽比只有 1.79，一张都没触发 2.0 阈值，但表格和代码块被切了一地。** 这两类的共同点是：官方 `commonltr_print.css` 里的规则**优先级比模板 CSS 高**，不显式覆盖就一定会溢出。

18. **表格横向溢出**（最严重）。官方只给 `table{width:100%}`，**没有 `table-layout`**。auto 布局下 WeasyPrint 按「内容最小宽度」分配列宽，列一多、单元格里又是长中文词组，总宽突破 100%，整表右侧被裁到纸外。实测一版 207–213 页「VRM 节点规格要求」5 列表，**第 5 列整列丢在纸外**。
    - 只加 `table-layout:fixed` 会引出新问题：fixed 下若没有 `<col>` 宽度，WeasyPrint 把各列**均分**，长文字列被压成一条竖线、中文一字一行 —— 比溢出更难看。
    - **正解 = fixed 布局 + 注入 `<colgroup>`，且列宽优先取作者自带的 `width` 属性**。华为的 DITA 导出几乎每张表都自带列宽（实测 1225 张里 1224 张的 `td/th` 上有 `width="19.98%"` 这种属性，加起来正好 100%）。这是作者在文档工具里真正排过的比例，**权威性远高于任何自动推断**。`_native_widths()` 优先读它，取不到才回退到按内容长度加权。
    - **别用自己推算的宽度去覆盖原生 width**。一张 3 列表原生是 19.98 / 19.97 / 60.05，被加权算法改成 15.66 / 68.50 / 15.84 后，中间巨宽列没内容、两边窄列把中文压成一字一行，整页废掉。这一条和上一条是同一个教训的两面：**能拿到权威数据时，不要自己推断**。
    - **必须按 `colspan` 展开单元格**。参数表普遍用「合并单元格」做分组标题（该行只有 1 个 `<td>` 但 `colspan=2`，内容是「基本信息」这类）。若按固定下标算列，后面每一行的列位置都会**错位一格**，权重全算垮。实测一张 3 列 29 行参数表，不处理 colspan 时被算成 2 列 34%/65%，「参数」列被压成一字一行。
    - **回退路径的每列下限随列数变**：`low = max(12.0, 50.0/ncol)`。5 列时 12% ≈ 53pt（一行 2~3 个中文字）；2 列时给到 25%。
    - **`td/th` 上要同时给 `word-break:break-word` 和 `overflow-wrap:anywhere`，但前提是布局已定死**。两者缺一不可：`break-word` 管中文与常规断行；`anywhere` 管「一整串没有空格的 Latin 串」（LDAP DN、路径、长命令）。只有 `break-word` 时，`cn=username,ou=people,dc=example,dc=com` 这种串不会断，整行顶出纸外约 2mm。
    - 这里有个**容易走反方向的误解**：只在 auto 布局下加 `overflow-wrap:anywhere`，WeasyPrint 会按「每列最小宽度 = 一个字符」均分，5 列全变竖排细条 —— 很容易误判成「这个属性绝不能用」，其实错在**布局没定死**。改成 `table-layout:fixed` + 注入 `<colgroup>` 之后，列宽由 colgroup 决定、不再看 min-content，`anywhere` 就完全安全。口诀：**先定死列宽，再允许任意断行**。

19. **代码块右侧被裁**。官方是 `pre.screen{margin:0;white-space:pre}`（选择器权重 0,1,1），**压得住**模板里的 `pre,code{white-space:pre-wrap}`（0,0,1）。结果命令行永不换行，行尾的 `#注释` 全部跑到纸外。实测一版 155–157 页 `#配置本地用户名和密码，实际执行时…` 整段丢失、2438 页 `sha256sum` 的长路径被切。

    ```css
    pre, code, tt, pre.screen, pre.code, pre.screen code, .screen, .code {
      white-space:pre-wrap !important; word-break:break-all;
      overflow-wrap:anywhere; max-width:100%; }
    ```

    覆盖时选择器要够长（或直接 `!important`），只写 `pre` 一条会被官方规则压回去。

20. **图注与图分离**。DITA 结构是 `<div class="fignone"><span class="figcap">图1 …</span><p><img></p></div>` —— 图注在 `img` **前面**、且在 `<p>` 之外，两者没有共同的 break-inside 宿主。只给 `figure,.fig` 设 `break-inside:avoid` 管不到，于是「图注留在本页、图被推到下一页」。实测某版第 51 页只剩一行「图1 …示意图」，页面其余全空。
    - 把容器类名全列上：`figure, .fig, .fignone, .figure, .figborder, .figsides { break-inside:avoid; }`

21. **单元格内的列表缩进会把列撑爆**（比第 18 条更隐蔽，只在「高行跨页」时现形）。列宽一旦是硬约束（`table-layout:fixed` + colgroup），单元格里 `<ol>` 的默认左缩进、`.note` 的内边距就**不会跟着缩放**，只会把正文挤到没地方。
    - 症状：某列被压成**一列竖排单字**，同时表格里多画出一条竖线（6 列变 7 列），整表宽出内容区约 10%、右缘顶出纸外 10pt 左右。实测某版连续 14 页如此。
    - 触发条件：那一行**比一页还高**（本例是 6 列账户表，第 5 列装着几十条密码规则）。行被拆到下一页后，碎片里的列位计算就会错乱。
    - 正解：`table td ol, table td ul { margin:0; padding-left:1.2em }`、`table td li{margin:0}`、`dl/dd{margin-left:0}`。实测把这张表从 9 页压回 4 页、6 列比例完全恢复。
    - 口诀：**列宽定死之后，单元格里任何固定缩进都是隐患**。排查时先怀疑 `ol/ul/dl/blockquote` 的 margin，再怀疑 note/tip 这类带内边距的盒子。
    - 别指望 `tr{break-inside:avoid}` 救场 —— 行本身比一页高时，WeasyPrint 照拆不误。

## 为什么「只看图片」不够

早期的排查方式是逐张盯超长图，结果表格列被裁的问题在另一份文档里一直活到别人翻开才发现。回头看第一本时才发现**它自己也有 234 页越界**（168 页代码块 + 66 页表格），其中一段 5 列表格第 5 列整条丢在纸外。

结论：**先转的那本不一定是干净的那本，每本都要查；而查的方法必须是全书级的，不是抽几页翻。**
