# 图片和 PDF 转 JSON 的多模态提取 demo

[![tests](https://github.com/dijundev/multimodal-extract-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/dijundev/multimodal-extract-demo/actions/workflows/ci.yml)

手里攒了一堆发票照片和扫描的合同，想弄成表格，手动敲太慢。这个仓库是我试出来的最小可用版本：把文件丢进去，模型把字段、表格、文字读出来，返回 JSON。

先说清楚它做不到什么。它不保证准确，金额、日期这类关键字段最好自己再核一遍——样例里我是拿 `sum(明细金额) == 合计` 对过的，对得上才敢说跑通了。这个核对后来单独写成了 `verify.py`。

## 跑出来什么效果

三个样例，左边是喂进去的文件，右边是拿到的 JSON。

发票（900×1180 的 PNG）：

![发票样例](https://cdn.jsdelivr.net/gh/dijundev/multimodal-extract-demo@main/figures/01_invoice.png)

用手机拍歪的表格照片，转了 1.6 度，加了点噪点：

![表格样例](https://cdn.jsdelivr.net/gh/dijundev/multimodal-extract-demo@main/figures/02_table_photo.png)

两页的 PDF，脚本内部按 150 dpi 渲染成图再送去识别：

![合同样例](https://cdn.jsdelivr.net/gh/dijundev/multimodal-extract-demo@main/figures/03_contract_p1.png)

## 怎么跑

```bash
pip install -r requirements.txt
cp .env.example .env        # 打开填自己的 key
python extract_demo.py
```

默认跑 `samples/` 下的三个文件。也可以指定文件名或目录：

```bash
python extract_demo.py 我的发票.jpg 合同.pdf
python extract_demo.py docs/ -o result
```

Windows 双击 `run.bat` 也行，里面就是 `python extract_demo.py` 加一句 pause，省得窗口一闪就没了。

想先确认文件能不能处理又不想花钱，加 `--dry-run`。它会把 PDF 渲染成图、打印每页的尺寸和体积，但不发请求：

```bash
python extract_demo.py --dry-run
```

## 一个文件进去，中间发生了什么

1. `collect_files` 把目录里的图片和 PDF 收进来，其它扩展名直接跳过。
2. `count_pages` 先数有几页，后面提示词里要写"第 2 页 / 共 3 页"。
3. `iter_pages` 一页一页往外吐图。PDF 走 PyMuPDF 的 `get_pixmap(dpi=150)` 渲染，图片用 PIL 按 `n_frames` 拆多页 TIFF。这里是生成器，几十页的 PDF 不会一次性把图全塞内存里。
4. `to_data_uri` 把图长边压到 1600、转成 JPEG、base64，拼成 `data:image/jpeg;base64,...`。
5. `build_prompt` 把字段模板和这页的原生文字拼进提示词。PDF 有文本层的，把文字一起给模型，可以少几个错字。
6. 发请求之前先查缓存，命中就直接用上次的结果，不再花钱。
7. `ModelClient.extract` 发一次 `/chat/completions`，429 和 5xx 退避重试三次，4xx 不重试。
8. `parse_json` 剥掉模型可能加的 ``` 包裹，解析成 dict。解析不出来就原样塞进 `_raw_output`，不吞掉。
9. 结果写进 `output/extract_all.json`，每个文件再单独存一份。

没用 openai 这个 SDK，请求是用 urllib 手写的。想看清楚发出去的 body 长什么样，直接看 `ModelClient._post` 那二十行。

## 踩过的坑

**deepseek-v4-pro 不吃图。** 同样的请求，它回我"我无法查看当前图片内容"。换 `deepseek-flash` 就正常了。选模型之前先拿一张图试一下，别看名字猜。

**PDF 直接抽文字会得到空串。** 扫描件没有文本层，`get_text()` 返回空。所以必须渲染成图，这条路绕不过去。

**模型爱把 JSON 包在代码块里。** 前后加 ```json ``` 还算好的，有时候还会来一句"以下是提取结果"。`parse_json` 里就是干这个的：剥壳，再从第一个 `{` 截到最后一个 `}`。

**中文在 Windows 控制台会变成乱码。** `run.bat` 里加了 `chcp 65001`，Python 那边也做了 `sys.stdout.reconfigure(encoding="utf-8")`，两边都得有。

**dry-run 曾经把正式结果盖掉。** 早先两个模式写同一个 `extract_all.json`，跑一次 dry-run 就把上次的结果冲掉了，里面全是没 data 的空壳。现在 dry-run 单独写 `dryrun_report.json`。

## 提取完还得验一下

模型读完不等读对。`verify.py` 不用标准答案，只拿文档本身该成立的算术关系去查：

```bash
python verify.py                               # 默认读 output/extract_all.json
python verify.py result.json --tol 0.01 --report check.json
```

四条规则：表格每行的列数是否和表头一致、数量×单价是否等于金额、明细求和是否等于合计、日期是不是 `YYYY-MM-DD` 且年份不像编的。

样例上跑出来是这样：

```
invoice.png
  第 1 页
    [OK  ] 表格1列数: 3 行 × 5 列
    [OK  ] 表格1单价核对: 数量×单价都对得上
    [OK  ] 表格1合计核对: 明细求和 4028.00 = 价税合计（小写）
    [OK  ] 日期 开票日期: 2026-11-08
    [OK  ] 字段完整度: 没有空字段
```

我把某行金额从 336 故意改成 363 试过，它一次报三个 FAIL：列数对不上、单价算不平、合计差 2670 块，退出码 1。返回值是刻意这样设的，可以直接当闸门挂在批处理脚本里。

它查不出来的也说清楚：如果模型读错的地方恰好让加减乘除都自洽（比如整列都错成同一个数），这套规则发现不了。真想兜住只能对标准答案。

## 同一份文件别付两次钱

每页结果按「文件内容 sha1 + 页码 + 模型 + dpi/长边/质量 + 字段模板」存进 `.cache/`。文件没动就再跑一遍，第二次是 0.5 秒、0 token：

```
  · invoice.png  1 页 / 0.12s（1 页来自缓存）
完成：3 个文件 / 4 页 / 0.47s / 0 tokens（4 页命中缓存，本次没花 token）
```

改提示词那阵子最管用，动一个字不用把整批文件重新烧一遍。文件改了、换模型了、模板变了，或者把 `PROMPT_VERSION` 加一，旧缓存自动失效。`--no-cache` 可以完全绕开。

## 表格顺手导成 Excel

JSON 给人看还行，给同事用还是表格顺眼。加 `--tables-xlsx`：

```bash
python extract_demo.py samples/ --tables-xlsx
```

一张表一个 sheet，表头加粗，列宽按内容估。`output/invoice.tables.xlsx` 和 `output/table_photo.tables.xlsx` 就是这么出来的。

## 测试

```bash
pip install -r requirements-dev.txt
pytest -q        # 23 个用例，1 秒跑完，不联网
```

覆盖的都是容易错又不依赖网络的地方：JSON 剥壳、图片缩放和透明底压平、PDF 页数、缓存命中与失效、校验规则能不能抓到错的金额和缺的列、提示词里有没有带上页码和那条防注入。

GitHub Actions 每次 push 和 PR 会跑 3.10 和 3.12 两个版本，顺便再跑一遍 `--dry-run` 确认预处理没坏。CI 里不调模型，不烧钱。

## 默认字段

`DEFAULT_SCHEMA` 里的值写的是"这个字段是什么意思"，模型负责换成真实内容，所以改这个 dict 就是换业务：

- `doc_type`、`language`：文档类型和语种
- `summary`：一句话概括
- `key_values`：关键字段，按实际出现的填，不预设键名
- `tables`：`title` + `headers` + `rows`，保持原行序
- `full_text`：按阅读顺序的完整文字
- `notes`：看不清、被遮挡、缺失的信息

要固定字段（比如只抽发票），写个 JSON 传进去：

```bash
python extract_demo.py samples/invoice.png --schema examples/invoice_fields.json
```

模板长这样，键名就是输出里的键名：

```json
{
  "invoice_no": "发票号码",
  "issue_date": "开票日期，格式 YYYY-MM-DD",
  "total_amount": "价税合计小写金额，只要数字",
  "items": [{ "name": "项目名称", "qty": "数量", "unit_price": "单价", "amount": "金额" }]
}
```

## 换模型

走的是 OpenAI 兼容的 `/chat/completions`，只要接口能收 base64 的 `image_url` 就能用。配置按这个顺序找，前面的优先：

命令行 `--api-key --base-url --model` → 环境变量 → `.env` → 代码里的默认值。

```bash
python extract_demo.py --model gpt-4o-mini \
  --base-url https://api.openai.com/v1 --api-key sk-xxx
```

改 `.env` 更省事：

```
EXTRACT_API_KEY=sk-xxx
EXTRACT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EXTRACT_MODEL=qwen-vl-plus
```

`.env` 已经在 `.gitignore` 里了，别手动 `git add -f`。

## 参数

| 参数 | 干什么 | 默认 |
|---|---|---|
| `targets` | 文件或目录，可以写多个 | `samples/` |
| `-o, --out` | 输出目录 | `output/` |
| `--schema` | 自定义字段模板 | 内置通用模板 |
| `--dpi` | PDF 渲染 dpi，调高更清晰也更贵 | 150 |
| `--max-side` | 图片长边上限，超了等比缩小 | 1600 |
| `--quality` | 转 JPEG 的质量 | 92 |
| `--jobs` | 同时跑几个文件 | 3 |
| `--timeout` | 单次请求超时秒数 | 120 |
| `--no-cache` | 不读缓存，每次都真调模型 | 关 |
| `--tables-xlsx` | 把 tables 导成 Excel | 关 |
| `--dry-run` | 只预处理，不调模型 | 关 |
| `--show` | 结果同时打到终端 | 关 |

## 已知问题

- 表格跨页会拆成两张，得自己合。
- 手写体、宽度小于 800px 的图、有反光的照片，错字明显变多。
- 每页花多少 token 基本看图片面积。几十页的 PDF 先 `--dry-run` 看一眼再决定，别直接怼。
- 多页之间不合并，一页一个结果，`full_text` 也是分页存的。
- `verify.py` 只能抓算术矛盾。整列读成同一个错数值这种，它看不出来。
- 缓存按文件内容算，同一个文件改个名字算新文件，会重新花一次钱。

## 花多少钱

三个样例四页，一共 7180 个 token，跑了 6 秒。按写这个的时候 DeepSeek 的价格算，不到一分钱。换 GPT-4o 那档会贵两三个数量级，跑长文档前先算一下页数。

## 目录

```
.
├─ extract_demo.py              主脚本，单文件
├─ verify.py                    结果自检：列数、单价、合计、日期
├─ run.bat                      Windows 双击启动
├─ .env.example                 配置模板，复制成 .env 再填
├─ requirements.txt             pillow、pymupdf、openpyxl
├─ requirements-dev.txt         再加一个 pytest
├─ tests/test_core.py           23 个用例，不联网
├─ .github/workflows/ci.yml     push 就跑测试
├─ examples/invoice_fields.json 固定字段的写法示例
├─ samples/                     三个样例：发票 PNG、拍歪的表格 JPG、两页 PDF
├─ output/                      跑出来的 JSON 和 Excel
└─ figures/                     README 里的对比图
```

`samples/` 里的发票和合同是我用 PIL 画的假数据，公司名、税号都是编的，可以放心用。

README 里那几个图用的是 jsDelivr 直链，不是相对路径。原因：Gitee 不渲染相对路径的图片，而直接从 GitHub 页面引 Gitee 的 raw 链接会被防盗链拦成 403，只有 CDN 这种两边都认。

MIT 许可，拿去改随便。
