# 国内招标信息采集（AGV / GPU）

脚本会搜索国内公开可检索招标网站中的 AGV 或 GPU 相关招标信息，筛选最近一段时间内新增条目并通过邮件发送结果。

## 使用方法

1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 运行 AGV 报告

```bash
python3 agv_tender_scraper.py \
  --keyword AGV \
  --days 7 \
  --max-pages 5 \
  --email-sender your_email@qq.com \
  --email-password your_smtp_auth_code \
  --email-receivers receiver1@qq.com,receiver2@qq.com
```

3. 运行 GPU 报告

```bash
python3 gpu_tender_scraper.py \
  --days 7 \
  --max-pages 3 \
  --email-sender your_email@qq.com \
  --email-password your_smtp_auth_code
```

## 关键词规则

- AGV 默认关键词：`AGV`
- GPU 默认关键词：`GPU,GPU服务器,GPU算力,算力服务,GPU租赁,算力租赁,智算服务`
- 兼容旧参数：`--keyword`
- 多关键词参数：`--keywords GPU,GPU服务器,算力服务`

当同时传入 `--keywords` 和 `--keyword` 时，优先使用 `--keywords`。

## 仅查询部分站点

```bash
python3 agv_tender_scraper.py --list-sites
python3 gpu_tender_scraper.py --list-sites
python3 gpu_tender_scraper.py \
  --sites ccgp,cebpub,365trade,ecsg,ygcgfw \
  --keywords GPU,GPU服务器
```

## 环境变量方式

```bash
export EMAIL_SENDER=your_email@qq.com
export EMAIL_PASSWORD=your_smtp_auth_code
export EMAIL_RECEIVERS=receiver1@qq.com,receiver2@qq.com
python3 agv_tender_scraper.py --keyword AGV --days 7
python3 gpu_tender_scraper.py --days 7
```

## 预览邮件内容

```bash
python3 agv_tender_scraper.py --keyword AGV --days 7 --dry-run
python3 gpu_tender_scraper.py --days 7 --dry-run
```

## GitHub Actions

仓库包含两条工作流：

- `AGV Weekly Email Report`
- `GPU Weekly Email Report`

默认触发时间：

- AGV：每周一北京时间 09:00
- GPU：每周一北京时间 09:30

两条工作流都支持 `workflow_dispatch` 手动触发。

需要在仓库 `Settings -> Secrets and variables -> Actions` 配置：

- `EMAIL_SENDER`：发件人邮箱
- `EMAIL_PASSWORD`：邮箱 SMTP 授权码
- `AGV_EMAIL_RECEIVERS`：AGV 收件人列表（逗号分隔，可留空则默认发给发件人）
- `GPU_EMAIL_RECEIVERS`：GPU 收件人列表（逗号分隔，可留空则默认发给发件人）

## 国内站点范围

当前内置站点均为国内平台，包含：

- 中国政府采购网、全国公共资源交易平台、政采云、中央政府采购网
- 中国招标投标公共服务平台（bulletin 与 www）
- 中国国际招标网（商务部）
- 深圳电子采购平台、南方电网电子采购交易平台、阳光采购服务平台
- 中招联合招标采购平台、必联网、采招网、中国采购与招标网
- 石化、石油、铁路、烟草等国内行业平台

一期只覆盖国内公开可检索站点，不接入需要更深 JS 逆向或登录校验的平台。

## 说明

- 当前共享核心位于 `tender_core.py`，`agv_tender_scraper.py` 和 `gpu_tender_scraper.py` 为独立入口。
- 抓取执行模型为“按站点 x 关键词展开请求，再按 URL 去重”。
- `--output` 参数已弃用，脚本不再生成 Excel。
