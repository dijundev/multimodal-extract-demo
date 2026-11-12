"""把「输入文件」和「提取出的 JSON」拼成左右对比图，README 里那三张就是这么来的。

前提是先跑过 `python extract_demo.py`，脚本读 output/extract_all.json。
    python tools/make_figures.py       # 覆盖写 figures/
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FIGS = ROOT / "figures"
FIGS.mkdir(exist_ok=True)

# 中文字体挨个找，找不到就退回 PIL 默认（可能出方框，但不会崩）
FONT_CANDIDATES = {
    False: ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc"],
    True: ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
}


def _font(size, bold=False):
    for p in FONT_CANDIDATES[bold]:
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1 不收 size
        return ImageFont.load_default()

W = 1720
PAD = 44
LEFT_W = 700
GAP = 36
RIGHT_W = W - PAD * 2 - LEFT_W - GAP
TOP = 150
BOTTOM = 44
BODY = 15           # JSON 正文字号
LINE_H = 23

C_BG = "#FFFFFF"
C_CARD = "#FBFCFD"
C_LINE = "#E4E7EC"
C_TITLE = "#101828"
C_SUB = "#667085"
C_KEY = "#0B5CA8"
C_STR = "#1F2328"
C_NUM = "#B45309"
C_NULL = "#7C3AED"
C_PUNC = "#7A828E"


def f(size, bold=False):
    return _font(size, bold)


F_TITLE = f(30, True)
F_SUB = f(17)
F_CHIP = f(15, True)
F_BODY = f(BODY)
F_BODY_B = f(BODY, True)
F_MONO_NOTE = f(15)


# ------------------------------------------------------------ JSON -> 带角色的行
def shorten(obj):
    """把 full_text 这类超长字段截断，图上只展示开头，完整版看 JSON 文件。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "full_text" and isinstance(v, str) and len(v) > 88:
                out[k] = v[:88].replace("\n", " ") + "…"
            else:
                out[k] = shorten(v)
        return out
    if isinstance(obj, list):
        return [shorten(x) for x in obj]
    return obj


def seg(text, kind="punc"):
    return (text, kind)


def scalar_segs(v):
    if isinstance(v, str):
        return [seg(f'"{v}"', "str")]
    if isinstance(v, bool):
        return [seg("true" if v else "false", "null")]
    if v is None:
        return [seg("null", "null")]
    if isinstance(v, (int, float)):
        return [seg(f"{v}", "num")]
    return [seg(str(v), "str")]


