#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全书「内容顶出纸外」体检：找出内容被裁到纸边的页。

用法:
    # 快检（秒级）：只解析文本坐标，抓表格列 / 代码块被裁
    scan_overflow.py <pdf> --fast

    # 全检（3~5 分钟/千页）：光栅化后看边缘着墨，连图片一起抓
    scan_overflow.py <pdf>

    # 定向复检：只扫指定页段
    scan_overflow.py <pdf> --pages 2900-3100
    scan_overflow.py <pdf> --pages 2900-3100 --fast

    scan_overflow.py <pdf> [--dpi 40] [--edge 8] [--thresh 12] [--top 60] [--keep] [--out DIR]

两种模式的分工（别只信一种）:
    --fast  用 `pdftotext -bbox` 拿每个词的精确包围盒，只看**文字**。
            6300 页约 15 秒。表格列被裁、代码块不换行 100% 能抓到；
            **图片被裁完全抓不到**（图片没有文字）。
    默认    渲染成位图，比较最右 3 列与最左 3 列的着墨量。
            左侧干净、右侧有墨 = 内容顶到纸边。图片、表格、代码块一律通吃，
            但慢，且只给页号不说是哪一类。

建议流程：改完 CSS 先 `--fast` 定性，全绿再跑一次默认模式兜图片。

