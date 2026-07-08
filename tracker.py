import asyncio
import os
from playwright.async_api import async_playwright
from telegram import Bot
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

# 설정값
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
TARGET_PRICE = 600000  # 알림을 받을 목표 가격 (2인 합산 총액 기준, 예: 60만원 이하)

async def send_telegram_message(message):
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=message)
    print("Telegram message sent!")

def parse_price(price_text):
    if len(price_text) > 100:
        return None
    # '₩354,200', '354,200원' 등에서 숫자만 추출
    numbers = re.findall(r'\d+', price_text)
    if not numbers:
        return None
    return int(''.join(numbers))

async def check_flights():
    # 인천(ICN/GMP) -> 도쿄(NRT/HND) 2026-10-22 ~ 2026-10-25 (성인 2명)
    url = "https://www.google.com/travel/flights?q=Flights%20to%20Tokyo%20from%20Seoul%20on%202026-10-22%20through%202026-10-25%20for%202%20adults"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale='ko-KR',
            timezone_id='Asia/Seoul',
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()
        
        print(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until='networkidle', timeout=60000)
            print("Waiting for results to render...")
            await page.wait_for_timeout(10000) # JS 렌더링 대기
            
            # 가격 텍스트 추출 (₩ 기호가 포함된 텍스트)
            prices_text = await page.evaluate('''() => {
                const elements = Array.from(document.querySelectorAll('*'));
                const priceElements = elements.filter(el => 
                    el.textContent && (el.textContent.includes('₩') || el.textContent.includes('원')) && el.children.length === 0
                );
                return priceElements.map(el => el.textContent.trim());
            }''')
            
            valid_prices = []
            for pt in prices_text:
                price = parse_price(pt)
                if price and 50000 < price < 2000000:
                    valid_prices.append(price)
            
            if valid_prices:
                lowest_price = min(valid_prices)
                print(f"Lowest price found: {lowest_price:,}원")
                
                if lowest_price <= TARGET_PRICE:
                    msg = f"🚨 항공권 가격 알림 🚨\n\n서울 ✈️ 도쿄\n일정: 10/22(목) ~ 10/25(일)\n\n현재 구글플라이트 최저가: {lowest_price:,}원\n(목표가: {TARGET_PRICE:,}원 이하)\n\n⚠️ 주의: 아고다 등 여행사 가격일 수 있으니 결제 전 '공식 항공사'인지 꼭 확인하세요!\n\n예매 링크:\n{url}"
                    await send_telegram_message(msg)
                else:
                    print(f"Price {lowest_price:,} is higher than target {TARGET_PRICE:,}. No alert sent.")
            else:
                print("No valid prices found on the page.")
                
        except Exception as e:
            print(f"Error during scraping: {e}")
            
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(check_flights())
