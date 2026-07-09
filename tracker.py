import os
import requests
import re
import json

def test_trip_html():
    SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
    if not SCRAPER_API_KEY:
        print("❌ No SCRAPER_API_KEY found in environment.")
        return
        
    url = "https://kr.trip.com/flights/seoul-to-tokyo/tickets-sel-tyo?FlightWay=Return&class=Y&Quantity=3&dcity=sel&acity=tyo&ddate=2026-10-22&rdate=2026-10-25"
    scraper_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={url}"
    
    try:
        print("🚀 Fetching raw HTML from Trip.com via ScraperAPI (1 credit)...")
        res = requests.get(scraper_url, timeout=30)
        print("📡 Status Code:", res.status_code)
        html = res.text
        
        print("📄 HTML Length:", len(html))
        
        if len(html) < 5000:
            print("⚠️ HTML is suspiciously small. Might be a WAF/CAPTCHA challenge block.")
            print("Excerpt:", html[:500])
        else:
            # 트립닷컴은 초기 상태(초기 렌더링 데이터)를 IBU_FLIGHT_DATALAYER나 NEXT_DATA에 저장할 수 있음.
            # 모든 쉼표 포함 숫자를 찾아본다.
            raw_prices = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+)', html)
            print(f"🔍 Found {len(raw_prices)} raw numbers formatted with commas.")
            
            valid_prices = []
            for p in raw_prices:
                clean_price = p.replace(',', '').strip()
                if clean_price.isdigit():
                    num_price = int(clean_price)
                    # 5만원 이상 500만원 이하 (1인당 가격일 수도 있으므로 필터 완화)
                    if 50000 < num_price < 5000000:
                        valid_prices.append(num_price)
                        
            # 중복 제거
            valid_prices = sorted(list(set(valid_prices)))
            print("🎯 Valid Flight Prices found (5만 ~ 500만):", valid_prices[:20]) # 처음 20개만
            
            if "원" in html:
                print("✅ Korean Won ('원') detected in HTML.")
            elif "$" in html or "USD" in html:
                print("⚠️ USD detected in HTML. IP might be US-based.")
            
            # JSON 덩어리가 있는지 확인
            if "window.IBU_FLIGHT_DATALAYER" in html:
                print("✅ Found window.IBU_FLIGHT_DATALAYER")
            if "window.__INITIAL_STATE__" in html:
                print("✅ Found window.__INITIAL_STATE__")
            
            # price 글자 주변 텍스트 출력
            idx = html.find('"price":')
            if idx == -1:
                idx = html.find('price')
                
            if idx != -1:
                print("\n--- Snippet around 'price' ---")
                print(html[max(0, idx-50):idx+150])
                print("------------------------------")
                
    except Exception as e:
        print("❌ Error fetching data:", e)

if __name__ == "__main__":
    test_trip_html()
