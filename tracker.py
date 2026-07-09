import os
import requests
import re
import urllib.parse

def test_google_flights():
    SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
    if not SCRAPER_API_KEY:
        print("❌ No SCRAPER_API_KEY found.")
        return
        
    # 구글 플라이트: 서울(ICN/GMP) -> 도쿄(NRT/HND) 2026-10-22 ~ 10-25, 성인 3명, 이코노미
    # tfs 파라미터는 ICN-NRT 성인 3명 기준으로 임의 생성된 안전한 URL입니다.
    url = "https://www.google.com/travel/flights/search?tfs=CBwQAhoqEgoyMDI2LTEwLTIyagwIAxIIL20vMGo3ZDhyDAgDEggvbS8wN2Rma3ABGioSCjIwMjYtMTAtMjVqDAgDEggvbS8wN2Rma3IMCAMSCC9tLzBqN2Q4cAEQAxgDQAFAAUgBmAEB&hl=ko&curr=KRW"
    
    # render=true (5크레딧 소모) 사용하여 ScraperAPI가 자바스크립트를 모두 실행한 뒤 HTML을 돌려주도록 설정
    scraper_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={urllib.parse.quote(url)}&render=true"
    
    try:
        print("🚀 Fetching Google Flights via ScraperAPI (render=true, 5 credits)...")
        res = requests.get(scraper_url, timeout=90) # 렌더링에 시간이 걸리므로 90초 부여
        print("📡 Status Code:", res.status_code)
        html = res.text
        
        print("📄 HTML Length:", len(html))
        
        if len(html) < 20000:
            print("⚠️ HTML is suspiciously small. Might be blocked or not fully rendered.")
            print("Excerpt:", html[:500])
        else:
            # 구글 플라이트는 ₩123,456 또는 123,456 형식으로 가격을 표시함
            raw_prices = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+)', html)
            print(f"🔍 Found {len(raw_prices)} raw numbers formatted with commas.")
            
            valid_prices = []
            for p in raw_prices:
                clean_price = p.replace(',', '').strip()
                if clean_price.isdigit():
                    num_price = int(clean_price)
                    if 50000 < num_price < 5000000:
                        valid_prices.append(num_price)
                        
            valid_prices = sorted(list(set(valid_prices)))
            if valid_prices:
                print("🎯 Valid Flight Prices found (5만 ~ 500만):", valid_prices[:20])
            else:
                print("❌ No valid prices found in the rendered HTML.")
                
            if "₩" in html or "원" in html:
                print("✅ Korean currency symbols detected in HTML.")
            
    except Exception as e:
        print("❌ Error fetching data:", e)

if __name__ == "__main__":
    test_google_flights()
