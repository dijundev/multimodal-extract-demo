#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把图片、PDF 丢给视觉模型，拿回结构化 JSON。

用法：
    python extract_demo.py                      # 跑 samples/ 下所有文件
    python extract_demo.py a.jpg b.pdf          # 指定文件
    python extract_demo.py docs/ -o output      # 指定目录和输出目录
    python extract_demo.py a.pdf --schema f.json
    python extract_demo.py --dry-run            # 不调模型，只检查预处理

接口配置，优先级从高到低：
    1. 命令行 --api-key / --base-url / --model
    2. 环境变量 EXTRACT_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY，
               以及 EXTRACT_BASE_URL / EXTRACT_MODEL
    3. 脚本同目录的 .env
    4. 代码里的默认值（DeepSeek + deepseek-flash）

依赖 pillow 和 pymupdf，装法：pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

SCRIPT_DIR = Path(__file__).resolve().parent

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}

# ---------------------------------------------------------------- 默认提取模板
# 模板里的值写的是「字段含义」，模型会替换成真实内容。换模板就是换业务。
DEFAULT_SCHEMA = {
    "doc_type": "文档类型，如 发票 / 合同 / 报表 / 会议纪要",
    "language": "主要语言，zh 或 en",
    "summary": "一句话概括这份材料讲什么",
    "key_values": {
        "字段名": "关键字段值，例如 发票号码、金额、日期、签约方；按实际出现的内容填"
    },
    "tables": [
        {"title": "表格标题，没有就留空", "headers": ["列名"], "rows": [["单元格"]]}
    ],
    "full_text": "按阅读顺序的完整文字，保留段落换行；不要臆造内容",
    "notes": "识别不清、被遮挡或缺失的信息写在这里，没有就留空",
}

SYSTEM_PROMPT = """你是一个文档信息提取引擎。

规则：
1. 只输出一个 JSON 对象，不要输出任何解释、前言、Markdown 代码块符号。
2. 严格使用用户给定的字段名和层级，不要增删字段。
3. 只提取图片里真实存在的信息；看不清或没有的字段填 null 或空字符串，绝不编造。
4. 数字只保留数值（去掉千分位逗号），金额保留两位小数，日期统一写成 YYYY-MM-DD。
5. 表格按行列还原，保持原有的行顺序。
"""

# ---------------------------------------------------------------- 环境与小工具


def load_dotenv(path: Path) -> None:
    """极简 .env 读取：每行 KEY=VALUE，# 开头忽略。已存在的环境变量不覆盖。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def to_data_uri(img: Image.Image, max_side: int, quality: int) -> tuple[str, str]:
    """PIL 图像 -> data URI。顺带等比缩小，控制 token 消耗。返回 (uri, 说明)。"""
    img = ImageOps.exif_transpose(img)  # 手机照片按 EXIF 转正
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    note = f"{img.width}x{img.height}"
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                         Image.LANCZOS)
        note += f" -> {img.width}x{img.height}"

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    data = buf.getvalue()
    uri = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    return uri, f"{note}, {human_size(len(data))}"


# ---------------------------------------------------------------- 输入预处理


def count_pages(path: Path) -> int:
    """先数一下多少页，用来在提示词里写「第 3 页 / 共 8 页」。"""
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        import pymupdf
        with pymupdf.open(path) as doc:
            return doc.page_count
    if ext in IMAGE_EXT:
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1)
    raise ValueError(f"暂不支持的文件类型：{ext or '(无扩展名)'}")


def iter_pages(path: Path, dpi: int):
    """一页一页产出 (页码, 图像, 原生文本)。

    PDF 用 PyMuPDF 渲染成位图，扫描件没有文本层也能处理；图片直接按帧拆。
    这里是生成器，几十页的 PDF 不会一次性把图都塞进内存。
    """
    ext = path.suffix.lower()

    if ext in PDF_EXT:
        import pymupdf  # 延迟导入，只装了 pillow 也能跑图片部分

        with pymupdf.open(path) as doc:
            for i, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=dpi)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                yield i, img, (page.get_text() or "").strip()
        return

    if ext in IMAGE_EXT:
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            for i in range(n):
                im.seek(i)
                yield i + 1, im.copy(), ""
        return

    raise ValueError(f"暂不支持的文件类型：{ext or '(无扩展名)'}")


# ---------------------------------------------------------------- 模型调用


class ModelClient:
    """OpenAI 兼容的 chat/completions 客户端，只用标准库，带重试。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 120):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def extract(self, image_uri: str, prompt: str, retries: int = 3) -> tuple[dict, dict]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_uri}},
                    ],
                },
            ],
        }

        last_err = ""
        for attempt in range(1, retries + 1):
            try:
                resp = self._post(payload)
                text = resp["choices"][0]["message"]["content"] or ""
                return parse_json(text), resp.get("usage", {})
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:300]
                last_err = f"HTTP {e.code}: {body}"
                if e.code not in (408, 409, 429, 500, 502, 503, 504):
                    break  # 4xx 重试没意义
            except (urllib.error.URLError, TimeoutError, KeyError) as e:
                last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(2 * attempt)
        raise RuntimeError(last_err or "未知错误")


