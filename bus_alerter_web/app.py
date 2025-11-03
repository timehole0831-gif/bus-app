# app.py (웹 호스팅용 최종본)

import json
import sqlite3
import atexit
from flask import Flask, render_template, request, jsonify
import requests
from pywebpush import webpush, WebPushException
from urllib.parse import urlparse
from apscheduler.schedulers.background import BackgroundScheduler # 1. 스케줄러 라이브러리

# --- Flask 앱 및 기본 설정 ---
app = Flask(__name__)

# VAPID 키 (그대로 유지)
VAPID_PRIVATE_KEY = "7OgSMB-QyC9fdzmQtTUvXgm0P7JusIjGPjND3ySEoxo"
VAPID_PUBLIC_KEY = "BHD7yQNjasAtJb78-u8O9CdSQjh_5D9ZjqjSvUTsCrUxO4Mj5HdvlqUOYaErKFZf9cLR5bdsf1NEmbLSRafkagA"

# --- 2. 데이터베이스(SQLite) 설정 ---
DB_NAME = 'subscriptions.db' # 이 이름으로 DB 파일이 생성됩니다.

def init_db():
    """ 데이터베이스 테이블을 초기화하는 함수 """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # 구독 정보(subscription_json)와 어떤 버스인지(route_id 등)를 함께 저장
    c.execute('''
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city_code TEXT NOT NULL,
        node_id TEXT NOT NULL,
        route_id TEXT NOT NULL,
        bus_number TEXT NOT NULL,
        station_name TEXT NOT NULL,
        subscription_json TEXT NOT NULL,
        UNIQUE(route_id, subscription_json) -- 한 사용자가 같은 버스를 중복 구독하는 것 방지
    )
    ''')
    conn.commit()
    conn.close()

# --- BusTrackerApi 클래스 (그대로 유지) ---
class BusTrackerApi:
    def __init__(self, service_key):
        self.service_key = service_key
        self.station_url = "http://apis.data.go.kr/1613000/BusSttnInfoInqireService"
        self.arrival_url = "http://apis.data.go.kr/1613000/ArvlInfoInqireService"

    def _make_request(self, base_url, endpoint, params):
        url = f"{base_url}/{endpoint}"
        base_params = {'serviceKey': self.service_key, '_type': 'json'}
        base_params.update(params)
        try:
            response = requests.get(url, params=base_params, timeout=10)
            response.raise_for_status()
            data = response.json()
            body = data.get("response", {}).get("body")
            if body and body.get("items"):
                items = body["items"].get("item")
                if items:
                    return [items] if isinstance(items, dict) else items
            return None
        except Exception as e:
            print(f"❌ API 요청 오류: {e}")
            return None

    def find_station_by_number(self, city_code, station_number):
        return self._make_request(self.station_url, 'getSttnNoList', {'cityCode': city_code, 'nodeNo': station_number})
    def find_station_by_name(self, city_code, station_name):
        return self._make_request(self.station_url, 'getSttnNmList', {'cityCode': city_code, 'nodeNm': station_name})
    def get_routes_at_station(self, city_code, node_id):
        return self._make_request(self.station_url, "getSttnThrghRouteList", {'cityCode': city_code, 'nodeid': node_id})
    def get_arrival_info(self, city_code, node_id, route_id):
        return self._make_request(self.arrival_url, "getSttnAcctoSpcifyRouteBusArvlPrearngeInfoList", {'cityCode': city_code, 'nodeId': node_id, 'routeId': route_id})


SERVICE_KEY = "b5c41e075a1fb41b7b611207641135a0b70667b6975f1eec9d245e50cea6edc9"
api = BusTrackerApi(SERVICE_KEY)

