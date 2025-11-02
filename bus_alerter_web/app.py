# app.py (웹 호스팅용 최종본)

import json
from flask import Flask, render_template, request, jsonify
import requests
from pywebpush import webpush, WebPushException
from urllib.parse import urlparse # 403 에러 해결용

# --- Flask 앱 및 기본 설정 ---
app = Flask(__name__)

# VAPID 키 (설정한 값을 그대로 유지)
VAPID_PRIVATE_KEY = "7OgSMB-QyC9fdzmQtTUvXgm0P7JusIjGPjND3ySEoxo"
VAPID_PUBLIC_KEY = "BHD7yQNjasAtJb78-u8O9CdSQjh_5D9ZjqjSvUTsCrUxO4Mj5HdvlqUOYaErKFZf9cLR5bdsf1NEmbLSRafkagA"

user_subscriptions = []

# --- BusTrackerApi 클래스 ---
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

# --- 라우팅 ---
@app.route("/")
def index():
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

@app.route("/api/save-subscription", methods=["POST"])
def save_subscription():
    subscription_data = request.json
    if subscription_data not in user_subscriptions:
        user_subscriptions.append(subscription_data)
    print(f"새로운 구독자 저장! 총 {len(user_subscriptions)}명")
    return jsonify({"success": True}), 201

@app.route("/api/arrival-info")
def get_arrival_info():
    city_code = request.args.get('cityCode')
    node_id = request.args.get('nodeId')
    route_id = request.args.get('routeId')
    bus_number = request.args.get('busNumber')
    station_name = request.args.get('stationName')

    arrival_info = api.get_arrival_info(city_code, node_id, route_id)

    if arrival_info and user_subscriptions:
        for bus in arrival_info:
            arrival_time_min = bus.get("arrtime", 0) // 60

            if arrival_time_min <= 5:
                message = f"{arrival_time_min}분 후 도착 예정 ({bus.get('arrprevstationcnt')}개 정류장 남음)"
                notification_data = { "title": f"🚍 {bus_number}번 버스 곧 도착!", "body": message }

                for sub in user_subscriptions:
                    try:
                        endpoint = sub["endpoint"]
                        audience = urlparse(endpoint).scheme + "://" + urlparse(endpoint).netloc

                        dynamic_vapid_claims = {
                            "sub": "mailto:timehole0828@naver.com",
                            "aud": audience
                        }

                        webpush(
                            subscription_info=sub,
                            data=json.dumps(notification_data),
                            vapid_private_key=VAPID_PRIVATE_KEY,
                            vapid_claims=dynamic_vapid_claims
                        )
                        print(f"알림 발송 성공: {audience}")
                    except WebPushException as ex:
                        print(f"알림 발송 실패: {ex}")

                break

    return jsonify(arrival_info if arrival_info else [])

if __name__ == "__main__":
    # use_reloader=False 추가: 파일이 변경되어도 서버가 재시작되지 않도록 설정
    app.run(debug=True, use_reloader=False)