退出码: 0 = 未发现；1 = 有嫌疑页；2 = 环境/参数错误
"""
import sys, os, glob, subprocess, shutil, argparse, re


def _parse_pages(spec, total):
    """'2900-3100' / '17' / '1-30,58,900-910' -> [(1,30),(58,58),...]"""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.append((max(1, int(a)), min(total, int(b))))
        else:
            n = int(part)
            out.append((max(1, n), min(total, n)))
    return out


def _fast_text_scan(pdf, edge_pt, top, ranges):
    """解析 pdftotext -bbox，抓 xMax 超过「页宽 - edge_pt」的词"""
    r = subprocess.run(["pdftotext", "-bbox", pdf, "-"], capture_output=True)
    if r.returncode != 0:
        print("[x] pdftotext 失败:", r.stderr.decode("utf-8", "replace")[:300])
        return 2
    xml = r.stdout.decode("utf-8", "replace")

    page_re = re.compile(r'<page\s+width="([\d.]+)"\s+height="([\d.]+)"\s*>(.*?)</page>', re.S)
    word_re = re.compile(r'<word\s+xMin="([\d.]+)"\s+yMin="([\d.]+)"\s+xMax="([\d.]+)"\s+yMax="([\d.]+)"\s*>(.*?)</word>', re.S)

    susp = []
    total = 0
    for pi, pm in enumerate(page_re.finditer(xml), 1):
        total += 1
        if ranges and not any(a <= pi <= b for a, b in ranges):
            continue
        pw = float(pm.group(1))
        ph = float(pm.group(2))
        limit = pw - edge_pt
        worst = None
        cnt = 0
        sample = ""
        for wm in word_re.finditer(pm.group(3)):
            xmin, ymin, xmax, ymax = (float(wm.group(i)) for i in range(1, 5))
            # 跳过页眉页脚带（上下各 7%）
            if ymax < ph * 0.07 or ymin > ph * 0.93:
                continue
            if xmax > limit:
                cnt += 1
                if worst is None or xmax > worst:
                    worst = xmax
                    sample = re.sub(r"\s+", " ", wm.group(5)).strip()[:28]
        if cnt:
            susp.append((pi, round(worst - limit, 1), cnt, sample))

    if not susp:
        print("[✓] 文本层未发现越界（共扫描 %d 页）" % total)
        print("    注意：本模式看不见图片。图片是否被裁要跑默认的光栅模式。")
        return 0

    print("[!] 文本越界页 %d 个（共扫描 %d 页，阈值：越过纸边 %.1fpt 内）" % (len(susp), total, edge_pt))
    for p, over, cnt, s in susp[:top]:
        print("    page %-5d 出界最大 %5.1fpt  越界词 %-3d  例如「%s」" % (p, over, cnt, s))
    if len(susp) > top:
        print("    ... 另有 %d 页未列出" % (len(susp) - top))
    print("\n下一步：`--fast` 只能定位，看不清是哪一类。挑几页渲染确认：")
    print("  pdftoppm -png -r 82 -f <页> -l <页> %s /tmp/chk" % pdf)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--fast", action="store_true", help="只查文本层（秒级，抓不到图片）")
    ap.add_argument("--pages", default="", help="只查这些页：'17' / '2900-3100' / '1-30,58'")
    ap.add_argument("--dpi", type=int, default=40, help="光栅模式渲染分辨率，40 足够")
    ap.add_argument("--edge", type=float, default=8.0,
                    help="快检阈值：词右缘距纸边多少 pt 内算越界（默认 8 ≈ 2.8mm）")
    ap.add_argument("--out", default="/tmp/hdx_overflow_scan")
    ap.add_argument("--keep", action="store_true", help="保留渲染出的 PNG")
    ap.add_argument("--thresh", type=int, default=12, help="光栅模式右边缘着墨像素阈值")
    ap.add_argument("--top", type=int, default=60, help="最多列出多少页（-1 = 全列）")
    a = ap.parse_args()

    if not os.path.exists(a.pdf):
        print("[x] 找不到文件:", a.pdf)
        return 2

    if a.top is None or a.top <= 0:
        a.top = 10 ** 9

    if a.fast:
        if shutil.which("pdftotext") is None:
            print("[x] 需要 poppler 的 pdftotext")
            return 2
        # 先拿总页数以便解析范围
        try:
            info = subprocess.run(["pdfinfo", a.pdf], capture_output=True).stdout.decode("utf-8", "replace")
            total = int(re.search(r"Pages:\s+(\d+)", info).group(1))
        except Exception:
            total = 10 ** 9
        ranges = _parse_pages(a.pages, total) if a.pages else []
        return _fast_text_scan(a.pdf, a.edge, a.top, ranges)

    try:
        from PIL import Image
    except ImportError:
        print("[x] 需要 Pillow：pip install -r requirements.txt")
        return 2
    if shutil.which("pdftoppm") is None:
        print("[x] 需要 poppler 的 pdftoppm")
        return 2

    # 页段
    try:
        info = subprocess.run(["pdfinfo", a.pdf], capture_output=True).stdout.decode("utf-8", "replace")
        total_pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
    except Exception:
        total_pages = 0
    ranges = _parse_pages(a.pages, total_pages or 10 ** 9) if a.pages else []

    outdir = a.out
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir, exist_ok=True)

    if ranges:
        # 分段渲染，pdftoppm 的 -f/-l 用真实页号，输出文件名也带真实页号
        for (s, e) in ranges:
            print("[i] 渲染 %s 第 %d-%d 页 @ %ddpi ..." % (os.path.basename(a.pdf), s, e, a.dpi))
            subprocess.run(["pdftoppm", "-png", "-r", str(a.dpi), "-f", str(s), "-l", str(e),
                            a.pdf, os.path.join(outdir, "pg")], capture_output=True)
    else:
        print("[i] 渲染 %s @ %ddpi ..." % (os.path.basename(a.pdf), a.dpi))
        r = subprocess.run(["pdftoppm", "-png", "-r", str(a.dpi), a.pdf,
                            os.path.join(outdir, "pg")], capture_output=True)
        if r.returncode != 0:
            print("[x] 渲染失败:", r.stderr.decode("utf-8", "replace")[:500])
            return 2

    files = sorted(glob.glob(os.path.join(outdir, "pg-*.png")))
    if not files:
        print("[x] 没有产出任何页图")
        return 2
    print("[i] 共 %d 页，开始边缘检测" % len(files))

    susp = []
    for f in files:
        try:
            im = Image.open(f).convert("L")
        except Exception:
            continue
        w, h = im.size
        px = im.load()
        y0, y1 = int(h * 0.08), int(h * 0.92)

        def ink(x):
            return sum(1 for y in range(y0, y1) if px[x, y] < 230)

        right = ink(w - 4) + ink(w - 3) + ink(w - 2)
        left = ink(3) + ink(4) + ink(5)
        if right > a.thresh and right > left * 4:
            pageno = int(os.path.basename(f).split("-")[-1].split(".")[0])
            susp.append((pageno, right, left))

    susp.sort()
    if not susp:
        print("[✓] 未发现内容顶到纸边的页")
        if not a.keep:
            shutil.rmtree(outdir, ignore_errors=True)
        return 0

    print("[!] 嫌疑页 %d 个：" % len(susp))
    for p, r_, l_ in susp[:a.top]:
        print("    page %-5d right_ink=%-4d left_ink=%d" % (p, r_, l_))
    if len(susp) > a.top:
        print("    ... 另有 %d 页未列出（--top -1 可全列）" % (len(susp) - a.top))

    # 把完整清单落盘，免得像以前那样被管道截断
    listfile = os.path.join("/tmp", "hdx_overflow_pages.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for p, r_, l_ in susp:
            fh.write("%d\t%d\t%d\n" % (p, r_, l_))
    print("\n[i] 完整清单已写入 %s（%d 行）" % (listfile, len(susp)))

    print("\n下一步：挑几页放大看，确认是哪一类溢出")
    print("  pdftoppm -png -r 82 -f <页> -l <页> %s /tmp/chk" % a.pdf)
    print("  · 表格列被切 → 检查 table-layout / colgroup 宽度")
    print("  · 代码行被切 → 检查 pre.screen 的 white-space 优先级")
    print("  · 图片被切   → 检查 img 的 max-width / max-height")

    if not a.keep:
        shutil.rmtree(outdir, ignore_errors=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
