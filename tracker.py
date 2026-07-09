import os
import requests
import re
import json
import sys
from datetime import datetime, timezone, timedelta
import urllib.parse
from telegram import Bot
import asyncio

SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# 구글 플라이트는 3인 총액이 아니라 '1인당 요금'을 보여줄 때가 많음.
# 도쿄 왕복 1인당 목표가를 25만원으로 설정 (총액 75만원)
TARGET_PRICE = 250000 

HISTORY_FILE = "price_history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"prices": [], "lowest_ever": float('inf'), "last_run_time": None}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=4, ensure_ascii=False)

def get_trend_info(history, current_price):
    if not history.get("prices"):
        return "📉 가격 변동 정보 없음", True, ""
    
    prices_only = [entry["price"] for entry in history["prices"]]
    lowest_ever = history.get("lowest_ever", float('inf'))
    
    is_new_lowest = False
    if current_price < lowest_ever:
        is_new_lowest = True
        lowest_ever = current_price
        
    last_price = prices_only[-1]
    diff = current_price - last_price
    
    if diff > 0:
        trend = f"📈 직전 대비 {diff:,}원 상승"
    elif diff < 0:
        trend = f"📉 직전 대비 {abs(diff):,}원 하락!"
    else:
        trend = "➖ 직전과 가격 동일"
        
    stats = (
        f"👑 역대 최저가: {lowest_ever:,}원\n"
        f"📊 최근 평균가: {sum(prices_only[-5:])//len(prices_only[-5:]):,}원 (최근 5회)"
    )
    
    return trend, is_new_lowest, stats

async def send_telegram_message(message):
    print("Telegram Message:\n", message)
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            bot = Bot(token=TELEGRAM_TOKEN)
            await bot.send_message(chat_id=CHAT_ID, text=message)
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")

async def main():
    now = datetime.now(timezone.utc) + timedelta(hours=9)
    now_str = now.strftime("%Y-%m-%d %H:%M KST")
    
    history = load_history()
    
    # --- 크레딧 보호(1시간 간격 강제) 로직 ---
    # cron-job.org가 10분마다 찔러도, 마지막 실행 후 55분이 안 지났으면 크레딧 소모 없이 패스
    if history.get("last_run_time"):
        try:
            last_run = datetime.strptime(history["last_run_time"], "%Y-%m-%d %H:%M KST")
            time_diff = now.replace(tzinfo=None) - last_run
            if time_diff.total_seconds() < 55 * 60:
                print(f"⏸️ 1시간 쿨타임 대기 중입니다. (마지막 실행: {history['last_run_time']})")
                print("무료 크레딧(5000개) 방어를 위해 조회를 건너뜁니다.")
                sys.exit(0)
        except Exception as e:
            print("시간 파싱 에러 (무시하고 진행):", e)
    
    print(f"🕐 {now_str} — 구글 플라이트 가격 조회 시작 (render=true)")
    
    if not SCRAPER_API_KEY:
        print("❌ SCRAPER_API_KEY가 없습니다.")
        sys.exit(1)

    url = "https://www.google.com/travel/flights/search?tfs=CBwQAhoqEgoyMDI2LTEwLTIyagwIAxIIL20vMGo3ZDhyDAgDEggvbS8wN2Rma3ABGioSCjIwMjYtMTAtMjVqDAgDEggvbS8wN2Rma3IMCAMSCC9tLzBqN2Q4cAEQAxgDQAFAAUgBmAEB&hl=ko&curr=KRW"
    scraper_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={urllib.parse.quote(url)}&render=true"
    
    lowest_price = None
    
    try:
        res = requests.get(scraper_url, timeout=90)
        if res.status_code == 200:
            html = res.text
            
            # 구글 플라이트 가격 정규식 (₩123,456 또는 123,456원)
            won_prices1 = re.findall(r'[₩]\s*([0-9]{1,3}(?:,[0-9]{3})+)', html)
            won_prices2 = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+)\s*[원]', html)
            all_raw = won_prices1 + won_prices2
            
            valid_prices = []
            for p in all_raw:
                clean_price = p.replace(',', '').strip()
                if clean_price.isdigit():
                    num_price = int(clean_price)
                    # 일본 왕복 1인당 최소 10만원 ~ 최대 150만원 사이로 필터링
                    # (구글 플라이트는 리스트에 1인당 가격을 노출함)
                    if 100000 < num_price < 1500000:
                        valid_prices.append(num_price)
                        
            if valid_prices:
                lowest_price = min(valid_prices)
                print(f"🎯 구글 플라이트 최저가 발견: 1인당 {lowest_price:,}원 (총액 아님)")
        else:
            print(f"❌ ScraperAPI Error: HTTP {res.status_code}")
            
    except Exception as e:
        print("❌ Request Error:", e)

    if lowest_price is None:
        print("⚠️ 가격을 찾지 못했습니다. 크레딧만 소모됨.")
        # 실패 시에는 last_run_time을 갱신하지 않아 다음 10분 뒤에 재시도하도록 함
        sys.exit(1)

    # 성공 시에만 쿨타임 갱신
    history["last_run_time"] = now_str
    
    trend, is_new_lowest, stats = get_trend_info(history, lowest_price)

    history["prices"].append({
        "time": now_str,
        "price": lowest_price
    })
    
    if is_new_lowest:
        history["lowest_ever"] = lowest_price
        
    save_history(history)

    diff = 0
    if history.get("prices") and len(history["prices"]) >= 2:
        diff = lowest_price - history["prices"][-2]["price"]

    # 목표가 도달 또는 최저가 갱신 시
    if lowest_price <= TARGET_PRICE or is_new_lowest:
        badge = "🔥 1인당 목표가 달성!" if lowest_price <= TARGET_PRICE else "👑 역대 최저가 갱신!"
        msg = (
            f"🚨 특가 알림 (구글 플라이트) 🚨\n\n"
            f"서울(인천) ✈️ 도쿄\n"
            f"📅 10/22(목) ~ 10/25(일)\n\n"
            f"💰 현재 최저가 (1인당): {lowest_price:,}원\n"
            f"👨‍👩‍👧 예상 총액 (3인): {lowest_price * 3:,}원\n"
            f"🔖 {badge}\n\n"
            f"{trend}\n"
            f"{stats}\n\n"
            f"🔗 구글 플라이트 링크:\n{url}\n\n"
            f"⏱️ {now_str}"
        )
        await send_telegram_message(msg)
    elif diff < 0:
        msg = (
            f"🔔 가격 하락 알림 (구글 플라이트)\n\n"
            f"현재가 (1인당): {lowest_price:,}원\n"
            f"변동: {abs(diff):,}원 저렴해졌습니다!\n"
            f"(목표가 {TARGET_PRICE:,}원까지 {(lowest_price - TARGET_PRICE):,}원 남음)\n\n"
            f"🔗 예매 링크:\n{url}\n\n"
            f"⏱️ {now_str}"
        )
        await send_telegram_message(msg)
    else:
        print(f"⏸️ 목표가 미달({TARGET_PRICE:,}원) 및 하락 없음 (알림 미전송)")

if __name__ == "__main__":
    asyncio.run(main())
