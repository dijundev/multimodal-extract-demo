#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取结果自检
============
模型读完文档不等于读对。这个脚本拿"文档自身应该满足的算术关系"去验提取结果，
不需要标准答案：

    1. 表格每行的列数是否和表头一致（少一格就是漏了单元格）
    2. 数量 × 单价 ≈ 金额（容差可调）
    3. 明细行求和 ≈ 合计 / 价税合计（这条最能抓错）
    4. 日期是否 YYYY-MM-DD，年份是否离谱
    5. 关键字段是不是空了一大片

用法：
    python verify.py                              # 读 output/extract_all.json
    python verify.py result.json --tol 0.01
    python verify.py result.json --report check.json

有 fail 时进程退出码为 1，方便挂到 CI 或者循环脚本里。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# 表头关键词，用中文就行；换了别的语种可以自己加
QTY_KEYS = ("数量", "件数", "qty", "quantity")
PRICE_KEYS = ("单价", "price", "unit price")
AMOUNT_KEYS = ("小计", "金额", "总额", "amount", "subtotal")
TOTAL_KEYS = ("价税合计", "合计", "总计", "总金额", "应付")

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CN_DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")


def parse_number(value) -> float | None:
    """把 '¥4,028.00 元'、'(120.00)' 这类写法转成 float，转不了返回 None。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip().replace(",", "").replace("，", "").replace("¥", "") \
        .replace("￥", "").replace("元", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    m = NUM_RE.search(s)
    if not m:
        return None
    try:
        n = float(m.group())
    except ValueError:
        return None
    return -n if neg else n


def pick_col(headers: list, keywords: tuple) -> int | None:
    """在表头里找第一个包含关键词的列。"""
    for i, h in enumerate(headers):
        text = str(h)
        for kw in keywords:
            if kw in text.lower() or kw in text:
                return i
    return None


def check(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


def verify_page(data: dict, tol: float) -> list[dict]:
    """对一页的提取结果跑全部规则，返回检查项列表。"""
    checks: list[dict] = []
    if not isinstance(data, dict):
        return [check("结果结构", "fail", "data 不是对象")]

    tables = data.get("tables") or []
    kv = data.get("key_values") or {}

    # 1. 表格列数
    for ti, tb in enumerate(tables, start=1):
        headers = tb.get("headers") or []
        rows = tb.get("rows") or []
        if not headers and not rows:
            continue
        bad = [i + 1 for i, r in enumerate(rows) if len(r) != len(headers)]
        if bad:
            checks.append(check(
                f"表格{ti}列数", "fail",
                f"{len(headers)} 列表头，第 {', '.join(map(str, bad[:5]))} 行对不上"))
        else:
            checks.append(check(f"表格{ti}列数", "pass",
                                f"{len(rows)} 行 × {len(headers)} 列"))

    # 2. 数量 × 单价 ≈ 金额；3. 明细求和 ≈ 合计
    for ti, tb in enumerate(tables, start=1):
        headers = tb.get("headers") or []
        rows = tb.get("rows") or []
        if not headers or not rows:
            continue
        ci_q = pick_col(headers, QTY_KEYS)
        ci_p = pick_col(headers, PRICE_KEYS)
        ci_a = pick_col(headers, AMOUNT_KEYS)

        if ci_q is not None and ci_p is not None and ci_a is not None:
            wrong = []
            for r in rows:
                if max(ci_q, ci_p, ci_a) >= len(r):
                    continue
                q, p, a = parse_number(r[ci_q]), parse_number(r[ci_p]), parse_number(r[ci_a])
                if None in (q, p, a):
                    continue
                if abs(q * p - a) > max(tol, 0.01):
                    wrong.append(f"{r[0] if r else '?'}: {q}×{p}={q * p:.2f}，表上写 {a:.2f}")
            if wrong:
                checks.append(check(f"表格{ti}单价核对", "fail", "；".join(wrong[:3])))
            else:
                checks.append(check(f"表格{ti}单价核对", "pass", "数量×单价都对得上"))

        if ci_a is not None:
            amounts = [parse_number(r[ci_a]) for r in rows if ci_a < len(r)]
            amounts = [x for x in amounts if x is not None]
            stated = None
            for k, v in kv.items():
                if any(t in str(k) for t in TOTAL_KEYS):
                    n = parse_number(v)
                    # 大写金额没有数字，跳过；"合计：3,306.00 元"能解析
                    if n is not None and "大写" not in str(k):
                        stated = (k, n)
                        break
            if stated and amounts:
                diff = abs(sum(amounts) - stated[1])
                if diff > max(tol, 0.01):
                    checks.append(check(
                        f"表格{ti}合计核对", "fail",
                        f"明细求和 {sum(amounts):.2f}，{stated[0]} 写 {stated[1]:.2f}，差 {diff:.2f}"))
                else:
                    checks.append(check(
                        f"表格{ti}合计核对", "pass",
                        f"明细求和 {sum(amounts):.2f} = {stated[0]}"))

    # 4. 日期
    for k, v in kv.items():
        if "日期" not in str(k) or v in (None, ""):
            continue
        text = str(v)
        if DATE_RE.match(text):
            year = int(text[:4])
            level = "pass" if 1990 <= year <= 2100 else "fail"
            detail = text if level == "pass" else f"{text} 年份异常"
        elif CN_DATE_RE.search(text):
            level, detail = "warn", f"{text}，建议后处理成 YYYY-MM-DD"
        else:
            level, detail = "warn", f"「{text}」不像标准日期"
        checks.append(check(f"日期 {k}", level, detail))

    # 5. 空字段
    if kv:
        empty = [k for k, v in kv.items() if v in (None, "", [], {})]
        ratio = len(empty) / len(kv)
        if ratio >= 0.5:
            checks.append(check("字段完整度", "warn",
                                f"{len(empty)}/{len(kv)} 个字段是空的：{', '.join(empty[:4])}"))
        elif empty:
            checks.append(check("字段完整度", "pass",
                                f"空字段 {len(empty)} 个：{', '.join(empty[:4])}"))
        else:
            checks.append(check("字段完整度", "pass", "没有空字段"))

    if not data.get("full_text"):
        checks.append(check("正文", "warn", "full_text 是空的"))

    return checks


def verify_file(path: Path, tol: float) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    results = []
    totals = {"pass": 0, "warn": 0, "fail": 0}

    records = doc["results"] if "results" in doc else [doc]
    for rec in records:
        pages = []
        for pg in rec.get("pages", []):
            if not pg.get("ok", True) or "data" not in pg:
                checks = [check("页面状态", "fail", pg.get("error") or "该页没有提取结果")]
            else:
                checks = verify_page(pg["data"], tol)
            counter = {"pass": 0, "warn": 0, "fail": 0}
            for c in checks:
                counter[c["level"]] += 1
                totals[c["level"]] += 1
            pages.append({"page": pg.get("page", "?"), "checks": checks, "count": counter})
        results.append({"file": rec.get("name") or rec.get("file"), "pages": pages})

    return {"source": str(path), "tolerance": tol, "results": results, "totals": totals}


MARK = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL"}


def main() -> int:
    ap = argparse.ArgumentParser(description="提取结果自检")
    ap.add_argument("input", nargs="?", default=str(SCRIPT_DIR / "output" / "extract_all.json"))
    ap.add_argument("--tol", type=float, default=0.01, help="金额允许的误差，默认 0.01")
    ap.add_argument("--report", help="把检查报告另存为 JSON")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    path = Path(args.input)
    if not path.is_file():
        print(f"找不到 {path}，先跑 extract_demo.py", file=sys.stderr)
        return 2

    report = verify_file(path, args.tol)

    for r in report["results"]:
        print(f"\n{r['file']}")
        for pg in r["pages"]:
            print(f"  第 {pg['page']} 页")
            for c in pg["checks"]:
                print(f"    [{MARK[c['level']]}] {c['name']}: {c['detail']}")

    t = report["totals"]
    print(f"\n合计：{t['pass']} 通过 / {t['warn']} 提醒 / {t['fail']} 失败")

    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        print(f"报告：{args.report}")

    return 1 if t["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
