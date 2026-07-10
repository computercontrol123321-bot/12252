import os
import sys
import json
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from telegram import Bot

# ==========================================
# 설정값
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
TEQUILA_API_KEY = os.environ.get("TEQUILA_API_KEY")

# 도쿄 왕복 1인당 목표가를 29만원으로 설정
TARGET_PRICE = 290000 
HISTORY_FILE = "price_history.json"

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
    if not TEQUILA_API_KEY:
        print("❌ TEQUILA_API_KEY is missing!")
        sys.exit(1)
        
    print(f"🚀 Tequila(Kiwi) API 호출 중...")
    
    url = "https://api.tequila.kiwi.com/v2/search"
    headers = {
        "apikey": TEQUILA_API_KEY
    }
    params = {
        "fly_from": "SEL",
        "fly_to": "TYO",
        "date_from": "22/10/2026",
        "date_to": "22/10/2026",
        "return_from": "25/10/2026",
        "return_to": "25/10/2026",
        "adults": 3,
        "curr": "KRW",
        "sort": "price",
        "limit": 1
    }
    
    try:
        res = requests.get(url, headers=headers, params=params)
        
        if res.status_code != 200:
            print(f"❌ Tequila API 호출 실패: HTTP {res.status_code}")
            print(res.text)
            return None
            
        data = res.json().get("data", [])
        
        if not data:
            print("⚠️ 비행기 표 데이터를 찾지 못했습니다.")
            return None
            
        best_flight = data[0]
        
        # 디버깅을 위해 원본 데이터 저장
        try:
            with open("last_flight_data.json", "w", encoding="utf-8") as f:
                json.dump(best_flight, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"디버그 파일 저장 실패: {e}")
        
        # Tequila API의 price는 성인 3명일 경우 3명 합산 총액으로 나옵니다.
        # 따라서 1인당 요금을 구하려면 3으로 나눠야 합니다.
        total_price = best_flight.get("price", 0)
        if total_price == 0:
            print("⚠️ JSON에서 가격(price) 필드를 찾지 못했습니다.")
            return None
            
        price_per_person = int(total_price / 3)
        
        # 딥링크 (예약 링크)
        deep_link = best_flight.get("deep_link", "https://www.kiwi.com/")
            
        # 항공편 상세 정보 추출
        details_str = ""
        try:
            route = best_flight.get("route", [])
            for leg in route:
                aln = leg.get("airline", "알 수 없음")
                flyFrom = leg.get("flyFrom", "")
                flyTo = leg.get("flyTo", "")
                
                # local_departure, local_arrival 시간 포맷 변경
                dep_time = leg.get("local_departure", "").replace("T", " ")[:16]
                arr_time = leg.get("local_arrival", "").replace("T", " ")[:16]
                
                details_str += f"\n  └ {aln} ({flyFrom} -> {flyTo} | {dep_time} ~ {arr_time})"
                
        except Exception as e:
            print(f"상세 정보 파싱 에러: {e}")
            details_str = "\n  └ 상세 정보를 불러올 수 없습니다."
            
        print(f"🎯 Tequila 추출 성공! 1인당 최저가: {price_per_person}원")
        return {"price": price_per_person, "details": details_str, "deep_link": deep_link}

    except Exception as e:
        print(f"❌ Tequila API 호출 중 에러 발생: {e}")
        return None

def main():
    # KST 시간대 설정 (UTC+9)
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    current_time_str = now.strftime("%Y-%m-%d %H:%M KST")
    print(f"\n🕐 {current_time_str} — Tequila API 가격 조회 시작")
    
    history = load_history()
    
    # 쿨타임 로직 (20분 간격 유지)
    if "last_run_time" in history:
        try:
            last_run = datetime.strptime(history["last_run_time"], "%Y-%m-%d %H:%M KST").replace(tzinfo=kst)
            time_diff = now - last_run
            if time_diff.total_seconds() < 19 * 60:
                print(f"⏸️ 20분 쿨타임 대기 중입니다. (마지막 실행: {history['last_run_time']})")
                print("무료 API 호출 제한을 방어하기 위해 조회를 건너뜁니다.")
                return
        except Exception as e:
            print(f"시간 파싱 에러 (무시하고 진행): {e}")

    # 가격 조회
    flight_data = get_flight_price()
    
    if flight_data is None:
        print("⚠️ 가격을 찾지 못했습니다. 쿨타임을 적용하지 않고 종료합니다.")
        sys.exit(1)
        
    lowest_price = flight_data["price"]
    details_str = flight_data.get("details", "")
    deep_link = flight_data.get("deep_link", "")
    
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
            f"✈️ **[키위닷컴 특가 알림]**\n\n"
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
            f"💰 **3인 총액:** `{total_price:,}원`\n"
            f"📝 **항공편 요약:**{details_str}\n\n"
            f"🔍 [키위닷컴으로 바로 예매하러 가기]({deep_link})\n"
            f"_(마지막 조회: {current_time_str})_"
        )
        
        print("텔레그램 알림 발송 중...")
        asyncio.run(send_telegram_message(msg))
        print("✅ 텔레그램 알림 발송 완료!")
    else:
        print(f"ℹ️ 현재 최저가({lowest_price:,}원)가 목표가({TARGET_PRICE:,}원)보다 높습니다. 알림 생략.")

if __name__ == "__main__":
    main()
