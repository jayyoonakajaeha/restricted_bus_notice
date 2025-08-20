"""
통합 API 서버 - 버스 API + 카카오톡 챗봇
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import json
from datetime import datetime, date
import asyncio
import logging
from contextlib import asynccontextmanager
import requests

# .env 파일 로드 (Windows 지원)
try:
    from dotenv import load_dotenv
    load_dotenv()  # .env 파일 자동 로드
except ImportError:
    # python-dotenv가 없으면 수동으로 로드
    try:
        from env_setup import setup_env
        setup_env()
    except ImportError:
        pass

# 기존 모듈 import
from restricted_bus import TOPISCrawler
from position_checker import check_control_by_position, get_stations_by_position

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 전역 변수
crawler = None
cached_notices = []
last_update = None

# ===== 기존 Pydantic 모델들 =====
class StationInfo(BaseModel):
    name: str
    periods: List[str]
    affected_routes: List[str]
    control_scope: str

class Notice(BaseModel):
    seq: str
    title: str
    create_date: str
    view_count: int
    category: str
    control_type: str
    general_periods: List[str]
    station_info: Dict[str, StationInfo]
    detour_routes: Dict[str, str]
    route_pages: Dict[str, int]

class RouteControlInfo(BaseModel):
    notice_title: str
    control_type: str
    affected_stations: List[Dict[str, Any]]
    detour_path: str
    periods: List[str]
    page_info: Optional[int]

class PositionRequest(BaseModel):
    tm_x: float
    tm_y: float
    radius: int = 500
    target_date: Optional[str] = None

class ControlResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None
    timestamp: datetime

# ===== 카카오톡 관련 추가 =====
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")

class KakaoResponse:
    """카카오톡 응답 생성 헬퍼"""
    
    @staticmethod
    def simple_text(text: str) -> Dict:
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}]
            }
        }
    
    @staticmethod
    def quick_replies(text: str, replies: List[str]) -> Dict:
        quick_replies = [{"label": reply, "action": "message", "messageText": reply} for reply in replies]
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": quick_replies
            }
        }
    
    @staticmethod
    def basic_card(title: str, description: str, buttons: List[Dict] = None) -> Dict:
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "basicCard": {
                        "title": title,
                        "description": description,
                        "buttons": buttons or []
                    }
                }]
            }
        }

def get_location_info(query: str) -> Optional[Dict]:
    """카카오 장소 검색"""
    if not KAKAO_REST_API_KEY:
        return None
    
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {"query": query}
    
    try:
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        
        if data.get("documents"):
            doc = data["documents"][0]
            return {
                "name": doc["place_name"],
                "address": doc.get("road_address_name") or doc.get("address_name"),
                "x": float(doc["x"]),  # 경도 (WGS84)
                "y": float(doc["y"])   # 위도 (WGS84)
            }
    except Exception as e:
        print(f"카카오 장소 검색 오류: {e}")
    
    return None

def wgs84_to_tm(lon: float, lat: float) -> tuple:
    """WGS84 좌표를 TM 좌표로 변환 (근사값)"""
    tm_x = (lon - 126.0) * 111320 * 0.8 + 196000
    tm_y = (lat - 37.0) * 111320 * 1.0 + 450000
    return tm_x, tm_y

# 사용자 세션 관리 (메모리 기반)
user_sessions = {}

def save_user_location(user_id: str, location_data: Dict):
    """사용자 위치 정보 저장 (메모리)"""
    user_sessions[user_id] = {
        "location": location_data,
        "updatedAt": datetime.now()
    }

def get_user_location(user_id: str) -> Optional[Dict]:
    """사용자 위치 정보 조회"""
    return user_sessions.get(user_id, {}).get("location")

async def initialize_crawler():
    """크롤러 초기화"""
    global crawler, cached_notices, last_update
    
    try:
        # 환경변수에서 API 키 가져오기
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("Gemini API Key가 설정되지 않았습니다.")
        
        crawler = TOPISCrawler(gemini_api_key=gemini_api_key)
        
        # 초기 데이터 로드
        cached_notices, cache_hit = crawler.crawl_notices()
        last_update = datetime.now()
        
        logger.info(f"크롤러 초기화 완료. {len(cached_notices)}개 공지사항 로드됨")
        
    except Exception as e:
        logger.error(f"크롤러 초기화 실패: {e}")
        raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 실행
    await initialize_crawler()
    yield
    # 종료 시 실행 (필요시)
    pass

# FastAPI 앱 생성
app = FastAPI(
    title="서울 버스 통제 알림 API + 카카오톡 챗봇",
    description="서울시 버스 운행 변경 및 통제 정보 조회 API + 카카오톡 인터페이스",
    version="2.0.0",
    lifespan=lifespan
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 프로덕션에서는 특정 도메인으로 제한
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def update_notices_background():
    """백그라운드에서 공지사항 업데이트"""
    global cached_notices, last_update
    
    try:
        new_notices, cache_hit = crawler.crawl_notices()
        cached_notices = new_notices
        last_update = datetime.now()
        logger.info(f"백그라운드 업데이트 완료. {len(cached_notices)}개 공지사항")
    except Exception as e:
        logger.error(f"백그라운드 업데이트 실패: {e}")

# ===== 기존 API 엔드포인트들 =====

@app.get("/", tags=["기본"])
async def root():
    """API 기본 정보"""
    return {
        "service": "서울 버스 통제 알림 API + 카카오톡 챗봇",
        "version": "2.0.0",
        "status": "running",
        "last_update": last_update,
        "cached_notices": len(cached_notices) if cached_notices else 0,
        "features": ["REST API", "카카오톡 챗봇", "위치 기반 서비스", "노선 이미지"]
    }

@app.get("/health", tags=["기본"])
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy", 
        "timestamp": datetime.now(),
        "kakao_api": "configured" if KAKAO_REST_API_KEY else "missing",
        "cached_notices": len(cached_notices) if cached_notices else 0
    }

@app.get("/notices", response_model=List[Notice], tags=["공지사항"])
async def get_notices(
    date: Optional[str] = Query(None, description="조회할 날짜 (YYYY-MM-DD)")
):
    """공지사항 목록 조회"""
    try:
        if not cached_notices:
            raise HTTPException(status_code=503, detail="데이터를 로드 중입니다. 잠시 후 다시 시도해주세요.")
        
        if date:
            # 날짜 유효성 검사
            try:
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
            
            filtered_notices = crawler.filter_by_date(cached_notices, date)
            return filtered_notices
        
        return cached_notices
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"공지사항 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="공지사항 조회 중 오류가 발생했습니다.")

@app.get("/routes/{route_number}/controls", response_model=List[RouteControlInfo], tags=["노선"])
async def get_route_controls(
    route_number: str,
    date: str = Query(..., description="조회할 날짜 (YYYY-MM-DD)")
):
    """특정 노선의 통제 정보 조회"""
    try:
        # 날짜 유효성 검사
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
        
        controls = crawler.get_control_info_by_route(cached_notices, date, route_number)
        
        if not controls:
            raise HTTPException(
                status_code=404, 
                detail=f"날짜 {date}에 노선 {route_number}의 통제 정보가 없습니다."
            )
        
        return controls
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"노선 통제 정보 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="노선 통제 정보 조회 중 오류가 발생했습니다.")

@app.post("/position/controls", tags=["위치"])
async def get_position_controls(request: PositionRequest):
    """위치 기반 통제 정류소 조회"""
    try:
        # 주변 정류소 조회
        service_key = crawler.service_key
        nearby_stations = get_stations_by_position(
            service_key, request.tm_x, request.tm_y, request.radius
        )
        
        if not nearby_stations:
            return ControlResponse(
                success=True,
                message="주변에 정류소가 없습니다.",
                data={"nearby_stations": [], "controlled_stations": []},
                timestamp=datetime.now()
            )
        
        # 통제 정보가 있는 경우만 확인
        controlled_stations = []
        if request.target_date:
            filtered_notices = crawler.filter_by_date(cached_notices, request.target_date)
            
            # 통제 정류소 목록 수집
            controlled_stations_dict = {}
            for notice in filtered_notices:
                station_info = notice.get('station_info', {})
                detour_routes = notice.get('detour_routes', {})
                
                for station_id, info in station_info.items():
                    controlled_stations_dict[station_id] = {
                        'name': info.get('name', ''),
                        'periods': info.get('periods', []),
                        'affected_routes': info.get('affected_routes', []),
                        'control_scope': info.get('control_scope', ''),
                        'notice_title': notice['title'],
                        'detour_routes': {k: v for k, v in detour_routes.items() 
                                        if k in info.get('affected_routes', [])}
                    }
            
            # 주변 정류소와 통제 정류소 매칭
            for nearby_station in nearby_stations:
                station_id = nearby_station['id']
                ars_id = nearby_station['ars_id']
                station_name = nearby_station['name']
                
                # 매칭 시도
                matched_control = None
                if ars_id and ars_id in controlled_stations_dict:
                    matched_control = controlled_stations_dict[ars_id]
                elif station_id in controlled_stations_dict:
                    matched_control = controlled_stations_dict[station_id]
                else:
                    # 이름으로 매칭
                    for ctrl_id, ctrl_info in controlled_stations_dict.items():
                        ctrl_name = ctrl_info['name']
                        if ctrl_name and (station_name in ctrl_name or ctrl_name in station_name):
                            matched_control = ctrl_info
                            break
                
                if matched_control:
                    controlled_stations.append({
                        'station_id': station_id,
                        'ars_id': ars_id,
                        'station_name': station_name,
                        'control_info': matched_control
                    })
        
        return ControlResponse(
            success=True,
            message=f"반경 {request.radius}m 내 정류소 {len(nearby_stations)}개 발견, 통제 정류소 {len(controlled_stations)}개",
            data={
                "search_info": {
                    "tm_x": request.tm_x,
                    "tm_y": request.tm_y,
                    "radius": request.radius,
                    "target_date": request.target_date
                },
                "nearby_stations": nearby_stations,
                "controlled_stations": controlled_stations
            },
            timestamp=datetime.now()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"위치 기반 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="위치 기반 조회 중 오류가 발생했습니다.")

@app.post("/update", tags=["관리"])
async def manual_update(background_tasks: BackgroundTasks):
    """수동으로 공지사항 업데이트"""
    try:
        background_tasks.add_task(update_notices_background)
        
        return ControlResponse(
            success=True,
            message="백그라운드에서 업데이트를 시작했습니다.",
            data={"last_update": last_update},
            timestamp=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"수동 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail="업데이트 중 오류가 발생했습니다.")

# ===== 카카오톡 웹훅 엔드포인트들 =====

@app.post("/webhook/bus_info", tags=["카카오톡"])
async def bus_info_webhook(req: Request):
    """버스 통제 정보 조회 웹훅"""
    body = await req.json()
    
    # 사용자 ID 추출
    if 'userRequest' in body:
        user_id = body['userRequest']['user']['id']
        utterance = body['userRequest']['utterance']
    else:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    # 오늘 날짜
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # 오늘의 버스 통제 정보 조회
        filtered_notices = crawler.filter_by_date(cached_notices, today)
        
        if not filtered_notices:
            return KakaoResponse.simple_text(f"오늘({today}) 버스 통제 정보가 없습니다. 🚌✅")
        
        # 통제 정보 요약
        control_summary = {}
        for notice in filtered_notices[:3]:  # 최대 3개만
            control_type = notice.get('control_type', '통제')
            control_summary[control_type] = control_summary.get(control_type, 0) + 1
        
        summary_text = f"📅 오늘({today}) 버스 통제 현황\n\n"
        summary_text += f"🚨 총 {len(filtered_notices)}건의 통제 정보\n"
        
        for control_type, count in control_summary.items():
            summary_text += f"• {control_type}: {count}건\n"
        
        # 주요 통제 정보 미리보기
        summary_text += "\n📋 주요 통제 정보:\n"
        for i, notice in enumerate(filtered_notices[:2], 1):
            title = notice.get('title', '제목 없음')
            if len(title) > 30:
                title = title[:30] + "..."
            summary_text += f"{i}. {title}\n"
        
        if len(filtered_notices) > 2:
            summary_text += f"   ... 외 {len(filtered_notices)-2}건\n"
        
        # 빠른 답변 버튼
        quick_replies = ["내 위치 주변 확인", "특정 노선 조회", "도움말"]
        
        return KakaoResponse.quick_replies(summary_text, quick_replies)
        
    except Exception as e:
        return KakaoResponse.simple_text(f"버스 정보 조회 중 오류가 발생했습니다: {str(e)}")

@app.post("/webhook/route_check", tags=["카카오톡"])
async def route_check_webhook(req: Request):
    """특정 노선 통제 정보 조회"""
    body = await req.json()
    
    if 'userRequest' in body:
        user_id = body['userRequest']['user']['id']
        # 파라미터에서 노선 번호 추출
        params = body.get('action', {}).get('params', {})
        route_number = params.get('route_number', '').strip()
    else:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    if not route_number:
        return KakaoResponse.simple_text("노선 번호를 입력해주세요.\n예: 406, 143, 7016")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        controls = crawler.get_control_info_by_route(cached_notices, today, route_number)
        
        if not controls:
            return KakaoResponse.simple_text(f"🚌 노선 {route_number}번\n오늘({today}) 통제 정보가 없습니다. ✅")
        
        # 통제 정보 포맷팅
        response_text = f"🚨 노선 {route_number}번 통제 정보\n\n"
        
        for i, control in enumerate(controls, 1):
            response_text += f"【{i}】 {control.get('control_type', '통제')}\n"
            response_text += f"📄 {control.get('notice_title', '제목 없음')}\n"
            
            # 영향 정류소
            stations = control.get('affected_stations', [])
            if stations:
                station_names = [s.get('station_name', '이름없음') for s in stations[:3]]
                response_text += f"🚏 영향 정류소: {', '.join(station_names)}"
                if len(stations) > 3:
                    response_text += f" 외 {len(stations)-3}곳"
                response_text += "\n"
            
            # 우회 경로
            detour = control.get('detour_path', '')
            if detour:
                if len(detour) > 50:
                    detour = detour[:50] + "..."
                response_text += f"🔄 우회: {detour}\n"
            
            response_text += "\n"
        
        return KakaoResponse.simple_text(response_text)
        
    except Exception as e:
        return KakaoResponse.simple_text(f"노선 정보 조회 중 오류: {str(e)}")

@app.post("/webhook/location_save", tags=["카카오톡"])
async def location_save_webhook(req: Request, background_tasks: BackgroundTasks):
    """사용자 위치 저장"""
    body = await req.json()
    
    if 'userRequest' in body:
        user_id = body['userRequest']['user']['id']
        params = body.get('action', {}).get('params', {})
        location_name = params.get('location', '').strip()
    else:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    if not location_name:
        return KakaoResponse.simple_text("위치를 입력해주세요.\n예: 강남역, 홍대입구역, 명동")
    
    # 카카오 장소 검색
    location_info = get_location_info(location_name)
    
    if not location_info:
        return KakaoResponse.simple_text(f"'{location_name}' 위치를 찾을 수 없습니다.\n다른 키워드로 다시 시도해주세요.")
    
    # 백그라운드에서 저장
    background_tasks.add_task(save_user_location, user_id, location_info)
    
    response_text = f"📍 위치 저장 완료!\n\n"
    response_text += f"🏢 {location_info['name']}\n"
    response_text += f"📮 {location_info['address']}\n\n"
    response_text += "이제 '내 주변 확인'으로 주변 버스 통제 정보를 확인할 수 있습니다."
    
    quick_replies = ["내 주변 확인", "오늘 버스 정보", "노선 조회"]
    
    return KakaoResponse.quick_replies(response_text, quick_replies)

@app.post("/webhook/nearby_check", tags=["카카오톡"])
async def nearby_check_webhook(req: Request):
    """사용자 주변 통제 정보 조회"""
    body = await req.json()
    
    if 'userRequest' in body:
        user_id = body['userRequest']['user']['id']
    else:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    # 사용자 위치 확인
    location = get_user_location(user_id)
    if not location:
        return KakaoResponse.simple_text("먼저 위치를 등록해주세요.\n'위치 등록' 메뉴를 이용하세요.")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # WGS84 → TM 좌표 변환
        tm_x, tm_y = wgs84_to_tm(location["x"], location["y"])
        
        # 위치 기반 조회
        request_data = PositionRequest(tm_x=tm_x, tm_y=tm_y, radius=500, target_date=today)
        result = await get_position_controls(request_data)
        
        if not result.success:
            return KakaoResponse.simple_text(f"주변 정보 조회 실패: {result.message}")
        
        data = result.data
        nearby_stations = data.get('nearby_stations', [])
        controlled_stations = data.get('controlled_stations', [])
        
        response_text = f"📍 {location['name']} 주변 500m\n\n"
        response_text += f"🚏 주변 정류소: {len(nearby_stations)}개\n"
        response_text += f"🚨 통제 정류소: {len(controlled_stations)}개\n\n"
        
        if controlled_stations:
            response_text += "⚠️ 통제 중인 정류소:\n"
            for i, station in enumerate(controlled_stations[:3], 1):
                station_name = station.get('station_name', '이름없음')
                control_info = station.get('control_info', {})
                affected_routes = control_info.get('affected_routes', [])
                
                response_text += f"{i}. {station_name}\n"
                if affected_routes:
                    routes_text = ', '.join(affected_routes[:5])
                    if len(affected_routes) > 5:
                        routes_text += f" 외 {len(affected_routes)-5}개"
                    response_text += f"   🚌 {routes_text}\n"
                response_text += "\n"
            
            if len(controlled_stations) > 3:
                response_text += f"... 외 {len(controlled_stations)-3}곳\n"
        else:
            response_text += "✅ 주변에 통제 중인 정류소가 없습니다."
        
        return KakaoResponse.simple_text(response_text)
        
    except Exception as e:
        return KakaoResponse.simple_text(f"주변 정보 조회 중 오류: {str(e)}")

@app.post("/webhook/help", tags=["카카오톡"])
async def help_webhook(req: Request):
    """도움말"""
    help_text = """🚌 서울 버스 통제 알림봇 사용법