def parse_json(text: str) -> dict:
    """模型偶尔带回 ```json 包裹或前后废话，这里做兜底清洗。"""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```")[1] if "```" in s[3:] else s.lstrip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    s = s.strip().strip("`").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {"_raw_output": text, "_parse_error": "模型返回不是合法 JSON"}


# ---------------------------------------------------------------- 提示词拼装


def build_prompt(schema: dict, page_no: int, total: int, native_text: str) -> str:
    parts = [
        f"这是文档的第 {page_no} 页（共 {total} 页）。请按下面的 JSON 模板提取本页内容：",
        json.dumps(schema, ensure_ascii=False, indent=2),
        "只输出 JSON，字段名与模板完全一致。",
    ]
    if native_text:
        # PDF 自带文本层时附上，模型可以校准识别结果，减少错字
        parts.append("参考：该页 PDF 文本层内容（可能有排版错乱，仅供校对）：\n" + native_text[:4000])
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 主流程


def collect_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for t in targets:
        p = Path(t).expanduser()
        if p.is_dir():
            files.extend(sorted(x for x in p.rglob("*")
                                if x.is_file() and x.suffix.lower() in IMAGE_EXT | PDF_EXT))
        elif p.is_file():
            files.append(p)
        else:
            print(f"  ! 找不到：{t}", file=sys.stderr)
    return files


def process_file(path: Path, client: ModelClient | None, schema: dict,
                 args) -> dict:
    ext = path.suffix.lower()
    kind = "pdf" if ext in PDF_EXT else ("image" if ext in IMAGE_EXT else "unknown")
    record = {
        "file": str(path),
        "name": path.name,
        "kind": kind,
        "size": human_size(path.stat().st_size),
        "size_bytes": path.stat().st_size,
        "pages": [],
    }

    started = time.time()
    try:
        total = count_pages(path)
    except Exception as e:
        record["error"] = f"打不开文件：{type(e).__name__}: {e}"
        record["elapsed_sec"] = round(time.time() - started, 2)
        return record

    page_no = 0
    try:
        for page_no, img, native_text in iter_pages(path, args.dpi):
            page_rec: dict = {"page": page_no}
            try:
                uri, img_note = to_data_uri(img, args.max_side, args.quality)
                page_rec["image"] = img_note
                if client is None:  # --dry-run
                    page_rec["ok"] = True
                    page_rec["skipped"] = "dry-run，未调用模型"
                    record["pages"].append(page_rec)
                    continue

                t0 = time.time()
                data, usage = client.extract(
                    uri, build_prompt(schema, page_no, total, native_text))
                page_rec.update({
                    "ok": "_parse_error" not in data,
                    "data": data,
                    "usage": usage,
                    "elapsed_sec": round(time.time() - t0, 2),
                })
            except Exception as e:
                page_rec.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
            record["pages"].append(page_rec)
    except Exception as e:
        # 渲染中途出错（坏 PDF、加密文件等），把已经跑完的页留住
        record["pages"].append({"page": page_no + 1, "ok": False,
                                "error": f"读取页面失败：{type(e).__name__}: {e}"})

    record["elapsed_sec"] = round(time.time() - started, 2)
    record["ok"] = all(p.get("ok") for p in record["pages"])
    return record


def main() -> int:
    ap = argparse.ArgumentParser(
        description="多模态信息提取 demo：图片 / PDF -> JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("targets", nargs="*", help="文件或目录，默认 samples/")
    ap.add_argument("-o", "--out", default=str(SCRIPT_DIR / "output"), help="输出目录")
    ap.add_argument("--schema", help="自定义字段模板 JSON 文件")
    ap.add_argument("--model", help="模型名，如 deepseek-flash / gpt-4o-mini")
    ap.add_argument("--base-url", help="OpenAI 兼容接口地址")
    ap.add_argument("--api-key", help="API Key（不传则读环境变量或 .env）")
    ap.add_argument("--dpi", type=int, default=150, help="PDF 渲染分辨率，默认 150")
    ap.add_argument("--max-side", type=int, default=1600, help="图片长边上限，默认 1600")
    ap.add_argument("--quality", type=int, default=92, help="转 JPEG 的质量，默认 92")
    ap.add_argument("--jobs", type=int, default=3, help="并发文件数，默认 3")
    ap.add_argument("--timeout", type=int, default=120, help="单次请求超时秒数，默认 120")
    ap.add_argument("--dry-run", action="store_true", help="只做预处理不调模型（不花钱）")
    ap.add_argument("--show", action="store_true", help="把 JSON 打到终端")
    args = ap.parse_args()

    # Windows 老控制台默认 GBK，中文容易乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    load_dotenv(SCRIPT_DIR / ".env")

    api_key = args.api_key or os.getenv("EXTRACT_API_KEY") or os.getenv("OPENAI_API_KEY") \
        or os.getenv("DEEPSEEK_API_KEY") or ""
    base_url = args.base_url or os.getenv("EXTRACT_BASE_URL") or "https://api.deepseek.com"
    model = args.model or os.getenv("EXTRACT_MODEL") or "deepseek-flash"

    schema = DEFAULT_SCHEMA
    if args.schema:
        raw = json.loads(Path(args.schema).read_text(encoding="utf-8"))
        schema = raw.get("fields", raw)
        print(f"字段模板：{args.schema}（{len(schema)} 个字段）")

    targets = args.targets or [str(SCRIPT_DIR / "samples")]
    files = collect_files(targets)
    if not files:
        print("没有可处理的文件。", file=sys.stderr)
        return 2

    client = None
    if not args.dry_run:
        if not api_key:
            print("缺少 API Key：用 --api-key 传，或写进 .env / 环境变量 EXTRACT_API_KEY。",
                  file=sys.stderr)
            return 2
        client = ModelClient(base_url, api_key, model, timeout=args.timeout)

    print(f"模型：{model} @ {base_url}")
    print(f"待处理：{len(files)} 个文件 -> {args.out}")
    print("-" * 68)

    def report(r: dict, name: str) -> None:
        """打印单个文件的结果行；失败时把原因带出来，不用去翻 JSON。"""
        tail = f"  {len(r['pages'])} 页 / {r.get('elapsed_sec', 0)}s"
        if not r.get("ok"):
            reason = r.get("error") or next(
                (p.get("error") for p in r["pages"] if p.get("error")), "见 JSON")
            tail += f"  [失败：{reason[:60]}]"
        print(f"  · {name}{tail}")

    started = time.time()
    results: list[dict] = []
    if args.jobs <= 1:
        for p in files:
            r = process_file(p, client, schema, args)
            report(r, p.name)
            results.append(r)
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(process_file, p, client, schema, args): p for p in files}
            for fut, p in futures.items():
                r = fut.result()
                report(r, p.name)
                results.append(r)

    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for r in results:
        for pg in r["pages"]:
            for k in usage_total:
                usage_total[k] += (pg.get("usage") or {}).get(k, 0)

    payload = {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool": "multimodal-extract-demo/1.0",
            "provider": {"base_url": base_url, "model": model},
            "settings": {"dpi": args.dpi, "max_side": args.max_side,
                         "quality": args.quality, "dry_run": args.dry_run},
            "schema_fields": list(schema.keys()),
            "totals": {
                "files": len(results),
                "pages": sum(len(r["pages"]) for r in results),
                "elapsed_sec": round(time.time() - started, 2),
                "usage": usage_total,
            },
        },
        "results": results,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # dry-run 的结果没有 data，单独存一个文件名，别把上次真正跑出来的结果盖掉
    all_path = out_dir / ("dryrun_report.json" if args.dry_run else "extract_all.json")
    all_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.dry_run:
        for r in results:  # 每个文件再单独存一份，方便直接看
            one = {"meta": payload["meta"], "file": r["file"], "pages": r["pages"]}
            (out_dir / f"{Path(r['name']).stem}.json").write_text(
                json.dumps(one, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 68)
    print(f"完成：{payload['meta']['totals']['files']} 个文件 / "
          f"{payload['meta']['totals']['pages']} 页 / "
          f"{payload['meta']['totals']['elapsed_sec']}s / "
          f"{usage_total['total_tokens']} tokens")
    print(f"结果：{all_path}")

    if args.show:
        print(json.dumps(payload["results"], ensure_ascii=False, indent=2))

    bad = [r for r in results if not r.get("ok")]
    if bad:
        print(f"注意：{len(bad)} 个文件有失败页，详见 JSON 里的 error 字段", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
