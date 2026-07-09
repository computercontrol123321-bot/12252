import os
import sys
import json
import asyncio
import time
import requests
from datetime import datetime
from telegram import Bot

# ==========================================
# 설정값
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

# 도쿄 왕복 1인당 목표가를 29만원으로 설정
TARGET_PRICE = 290000 
HISTORY_FILE = "price_history.json"

# Apify Actor ID
ACTOR_ID = "scrapemesh/google-flights-scraper"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading history: {e}")
    return {"lowest_ever": float('inf'), "last_run_time": None}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

async def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram Token or Chat ID is missing!")
        return
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')

def get_flight_price():
    if not APIFY_TOKEN:
        print("❌ APIFY_TOKEN is missing!")
        sys.exit(1)
        
    run_input = {
        "departure": "SEL",
        "arrival": "TYO",
        "departureDate": "2026-10-22",
        "returnDate": "2026-10-25",
        "passengers": 3,
        "currency": "KRW",
        "type": "round",
        "maxItems": 5,
    }
    
    print(f"🚀 Apify Google Flights Scraper 호출 중... (Actor: {ACTOR_ID})")
    
    try:
        # 1. 봇 실행 (비동기로 실행하고 Run ID를 받음)
        actor_path = ACTOR_ID.replace("/", "~")
        start_url = f"https://api.apify.com/v2/acts/{actor_path}/runs?token={APIFY_TOKEN}"
        res = requests.post(start_url, json=run_input)
        
        if res.status_code != 201:
            print(f"❌ Apify 실행 실패: HTTP {res.status_code}")
            print(res.text)
            return None
            
        data = res.json()["data"]
        run_id = data["id"]
        dataset_id = data["defaultDatasetId"]
        
        print(f"⏳ 데이터 수집 대기 중... (Run ID: {run_id})")
        
        # 2. 완료될 때까지 상태 확인 (최대 3분 대기)
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
        for _ in range(36): # 5초 * 36 = 180초 대기
            time.sleep(5)
            s_res = requests.get(status_url)
            status = s_res.json()["data"]["status"]
            if status == "SUCCEEDED":
                break
            elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                print(f"❌ Apify 작업 실패 (상태: {status})")
                return None
                
        # 3. 완료된 데이터 가져오기
        items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}"
        i_res = requests.get(items_url)
        items = i_res.json()
        
        if not items:
            print("⚠️ 비행기 표 데이터를 찾지 못했습니다.")
            return None
            
        best_flight = items[0]
        
        # 가격 데이터 파싱 (다양한 Apify 봇들의 JSON 형식에 모두 대응)
        price_val = best_flight.get("price")
        
        # 1. price가 딕셔너리인 경우 (예: {"amount": 290000, "currency": "KRW"})
        if isinstance(price_val, dict):
            price_val = price_val.get("amount") or price_val.get("total")
            
        # 2. price가 없고 prices라는 배열/객체가 있는 경우
        if not price_val and "prices" in best_flight:
             prices_obj = best_flight["prices"]
             if isinstance(prices_obj, dict):
                 price_val = prices_obj.get("total") or prices_obj.get("amount")
             elif isinstance(prices_obj, list) and len(prices_obj) > 0:
                 price_val = prices_obj[0].get("amount")
             
        if not price_val:
            print("⚠️ JSON에서 가격(price) 필드를 찾지 못했습니다.")
            print("RAW Data:", best_flight)
            return None
            
        # 3. 가격이 문자열(예: '₩250,000')일 경우 숫자만 추출
        if isinstance(price_val, str):
            clean_price = "".join(filter(str.isdigit, price_val))
            if clean_price:
                price_val = int(clean_price)
            else:
                return None
                
        # 최종 가격이 숫자인지 안전하게 변환
        try:
            price_val = int(price_val)
        except ValueError:
            print(f"⚠️ 가격을 숫자로 변환할 수 없습니다: {price_val}")
            return None
                
        if price_val > 600000:
            price_per_person = price_val // 3
        else:
            price_per_person = price_val
            
        print(f"🎯 Apify 추출 성공! 1인당 최저가: {price_per_person}원 (원본: {price_val})")
        return price_per_person

    except Exception as e:
        print(f"❌ Apify API 호출 중 에러 발생: {e}")
        return None

def main():
    now = datetime.now()
    current_time_str = now.strftime("%Y-%m-%d %H:%M KST")
    print(f"\n🕐 {current_time_str} — Apify 구글 플라이트 가격 조회 시작")
    
    history = load_history()
    
    # --- 크레딧 보호(20분 간격 강제) 로직 ---
    # Apify는 비용이 저렴하므로 쿨타임을 20분으로 대폭 축소
    if history.get("last_run_time"):
        try:
            last_run = datetime.strptime(history["last_run_time"], "%Y-%m-%d %H:%M KST")
            time_diff = now.replace(tzinfo=None) - last_run
            if time_diff.total_seconds() < 19 * 60:
                print(f"⏸️ 20분 쿨타임 대기 중입니다. (마지막 실행: {history['last_run_time']})")
                print("무료 캐시($5) 방어를 위해 조회를 건너뜁니다.")
                sys.exit(0)
        except Exception as e:
            print(f"시간 파싱 에러 (무시하고 진행): {e}")

    # 가격 조회
    lowest_price = get_flight_price()
    
    if lowest_price is None:
        print("⚠️ 가격을 찾지 못했습니다. 쿨타임을 적용하지 않고 종료합니다.")
        sys.exit(1)
        
    history["last_run_time"] = current_time_str
    lowest_ever = history.get("lowest_ever", float('inf'))
    
    is_new_lowest = False
    if lowest_price < lowest_ever:
        is_new_lowest = True
        history["lowest_ever"] = lowest_price
        
    save_history(history)
    
    total_price = lowest_price * 3
    
    # 목표가 달성 또는 역대 최저가 갱신 시 알림
    if lowest_price <= TARGET_PRICE or is_new_lowest:
        msg = (
            f"✈️ **[구글 플라이트 특가 알림]**\n\n"
            f"**노선:** 서울(SEL) ➡️ 도쿄(TYO)\n"
            f"**일정:** 10/22(목) ~ 10/25(일) 왕복\n"
            f"**인원:** 성인 3명\n\n"
        )
        
        if lowest_price <= TARGET_PRICE:
            msg += f"🔥 **목표가({TARGET_PRICE:,}원) 달성!**\n"
        elif is_new_lowest:
            msg += f"📉 **역대 최저가 갱신!**\n"
            
        msg += (
            f"💸 **1인당 요금:** `{lowest_price:,}원`\n"
            f"💰 **3인 총액:** `{total_price:,}원`\n\n"
            f"🔍 [구글 플라이트로 예매하러 가기 (검색창 자동완성)](https://www.google.com/travel/flights?q=Flights%20from%20SEL%20to%20TYO%20on%202026-10-22%20through%202026-10-25%20for%203%20adults)\n"
            f"_(마지막 조회: {current_time_str})_"
        )
        
        print("텔레그램 알림 발송 중...")
        asyncio.run(send_telegram_message(msg))
        print("✅ 텔레그램 알림 발송 완료!")
    else:
        print(f"ℹ️ 현재 최저가({lowest_price:,}원)가 목표가({TARGET_PRICE:,}원)보다 높습니다. 알림 생략.")

if __name__ == "__main__":
    main()
