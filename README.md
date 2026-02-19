# AGV 招标信息采集

脚本会搜索国内招标网站中的 AGV 招标信息，筛选最近 7 天内新增条目并通过邮件发送结果。

## 使用方法

1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 运行

```bash
python agv_tender_scraper.py \
  --keyword AGV \
  --days 7 \
  --max-pages 5 \
  --email-sender your_email@qq.com \
  --email-password your_smtp_auth_code \
  --email-receivers receiver1@qq.com,receiver2@qq.com
```

### 仅查询部分站点

```bash
python agv_tender_scraper.py --list-sites
python agv_tender_scraper.py --sites ccgp,ggzy,cebpubservice --keyword AGV --days 7
```

### 环境变量方式

```bash
export EMAIL_SENDER=your_email@qq.com
export EMAIL_PASSWORD=your_smtp_auth_code
export EMAIL_RECEIVERS=receiver1@qq.com,receiver2@qq.com
python agv_tender_scraper.py --keyword AGV --days 7
```

### 预览邮件内容（不发送）

```bash
python agv_tender_scraper.py --keyword AGV --days 7 --dry-run
```

## GitHub Actions

仓库已包含工作流：`.github/workflows/agv_weekly_email.yml`

- 定时：每周一北京时间 09:00（GitHub cron 为 UTC，对应 `0 1 * * 1`）
- 支持手动触发：`Actions -> AGV Weekly Email Report -> Run workflow`

需要在仓库 `Settings -> Secrets and variables -> Actions` 配置：

- `EMAIL_SENDER`：发件人邮箱
- `EMAIL_PASSWORD`：邮箱 SMTP 授权码
- `EMAIL_RECEIVERS`：收件人列表（逗号分隔，可留空则默认发给发件人）

## 说明

- 当前内置站点涵盖中国政府采购网、全国公共资源交易平台、政采云、中央政府采购网、
  中国招标投标公共服务平台（bulletin 与 www 交易公开）、深圳电子采购平台、
  中国国际招标网、石化/石油/央企平台、必联网、采招网、中国采购与招标网等。
- 部分站点搜索入口可能使用动态脚本或登录校验；脚本会尝试自动识别搜索表单，但可能需要
  手工调整 `agv_tender_scraper.py` 中的站点配置。
- `--output` 参数已弃用，脚本不再生成 Excel 文件。
