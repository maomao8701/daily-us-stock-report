import argparse
import html
import json
import math
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf
from openai import OpenAI


TICKERS = {
    "标普500": "^GSPC",
    "道指": "^DJI",
    "纳指": "^IXIC",
    "NVDA": "NVDA",
    "QQQ": "QQQ",
    "VOO": "VOO",
    "VXUS": "VXUS",
}
HOLDINGS = {
    "VOO": {"shares": 0.4, "avg_cost": 650.3214875},
    "VXUS": {"shares": 3.0, "avg_cost": 83.228455333},
    "QQQ": {"shares": 0.6, "avg_cost": 722.412286667},
}
TARGET_VALUES = {"VOO": 1800, "QQQ": 750, "VXUS": 450}
TRADES = [
    {"date": "2026-04-06", "symbol": "VOO", "side": "buy", "shares": 0.10, "price": 603.66},
    {"date": "2026-05-05", "symbol": "VOO", "side": "buy", "shares": 0.30, "price": 663.54},
    {"date": "2026-05-05", "symbol": "VXUS", "side": "buy", "shares": 3.00, "price": 83.11},
    {"date": "2026-06-16", "symbol": "QQQ", "side": "buy", "shares": 0.04, "price": 737.94, "commission": 0.30, "basis": 29.81},
    {"date": "2026-07-15", "symbol": "QQQ", "side": "buy", "shares": 0.56, "price": 720.15, "commission": 0.35, "basis": 403.63},
]
CHART_LABELS = {"标普500": "S&P 500", "道指": "Dow", "纳指": "Nasdaq", "NVDA": "NVDA", "QQQ": "QQQ", "VOO": "VOO", "VXUS": "VXUS"}
BASE_URL = "https://maomao8701.github.io/daily-us-stock-report"
DOCS_DIR = Path("docs")
PENDING_MESSAGE = Path(".pending-feishu-message.json")
MARKET_HOLIDAYS = {
    2026: {
        "2026-01-01",
        "2026-01-19",
        "2026-02-16",
        "2026-04-03",
        "2026-05-25",
        "2026-06-19",
        "2026-07-03",
        "2026-09-07",
        "2026-11-26",
        "2026-12-25",
    }
}
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "market_conclusion": {"type": "string"},
        "ai_conclusion": {"type": "string"},
        "macro_points": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
        "macro_conclusion": {"type": "string"},
        "etf_judgements": {
            "type": "object",
            "properties": {symbol: {"type": "string"} for symbol in HOLDINGS},
            "required": list(HOLDINGS),
            "additionalProperties": False,
        },
        "strong_buy_signal": {"type": "string"},
        "executable_signal": {"type": "string"},
        "next_priorities": {
            "type": "object",
            "properties": {symbol: {"type": "string"} for symbol in HOLDINGS},
            "required": list(HOLDINGS),
            "additionalProperties": False,
        },
        "today_actions": {
            "type": "object",
            "properties": {
                "VOO": {"type": "string"},
                "VXUS": {"type": "string"},
                "QQQ": {"type": "string"},
                "new_funds": {"type": "string"},
                "take_profit": {"type": "string"},
            },
            "required": ["VOO", "VXUS", "QQQ", "new_funds", "take_profit"],
            "additionalProperties": False,
        },
    },
    "required": [
        "market_conclusion",
        "ai_conclusion",
        "macro_points",
        "macro_conclusion",
        "etf_judgements",
        "strong_buy_signal",
        "executable_signal",
        "next_priorities",
        "today_actions",
    ],
    "additionalProperties": False,
}


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def fmt_price(value):
    return f"{value:,.2f}"


def fmt_pct(value):
    return f"{value:+.2f}%"


def fmt_usd(value):
    return f"{value:+.2f}美元"


def is_missing_number(value):
    try:
        return value is None or math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def latest_market_price(symbol):
    try:
        price = yf.Ticker(symbol).fast_info.last_price
    except Exception:
        return None
    if is_missing_number(price):
        return None
    return float(price)