# (REGION_DATA는 그대로 유지)
REGION_DATA = {
    "경기도": {
        "고양시": "31100", "수원시": "31010", "성남시": "31020", "용인시": "31190", "부천시": "31050",
        "안산시": "31090", "안양시": "31040", "남양주시": "31130", "화성시": "31240", "평택시": "31070",
        "의정부시": "31030", "시흥시": "31150", "파주시": "31200", "김포시": "31230", "광명시": "31060",
        "군포시": "31160", "오산시": "31140", "이천시": "31210", "안성시": "31220", "하남시": "31180",
        "의왕시": "31170", "포천시": "31270", "여주시": "31320", "양평군": "31380", "동두천시": "31080",
        "광주시": "31250", "과천시": "31110", "구리시": "31120", "양주시": "31260", "가평군": "31370", "연천군": "31350"
    },
    "서울특별시": { "서울 전체": "11" },
    "부산광역시": { "부산 전체": "21" },
    "대구광역시": { "대구 전체": "22" },
    "인천광역시": { "인천 전체": "23" },
    "광주광역시": { "광주 전체": "24" },
    "대전광역시": { "대전 전체": "25" },
    "울산광역시": { "울산 전체": "26" },
    "세종특별자치시": { "세종 전체": "29" },
    "제주특별자치도": { "제주 전체": "39" }
}

# --- 3. 알림 발송 헬퍼 함수 ---
def send_notification(subscription_info, message_data):
    """ 구독자에게 실제 푸시 알림을 발송하는 함수 """
    try:
        endpoint = subscription_info["endpoint"]
        audience = urlparse(endpoint).scheme + "://" + urlparse(endpoint).netloc
        dynamic_vapid_claims = {
            "sub": "mailto:timehole0828@naver.com", # Eric님 이메일
            "aud": audience
        }
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(message_data),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=dynamic_vapid_claims
        )
        print(f"알림 발송 성공: {audience}")
    except WebPushException as ex:
        print(f"알림 발송 실패: {ex}")


# --- 4. 백그라운드 스케줄러가 실행할 함수 (핵심!) ---
def check_buses_and_notify():
    """ 30초마다 실행되며, DB의 모든 구독 버스를 확인하고 알림을 보냅니다. """
    print(f"\n--- (매 30초) 백그라운드 알림 작업 시작 ---")
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row # 결과를 dict처럼 접근 가능하게
    c = conn.cursor()

    # 1. DB에서 구독된 *모든* 버스 목록을 중복 없이 가져오기
    c.execute("SELECT DISTINCT city_code, node_id, route_id, bus_number, station_name FROM subscriptions")
    subscribed_buses = c.fetchall()

    if not subscribed_buses:
        print("구독된 버스가 없습니다. 작업 종료.")
        conn.close()
        return

    print(f"총 {len(subscribed_buses)}개의 고유 버스 노선 도착 정보 확인 중...")

    # 2. 각 버스의 실시간 도착 정보 조회
    for bus in subscribed_buses:
        arrival_info_list = api.get_arrival_info(bus['city_code'], bus['node_id'], bus['route_id'])

        if not arrival_info_list:
            continue # 해당 버스 도착 정보 없음

        # 3. 5분 이내 도착 예정인 버스인지 확인
        for arrival_info in arrival_info_list:
            arrival_time_min = arrival_info.get("arrtime", 9999) // 60

            if arrival_time_min <= 5:
                print(f"곧 도착! ({bus['bus_number']}번 버스, {arrival_time_min}분 후)")

                # 4. 이 버스(route_id)를 구독한 *모든* 사용자 찾기
                c.execute("SELECT subscription_json FROM subscriptions WHERE route_id = ?", (bus['route_id'],))
                subscribers = c.fetchall()

                message = f"{arrival_time_min}분 후 도착 예정 ({arrival_info.get('arrprevstationcnt')}개 정류장 남음)"
                notification_data = {
                    "title": f"🚍 {bus['bus_number']}번 버스 (@{bus['station_name']})", # 제목에 정류소 이름 추가
                    "body": message
                }

                # 5. 찾은 사용자들에게만 알림 발송
                for sub_row in subscribers:
                    subscription_info_obj = json.loads(sub_row['subscription_json'])
                    send_notification(subscription_info_obj, notification_data)

                # 알림은 이 노선에 대해 한 번만 보내면 되므로, 다음 버스로 넘어감
                break

    conn.close()
    print("--- 백그라운드 알림 작업 종료 ---")


# --- 라우팅 ---
@app.route("/")
def index():
    # index.html 파일을 templates 폴더에서 찾아 렌더링
    return render_template("index.html", region_data=REGION_DATA, vapid_public_key=VAPID_PUBLIC_KEY)

