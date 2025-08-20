"""
통합 API 서버 - 버스 API + 카카오톡 챗봇 (수정 버전)
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

# .env 파일 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 기존 모듈 import (없으면 스킵)
try:
    from restricted_bus import TOPISCrawler
    from position_checker import get_stations_by_position
    CRAWLER_AVAILABLE = True
except ImportError:
    print("⚠️ 크롤러 모듈을 찾을 수 없습니다. API 기능이 제한됩니다.")
    CRAWLER_AVAILABLE = False

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 전역 변수
crawler = None
cached_notices = []
last_update = None

# Pydantic 모델들
class ControlResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None
    timestamp: datetime

class PositionRequest(BaseModel):
    tm_x: float
    tm_y: float
    radius: int = 500
    target_date: Optional[str] = None

# 카카오톡 관련
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
                "x": float(doc["x"]),
                "y": float(doc["y"])
            }
    except Exception as e:
        print(f"카카오 장소 검색 오류: {e}")
    
    return None

def wgs84_to_tm(lon: float, lat: float) -> tuple:
    """WGS84 좌표를 TM 좌표로 변환"""
    tm_x = (lon - 126.0) * 111320 * 0.8 + 196000
    tm_y = (lat - 37.0) * 111320 * 1.0 + 450000
    return tm_x, tm_y

# 사용자 세션 관리
user_sessions = {}

def save_user_location(user_id: str, location_data: Dict):
    """사용자 위치 정보 저장"""
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
    
    if not CRAWLER_AVAILABLE:
        logger.warning("크롤러 모듈을 사용할 수 없습니다.")
        cached_notices = []
        last_update = datetime.now()
        return
    
    try:
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("Gemini API Key가 설정되지 않았습니다.")
        
        crawler = TOPISCrawler(gemini_api_key=gemini_api_key)
        cached_notices, cache_hit = crawler.crawl_notices()
        last_update = datetime.now()
        
        logger.info(f"크롤러 초기화 완료. {len(cached_notices)}개 공지사항 로드됨")
        
    except Exception as e:
        logger.error(f"크롤러 초기화 실패: {e}")
        cached_notices = []
        last_update = datetime.now()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialize_crawler()
    yield

# FastAPI 앱 생성
app = FastAPI(
    title="서울 버스 통제 알림 API + 카카오톡 챗봇",
    description="서울시 버스 운행 변경 및 통제 정보 조회 API + 카카오톡 인터페이스",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 기본 엔드포인트들
@app.get("/", tags=["기본"])
async def root():
    """API 기본 정보"""
    return {
        "service": "서울 버스 통제 알림 API + 카카오톡 챗봇",
        "version": "2.0.0",
        "status": "running",
        "last_update": last_update,
        "cached_notices": len(cached_notices) if cached_notices else 0,
        "features": ["REST API", "카카오톡 챗봇", "위치 기반 서비스"],
        "crawler_available": CRAWLER_AVAILABLE
    }

@app.get("/health", tags=["기본"])
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(),
        "kakao_api": "configured" if KAKAO_REST_API_KEY else "missing",
        "cached_notices": len(cached_notices) if cached_notices else 0,
        "crawler_status": "available" if CRAWLER_AVAILABLE else "unavailable"
    }

@app.get("/notices", tags=["공지사항"])
async def get_notices(date: Optional[str] = Query(None, description="조회할 날짜 (YYYY-MM-DD)")):
    """공지사항 목록 조회"""
    if not CRAWLER_AVAILABLE:
        raise HTTPException(status_code=503, detail="크롤러 모듈을 사용할 수 없습니다.")
    
    if not cached_notices:
        raise HTTPException(status_code=503, detail="데이터를 로드 중입니다.")
    
    if date:
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다.")
        
        filtered_notices = crawler.filter_by_date(cached_notices, date)
        return filtered_notices
    
    return cached_notices

@app.get("/routes/{route_number}/controls", tags=["노선"])
async def get_route_controls(route_number: str, date: str = Query(..., description="조회할 날짜")):
    """특정 노선의 통제 정보 조회"""
    if not CRAWLER_AVAILABLE:
        raise HTTPException(status_code=503, detail="크롤러 모듈을 사용할 수 없습니다.")
    
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다.")
    
    controls = crawler.get_control_info_by_route(cached_notices, date, route_number)
    
    if not controls:
        raise HTTPException(status_code=404, detail=f"날짜 {date}에 노선 {route_number}의 통제 정보가 없습니다.")
    
    return controls

@app.post("/position/controls", tags=["위치"])
async def get_position_controls(request: PositionRequest):
    """위치 기반 통제 정류소 조회"""
    if not CRAWLER_AVAILABLE:
        return ControlResponse(
            success=False,
            message="크롤러 모듈을 사용할 수 없습니다.",
            timestamp=datetime.now()
        )
    
    try:
        service_key = crawler.service_key
        nearby_stations = get_stations_by_position(service_key, request.tm_x, request.tm_y, request.radius)
        
        return ControlResponse(
            success=True,
            message=f"반경 {request.radius}m 내 정류소 {len(nearby_stations)}개 발견",
            data={"nearby_stations": nearby_stations},
            timestamp=datetime.now()
        )
        
    except Exception as e:
        return ControlResponse(
            success=False,
            message=f"위치 기반 조회 오류: {str(e)}",
            timestamp=datetime.now()
        )

# 카카오톡 웹훅 엔드포인트들
@app.post("/webhook/bus_info", tags=["카카오톡"])
async def bus_info_webhook(req: Request):
    """버스 통제 정보 조회 웹훅"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return KakaoResponse.simple_text("현재 버스 정보 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.")
    
    try:
        filtered_notices = crawler.filter_by_date(cached_notices, today)
        
        if not filtered_notices:
            return KakaoResponse.simple_text(f"오늘({today}) 버스 통제 정보가 없습니다. 🚌✅")
        
        control_summary = {}
        for notice in filtered_notices[:3]:
            control_type = notice.get('control_type', '통제')
            control_summary[control_type] = control_summary.get(control_type, 0) + 1
        
        summary_text = f"📅 오늘({today}) 버스 통제 현황\n\n"
        summary_text += f"🚨 총 {len(filtered_notices)}건의 통제 정보\n"
        
        for control_type, count in control_summary.items():
            summary_text += f"• {control_type}: {count}건\n"
        
        summary_text += "\n📋 주요 통제 정보:\n"
        for i, notice in enumerate(filtered_notices[:2], 1):
            title = notice.get('title', '제목 없음')
            if len(title) > 30:
                title = title[:30] + "..."
            summary_text += f"{i}. {title}\n"
        
        if len(filtered_notices) > 2:
            summary_text += f"   ... 외 {len(filtered_notices)-2}건\n"
        
        quick_replies = ["내 위치 주변 확인", "특정 노선 조회", "도움말"]
        return KakaoResponse.quick_replies(summary_text, quick_replies)
        
    except Exception as e:
        return KakaoResponse.simple_text(f"버스 정보 조회 중 오류가 발생했습니다: {str(e)}")

