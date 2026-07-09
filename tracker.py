import asyncio
import os
import json
import datetime
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from telegram import Bot
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

# ─── 설정값 ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
TARGET_PRICE = 930000  # 목표 가격 (3인 합산 총액, 93만원 이하)
HISTORY_FILE = "price_history.json"
MAX_HISTORY = 288  # 5분 × 288 = 24시간치 기록 보관
MAX_RETRIES = 2  # 스크래핑 실패 시 재시도 횟수

# ─── 가격 이력 관리 ──────────────────────────────────────────
def load_history():
    """가격 이력 JSON 파일 로드"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"prices": [], "lowest_ever": None}
    return {"prices": [], "lowest_ever": None}


def save_history(history):
    """가격 이력 JSON 파일 저장"""
    # 최근 MAX_HISTORY 건만 유지
    history["prices"] = history["prices"][-MAX_HISTORY:]
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_trend_info(history, current_price):
    """가격 변동 추이 분석"""
    prices = history.get("prices", [])
    lowest_ever = history.get("lowest_ever")

    trend = ""
    prev_price = None

    # 직전 가격과 비교
    if prices:
        prev_price = prices[-1]["price"]
        diff = current_price - prev_price
        if diff < 0:
            trend = f"📉 {abs(diff):,}원 하락 (직전 {prev_price:,}원)"
        elif diff > 0:
            trend = f"📈 {diff:,}원 상승 (직전 {prev_price:,}원)"
        else:
            trend = f"➡️ 변동 없음 ({prev_price:,}원)"

    # 역대 최저가 갱신 확인
    is_new_lowest = False
    if lowest_ever is None or current_price < lowest_ever:
        is_new_lowest = True

    # 최근 1시간 (12건) 최저/최고
    recent = prices[-12:] if len(prices) >= 12 else prices
    stats = ""
    if recent:
        recent_prices = [p["price"] for p in recent]
        stats = f"최근 {len(recent)}회 조회: 최저 {min(recent_prices):,}원 / 최고 {max(recent_prices):,}원"

    return trend, is_new_lowest, stats


# ─── 가격 파싱 ──────────────────────────────────────────────
def parse_price(price_text):
    """가격 텍스트에서 숫자 추출"""
    if not price_text or len(price_text) > 100:
        return None
    numbers = re.findall(r'\d+', price_text)
    if not numbers:
        return None
    price = int(''.join(numbers))
    return price


# ─── 텔레그램 알림 ──────────────────────────────────────────
async def send_telegram_message(message):
    """텔레그램 봇으로 메시지 전송"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ TELEGRAM_TOKEN or CHAT_ID not set. Skipping notification.")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=message)
        print("✅ Telegram message sent!")
    except Exception as e:
        print(f"❌ Failed to send Telegram message: {e}")