📋 주요 기능:
• 오늘 버스 정보 - 전체 통제 현황
• 노선 조회 - 특정 노선 통제 정보
• 위치 등록 - 내 위치 저장
• 내 주변 확인 - 주변 통제 정류소

💬 사용 예시:
• "406번 확인해줘"
• "강남역 등록"
• "내 주변 알려줘"

🔄 실시간 업데이트:
서울시 TOPIS 시스템과 연동하여
최신 버스 통제 정보를 제공합니다.
"""
    
    quick_replies = ["오늘 버스 정보", "위치 등록", "노선 조회"]
    
    return KakaoResponse.quick_replies(help_text, quick_replies)

# ===== 기타 엔드포인트들 =====

@app.get("/stats", tags=["통계"])
async def get_statistics():
    """시스템 통계 정보"""
    try:
        stats = {
            "total_notices": len(cached_notices),
            "last_update": last_update,
            "cache_file_size": 0,
            "notices_by_type": {},
            "recent_notices": [],
            "user_sessions": len(user_sessions)
        }
        
        # 캐시 파일 크기
        cache_file = "topis_cache.json"
        if os.path.exists(cache_file):
            stats["cache_file_size"] = os.path.getsize(cache_file)
        
        # 통제 유형별 통계
        for notice in cached_notices:
            control_type = notice.get('control_type', '기타')
            stats["notices_by_type"][control_type] = stats["notices_by_type"].get(control_type, 0) + 1
        
        # 최근 5개 공지사항
        stats["recent_notices"] = [
            {
                "seq": notice.get('seq'),
                "title": notice.get('title'),
                "create_date": notice.get('create_date'),