@app.post("/webhook/route_check", tags=["카카오톡"])
async def route_check_webhook(req: Request):
    """특정 노선 통제 정보 조회"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    params = body.get('action', {}).get('params', {})
    route_number = params.get('route_number', '').strip()
    
    if not route_number:
        return KakaoResponse.simple_text("노선 번호를 입력해주세요.\n예: 406, 143, 7016")
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return KakaoResponse.simple_text("현재 버스 정보 서비스를 사용할 수 없습니다.")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        controls = crawler.get_control_info_by_route(cached_notices, today, route_number)
        
        if not controls:
            return KakaoResponse.simple_text(f"🚌 노선 {route_number}번\n오늘({today}) 통제 정보가 없습니다. ✅")
        
        response_text = f"🚨 노선 {route_number}번 통제 정보\n\n"
        
        for i, control in enumerate(controls, 1):
            response_text += f"【{i}】 {control.get('control_type', '통제')}\n"
            title = control.get('notice_title', '제목 없음')
            if len(title) > 40:
                title = title[:40] + "..."
            response_text += f"📄 {title}\n"
            
            stations = control.get('affected_stations', [])
            if stations:
                station_names = [s.get('station_name', '이름없음') for s in stations[:3]]
                response_text += f"🚏 영향 정류소: {', '.join(station_names)}"
                if len(stations) > 3:
                    response_text += f" 외 {len(stations)-3}곳"
                response_text += "\n"
            
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
    
    if 'userRequest' not in body:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    user_id = body['userRequest']['user']['id']
    params = body.get('action', {}).get('params', {})
    location_name = params.get('location', '').strip()
    
    if not location_name:
        return KakaoResponse.simple_text("위치를 입력해주세요.\n예: 강남역, 홍대입구역, 명동")
    
    location_info = get_location_info(location_name)
    
    if not location_info:
        return KakaoResponse.simple_text(f"'{location_name}' 위치를 찾을 수 없습니다.\n다른 키워드로 다시 시도해주세요.")
    
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
    
    if 'userRequest' not in body:
        return KakaoResponse.simple_text("잘못된 요청입니다.")
    
    user_id = body['userRequest']['user']['id']
    location = get_user_location(user_id)
    
    if not location:
        return KakaoResponse.simple_text("먼저 위치를 등록해주세요.\n'위치 등록' 메뉴를 이용하세요.")
    
    if not CRAWLER_AVAILABLE:
        return KakaoResponse.simple_text("현재 위치 기반 서비스를 사용할 수 없습니다.")
    
    try:
        tm_x, tm_y = wgs84_to_tm(location["x"], location["y"])
        today = datetime.now().strftime("%Y-%m-%d")
        
        request_data = PositionRequest(tm_x=tm_x, tm_y=tm_y, radius=500, target_date=today)
        result = await get_position_controls(request_data)
        
        if not result.success:
            return KakaoResponse.simple_text(f"주변 정보 조회 실패: {result.message}")
        
        data = result.data
        nearby_stations = data.get('nearby_stations', [])
        
        response_text = f"📍 {location['name']} 주변 500m\n\n"
        response_text += f"🚏 주변 정류소: {len(nearby_stations)}개\n"
        response_text += f"✅ 현재 주변에 통제 중인 정류소가 없습니다."
        
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

@app.get("/stats", tags=["통계"])
async def get_statistics():
    """시스템 통계 정보"""
    stats = {
        "total_notices": len(cached_notices),
        "last_update": last_update,
        "user_sessions": len(user_sessions),
        "crawler_available": CRAWLER_AVAILABLE,
        "kakao_api_configured": bool(KAKAO_REST_API_KEY)
    }
    
    if cached_notices:
        control_types = {}
        for notice in cached_notices:
            control_type = notice.get('control_type', '기타')
            control_types[control_type] = control_types.get(control_type, 0) + 1
        stats["notices_by_type"] = control_types
    
    return ControlResponse(
        success=True,
        message="통계 정보 조회 완료",
        data=stats,
        timestamp=datetime.now()
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