# ─── 메인 스크래핑 ──────────────────────────────────────────
async def check_flights():
    """네이버 항공권 가격 조회 (ScraperAPI 경유)"""
    url = "https://flight.naver.com/flights/international/SEL-TYO-20261022/TYO-SEL-20261025?adult=3&fareType=Y"

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    now_str = now.strftime("%Y-%m-%d %H:%M KST")
    print(f"🕐 {now_str} — 가격 조회 시작 (Naver Flights via ScraperAPI)")

    history = load_history()
    lowest_price = None

    async with async_playwright() as p:
        # ScraperAPI 프록시 설정
        proxy_settings = None
        if SCRAPER_API_KEY:
            proxy_settings = {
                "server": "http://proxy-server.scraperapi.com:8001",
                "username": "scraperapi",
                "password": SCRAPER_API_KEY
            }
            print("🌐 ScraperAPI 프록시 적용 완료")
        else:
            print("⚠️ SCRAPER_API_KEY가 설정되지 않아 로컬 네트워크로 직접 접속합니다.")

        # 가상 디스플레이(xvfb)가 켜져 있으므로 headless=False로 구동하여 봇 탐지 우회
        browser = await p.chromium.launch(headless=False, proxy=proxy_settings)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ko-KR",
            ignore_https_errors=True
        )
        
        for attempt in range(1, MAX_RETRIES + 2):
            page = await context.new_page()
            
            # 불필요한 리소스 차단 (ScraperAPI 동시 접속 제한 방지)
            async def intercept_route(route):
                if route.request.resource_type in ["image", "media", "font"]:
                    await route.abort()
                else:
                    await route.continue_()
                    
            await page.route("**/*", intercept_route)

            # 봇 탐지 우회 스크립트 주입
            await Stealth().apply_stealth_async(page)
            try:
                print(f"  [시도 {attempt}/{MAX_RETRIES + 1}] Naver Flights 로딩 중...")
                # 타임아웃을 60초로 넉넉하게 설정
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                
                # 네이버 항공권 렌더링 대기 (최대 40초 동적 대기)
                valid_prices = []
                for wait_idx in range(20):
                    await page.wait_for_timeout(2000)
                    
                    # 10초가 지났는데도 못 찾았다면 검색 버튼 한번 클릭 시도 (멈춤 방지용)
                    if wait_idx == 5:
                        try:
                            await page.get_by_role("button", name="검색").click(timeout=3000)
                            print("  🔄 검색 버튼 강제 클릭 완료")
                        except:
                            pass
                            
                    html = await page.content()
                    
                    # 네이버 가격 클래스 추출
                    import re
                    # item_Price__ 클래스 내부의 가격
                    prices = re.findall(r'item_Price__[^>]+>([^<]+)</i>', html)
                    if not prices:
                        prices = re.findall(r'item_Price__[^>]+><b[^>]*>([^<]+)</b>', html)
                    if not prices:
                        # 좀 더 범용적인 정규식 (10만원 이상 금액)
                        prices = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+)원', html)
                    
                    for p in prices:
                        clean_price = p.replace(',', '').replace('원', '').strip()
                        if clean_price.isdigit():
                            num_price = int(clean_price)
                            # 왕복 3인 기준이므로 최소 30만원 이상일 것
                            if num_price > 300000:
                                valid_prices.append(num_price)
                                
                    if valid_prices:
                        print(f"  ⚡ {wait_idx * 2 + 2}초 만에 화면 로딩 완료!")
                        break  # 가격을 찾으면 즉시 대기 루프 탈출
                

                if valid_prices:
                    # Naver Flights는 파라미터에 따라 3인 총액을 보여주므로 그대로 사용
                    lowest_price = min(valid_prices)
                    print(f"  ✅ 최저가 발견: {lowest_price:,}원 (총 {len(valid_prices)}개 가격)")
                    break  # 성공 → 루프 탈출
                else:
                    print(f"  ⚠️ 유효한 가격을 찾지 못함 (시도 {attempt})")
                    # 디버깅용 스크린샷 텔레그램 전송
                    screenshot_path = f"debug_attempt_{attempt}.png"
                    await page.screenshot(path=screenshot_path)
                    try:
                        if TELEGRAM_TOKEN and CHAT_ID:
                            bot = Bot(token=TELEGRAM_TOKEN)
                            with open(screenshot_path, "rb") as photo_file:
                                await bot.send_photo(chat_id=CHAT_ID, photo=photo_file, caption=f"🔍 시도 {attempt} 실패 화면 (어떤 화면인지 확인용)")
                    except Exception as e:
                        print(f"  ❌ 스크린샷 전송 실패: {e}")

            except Exception as e:
                print(f"  ❌ 스크래핑 오류 (시도 {attempt}): {e}")

            finally:
                await page.close()

            if attempt <= MAX_RETRIES:
                print(f"  ⏳ 10초 후 재시도...")
                await asyncio.sleep(10)

        await browser.close()

    # ─── 결과 처리 ────────────────────────────────────────
    if lowest_price is None:
        print("❌ 모든 시도에서 가격을 찾지 못했습니다.")
        await send_telegram_message(
            f"⚠️ 항공권 가격 조회 실패 ({now_str})\n"
            f"Google Flights에서 가격을 추출하지 못했습니다.\n"
            f"수동 확인: {url}"
        )
        return

    # 가격 추이 분석
    trend, is_new_lowest, stats = get_trend_info(history, lowest_price)

    # 이력 저장
    history["prices"].append({
        "time": now_str,
        "price": lowest_price
    })
    if is_new_lowest:
        history["lowest_ever"] = lowest_price
    save_history(history)

    # ─── 알림 조건 판단 ───────────────────────────────────
    diff = 0
    if history.get("prices") and len(history["prices"]) >= 2:
        diff = lowest_price - history["prices"][-2]["price"]

    # 1. 역대 최저가 또는 목표가 도달
    if lowest_price <= TARGET_PRICE or is_new_lowest:
        badge = "🎯 목표가 달성!" if lowest_price <= TARGET_PRICE else "🏆 역대 최저가 갱신!"
        msg = (
            f"🚨 항공권 초특가 알림 🚨\n\n"
            f"서울(인천) ✈️ 도쿄\n"
            f"📅 10/22(목) ~ 10/25(일) | 👤 성인 3명\n\n"
            f"💰 현재 최저가: {lowest_price:,}원\n"
            f"✨ {badge}\n\n"
            f"{trend}\n"
            f"{stats}\n\n"
            f"🔗 예매 링크:\n{url}\n\n"
            f"⏱️ {now_str}"
        )
        await send_telegram_message(msg)

    # 2. 가격 하락 (직전 시간 대비)
    elif diff < 0:
        msg = (
            f"📉 항공권 가격 하락 알림\n\n"
            f"현재가: {lowest_price:,}원\n"
            f"변동: {abs(diff):,}원 저렴해졌습니다!\n"
            f"(목표가 {TARGET_PRICE:,}원까지 {(lowest_price - TARGET_PRICE):,}원 남음)\n\n"
            f"🔗 예매 링크:\n{url}\n\n"
            f"⏱️ {now_str}"
        )
        await send_telegram_message(msg)

    # 3. 매일 아침 8시 요약 브리핑
    elif now.hour == 8 and now.minute < 5:
        msg = (
            f"🌅 일일 항공권 브리핑\n\n"
            f"현재가: {lowest_price:,}원\n"
            f"(목표가: {TARGET_PRICE:,}원)\n\n"
            f"{stats}\n\n"
            f"🔗 예매 링크:\n{url}"
        )
        await send_telegram_message(msg)

    else:
        print(f"  ℹ️ 목표가 미달성({TARGET_PRICE:,}원) 및 하락 없음 — 알림 미전송")
        if trend:
            print(f"  {trend}")


if __name__ == "__main__":
    asyncio.run(check_flights())