@app.route("/api/search-station")
def search_station():
    city_code = request.args.get('cityCode')
    query = request.args.get('query')
    stations = None
    if len(city_code) < 5:
        stations = api.find_station_by_name(city_code, query)
    else:
        stations = api.find_station_by_number(city_code, query)
    if not stations:
        return jsonify({"error": "검색 결과가 없습니다. 오타를 확인하거나 다른 검색어를 입력해주세요."}), 404
    return jsonify(stations)

@app.route("/api/get-routes")
def get_routes():
    city_code = request.args.get('cityCode')
    node_id = request.args.get('nodeId')
    routes = api.get_routes_at_station(city_code, node_id)
    if not routes:
        return jsonify({"error": "해당 정류소의 노선 정보를 가져올 수 없습니다."}), 404
    return jsonify(routes)

# --- 5. 구독 관련 API ---

@app.route("/api/subscribe-bus", methods=["POST"])
def subscribe_bus():
    """ 특정 버스에 대한 알림 구독 요청을 DB에 저장 """
    data = request.json
    bus_info = data.get('busInfo')
    subscription_info = data.get('subscription')

    if not bus_info or not subscription_info:
        return jsonify({"success": False, "error": "필수 정보 누락"}), 400

    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO subscriptions (city_code, node_id, route_id, bus_number, station_name, subscription_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            bus_info['cityCode'], bus_info['stationId'], bus_info['routeId'],
            bus_info['busNumber'], bus_info['stationName'],
            json.dumps(subscription_info) # 구독 정보는 통째로 JSON 문자열로 저장
        ))
        conn.commit()
        conn.close()
        print(f"새로운 구독: {bus_info['busNumber']}번 버스")
        return jsonify({"success": True}), 201
    except sqlite3.IntegrityError:
        print(f"이미 구독됨: {bus_info['busNumber']}번 버스")
        return jsonify({"success": True, "message": "이미 구독됨"}), 200
    except Exception as e:
        print(f"구독 저장 오류: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/unsubscribe-bus", methods=["POST"])
def unsubscribe_bus():
    """ 특정 버스에 대한 알림 구독을 DB에서 삭제 """
    data = request.json
    bus_info = data.get('busInfo')
    subscription_info = data.get('subscription')

    if not bus_info or not subscription_info:
        return jsonify({"success": False, "error": "필수 정보 누락"}), 400

    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            DELETE FROM subscriptions 
            WHERE route_id = ? AND subscription_json = ?
        """, (
            bus_info['routeId'],
            json.dumps(subscription_info) # 동일한 사용자의 동일한 구독 정보
        ))
        conn.commit()
        conn.close()
        print(f"구독 취소: {bus_info['busNumber']}번 버스")
        return jsonify({"success": True}), 200
    except Exception as e:
        print(f"구독 취소 오류: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/arrival-info")
def get_arrival_info():
    """ 이 API는 이제 알림 발송 없이, 순수하게 도착 정보만 조회 """
    city_code = request.args.get('cityCode')
    node_id = request.args.get('nodeId')
    route_id = request.args.get('routeId')

    arrival_info = api.get_arrival_info(city_code, node_id, route_id)

    return jsonify(arrival_info if arrival_info else [])


# --- 6. 앱 실행시 스케줄러 시작 ---
if __name__ == "__main__":
    init_db() # 1. 앱 시작 시 DB 테이블이 없으면 생성

    # 2. 백그라운드 스케줄러 설정 및 시작
    scheduler = BackgroundScheduler()
    # 30초마다 check_buses_and_notify 함수를 실행하도록 예약
    scheduler.add_job(func=check_buses_and_notify, trigger="interval", seconds=30)
    scheduler.start()

    # 3. 앱 종료 시 스케줄러도 함께 종료되도록 등록
    atexit.register(lambda: scheduler.shutdown())

    # 4. Flask 앱 실행
    # use_reloader=False 는 스케줄러가 두 번 실행되는 것을 방지하기 위해 필수
    app.run(debug=True, use_reloader=False)