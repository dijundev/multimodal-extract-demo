# 图片和 PDF 转 JSON 的多模态提取 demo

手里攒了一堆发票照片和扫描的合同，想弄成表格，手动敲太慢。这个仓库是我试出来的最小可用版本：把文件丢进去，模型把字段、表格、文字读出来，返回 JSON。

先说清楚它做不到什么。它不保证准确，金额、日期这类关键字段最好自己再核一遍——样例里我是拿 `sum(明细金额) == 合计` 对过的，对得上才敢说跑通了。

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
6. `ModelClient.extract` 发一次 `/chat/completions`，429 和 5xx 退避重试三次，4xx 不重试。
7. `parse_json` 剥掉模型可能加的 ``` 包裹，解析成 dict。解析不出来就原样塞进 `_raw_output`，不吞掉。
8. 结果写进 `output/extract_all.json`，每个文件再单独存一份。

没用 openai 这个 SDK，请求是用 urllib 手写的。想看清楚发出去的 body 长什么样，直接看 `ModelClient._post` 那二十行。

## 踩过的坑

**deepseek-v4-pro 不吃图。** 同样的请求，它回我"我无法查看当前图片内容"。换 `deepseek-flash` 就正常了。选模型之前先拿一张图试一下，别看名字猜。

**PDF 直接抽文字会得到空串。** 扫描件没有文本层，`get_text()` 返回空。所以必须渲染成图，这条路绕不过去。

**模型爱把 JSON 包在代码块里。** 前后加 ```json ``` 还算好的，有时候还会来一句"以下是提取结果"。`parse_json` 里就是干这个的：剥壳，再从第一个 `{` 截到最后一个 `}`。

**中文在 Windows 控制台会变成乱码。** `run.bat` 里加了 `chcp 65001`，Python 那边也做了 `sys.stdout.reconfigure(encoding="utf-8")`，两边都得有。

**dry-run 曾经把正式结果盖掉。** 早先两个模式写同一个 `extract_all.json`，跑一次 dry-run 就把上次的结果冲掉了，里面全是没 data 的空壳。现在 dry-run 单独写 `dryrun_report.json`。

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
| `--dry-run` | 只预处理，不调模型 | 关 |
| `--show` | 结果同时打到终端 | 关 |

## 已知问题

- 表格跨页会拆成两张，得自己合。
- 手写体、宽度小于 800px 的图、有反光的照片，错字明显变多。
- 每页花多少 token 基本看图片面积。几十页的 PDF 先 `--dry-run` 看一眼再决定，别直接怼。
- 多页之间不合并，一页一个结果，`full_text` 也是分页存的。

## 花多少钱

三个样例四页，一共 8770 个 token，跑了 10 秒。按写这个的时候 DeepSeek 的价格算，不到一分钱。换 GPT-4o 那档会贵两三个数量级，跑长文档前先算一下页数。

## 目录

```
.
├─ extract_demo.py              主脚本，单文件
├─ run.bat                      Windows 双击启动
├─ .env.example                 配置模板，复制成 .env 再填
├─ requirements.txt             pillow、pymupdf
├─ examples/invoice_fields.json 固定字段的写法示例
├─ samples/                     三个样例：发票 PNG、拍歪的表格 JPG、两页 PDF
├─ output/                      跑出来的 JSON
└─ figures/                     README 里的对比图
```

`samples/` 里的发票和合同是我用 PIL 画的假数据，公司名、税号都是编的，可以放心用。

README 里那几个图用的是 jsDelivr 直链，不是相对路径。原因：Gitee 不渲染相对路径的图片，而直接从 GitHub 页面引 Gitee 的 raw 链接会被防盗链拦成 403，只有 CDN 这种两边都认。