def flatten(obj, indent=0, max_w=None, prefix=None):
    """递归展开成 [(indent, segments)]；能压成一行的数组/对象就压成一行。"""
    lines = []
    ind = " " * indent

    def segs_width(segs):
        return sum(F_BODY.getlength(t) for t, _ in segs)

    def line(segs):
        if max_w:
            total, cut = 0, []
            for text, kind in segs:
                w = F_BODY.getlength(text)
                if total + w > max_w and kind == "str":
                    keep = ""
                    for ch in text:
                        if total + F_BODY.getlength(keep + ch) > max_w - 14:
                            break
                        keep += ch
                    cut.append(seg(keep + '…"', "str"))
                    total = max_w
                    break
                cut.append(seg(text, kind))
                total += w
            segs = cut
        lines.append((ind, segs))

    def inline(v, budget):
        """把纯标量的 list / dict 拼成单行；放不下返回 None。"""
        if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
            segs = [seg("[", "punc")]
            for i, x in enumerate(v):
                segs += scalar_segs(x)
                if i < len(v) - 1:
                    segs.append(seg(", ", "punc"))
            segs.append(seg("]", "punc"))
            return segs if segs_width(segs) <= budget else None
        if isinstance(v, dict) and all(not isinstance(x, (dict, list)) for x in v.values()):
            segs = [seg("{", "punc")]
            for i, (k, x) in enumerate(v.items()):
                segs += [seg(f'"{k}"', "key"), seg(": ", "punc")] + scalar_segs(x)
                if i < len(v) - 1:
                    segs.append(seg(", ", "punc"))
            segs.append(seg("}", "punc"))
            return segs if segs_width(segs) <= budget else None
        return None

    if isinstance(obj, dict):
        items = list(obj.items())
        for i, (k, v) in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            key_segs = [seg(f'"{k}"', "key"), seg(": ", "punc")]
            prefix_w = F_BODY.getlength(ind) + segs_width(key_segs)
            budget = (max_w or 9999) - prefix_w
            if isinstance(v, (dict, list)):
                if not v:
                    line(key_segs + [seg("{}" if isinstance(v, dict) else "[]", "punc")]
                         + [seg(comma, "punc")])
                    continue
                compact = inline(v, budget)
                if compact:
                    line(key_segs + compact + [seg(comma, "punc")])
                    continue
                line(key_segs + [seg("{" if isinstance(v, dict) else "[", "punc")])
                lines.extend(flatten(v, indent + 2, max_w))
                line([seg(("}" if isinstance(v, dict) else "]") + comma, "punc")])
            else:
                line(key_segs + scalar_segs(v) + [seg(comma, "punc")])
        return lines

    if isinstance(obj, list):
        for i, v in enumerate(obj):
            comma = "," if i < len(obj) - 1 else ""
            budget = (max_w or 9999) - F_BODY.getlength(ind)
            if isinstance(v, (dict, list)):
                if not v:
                    line([seg("{}" if isinstance(v, dict) else "[]", "punc"), seg(comma, "punc")])
                    continue
                compact = inline(v, budget)
                if compact:
                    line(compact + [seg(comma, "punc")])
                    continue
                line([seg("{" if isinstance(v, dict) else "[", "punc")])
                lines.extend(flatten(v, indent + 2, max_w))
                line([seg(("}" if isinstance(v, dict) else "]") + comma, "punc")])
            else:
                line(scalar_segs(v) + [seg(comma, "punc")])
        return lines

    line(scalar_segs(obj))
    return lines


def draw_lines(d, x, y, lines, max_lines, limit):
    shown = lines[:max_lines]
    for ind, segs in shown:
        cx = x
        d.text((cx, y), ind, font=F_BODY, fill=C_PUNC)
        cx += F_BODY.getlength(ind)
        for text, kind in segs:
            color = {"key": C_KEY, "str": C_STR, "num": C_NUM,
                     "null": C_NULL, "punc": C_PUNC}[kind]
            d.text((cx, y), text, font=F_BODY_B if kind == "key" else F_BODY, fill=color)
            cx += F_BODY.getlength(text)
        y += LINE_H
    if len(lines) > max_lines:
        d.text((x, y), f"… 另有 {len(lines) - max_lines} 行，完整结果见 output/*.json",
               font=F_MONO_NOTE, fill=C_SUB)
        y += LINE_H
    return y


def fit(img: Image.Image, box_w, box_h):
    r = min(box_w / img.width, box_h / img.height)
    return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)


