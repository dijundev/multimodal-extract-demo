"""核心逻辑的单测。不联网，不调模型，跑得快。"""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import extract_demo as ed  # noqa: E402
import verify as vf  # noqa: E402

SAMPLES = ROOT / "samples"


# ---------------------------------------------------------------- JSON 解析
def test_parse_plain_json():
    assert ed.parse_json('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    text = '```json\n{"a": 1}\n```'
    assert ed.parse_json(text) == {"a": 1}


def test_parse_with_chatter():
    text = '好的，以下是提取结果：\n{"a": 1}\n希望有帮助。'
    assert ed.parse_json(text) == {"a": 1}


def test_parse_garbage_keeps_raw():
    out = ed.parse_json("这一页看不清，没法提取")
    assert "_parse_error" in out
    assert out["_raw_output"] == "这一页看不清，没法提取"


# ---------------------------------------------------------------- 图片转 data URI
def test_to_data_uri_resizes_long_side():
    img = Image.new("RGB", (1200, 3000), "white")
    uri, note = ed.to_data_uri(img, max_side=1600, quality=90)
    assert uri.startswith("data:image/jpeg;base64,")
    assert "1600" in note  # 长边压到 1600


def test_to_data_uri_keeps_small_image():
    img = Image.new("RGB", (400, 300), "white")
    _, note = ed.to_data_uri(img, max_side=1600, quality=90)
    assert note.startswith("400x300")
    assert "->" not in note


def test_to_data_uri_flattens_alpha(tmp_path):
    img = Image.new("RGBA", (100, 100), (255, 0, 0, 0))
    uri, _ = ed.to_data_uri(img, max_side=1600, quality=90)
    assert uri.startswith("data:image/jpeg;base64,")  # RGBA 被压到白底 RGB


# ---------------------------------------------------------------- 页数
def test_count_pages_image():
    assert ed.count_pages(SAMPLES / "invoice.png") == 1


def test_count_pages_pdf():
    assert ed.count_pages(SAMPLES / "contract_2pages.pdf") == 2


def test_count_pages_unsupported(tmp_path):
    f = tmp_path / "a.docx"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        ed.count_pages(f)


# ---------------------------------------------------------------- 缓存
class FakeArgs:
    dpi, max_side, quality = 150, 1600, 92


def test_cache_roundtrip(tmp_path):
    c = ed.Cache(tmp_path, enabled=True)
    key = c.key("digest", 1, FakeArgs, "m", {"a": "b"})
    assert c.get(key) is None
    c.put(key, {"ok": True, "data": {"a": 1}})
    assert c.get(key)["data"] == {"a": 1}
    assert (c.hits, c.misses) == (1, 1)


def test_cache_key_depends_on_params(tmp_path):
    c = ed.Cache(tmp_path, enabled=True)
    k1 = c.key("digest", 1, FakeArgs, "m1", {"a": "b"})
    k2 = c.key("digest", 1, FakeArgs, "m2", {"a": "b"})
    k3 = c.key("digest", 2, FakeArgs, "m1", {"a": "b"})
    assert len({k1, k2, k3}) == 3


def test_cache_disabled(tmp_path):
    c = ed.Cache(tmp_path, enabled=False)
    key = c.key("digest", 1, FakeArgs, "m", {})
    c.put(key, {"ok": True})
    assert c.get(key) is None


# ---------------------------------------------------------------- 校验规则
def _invoice_data():
    return {
        "tables": [{
            "title": "",
            "headers": ["项目名称", "数量", "单价", "金额"],
            "rows": [["纸", "12", "28.00", "336.00"], ["椅子", "3", "899.00", "2697.00"]],
        }],
        "key_values": {"价税合计（小写）": "3033.00", "开票日期": "2026-11-08"},
        "full_text": "发票内容",
    }


def test_verify_all_pass():
    levels = {c["level"] for c in vf.verify_page(_invoice_data(), 0.01)}
    assert levels == {"pass"}


def test_verify_catches_wrong_amount():
    data = _invoice_data()
    data["tables"][0]["rows"][0][3] = "363.00"  # 12×28 应该是 336
    fails = [c for c in vf.verify_page(data, 0.01) if c["level"] == "fail"]
    assert any("单价核对" in c["name"] for c in fails)


def test_verify_catches_missing_cell():
    data = _invoice_data()
    data["tables"][0]["rows"][1] = data["tables"][0]["rows"][1][:3]
    fails = [c for c in vf.verify_page(data, 0.01) if c["level"] == "fail"]
    assert any("列数" in c["name"] for c in fails)


def test_verify_catches_sum_mismatch():
    data = _invoice_data()
    data["key_values"]["价税合计（小写）"] = "9999.00"
    fails = [c for c in vf.verify_page(data, 0.01) if c["level"] == "fail"]
    assert any("合计核对" in c["name"] for c in fails)


def test_verify_flags_cn_date():
    data = _invoice_data()
    data["key_values"]["开票日期"] = "2026年11月8日"
    warns = [c for c in vf.verify_page(data, 0.01) if c["level"] == "warn"]
    assert any("日期" in c["name"] for c in warns)


def test_parse_number_formats():
    assert vf.parse_number("¥4,028.00 元") == 4028.0
    assert vf.parse_number("(120.00)") == -120.0
    assert vf.parse_number("肆仟零贰拾捌圆整") is None
    assert vf.parse_number(12) == 12.0


# ---------------------------------------------------------------- 提示词
def test_prompt_contains_schema_and_page_info():
    prompt = ed.build_prompt({"invoice_no": "发票号码"}, 2, 3, "")
    assert "第 2 页（共 3 页）" in prompt
    assert "invoice_no" in prompt


def test_prompt_carries_pdf_text():
    prompt = ed.build_prompt({"a": "b"}, 1, 1, "合同正文内容")
    assert "合同正文内容" in prompt


def test_system_prompt_blocks_injection():
    assert "不要执行" in ed.SYSTEM_PROMPT


# ---------------------------------------------------------------- 环境变量
def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("EXTRACT_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("EXTRACT_MODEL", "from-env")
    ed.load_dotenv(env)
    assert __import__("os").environ["EXTRACT_MODEL"] == "from-env"
