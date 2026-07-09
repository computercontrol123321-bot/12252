import os
import re
import json
import asyncio
import urllib.parse
import aiohttp
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
    
    print(f"🕐 {now_str} — 가격 조회 시작 (Naver Flights 'API-Only Proxy' Architecture)")
    
    history = load_history()
    lowest_price = None
    
    url = "https://flight.naver.com/flights/international/SEL-TYO-20261022/TYO-SEL-20261025?adult=3&fareType=Y"

    async with async_playwright() as p:
        # 프록시 없이 일반 브라우저 실행 (HTML/CSS/JS는 프록시 없이 무료로 빠르게 로드!)
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ko-KR",
            ignore_https_errors=True
        )
        
        for attempt in range(1, MAX_RETRIES + 2):
            page = await context.new_page()
            
            api_prices = []
            
            # API 요청만 가로채서 ScraperAPI를 통해 1크레딧만 소모하여 우회 전송
            async def intercept_route(route):
                request = route.request
                
                # 차단될 API 요청만 프록시로 전송 (searchFlights API)
                if "searchFlights" in request.url:
                    print(f"  🔍 API 요청 가로챔: {request.url}")
                    
                    try:
                        post_data = request.post_data
                        headers = request.headers
                        # 보안을 위해 불필요한 브라우저 내부 헤더 제거
                        headers.pop("host", None)
                        headers.pop("content-length", None)
                        
                        scraper_api_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={urllib.parse.quote(request.url)}"
                        
                        async with aiohttp.ClientSession() as session:
                            # 15초 타임아웃
                            async with session.post(scraper_api_url, data=post_data, headers=headers, timeout=15) as response:
                                resp_body = await response.read()
                                resp_headers = dict(response.headers)
                                
                                # 성공적으로 JSON 응답을 받은 경우 가격 추출 시도
                                if response.status == 200:
                                    try:
                                        data = json.loads(resp_body)
                                        # 네이버 API 구조 분석
                                        # data 안의 itineraries 에서 가격 정보 파싱
                                        # 만약 여기서 못 찾아도 페이지가 DOM으로 렌더링되도록 fulfill
                                        # 응답 본문에서 모든 숫자를 추출 (정규식 사용)
                                        pass
                                    except:
                                        pass
                                
                                await route.fulfill(status=response.status, headers=resp_headers, body=resp_body)
                    except Exception as e:
                        print(f"  ❌ API 우회 중 에러: {e}")
                        await route.abort()
                else:
                    # 그 외의 모든 이미지, CSS, JS는 프록시 없이 즉시 로드! (빠르고 크레딧 소모 0)
                    await route.continue_()
                        
            await page.route("**/*", intercept_route)
            await Stealth().apply_stealth_async(page)
            
            try:
                print(f"  [시도 {attempt}/{MAX_RETRIES + 1}] Naver Flights 로딩 중...")
                # 45초 대기
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                
                valid_prices = []
                for wait_idx in range(15):
                    await page.wait_for_timeout(2000)
                    
                    if wait_idx == 4:
                        try:
                            await page.get_by_role("button", name="검색").click(timeout=3000)
                            print("  🔄 검색 버튼 강제 클릭 완료")
                        except:
                            pass
                            
                    html = await page.content()
                    
                    # 네이버 가격 클래스 추출
                    prices = re.findall(r'item_Price__[^>]+>([^<]+)</i>', html)
                    if not prices:
                        prices = re.findall(r'item_Price__[^>]+><b[^>]*>([^<]+)</b>', html)
                    if not prices:
                        prices = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+)원', html)
                    
                    for p in prices:
                        clean_price = p.replace(',', '').replace('원', '').strip()
                        if clean_price.isdigit():
                            num_price = int(clean_price)
                            # 왕복 3명 기준이므로 최소 30만원 이상
                            if num_price > 300000:
                                valid_prices.append(num_price)
                                
                    if valid_prices:
                        print(f"  ✅ {wait_idx * 2 + 2}초만에 화면 로딩 완료!")
                        break

                if valid_prices:
                    lowest_price = min(valid_prices)
                    print(f"  🎯 최저가 발견: {lowest_price:,}원 (총 {len(valid_prices)}개 가격)")
                    break
                else:
                    print(f"  ⚠️ 유효한 가격을 찾지 못함 (시도 {attempt})")
                    screenshot_path = f"debug_attempt_{attempt}.png"
                    await page.screenshot(path=screenshot_path)
                    try:
                        if TELEGRAM_TOKEN and CHAT_ID:
                            bot = Bot(token=TELEGRAM_TOKEN)
                            with open(screenshot_path, "rb") as photo_file:
                                await bot.send_photo(chat_id=CHAT_ID, photo=photo_file, caption=f"❌ 시도 {attempt} 실패 화면 (프록시 API 1크레딧 우회 방식)")
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
            f"Naver Flights에서 가격을 추출하지 못했습니다.\n"
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
