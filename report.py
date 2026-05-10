import json
import os
import sys
from datetime import datetime
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
    "VOO": {"shares": 0.4, "avg_cost": 648.57},
    "VXUS": {"shares": 3.0, "avg_cost": 83.11},
    "QQQ": {"shares": 0.0, "avg_cost": 0.0},
}

FEISHU_WEBHOOK_URL = os.environ["FEISHU_WEBHOOK_URL"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]


def fetch_quotes():
    data = yf.download(
        list(TICKERS.values()),
        period="7d",
        interval="1d",
        group_by="ticker",
        progress=False,
        auto_adjust=False,
    )

    quotes = {}
    for name, symbol in TICKERS.items():
        frame = data[symbol].dropna(subset=["Close"])
        last = frame.iloc[-1]
        prev = frame.iloc[-2]
        close = float(last["Close"])
        prev_close = float(prev["Close"])
        quotes[name] = {
            "symbol": symbol,
            "close": round(close, 2),
            "change_pct": round((close / prev_close - 1) * 100, 2),
            "last_date": str(frame.index[-1].date()),
        }

    return quotes


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
                summary = getattr(entry, "summary", "").strip()
                if title:
                    items.append({"title": title, "summary": summary[:240]})
        except Exception:
            pass

    seen = set()
    deduped = []
    for item in items:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped[:10]


def portfolio(quotes):
    invested = sum(v["shares"] * v["avg_cost"] for v in HOLDINGS.values())
    market_value = sum(HOLDINGS[s]["shares"] * quotes[s]["close"] for s in HOLDINGS)
    total_pnl = market_value - invested

    holdings = {}
    for symbol, h in HOLDINGS.items():
        close = quotes[symbol]["close"]
        shares = h["shares"]
        avg_cost = h["avg_cost"]
        current_value = close * shares
        if shares > 0:
            pnl_pct = (close / avg_cost - 1) * 100
            pnl_amount = (close - avg_cost) * shares
        else:
            pnl_pct = None
            pnl_amount = None

        holdings[symbol] = {
            "shares": shares,
            "avg_cost": avg_cost,
            "current_value": round(current_value, 2),
            "pnl_pct": None if pnl_pct is None else round(pnl_pct, 2),
            "pnl_amount": None if pnl_amount is None else round(pnl_amount, 2),
        }

    return {
        "target_allocation": "VOO 60% / QQQ 25% / VXUS 15%",
        "target_total_usd": 3000,
        "invested": round(invested, 2),
        "market_value": round(market_value, 2),
        "total_pnl": round(total_pnl, 2),
        "holdings": holdings,
        "target_values": {
            "VOO": 1800,
            "QQQ": 750,
            "VXUS": 450,
        },
    }


def generate_report(quotes, news, pf):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
你是一个谨慎、直接的美股个人投资助理。请基于下面真实行情、新闻标题和持仓数据，生成中文飞书日报。

重要要求：
1. 必须严格使用下面的版式，不要增加新的栏目。
2. 每天的“结论 / 判断 / 今日动作”必须根据当天数据重新分析，不要写固定套话。
3. 可以保守，但要具体说明为什么，例如：指数结构、纳指强弱、NVDA/QQQ强弱、宏观新闻、持仓浮盈浮亏。
4. 不要编造没有提供的数据。新闻只根据标题做谨慎判断。
5. 不要承诺收益，不要写确定性预测。
6. 语言要像真实投研备注，短句、直接、可执行。
7. 日期使用：{today}
8. 所有价格、涨跌幅、持仓、成本、浮盈金额必须使用输入数据，不要自己重新估算。
9. 如果今天是周末或行情日期不是最近一个交易日，可以继续使用最新收盘数据，但在结论里说明这是最新可得收盘数据。

行情数据：
{json.dumps(quotes, ensure_ascii=False, indent=2)}

新闻标题：
{json.dumps(news, ensure_ascii=False, indent=2)}

持仓与组合数据：
{json.dumps(pf, ensure_ascii=False, indent=2)}

请严格输出以下格式：

【美股情报简报｜{today}｜昨晚收盘】

大盘
- 标普500：
- 道指：
- 纳指：
- 结论：

AI
- 英伟达（NVDA）：
- QQQ：
- 结论：

宏观
-
-
- 结论：

ETF
- VOO：
  你的持仓：
  平均成本：
  当前浮盈：
  当前浮盈金额：
  判断：

- VXUS：
  你的持仓：
  平均成本：
  当前浮盈：
  当前浮盈金额：
  判断：

- QQQ：
  你的持仓：
  判断：

资金配置提醒
- 目标组合：VOO 60% / QQQ 25% / VXUS 15%
- 计划账户总资金：约3000美元
- 当前已投入：
- 当前市值：
- 当前组合浮盈：

按3000美元目标估算：
- VOO目标约1800美元，目前约：
- QQQ目标约750美元，目前：
- VXUS目标约450美元，目前约：

当前强买入信号：
当前可执行信号：

下一笔优先级
1）VOO：
2）QQQ：
3）VXUS：

今日动作
- VOO：
- VXUS：
- QQQ：
- 新资金：
- 止盈提醒：
"""

    response = client.responses.create(
        model="gpt-5.4-mini",
        input=prompt,
    )

    return response.output_text.strip()


def send_feishu(text):
    response = requests.post(
        FEISHU_WEBHOOK_URL,
        headers={"Content-Type": "application/json"},
        data=json.dumps({"msg_type": "text", "content": {"text": text}}),
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0 and body.get("StatusCode") != 0:
        raise RuntimeError(body)


def main():
    quotes = fetch_quotes()
    news = fetch_news()
    pf = portfolio(quotes)
    report = generate_report(quotes, news, pf)
    print(report)
    send_feishu(report)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"日报发送失败：{e}", file=sys.stderr)
        raise
