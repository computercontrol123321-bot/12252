import os
import re
import json
import asyncio
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from telegram import Bot

SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
TARGET_PRICE = 250000

HISTORY_FILE = "price_history.json"
MAX_RETRIES = 2

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"prices": [], "lowest_ever": float('inf')}

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
        f"📊 최근 평균가: {sum(prices_only[-10:])//len(prices_only[-10:]):,}원 (최근 10회)"
    )
    
    return trend, is_new_lowest, stats

async def send_telegram_message(message):
    print("Telegram Message:", message)
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            bot = Bot(token=TELEGRAM_TOKEN)
            await bot.send_message(chat_id=CHAT_ID, text=message)
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")

async def check_flights():
    now = datetime.now(timezone.utc) + timedelta(hours=9)
    now_str = now.strftime("%Y-%m-%d %H:%M KST")
    
    print(f"🕐 {now_str} — 가격 조회 시작 (Trip.com via ScraperAPI)")
    
    history = load_history()
    lowest_price = None
    
    # 트립닷컴 URL (왕복, 성인 3명)
    url = "https://kr.trip.com/flights/seoul-to-tokyo/tickets-sel-tyo?FlightWay=Return&class=Y&Quantity=3&dcity=sel&acity=tyo&ddate=2026-10-22&rdate=2026-10-25"

    async with async_playwright() as p:
        # ScraperAPI Proxy 모드 사용
        proxy_url = f"http://scraperapi:{SCRAPER_API_KEY}@proxy-server.scraperapi.com:8001" if SCRAPER_API_KEY else None
        
        browser = await p.chromium.launch(
            proxy={"server": proxy_url} if proxy_url else None,
            headless=True
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ko-KR",
            ignore_https_errors=True
        )
        
        for attempt in range(1, MAX_RETRIES + 2):
            page = await context.new_page()
            
            # 강력한 재시도 로직이 포함된 라우팅 (Timeout / Deadlock 방지)
            semaphore = asyncio.Semaphore(4)
            async def intercept_route(route):
                if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                    try:
                        await route.abort()
                    except:
                        pass
                    return
                
                async with semaphore:
                    for _ in range(3): # 최대 3번 재시도
                        try:
                            # 10초 타임아웃으로 Deadlock 방지
                            response = await route.fetch(timeout=10000)
                            # 429 Too Many Requests (ScraperAPI 동시접속 제한) 시 재시도
                            if response.status == 429:
                                await asyncio.sleep(1.5)
                                continue
                                
                            await route.fulfill(response=response)
                            return
                        except Exception:
                            await asyncio.sleep(1)
                            continue
                    
                    # 모든 재시도 실패 시 펜딩 방지를 위해 즉시 abort
                    try:
                        await route.abort()
                    except:
                        pass
                        
            await page.route("**/*", intercept_route)
            await Stealth().apply_stealth_async(page)
            
            try:
                print(f"  [시도 {attempt}/{MAX_RETRIES + 1}] Trip.com 로딩 중...")
                # 60초 대기, 에러 발생해도 무시하고 진행
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    print(f"  ⚠️ goto timeout 발생했으나 무시하고 계속 진행: {e}")
                
                valid_prices = []
                # 30초 동안 DOM 주기적 파싱 (트립닷컴은 렌더링이 빠름)
                for wait_idx in range(15):
                    await page.wait_for_timeout(2000)
                    html = await page.content()
                    
                    # 트립닷컴은 숫자로 된 가격이 포맷팅되어 나옴 (예: 123,456)
                    # 화면에 보이는 모든 10만원 이상 금액을 스크래핑
                    raw_prices = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+)', html)
                    
                    for p in raw_prices:
                        clean_price = p.replace(',', '').strip()
                        if clean_price.isdigit():
                            num_price = int(clean_price)
                            # 왕복 3명 최저가는 최소 30만원 이상일 것 (이상한 작은 숫자 필터링)
                            if 300000 < num_price < 5000000:
                                valid_prices.append(num_price)
                                
                    if valid_prices:
                        print(f"  ✅ {wait_idx * 2 + 2}초만에 가격 데이터 추출 완료!")
                        break

                if valid_prices:
                    lowest_price = min(valid_prices)
                    print(f"  🎯 최저가 발견: {lowest_price:,}원 (총 {len(valid_prices)}개 가격 중 최소값)")
                    break
                else:
                    print(f"  ⚠️ 유효한 가격을 찾지 못함 (시도 {attempt})")
                    screenshot_path = f"debug_attempt_{attempt}.png"
                    await page.screenshot(path=screenshot_path)
                    try:
                        if TELEGRAM_TOKEN and CHAT_ID:
                            bot = Bot(token=TELEGRAM_TOKEN)
                            with open(screenshot_path, "rb") as photo_file:
                                await bot.send_photo(chat_id=CHAT_ID, photo=photo_file, caption=f"❌ 시도 {attempt} 실패 화면 (Trip.com)")
                    except Exception:
                        pass

            except Exception as e:
                print(f"  ❌ 스크래핑 오류 (시도 {attempt}): {e}")

            finally:
                await page.close()

            if attempt <= MAX_RETRIES:
                print(f"  ⏳ 10초 후 재시도...")
                await asyncio.sleep(10)

        await browser.close()

    if lowest_price is None:
        print("❌ 모든 시도에서 가격을 찾지 못했습니다.")
        await send_telegram_message(
            f"⚠️ 항공권 가격 조회 실패 ({now_str})\n"
            f"Trip.com에서 가격을 추출하지 못했습니다.\n"
            f"수동 확인: {url}"
        )
        return

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

    if lowest_price <= TARGET_PRICE or is_new_lowest:
        badge = "🔥 목표가 달성!" if lowest_price <= TARGET_PRICE else "👑 역대 최저가 갱신!"
        msg = (
            f"🚨 특가 알림 🚨\n\n"
            f"서울(인천) ✈️ 도쿄\n"
            f"📅 10/22(목) ~ 10/25(일) | 👥 성인 3명\n\n"
            f"💰 현재 최저가: {lowest_price:,}원\n"
            f"🔖 {badge}\n\n"
            f"{trend}\n"
            f"{stats}\n\n"
            f"🔗 예매 링크:\n{url}\n\n"
            f"⏱️ {now_str}"
        )
        await send_telegram_message(msg)
    elif diff < 0:
        msg = (
            f"🔔 가격 하락 알림\n\n"
            f"현재가: {lowest_price:,}원\n"
            f"변동: {abs(diff):,}원 저렴해졌습니다!\n"
            f"(목표가 {TARGET_PRICE:,}원까지 {(lowest_price - TARGET_PRICE):,}원 남음)\n\n"
            f"🔗 예매 링크:\n{url}\n\n"
            f"⏱️ {now_str}"
        )
        await send_telegram_message(msg)
    elif now.hour == 8 and now.minute < 5:
        msg = (
            f"🌅 일일 요약 브리핑\n\n"
            f"현재가: {lowest_price:,}원\n"
            f"(목표가: {TARGET_PRICE:,}원)\n\n"
            f"{stats}\n\n"
            f"🔗 예매 링크:\n{url}"
        )
        await send_telegram_message(msg)
    else:
        print(f"  ⏸️ 목표가 미달({TARGET_PRICE:,}원) 및 하락 없음 (알림 미전송)")

if __name__ == "__main__":
    asyncio.run(check_flights())
