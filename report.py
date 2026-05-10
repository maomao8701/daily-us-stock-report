import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf

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

WEBHOOK = os.environ["FEISHU_WEBHOOK_URL"]


def fmt_price(x):
    return f"{x:,.2f}"


def fmt_pct(x):
    return f"{'+' if x >= 0 else ''}{x:.2f}%"


def fmt_usd(x):
    return f"{'+' if x >= 0 else ''}{x:.2f}美元"


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
            "close": close,
            "change_pct": (close / prev_close - 1) * 100,
        }

    return quotes


def macro_points():
    try:
        feed = feedparser.parse(
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EDJI,%5EIXIC,CL=F&region=US&lang=en-US"
        )
        titles = [e.title for e in feed.entries[:3] if getattr(e, "title", "")]
    except Exception:
        titles = []

    if len(titles) >= 2:
        return [f"市场关注：{titles[0]}", f"市场关注：{titles[1]}"]

    return [
        "宏观层面暂无单一强驱动，继续关注利率预期、通胀数据和油价变化。",
        "高位市场仍要控制追涨风险，保留现金等待更好的补仓价格。",
    ]


def market_conclusion(q):
    sp = q["标普500"]["change_pct"]
    ndx = q["纳指"]["change_pct"]

    if sp > 0.5 and ndx > 1:
        return "美股整体偏强，科技成长方向明显占优，但短线涨幅偏快，不适合重仓追涨。"
    if sp < -0.8 and ndx < -1:
        return "美股明显回调，风险偏好降温，可以观察机会，但不建议一次性加仓。"
    return "美股整体波动可控，市场没有出现特别极端的单边信号，继续以持有观察为主。"


def ai_conclusion(q):
    nvda = q["NVDA"]["change_pct"]
    qqq = q["QQQ"]["change_pct"]

    if qqq > 1 or nvda > 1:
        return "AI主线仍强，但短线涨幅较快，QQQ不适合在这个位置追高开第一笔。"
    if qqq < -1 or nvda < -1:
        return "AI/科技出现回调，可以开始观察第一笔建仓窗口，但仍建议分批进行。"
    return "AI/科技主线继续观察，QQQ等待更清晰的回调机会。"


def holding_text(symbol, quote):
    shares = HOLDINGS[symbol]["shares"]
    avg = HOLDINGS[symbol]["avg_cost"]
    close = quote["close"]

    if shares == 0:
        return f"""- {symbol}：{fmt_price(close)}
  你的持仓：0股
  判断：等待。当前没有持仓，等科技/AI回调后再考虑第一笔。"""

    pnl_pct = (close / avg - 1) * 100
    pnl_amount = (close - avg) * shares

    judgement = "继续持有。当前位置不建议追高加仓。"
    if pnl_pct < -3:
        judgement = "继续观察。若继续回调，可考虑小额分批补仓。"

    return f"""- {symbol}：{fmt_price(close)}
  你的持仓：{shares:g}股
  平均成本：约{avg:.2f}
  当前浮盈：{fmt_pct(pnl_pct)}
  当前浮盈金额：约 {fmt_usd(pnl_amount)}
  判断：{judgement}"""


def build_report(q):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    invested = sum(v["shares"] * v["avg_cost"] for v in HOLDINGS.values())
    market_value = sum(HOLDINGS[s]["shares"] * q[s]["close"] for s in HOLDINGS)
    total_pnl = market_value - invested

    voo_value = HOLDINGS["VOO"]["shares"] * q["VOO"]["close"]
    qqq_value = HOLDINGS["QQQ"]["shares"] * q["QQQ"]["close"]
    vxus_value = HOLDINGS["VXUS"]["shares"] * q["VXUS"]["close"]

    m = macro_points()

    return f"""

大盘
- 标普500：{fmt_price(q["标普500"]["close"])}，昨晚 {fmt_pct(q["标普500"]["change_pct"])}
- 道指：{fmt_price(q["道指"]["close"])}，昨晚 {fmt_pct(q["道指"]["change_pct"])}
- 纳指：{fmt_price(q["纳指"]["close"])}，昨晚 {fmt_pct(q["纳指"]["change_pct"])}
- 结论：{market_conclusion(q)}

AI
- 英伟达（NVDA）：{fmt_price(q["NVDA"]["close"])}，昨晚 {fmt_pct(q["NVDA"]["change_pct"])}
- QQQ：{fmt_price(q["QQQ"]["close"])}，昨晚 {fmt_pct(q["QQQ"]["change_pct"])}
- 结论：{ai_conclusion(q)}

宏观
- {m[0]}
- {m[1]}
- 结论：宏观短线以观察为主，不把单日消息当作重仓买入理由。

ETF
{holding_text("VOO", q["VOO"])}

{holding_text("VXUS", q["VXUS"])}

{holding_text("QQQ", q["QQQ"])}

资金配置提醒
- 目标组合：VOO 60% / QQQ 25% / VXUS 15%
- 计划账户总资金：约3000美元
- 当前已投入：约{invested:.2f}美元
- 当前市值：约{market_value:.2f}美元
- 当前组合浮盈：约 {fmt_usd(total_pnl)}

按3000美元目标估算：
- VOO目标约1800美元，目前约{voo_value:.0f}美元：
- QQQ目标约750美元，目前{qqq_value:.0f}美元：
- VXUS目标约450美元，目前约{vxus_value:.0f}美元：

当前强买入信号：没有
当前可执行信号：持有观察，保留现金

下一笔优先级
1）VOO：等回调再补核心仓位
2）QQQ：等AI/科技明显回调后建第一笔
3）VXUS：已有底仓，后续慢慢补即可

今日动作
- VOO：持有，不加仓
- VXUS：持有，不加仓
- QQQ：等待
- 新资金：继续保留现金
- 止盈提醒：VOO和VXUS如有浮盈但未到止盈区，不卖出
"""


def send(text):
    response = requests.post(
        WEBHOOK,
        headers={"Content-Type": "application/json"},
        data=json.dumps({"msg_type": "text", "content": {"text": text}}),
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0 and body.get("StatusCode") != 0:
        raise RuntimeError(body)


if __name__ == "__main__":
    try:
        report = build_report(fetch_quotes())
        print(report)
        send(report)
    except Exception as e:
        print(f"日报发送失败：{e}", file=sys.stderr)
        raise
