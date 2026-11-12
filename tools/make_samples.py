"""生成三个不同格式的待提取样例：发票图片、拍歪的表格照片、两页 PDF。

里面的人名、公司、税号全是编的，只用来验证提取链路。
    python tools/make_samples.py       # 覆盖写 samples/
"""
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
OUT.mkdir(parents=True, exist_ok=True)

# 中文字体挨个找，找不到就退回 PIL 默认（可能出方框，但不会崩）
FONT_CANDIDATES = {
    False: ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc"],
    True: ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
}


def _font_path(bold: bool):
    for p in FONT_CANDIDATES[bold]:
        if Path(p).is_file():
            return p
    return None


def f(size, bold=False):
    path = _font_path(bold)
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1 不收 size
        return ImageFont.load_default()


# ---------------- 1. 电子发票 ----------------
W, H = 900, 1180
img = Image.new("RGB", (W, H), "#FFFFFF")
d = ImageDraw.Draw(img)
d.rectangle([0, 0, W, 96], fill="#C0392B")
d.text((40, 24), "电子发票（普通发票）", font=f(38, True), fill="white")
d.text((W - 300, 36), "发票代码 044001900111", font=f(18), fill="#F5D6D2")

y = 130
d.text((40, y), "发票号码：", font=f(22), fill="#333333")
d.text((160, y), "24312000000123456789", font=f(22, True), fill="#111111")
d.text((520, y), "开票日期：2026年11月08日", font=f(22), fill="#333333")

y = 180
d.line([40, y, W - 40, y], fill="#DDDDDD", width=2)
y += 16
d.text((40, y), "购买方", font=f(20, True), fill="#C0392B")
d.text((140, y), "名称：深圳市云栈科技有限公司", font=f(21), fill="#222222")
y += 34
d.text((140, y), "纳税人识别号：91440300MA5F1XYZ2K", font=f(21), fill="#222222")
y += 34
d.text((140, y), "地址电话：深圳市南山区科技园 0755-8888 6666", font=f(21), fill="#222222")

y += 56
d.line([40, y, W - 40, y], fill="#DDDDDD", width=2)
y += 16
d.text((40, y), "销售方", font=f(20, True), fill="#C0392B")
d.text((140, y), "名称：广州启明办公用品有限公司", font=f(21), fill="#222222")
y += 34
d.text((140, y), "纳税人识别号：91440101MA9U2ABC7P", font=f(21), fill="#222222")

# 明细表
y += 60
cols = [(40, 250), (250, 470), (470, 590), (590, 720), (720, 860)]
heads = ["项目名称", "规格型号", "数量", "单价", "金额"]
rows = [
    ["A4 复印纸", "70g/500 张", "12", "28.00", "336.00"],
    ["人体工学椅", "YQ-880", "3", "899.00", "2697.00"],
    ["无线键鼠套装", "K380+M330", "5", "199.00", "995.00"],
]
d.rectangle([40, y, 860, y + 44], fill="#F2F4F7")
for (x0, x1), h in zip(cols, heads):
    d.text((x0 + 12, y + 10), h, font=f(19, True), fill="#333333")
y += 44
for r in rows:
    d.line([40, y, 860, y], fill="#E6E6E6", width=1)
    for (x0, x1), cell in zip(cols, r):
        d.text((x0 + 12, y + 10), cell, font=f(19), fill="#222222")
    y += 44
d.rectangle([40, y, 860, y + 52], outline="#CCCCCC", width=2)

y += 16
d.text((470, y), "价税合计（大写）", font=f(19), fill="#333333")
d.text((620, y), "肆仟零贰拾捌圆整", font=f(19, True), fill="#111111")
y += 46
d.text((560, y), "（小写）¥4,028.00", font=f(24, True), fill="#111111")

# 二维码占位
qy = H - 260
random.seed(7)
for i in range(18):
    for j in range(18):
        if random.random() > 0.5:
            d.rectangle([60 + i * 8, qy + j * 8, 60 + i * 8 + 8, qy + j * 8 + 8], fill="#111111")

d.text((250, qy + 8), "收款人：李静", font=f(20), fill="#333333")
d.text((250, qy + 44), "复核：王强", font=f(20), fill="#333333")
d.text((250, qy + 80), "开票人：张敏", font=f(20), fill="#333333")

# 票面红章
stamp = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
sd = ImageDraw.Draw(stamp)
sd.ellipse([10, 10, 210, 210], outline=(200, 40, 40, 150), width=5)
sd.text((58, 78), "发票专用章", font=f(26, True), fill=(200, 40, 40, 160))
stamp = stamp.rotate(-12, resample=Image.BICUBIC, expand=False)
img.paste(stamp, (600, H - 250), stamp)
img.save(OUT / "invoice.png")

# ---------------- 2. 拍歪的商品清单 ----------------
W2, H2 = 1000, 760
img2 = Image.new("RGB", (W2, H2), "#FAFAF7")
d2 = ImageDraw.Draw(img2)
d2.text((50, 40), "2026 年 10 月办公用品采购清单", font=f(34, True), fill="#1F2937")
d2.text((50, 96), "部门：行政部    制表人：陈晓    制表日期：2026-11-01", font=f(20), fill="#4B5563")