def fetch_market_data():
    raw = yf.download(
        list(TICKERS.values()),
        period="3mo",
        interval="1d",
        group_by="ticker",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    quotes = {}
    history = {}
    for name, symbol in TICKERS.items():
        frame = raw[symbol].copy()
        if frame.empty:
            raise RuntimeError(f"{symbol} 可用收盘数据不足")
        closes = frame["Close"].copy()
        if is_missing_number(closes.iloc[-1]):
            fallback_price = latest_market_price(symbol)
            if fallback_price is not None:
                closes.iloc[-1] = fallback_price
        closes = closes.dropna()
        if len(closes) < 2:
            raise RuntimeError(f"{symbol} 可用收盘数据不足")
        latest = float(closes.iloc[-1])
        previous = float(closes.iloc[-2])
        quotes[name] = {
            "symbol": symbol,
            "close": round(latest, 2),
            "change_pct": round((latest / previous - 1) * 100, 2),
            "last_date": str(frame.index[-1].date()),
        }
        history[name] = {str(index.date()): round(float(value), 4) for index, value in closes.items()}
    return quotes, history


def expected_market_date(now):
    today = now.date()
    expected = today - timedelta(days=1)
    if expected.weekday() >= 5:
        return None
    if expected.isoformat() in MARKET_HOLIDAYS.get(expected.year, set()):
        return None
    return expected.isoformat()


def market_closed_message(now):
    today = now.date()
    return f"【美股情报简报｜{today.isoformat()}｜美股休市】\n\n昨晚美股休市，没有新的收盘数据。"


def market_status_message(quotes, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date()
    expected = expected_market_date(now)
    if not expected:
        return market_closed_message(now)
    latest = quotes["标普500"]["last_date"]
    if latest != expected:
        return (
            f"【美股情报简报｜{today.isoformat()}｜行情数据未更新】\n\n"
            f"昨晚美股应为正常交易日，但当前行情源最新收盘日期仍是 {latest}，预期应为 {expected}。\n"
            "今日暂不生成完整日报，避免使用过期行情。请稍后手动重跑，或等待下一次自动更新。"
        )
    return None


def fetch_news():
    urls = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EDJI,%5EIXIC,QQQ,NVDA,VOO,VXUS,CL=F&region=US&lang=en-US",
        "https://www.investing.com/rss/news_25.rss",
    ]
    items = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = getattr(entry, "title", "").strip()
                if title:
                    items.append({"title": title, "summary": getattr(entry, "summary", "").strip()[:240]})
        except Exception:
            pass
    seen = set()
    result = []
    for item in items:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result[:10]


def portfolio(quotes):
    invested = sum(item["shares"] * item["avg_cost"] for item in HOLDINGS.values())
    holdings = {}
    for symbol, item in HOLDINGS.items():
        close = quotes[symbol]["close"]
        shares = item["shares"]
        avg_cost = item["avg_cost"]
        pnl_pct = None if not shares else round((close / avg_cost - 1) * 100, 2)
        pnl_amount = None if not shares else round((close - avg_cost) * shares, 2)
        holdings[symbol] = {
            **item,
            "current_value": round(close * shares, 2),
            "pnl_pct": pnl_pct,
            "pnl_amount": pnl_amount,
        }
    market_value = sum(item["current_value"] for item in holdings.values())
    return {
        "target_allocation": "VOO 60% / QQQ 25% / VXUS 15%",
        "target_total_usd": 3000,
        "invested": round(invested, 2),
        "market_value": round(market_value, 2),
        "total_pnl": round(market_value - invested, 2),
        "holdings": holdings,
        "target_values": TARGET_VALUES,
    }


def generate_analysis(quotes, news, pf):
    prompt = f"""你是谨慎、直接的美股个人投资助理。根据真实行情、新闻标题和个人持仓生成结构化分析。
要求：每天结合指数结构、NVDA/QQQ 强弱、宏观新闻和持仓盈亏重新判断；避免固定套话；短句、具体、可执行；
新闻仅根据标题谨慎判断；不要编造数据；不要承诺收益；不要使用 Markdown。

行情：{json.dumps(quotes, ensure_ascii=False)}
新闻：{json.dumps(news, ensure_ascii=False)}
持仓：{json.dumps(pf, ensure_ascii=False)}
"""
    response = OpenAI(api_key=env("OPENAI_API_KEY")).responses.create(
        model="gpt-5.4-mini",
        input=prompt,
        text={"format": {"type": "json_schema", "name": "stock_report", "strict": True, "schema": REPORT_SCHEMA}},
    )
    return json.loads(response.output_text)




def esc(value):
    return html.escape(str(value))


def holding_html(symbol, quote, item, judgement):
    pnl = "暂无持仓" if item["pnl_pct"] is None else f'{fmt_pct(item["pnl_pct"])} / {fmt_usd(item["pnl_amount"])}'
    return f"""<article class="holding"><h3>{symbol}<span>${fmt_price(quote["close"])}</span></h3>
<dl><dt>持仓</dt><dd>{item["shares"]:g} 股</dd><dt>平均成本</dt><dd>${item["avg_cost"]:.2f}</dd>
<dt>当前市值</dt><dd>${item["current_value"]:.2f}</dd><dt>浮盈亏</dt><dd>{esc(pnl)}</dd></dl>
<p>{esc(judgement)}</p></article>"""


def history_links():
    reports = sorted((DOCS_DIR / "reports").glob("*.html"), reverse=True)[:30]
    return "".join(f'<a href="reports/{item.name}">{item.stem}</a>' for item in reports)


def render_html(report_date, quotes, pf, analysis, report_path):
    actions = analysis["today_actions"]
    holdings = "".join(holding_html(symbol, quotes[symbol], pf["holdings"][symbol], analysis["etf_judgements"][symbol]) for symbol in HOLDINGS)
    priorities = "".join(f"<li><b>{symbol}</b>：{esc(analysis['next_priorities'][symbol])}</li>" for symbol in ["VOO", "QQQ", "VXUS"])
    action_items = "".join(f"<li><b>{label}</b>：{esc(actions[key])}</li>" for key, label in [("VOO", "VOO"), ("VXUS", "VXUS"), ("QQQ", "QQQ"), ("new_funds", "新资金"), ("take_profit", "止盈提醒")])
    metrics = "".join(f'<div class="metric">{name}<b>{fmt_price(quotes[name]["close"])}</b><span>{fmt_pct(quotes[name]["change_pct"])}</span></div>' for name in ["标普500", "道指", "纳指"])
    ai_metrics = "".join(f'<div class="metric">{name}<b>{fmt_price(quotes[name]["close"])}</b><span>{fmt_pct(quotes[name]["change_pct"])}</span></div>' for name in ["NVDA", "QQQ"])
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>美股情报简报｜{report_date}</title><style>
:root{{--ink:#172033;--muted:#64748b;--line:#e2e8f0;--soft:#f6f8fb;--blue:#1769aa;--green:#15803d}}*{{box-sizing:border-box}}
body{{margin:0;background:#f3f6f9;color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{max-width:1080px;margin:auto;background:white;min-height:100vh;padding:28px}}h1{{margin:0;font-size:28px}}h2{{margin:28px 0 12px;border-bottom:1px solid var(--line);padding-bottom:8px;font-size:20px}}
.meta{{color:var(--muted)}}.metrics,.holdings{{display:grid;gap:14px}}.metrics{{grid-template-columns:repeat(3,1fr)}}.holdings{{grid-template-columns:repeat(3,1fr)}}
.metric,.holding,.note{{border:1px solid var(--line);border-radius:6px;padding:14px;background:#fff}}.metric b{{display:block;font-size:21px}}h3{{margin:0 0 9px}}h3 span{{float:right;color:var(--blue)}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:0}}dt{{color:var(--muted)}}dd{{margin:0;text-align:right}}
.signal{{border-left:4px solid var(--green);background:#f0fdf4;padding:12px 15px}}.history a{{display:inline-block;margin:0 9px 7px 0}}ul{{padding-left:20px}}
@media(max-width:760px){{main{{padding:18px}}h1{{font-size:23px}}.metrics,.holdings{{grid-template-columns:1fr}}}}
</style></head><body><main><header><h1>美股情报简报</h1><div class="meta">{report_date}｜昨晚收盘</div></header>
<h2>大盘</h2><div class="metrics">{metrics}</div><p>{esc(analysis["market_conclusion"])}</p>
<h2>AI</h2><div class="metrics">{ai_metrics}</div><p>{esc(analysis["ai_conclusion"])}</p>
<h2>宏观</h2><ul><li>{esc(analysis["macro_points"][0])}</li><li>{esc(analysis["macro_points"][1])}</li></ul><p>{esc(analysis["macro_conclusion"])}</p>
<h2>ETF</h2><div class="holdings">{holdings}</div>
<h2>资金配置提醒</h2><div class="metrics"><div class="metric">目标组合<b>VOO 60%</b><span>QQQ 25% / VXUS 15%</span></div><div class="metric">当前已投入<b>${pf["invested"]:.2f}</b><span>计划总资金 $3000</span></div><div class="metric">组合浮盈亏<b>{fmt_usd(pf["total_pnl"])}</b><span>当前市值 ${pf["market_value"]:.2f}</span></div></div>
<p class="signal"><b>当前强买入信号：</b>{esc(analysis["strong_buy_signal"])}<br><b>当前可执行信号：</b>{esc(analysis["executable_signal"])}</p>
<h2>下一笔优先级</h2><ol>{priorities}</ol><h2>今日动作</h2><ul>{action_items}</ul>
<h2>历史日报</h2><nav class="history">{history_links()}</nav></main></body></html>'''
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(page, encoding="utf-8")
    (DOCS_DIR / "index.html").write_text(page, encoding="utf-8")
    (DOCS_DIR / ".nojekyll").touch()


def cleanup_history(today):
    cutoff = today - timedelta(days=30)
    reports_dir = DOCS_DIR / "reports"
    assets_dir = DOCS_DIR / "assets"
    for report in reports_dir.glob("*.html") if reports_dir.exists() else []:
        if date.fromisoformat(report.stem) < cutoff:
            report.unlink()
    for chart_dir in assets_dir.glob("*") if assets_dir.exists() else []:
        if chart_dir.is_dir() and date.fromisoformat(chart_dir.name) < cutoff:
            shutil.rmtree(chart_dir)


def write_pending(payload):
    PENDING_MESSAGE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def summary_card(report_date, analysis, report_url):
    actions = analysis["today_actions"]
    action_text = "\n".join(f"- {label}：{actions[key]}" for key, label in [("VOO", "VOO"), ("VXUS", "VXUS"), ("QQQ", "QQQ"), ("new_funds", "新资金")])
    return {
        "msg_type": "interactive",
        "card": {
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": f"美股情报简报｜{report_date}"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**大盘结论**\n{analysis['market_conclusion']}\n\n**AI 结论**\n{analysis['ai_conclusion']}\n\n**当前强买入信号**\n{analysis['strong_buy_signal']}\n\n**今日动作**\n{action_text}"}},
                {"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "查看完整 HTML 报告"}, "url": report_url, "type": "primary"}]},
            ],
        },
    }


def send_feishu(payload):
    response = requests.post(env("FEISHU_WEBHOOK_URL"), json=payload, timeout=20)
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0 and result.get("StatusCode") != 0:
        raise RuntimeError(result)


def send_pending():
    payload = json.loads(PENDING_MESSAGE.read_text(encoding="utf-8"))
    try:
        send_feishu(payload)
    except Exception:
        if payload.get("msg_type") != "interactive":
            raise
        card = payload["card"]
        title = card["header"]["title"]["content"]
        content = card["elements"][0]["text"]["content"].replace("**", "")
        url = card["elements"][1]["actions"][0]["url"]
        send_feishu({"msg_type": "text", "content": {"text": f"{title}\n\n{content}\n\n完整报告：{url}"}})


def write_status_message(message):
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    index = DOCS_DIR / "index.html"
    if not index.exists():
        index.write_text('<!doctype html><meta charset="utf-8"><title>美股情报简报</title><body><h1>美股情报简报</h1><p>暂无交易日报，下一次正常交易日收盘后更新。</p></body>', encoding="utf-8")
        (DOCS_DIR / ".nojekyll").touch()
    print(message)
    write_pending({"msg_type": "text", "content": {"text": message}})


def build_report(force=False):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if not force and expected_market_date(now) is None:
        write_status_message(market_closed_message(now))
        return

    quotes, history = fetch_market_data()
    status_message = None if force else market_status_message(quotes, now)
    if status_message:
        write_status_message(status_message)
        return
    report_date = now.date().isoformat()
    pf = portfolio(quotes)
    analysis = generate_analysis(quotes, fetch_news(), pf)
    cleanup_history(date.fromisoformat(report_date))
    render_html(report_date, quotes, pf, analysis, DOCS_DIR / "reports" / f"{report_date}.html")
    write_pending(summary_card(report_date, analysis, f"{BASE_URL}/reports/{report_date}.html"))
    print(f"Generated HTML report for {report_date}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-pending", action="store_true")
    parser.add_argument("--force-report", action="store_true")
    args = parser.parse_args()
    if args.send_pending:
        send_pending()
    else:
        build_report(force=args.force_report)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"日报任务失败：{exc}", file=sys.stderr)
        raise
