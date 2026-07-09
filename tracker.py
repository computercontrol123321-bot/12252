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
    """₩ 또는 '원' 표기된 가격 텍스트에서 숫자 추출 (KRW 전용)"""
    if not price_text or len(price_text) > 100:
        return None
    # '₩354,200', '354,200원', '₩ 354,200' 등에서 숫자만 추출
    numbers = re.findall(r'\d+', price_text)
    if not numbers:
        return None
    price = int(''.join(numbers))
    # KRW 항공권 가격 범위 필터 (5만원~200만원)
    if 50000 < price < 2000000:
        return price
    return None


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
    """Google Flights에서 항공권 가격을 1회 조회"""
    # URL에 KRW 통화, 한국어, 한국 지역 명시
    url = (
        "https://www.google.com/travel/flights?"
        "q=Flights%20to%20Tokyo%20from%20Seoul%20"
        "on%202026-10-22%20through%202026-10-25%20for%203%20adults"
        "&curr=KRW"
    )

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    now_str = now.strftime("%Y-%m-%d %H:%M KST")
    print(f"🕐 {now_str} — 가격 조회 시작")

    history = load_history()
    lowest_price = None
    stealth = Stealth()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            )
        )
        
        for attempt in range(1, MAX_RETRIES + 2):
            page = await context.new_page()
            await stealth.apply_stealth_async(page)
            try:
                print(f"  [시도 {attempt}/{MAX_RETRIES + 1}] 페이지 로딩 중...")
                await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                
                # '원'이나 '₩' 기호가 화면에 나타날 때까지 최대 40초 대기 (GitHub Actions 환경은 더 느릴 수 있으므로)
                try:
                    await page.wait_for_function(
                        "() => document.body.innerText.includes('원') || document.body.innerText.includes('₩')",
                        timeout=40000
                    )
                    await page.wait_for_timeout(3000)  # JS 렌더링이 완전히 안정화될 때까지 3초 추가 대기
                except Exception:
                    # 새로고침 버튼이 있는지 확인 (오류 발생 시)
                    if await page.locator("text='새로고침'").count() > 0:
                        print("  [오류 화면 감지] 새로고침 버튼 클릭 시도")
                        await page.locator("text='새로고침'").click()
                        
                        try:
                            await page.wait_for_function(
                                "() => document.body.innerText.includes('원') || document.body.innerText.includes('₩')",
                                timeout=20000
                            )
                        except Exception:
                            pass
                    pass  # 시간 초과 시 아래 필터링에서 걸러지고 재시도됨

                # ₩ 또는 '원' 포함된 leaf 요소에서 가격 추출
                prices_text = await page.evaluate('''() => {
                    const elements = Array.from(document.querySelectorAll('*'));
                    return elements
                        .filter(el =>
                            el.children.length === 0 &&
                            el.textContent &&
                            (el.textContent.includes('₩') || el.textContent.includes('원'))
                        )
                        .map(el => el.textContent.trim());
                }''')

                valid_prices = []
                for pt in prices_text:
                    price = parse_price(pt)
                    if price:
                        valid_prices.append(price)

                if valid_prices:
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
    if lowest_price <= TARGET_PRICE:
        # 🎯 목표가 이하 → 즉시 알림!
        new_low_badge = "🏆 역대 최저가 갱신! " if is_new_lowest else ""
        msg = (
            f"🚨 항공권 가격 알림 🚨\n"
            f"\n"
            f"서울(인천) ✈️ 도쿄\n"
            f"📅 10/22(목) ~ 10/25(일) | 👤 성인 3명\n"
            f"\n"
            f"💰 현재 최저가: {lowest_price:,}원\n"
            f"🎯 목표가: {TARGET_PRICE:,}원 이하 ✅ 달성!\n"
            f"\n"
            f"{new_low_badge}{trend}\n"
            f"{stats}\n"
            f"\n"
            f"⚠️ 예매 전 공식 항공사인지 꼭 확인하세요!\n"
            f"\n"
            f"🔗 예매 링크:\n{url}\n"
            f"\n"
            f"⏱️ 조회 시각: {now_str}"
        )
        await send_telegram_message(msg)

    else:
        print(f"  ℹ️ 현재가 {lowest_price:,}원 > 목표가 {TARGET_PRICE:,}원 — 알림 미전송")
        if trend:
            print(f"  {trend}")


if __name__ == "__main__":
    asyncio.run(check_flights())