x0, y0 = 50, 150
cw = [70, 300, 120, 130, 130]
heads2 = ["序号", "物品名称", "数量", "单价(元)", "小计(元)"]
rows2 = [
    ["1", "晨光中性笔 0.5mm", "48", "2.50", "120.00"],
    ["2", "得力订书机", "6", "35.00", "210.00"],
    ["3", "兄弟标签打印机", "2", "760.00", "1520.00"],
    ["4", "3M 双面胶", "20", "8.80", "176.00"],
    ["5", "碎纸机 5 级保密", "1", "1280.00", "1280.00"],
]
d2.rectangle([x0, y0, x0 + sum(cw), y0 + 52], fill="#1F2937")
cx = x0
for w, h in zip(cw, heads2):
    d2.text((cx + 14, y0 + 12), h, font=f(21, True), fill="white")
    cx += w
ry = y0 + 52
for i, r in enumerate(rows2):
    if i % 2 == 0:
        d2.rectangle([x0, ry, x0 + sum(cw), ry + 52], fill="#F3F4F6")
    cx = x0
    for w, cell in zip(cw, r):
        d2.text((cx + 14, ry + 12), cell, font=f(20), fill="#111827")
        cx += w
    ry += 52
d2.line([x0, ry, x0 + sum(cw), ry], fill="#9CA3AF", width=2)
d2.text((x0 + 610, ry + 18), "合计：", font=f(22, True), fill="#111827")
d2.text((x0 + 690, ry + 18), "3,306.00 元", font=f(22, True), fill="#B91C1C")
d2.text((x0, ry + 90), "备注：以上金额含税，请于 2026-11-15 前报销。", font=f(20), fill="#4B5563")

img2 = img2.rotate(-1.6, resample=Image.BICUBIC, expand=True, fillcolor="#FAFAF7")
img2 = img2.filter(ImageFilter.GaussianBlur(0.6))
noise = Image.effect_noise(img2.size, 14).convert("L")
img2 = Image.blend(img2, Image.merge("RGB", (noise, noise, noise)), 0.05)
img2.save(OUT / "table_photo.jpg", quality=82)

# ---------------- 3. 两页 PDF ----------------
PW, PH = 1240, 1754


def page1():
    p = Image.new("RGB", (PW, PH), "white")
    dd = ImageDraw.Draw(p)
    dd.text((150, 130), "房 屋 租 赁 合 同", font=f(48, True), fill="#111111")
    dd.line([150, 210, PW - 150, 210], fill="#111111", width=2)
    lines = [
        ("甲方（出租方）：深圳市云栈科技有限公司", 24),
        ("乙方（承租方）：深圳市南屿设计工作室", 24),
        ("第一条  租赁物：深圳市南山区科技园科苑路 8 号 3 栋 502 室，", 22),
        ("        建筑面积 186.5 平方米。", 22),
        ("第二条  租赁期限：自 2026 年 12 月 1 日起至 2028 年 11 月 30 日止。", 22),
        ("第三条  租金：每月人民币 32,000 元（大写：叁万贰仟圆整），", 22),
        ("        押金两个月共计 64,000 元。", 22),
        ("第四条  付款方式：乙方应于每月 5 日前支付当月租金至甲方账户。", 22),
        ("第五条  物业费、水电费由乙方按实际用量承担。", 22),
    ]
    yy = 270
    for text, size in lines:
        dd.text((150, yy), text, font=f(size), fill="#1F2328")
        yy += 62 if size >= 22 else 60
    dd.text((150, PH - 160), "第 1 页 / 共 2 页", font=f(20), fill="#6B7280")
    return p


def page2():
    p = Image.new("RGB", (PW, PH), "white")
    dd = ImageDraw.Draw(p)
    dd.text((150, 130), "第六条  违约责任与争议解决", font=f(36, True), fill="#111111")
    paras = [
        "1. 乙方逾期支付租金超过 15 日的，甲方有权解除本合同，",
        "   并没收全部押金。",
        "2. 甲方未按约定交付房屋的，应向乙方支付一个月租金作为违约金。",
        "3. 因本合同引起的争议，双方应协商解决；协商不成的，提交深圳",
        "   国际仲裁院仲裁。",
        "",
        "第七条  其他约定",
        "本合同一式两份，甲乙双方各执一份，自双方签字盖章之日起生效。",
    ]
    yy = 230
    for t in paras:
        dd.text((150, yy), t, font=f(23), fill="#1F2328")
        yy += 52
    dd.text((150, 900), "甲方（盖章）：", font=f(24), fill="#111111")
    dd.text((760, 900), "乙方（盖章）：", font=f(24), fill="#111111")
    dd.text((150, 990), "日期：2026 年 11 月 10 日", font=f(23), fill="#1F2328")
    dd.text((760, 990), "日期：2026 年 11 月 10 日", font=f(23), fill="#1F2328")
    st = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    sd.ellipse([12, 12, 248, 248], outline=(198, 40, 40, 170), width=6)
    sd.text((52, 60), "云栈科技", font=f(30, True), fill=(198, 40, 40, 180))
    sd.text((60, 130), "有限公司", font=f(24, True), fill=(198, 40, 40, 180))
    st = st.rotate(-10, resample=Image.BICUBIC)
    p.paste(st, (170, 700), st)
    dd.text((150, PH - 160), "第 2 页 / 共 2 页", font=f(20), fill="#6B7280")
    return p


p1, p2 = page1(), page2()
p1.save(OUT / "contract_2pages.pdf", save_all=True, append_images=[p2], resolution=150.0)

for p in sorted(OUT.iterdir()):
    print(f"{p.name:24s} {p.stat().st_size/1024:8.1f} KB")