def make_figure(src_path, json_path, page_index, out_name, title, subtitle):
    doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
    rec = doc["results"] if "results" in doc else None
    page = None
    if rec:
        for r in rec:
            if Path(r["name"]).stem == Path(src_path).stem:
                page = r["pages"][page_index]
                rec = r
                break
    else:
        page = doc["pages"][page_index]

    data = shorten(page["data"])
    body = flatten(data, 2, max_w=RIGHT_W - 2 * 24 - 8)
    lines = [("", [seg("{", "punc")])] + body + [("", [seg("}", "punc")])]

    # 画布高度：以 JSON 行数为准，左侧图跟着缩放
    json_h = len(lines) * LINE_H + 66 + 30
    canvas_h = max(TOP + json_h + BOTTOM, 1120)

    canvas = Image.new("RGB", (W, canvas_h), C_BG)
    d = ImageDraw.Draw(canvas)

    # 顶部标题
    d.text((PAD, 40), title, font=F_TITLE, fill=C_TITLE)
    d.text((PAD, 84), subtitle, font=F_SUB, fill=C_SUB)
    d.line([PAD, 130, W - PAD, 130], fill=C_LINE, width=2)

    # ---------------- 左：原始文件
    lx, ly = PAD, TOP
    lh = canvas_h - TOP - BOTTOM
    d.rounded_rectangle([lx + 3, ly + 5, lx + LEFT_W + 3, ly + lh + 5], 14, fill="#0000000A")
    d.rounded_rectangle([lx, ly, lx + LEFT_W, ly + lh], 14, fill="#F4F5F7",
                        outline=C_LINE, width=2)

    if src_path.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(src_path) as pd:
            pix = pd[page_index].get_pixmap(dpi=110)
            src = Image.open(__import__("io").BytesIO(pix.tobytes("png")))
    else:
        src = Image.open(src_path)
        if src.mode != "RGB":
            src = src.convert("RGB")

    box_w, box_h = LEFT_W - 56, lh - 110
    shot = fit(src, box_w, box_h)
    px = lx + (LEFT_W - shot.width) // 2
    py = ly + 56 + (box_h - shot.height) // 2
    d.rectangle([px + 4, py + 6, px + shot.width + 4, py + shot.height + 6], fill="#00000012")
    canvas.paste(shot, (px, py))
    d.rectangle([px, py, px + shot.width, py + shot.height], outline="#D0D5DD", width=2)

    d.rounded_rectangle([lx + 18, ly + 14, lx + 150, ly + 44], 15, fill="#E8EEF6")
    d.text((lx + 32, ly + 20), "输入 · INPUT", font=F_CHIP, fill="#0B5CA8")
    d.text((lx + 18, ly + lh - 40),
           f"{src_path.name}   {src.width}x{src.height}   "
           f"{src_path.stat().st_size / 1024:.0f} KB",
           font=F_MONO_NOTE, fill=C_SUB)

    # ---------------- 右：JSON
    rx = lx + LEFT_W + GAP
    rw = RIGHT_W
    rh = canvas_h - TOP - BOTTOM
    d.rounded_rectangle([rx + 3, ly + 5, rx + rw + 3, ly + rh + 5], 14, fill="#0000000A")
    d.rounded_rectangle([rx, ly, rx + rw, ly + rh], 14, fill=C_CARD,
                        outline=C_LINE, width=2)

    # 仿终端小圆点，标明这是「输出」
    for i, c in enumerate(("#F0A5A0", "#F3CE8E", "#A8D5A2")):
        d.ellipse([rx + 18 + i * 20, ly + 18, rx + 30 + i * 20, ly + 30], fill=c)
    d.text((rx + 88, ly + 15), "输出 · OUTPUT JSON", font=F_CHIP, fill="#0B5CA8")
    usage = (page.get("usage") or {}).get("total_tokens")
    d.text((rx + rw - 300, ly + 17),
           f"page {page['page']} · {page.get('elapsed_sec', 0)}s"
           + (f" · {usage} tokens" if usage else ""),
           font=F_MONO_NOTE, fill=C_SUB)
    d.line([rx, ly + 48, rx + rw, ly + 48], fill=C_LINE, width=1)

    max_lines = (rh - 66 - 22) // LINE_H
    draw_lines(d, rx + 24, ly + 66, lines, max_lines, max_lines)

    out = FIGS / out_name
    canvas.save(out, quality=95)
    print(f"{out.name}  {canvas.width}x{canvas.height}  {len(lines)} 行 JSON")
    return out


# ------------------------------------------------------------ 出图
allj = ROOT / "output" / "extract_all.json"
S = ROOT / "samples"
made = [
    make_figure(S / "invoice.png", allj, 0, "01_invoice.png",
                "样例 1 · 发票图片 → 结构化 JSON",
                "左：发票 PNG（900×1180）　右：模型识别出的字段与明细表，嵌套结构原样保留"),
    make_figure(S / "table_photo.jpg", allj, 0, "02_table_photo.png",
                "样例 2 · 拍歪的表格照片 → 表格数组",
                "左：JPEG 照片（旋转 −1.6°、轻微噪点与模糊）　右：行列完整还原为 tables[0].rows"),
    make_figure(S / "contract_2pages.pdf", allj, 0, "03_contract_p1.png",
                "样例 3 · 两页 PDF → 逐页 JSON",
                "左：PDF 第 1 页（脚本内部按 150 dpi 渲染成图）　右：第 1 页提取结果，第 2 页同样输出"),
]

# 一张总览：三份输入并排，右边放合并后的结果摘要
print("done")
