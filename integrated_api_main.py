"""
통합 API 서버 - 버스 API + 카카오톡 챗봇 (올바른 콜백 구현) - 한국 시간대 적용
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import json
from datetime import datetime, date
import pytz  # 한국 시간대 처리를 위해 추가
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

# 한국 시간대 설정
KST = pytz.timezone('Asia/Seoul')

def get_korean_time():
    """한국 시간 반환"""
    return datetime.now(KST)

def korean_date_string():
    """한국 시간 기준 날짜 문자열 반환 (YYYY-MM-DD)"""
    return get_korean_time().strftime("%Y-%m-%d")

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 전역 변수
crawler = None
cached_notices = []
last_update = None

# 필요한 디렉토리 생성 (Render 배포용)
def ensure_directories():
    """필요한 디렉토리가 존재하는지 확인하고 생성"""
    attachments_dir = "topis_attachments"
    images_dir = os.path.join(attachments_dir, "route_images")
    
    os.makedirs(attachments_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    
    logger.info(f"디렉토리 생성 확인: {attachments_dir}, {images_dir}")
    return attachments_dir

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
        "updatedAt": get_korean_time()  # 한국 시간으로 변경
    }

def get_user_location(user_id: str) -> Optional[Dict]:
    """사용자 위치 정보 조회"""
    return user_sessions.get(user_id, {}).get("location")

def generate_route_image_realtime(route_number: str, target_notice: Dict) -> Optional[str]:
    """실시간으로 노선 이미지 생성 및 URL 반환 (Gemini 호출 없이)"""
    try:
        if not CRAWLER_AVAILABLE:
            return None
        
        attachments = target_notice.get('attachments', [])
        route_pages = target_notice.get('route_pages', {})
        
        if not attachments:
            print(f"노선 {route_number}: 첨부파일이 없습니다.")
            return None
        
        if route_number not in route_pages:
            print(f"노선 {route_number}: 페이지 정보가 없습니다.")
            return None
        
        notice_seq = target_notice['seq']
        page_num = route_pages[route_number]
        
        print(f"노선 {route_number} 이미지 생성 시작... (페이지: {page_num})")
        
        # 첨부파일 다운로드
        for attachment in attachments:
            file_path = crawler._download_attachment(attachment, save_to_folder=True)
            if file_path:
                # HWP/HWPX 파일이면 PDF로 변환
                converted_path = crawler._convert_hwp_to_pdf(file_path)
                
                if converted_path.lower().endswith('.pdf'):
                    # 해당 페이지를 이미지로 변환
                    image_path = crawler._convert_pdf_page_to_image(
                        converted_path, page_num - 1, route_number, notice_seq
                    )
                    
                    if image_path and os.path.exists(image_path):
                        # 캐시 업데이트
                        if 'route_images' not in target_notice:
                            target_notice['route_images'] = {}
                        target_notice['route_images'][route_number] = image_path
                        
                        # 전체 캐시 저장
                        crawler._save_cache()
                        
                        # URL 생성
                        filename = os.path.basename(image_path)
                        base_url = os.getenv("RENDER_EXTERNAL_URL", "https://restricted-bus-notice.onrender.com")
                        image_url = f"{base_url}/topis_attachments/route_images/{filename}"
                        
                        print(f"노선 {route_number} 이미지 생성 완료: {filename}")
                        return image_url
                
                break  # 첫 번째 첨부파일만 처리
        
        print(f"노선 {route_number} 이미지 생성 실패")
        return None
        
    except Exception as e:
        print(f"노선 {route_number} 이미지 생성 중 오류: {e}")
        return None

async def send_kakao_callback_message(callback_url: str, message_data: Dict):
    """카카오톡 콜백 URL로 메시지 전송"""
    print(f"📡 콜백 전송 시도: {callback_url}")
    print(f"📋 메시지 데이터: {json.dumps(message_data, ensure_ascii=False, indent=2)}")
    
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                callback_url, 
                json=message_data, 
                headers={'Content-Type': 'application/json'}
            ) as response:
                response_text = await response.text()
                print(f"📨 콜백 응답 상태: {response.status}")
                print(f"📨 콜백 응답 내용: {response_text}")
                
                if response.status == 200:
                    print(f"✅ 카카오톡 콜백 전송 성공")
                else:
                    print(f"❌ 카카오톡 콜백 전송 실패: {response.status}")
                    
    except ImportError:
        print("📡 aiohttp 없음, requests로 fallback")
        # aiohttp가 없으면 requests로 fallback
        try:
            import requests
            response = requests.post(
                callback_url, 
                json=message_data, 
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            print(f"📨 콜백 응답 상태 (fallback): {response.status_code}")
            print(f"📨 콜백 응답 내용 (fallback): {response.text}")
            
            if response.status_code == 200:
                print(f"✅ 카카오톡 콜백 전송 성공 (fallback)")
            else:
                print(f"❌ 카카오톡 콜백 전송 실패 (fallback): {response.status_code}")
                
        except Exception as e2:
            print(f"❌ 콜백 전송 완전 실패: {e2}")
            
    except Exception as e:
        print(f"❌ 카카오톡 콜백 전송 오류: {e}")
        # requests로 재시도
        try:
            import requests
            response = requests.post(
                callback_url, 
                json=message_data, 
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            print(f"📨 콜백 응답 상태 (재시도): {response.status_code}")
            if response.status_code == 200:
                print(f"✅ 카카오톡 콜백 전송 성공 (재시도)")
            else:
                print(f"❌ 카카오톡 콜백 전송 실패 (재시도): {response.status_code}")
        except Exception as e2:
            print(f"❌ 콜백 재시도도 실패: {e2}")

async def generate_and_send_kakao_callback(route_number: str, target_date: str, 
                                         target_notice: Dict, callback_url: str, 
                                         notice_title: str, detour_path: str):
    """백그라운드에서 이미지 생성 후 카카오톡 콜백 전송"""
    try:
        print(f"🚀 콜백 함수 시작: 노선 {route_number}, URL: {callback_url}")
        
        # 이미지 생성
        route_image_url = generate_route_image_realtime(route_number, target_notice)
        
        print(f"📷 이미지 생성 결과: {route_image_url}")
        
        if route_image_url:
            # 성공 메시지 + 이미지 구성
            info_text = f"✅ 이미지 생성 완료!\n\n"
            info_text += f"🚌 노선 {route_number}번 우회 경로\n"
            info_text += f"📅 {target_date}\n\n"
            if notice_title:
                title_short = notice_title[:50] + '...' if len(notice_title) > 50 else notice_title
                info_text += f"📄 {title_short}\n"
            if detour_path:
                detour_short = detour_path[:60] + '...' if len(detour_path) > 60 else detour_path
                info_text += f"🔄 {detour_short}\n"
            info_text += "\n📍 자세한 우회 경로는 아래 이미지를 확인하세요."
            
            callback_message = {
                "version": "2.0",
                "useCallback": True,
                "template": {
                    "outputs": [
                        {"simpleText": {"text": info_text}},
                        {
                            "simpleImage": {
                                "imageUrl": route_image_url,
                                "altText": f"{route_number}번 버스 우회 경로"
                            }
                        }
                    ]
                }
            }
            print(f"📤 성공 콜백 메시지 준비 완료")
        else:
            # 실패 메시지
            callback_message = {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {
                            "simpleText": {
                                "text": f"❌ 이미지 생성 실패\n\n🚌 노선 {route_number}번\n📅 {target_date}\n\n⚠️ PDF 파일 처리 중 오류가 발생했습니다.\n잠시 후 다시 시도해주세요."
                            }
                        }
                    ]
                }
            }
            print(f"📤 실패 콜백 메시지 준비 완료")
        
        # 카카오톡 콜백 전송
        print(f"📡 콜백 전송 시작...")
        await send_kakao_callback_message(callback_url, callback_message)
        print(f"✅ 노선 {route_number} 이미지 생성 및 콜백 완료")
        
    except Exception as e:
        print(f"❌ 콜백 함수 전체 오류: {e}")
        # 오류 메시지 콜백
        error_message = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": f"❌ 이미지 생성 오류\n\n🚌 노선 {route_number}번\n시스템 오류로 이미지를 생성할 수 없습니다.\n관리자에게 문의해주세요.\n\n오류: {str(e)}"
                        }
                    }
                ]
            }
        }
        try:
            await send_kakao_callback_message(callback_url, error_message)
        except Exception as e2:
            print(f"❌ 오류 콜백 전송도 실패: {e2}")

async def initialize_crawler():
    """크롤러 초기화"""
    global crawler, cached_notices, last_update
    
    if not CRAWLER_AVAILABLE:
        logger.warning("크롤러 모듈을 사용할 수 없습니다.")
        cached_notices = []
        last_update = get_korean_time()  # 한국 시간으로 변경
        return
    
    try:
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("Gemini API Key가 설정되지 않았습니다.")
        
        crawler = TOPISCrawler(gemini_api_key=gemini_api_key)
        cached_notices, cache_hit = crawler.crawl_notices()
        last_update = get_korean_time()  # 한국 시간으로 변경
        
        logger.info(f"크롤러 초기화 완료. {len(cached_notices)}개 공지사항 로드됨")
        
    except Exception as e:
        logger.error(f"크롤러 초기화 실패: {e}")
        cached_notices = []
        last_update = get_korean_time()  # 한국 시간으로 변경

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 실행
    ensure_directories()
    await initialize_crawler()
    yield

# FastAPI 앱 생성
app = FastAPI(
    title="서울 버스 통제 알림 API + 카카오톡 챗봇",
    description="서울시 버스 운행 변경 및 통제 정보 조회 API + 카카오톡 인터페이스",
    version="2.1.0",
    lifespan=lifespan
)

# 디렉토리 생성 후 정적 파일 서빙 설정
attachments_dir = ensure_directories()
try:
    app.mount("/static", StaticFiles(directory=attachments_dir), name="static")
    logger.info(f"정적 파일 서빙 설정 완료: {attachments_dir}")
except Exception as e:
    logger.error(f"정적 파일 서빙 설정 실패: {e}")

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
        "version": "2.1.0",
        "status": "running",
        "last_update": last_update,
        "cached_notices": len(cached_notices) if cached_notices else 0,
        "features": ["REST API", "카카오톡 챗봇", "위치 기반 서비스", "실시간 이미지 생성"],
        "crawler_available": CRAWLER_AVAILABLE,
        "current_time": get_korean_time().strftime("%Y-%m-%d %H:%M:%S KST")  # 한국 시간 추가
    }

@app.get("/health", tags=["기본"])
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy",
        "timestamp": get_korean_time(),  # 한국 시간으로 변경
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
            timestamp=get_korean_time()  # 한국 시간으로 변경
        )
    
    try:
        service_key = crawler.service_key
        nearby_stations = get_stations_by_position(service_key, request.tm_x, request.tm_y, request.radius)
        
        return ControlResponse(
            success=True,
            message=f"반경 {request.radius}m 내 정류소 {len(nearby_stations)}개 발견",
            data={"nearby_stations": nearby_stations},
            timestamp=get_korean_time()  # 한국 시간으로 변경
        )
        
    except Exception as e:
        return ControlResponse(
            success=False,
            message=f"위치 기반 조회 오류: {str(e)}",
            timestamp=get_korean_time()  # 한국 시간으로 변경
        )

# 카카오톡 웹훅 엔드포인트들
@app.post("/webhook/bus_info", tags=["카카오톡"])
async def bus_info_webhook(req: Request):
    """버스 통제 정보 조회 웹훅"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    today = korean_date_string()  # 한국 시간 기준 날짜
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "현재 버스 정보 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요."}}]}}
    
    try:
        filtered_notices = crawler.filter_by_date(cached_notices, today)
        
        if not filtered_notices:
            return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"오늘({today}) 버스 통제 정보가 없습니다. 🚌✅"}}]}}
        
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
        
        quick_replies = [{"label": reply, "action": "message", "messageText": reply} for reply in ["내 위치 주변 확인", "특정 노선 조회", "도움말"]]
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": summary_text}}],
                "quickReplies": quick_replies
            }
        }
        
    except Exception as e:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"버스 정보 조회 중 오류가 발생했습니다: {str(e)}"}}]}}

@app.post("/webhook/route_image", tags=["카카오톡"])
async def route_image_webhook(req: Request, background_tasks: BackgroundTasks):
    """노선 우회 경로 이미지 전송 (즉시 응답 방식)"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    params = body.get('action', {}).get('params', {})
    route_number = params.get('route_number', '').strip()
    target_date = params.get('date', '').strip()
    
    if not route_number:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "노선 번호를 입력해주세요.\n예: 406, 143, 9401"}}]}}
    
    if not target_date:
        target_date = korean_date_string()
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "현재 서비스를 사용할 수 없습니다."}}]}}
    
    try:
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        
        route_image_url = None
        notice_title = None
        detour_path = None
        target_notice = None
        
        # 1단계: 기존 이미지 확인
        for notice in filtered_notices:
            route_images = notice.get('route_images', {})
            if route_number in route_images:
                image_path = route_images[route_number]
                if image_path and os.path.exists(image_path):
                    filename = os.path.basename(image_path)
                    base_url = os.getenv("RENDER_EXTERNAL_URL", "https://restricted-bus-notice.onrender.com")
                    route_image_url = f"{base_url}/topis_attachments/route_images/{filename}"
                    notice_title = notice.get('title', '제목 없음')
                    detour_routes = notice.get('detour_routes', {})
                    detour_path = detour_routes.get(route_number, '')
                    print(f"노선 {route_number} 기존 이미지 발견: {filename}")
                    break
            
            # 해당 노선이 포함된 공지사항 찾기
            route_pages = notice.get('route_pages', {})
            if route_number in route_pages:
                target_notice = notice
                notice_title = notice.get('title', '제목 없음')
                detour_routes = notice.get('detour_routes', {})
                detour_path = detour_routes.get(route_number, '')
        
        # 2단계: 기존 이미지가 있으면 즉시 응답
        if route_image_url:
            info_text = f"🚌 노선 {route_number}번 우회 경로\n"
            info_text += f"📅 {target_date}\n\n"
            if notice_title:
                title_short = notice_title[:50] + '...' if len(notice_title) > 50 else notice_title
                info_text += f"📄 {title_short}\n"
            if detour_path:
                detour_short = detour_path[:60] + '...' if len(detour_path) > 60 else detour_path
                info_text += f"🔄 {detour_short}\n"
            info_text += "\n📍 자세한 우회 경로는 아래 이미지를 확인하세요."
            
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": info_text}},
                        {
                            "simpleImage": {
                                "imageUrl": route_image_url,
                                "altText": f"{route_number}번 버스 우회 경로"
                            }
                        }
                    ]
                }
            }
        
        # 3단계: 이미지가 없는 경우 - 즉시 생성
        elif target_notice:
            print(f"📷 노선 {route_number} 이미지 즉시 생성 시작...")
            
            # 즉시 이미지 생성 (동기적으로)
            route_image_url = generate_route_image_realtime(route_number, target_notice)
            
            if route_image_url:
                # 성공 - 텍스트 + 이미지 함께 응답
                info_text = f"✅ 노선 {route_number}번 우회 경로\n"
                info_text += f"📅 {target_date}\n\n"
                if notice_title:
                    title_short = notice_title[:50] + '...' if len(notice_title) > 50 else notice_title
                    info_text += f"📄 {title_short}\n"
                if detour_path:
                    detour_short = detour_path[:60] + '...' if len(detour_path) > 60 else detour_path
                    info_text += f"🔄 {detour_short}\n"
                info_text += "\n📍 자세한 우회 경로는 아래 이미지를 확인하세요."
                
                return {
                    "version": "2.0",
                    "template": {
                        "outputs": [
                            {"simpleText": {"text": info_text}},
                            {
                                "simpleImage": {
                                    "imageUrl": route_image_url,
                                    "altText": f"{route_number}번 버스 우회 경로"
                                }
                            }
                        ]
                    }
                }
            else:
                # 실패 - 오류 메시지
                return {
                    "version": "2.0",
                    "template": {
                        "outputs": [
                            {
                                "simpleText": {
                                    "text": f"❌ 이미지 생성 실패\n\n🚌 노선 {route_number}번\n📅 {target_date}\n\n⚠️ PDF 파일 처리 중 오류가 발생했습니다.\n잠시 후 다시 시도해주세요."
                                }
                            }
                        ]
                    }
                }
        
        else:
            # 해당 노선 정보가 없는 경우
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {
                            "simpleText": {
                                "text": f"🚌 노선 {route_number}번\n📅 {target_date}\n\n❌ 해당 날짜에 통제 정보가 없습니다.\n다른 날짜나 노선번호를 확인해주세요."
                            }
                        }
                    ]
                }
            }
            
    except Exception as e:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"이미지 조회 중 오류: {str(e)}"}}]}}

@app.post("/webhook/route_check", tags=["카카오톡"])
async def route_check_webhook(req: Request, background_tasks: BackgroundTasks):
    """특정 노선 통제 정보 조회 (올바른 카카오톡 콜백)"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    params = body.get('action', {}).get('params', {})
    route_number = params.get('route_number', '').strip()
    target_date = params.get('date', '').strip()
    
    if not route_number:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "노선 번호를 입력해주세요.\n예: 406, 143, 7016"}}]}}
    
    if not target_date:
        target_date = korean_date_string()  # 한국 시간 기준 날짜
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "현재 버스 정보 서비스를 사용할 수 없습니다."}}]}}
    
    try:
        controls = crawler.get_control_info_by_route(cached_notices, target_date, route_number)
        
        if not controls:
            return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"🚌 노선 {route_number}번\n{target_date} 통제 정보가 없습니다. ✅"}}]}}
        
        response_text = f"🚨 노선 {route_number}번 통제 정보\n📅 {target_date}\n\n"
        
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
        
        # 이미지 확인 및 생성
        route_image_url = None
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        target_notice = None
        notice_title = None
        detour_path = None
        
        # 1단계: 기존 이미지 확인
        for notice in filtered_notices:
            route_images = notice.get('route_images', {})
            if route_number in route_images:
                image_path = route_images[route_number]
                if image_path and os.path.exists(image_path):
                    filename = os.path.basename(image_path)
                    base_url = os.getenv("RENDER_EXTERNAL_URL", "https://restricted-bus-notice.onrender.com")
                    route_image_url = f"{base_url}/static/route_images/{filename}"
                    break
            
            # 해당 노선이 포함된 공지사항 찾기
            route_pages = notice.get('route_pages', {})
            if route_number in route_pages:
                target_notice = notice
                notice_title = notice.get('title', '제목 없음')
                detour_routes = notice.get('detour_routes', {})
                detour_path = detour_routes.get(route_number, '')
        
        # 2단계: 기존 이미지가 있으면 텍스트 + 이미지 함께 전송
        if route_image_url:
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": response_text}},
                        {
                            "simpleImage": {
                                "imageUrl": route_image_url,
                                "altText": f"{route_number}번 버스 우회 경로"
                            }
                        }
                    ]
                }
            }
        
        # 3단계: 이미지가 없으면 처리
        elif target_notice:
            # 콜백 URL 확인
            callback_url = body.get('userRequest', {}).get('callbackUrl')
            
            if callback_url:
                # 백그라운드에서 이미지 생성 후 콜백 전송
                background_tasks.add_task(
                    generate_and_send_kakao_callback,
                    route_number, target_date, target_notice, callback_url, notice_title, detour_path
                )
                
                # 카카오톡 콜백 활성화 응답
                return {
                    "version": "2.0",
                    "useCallback": True,
                    "data": {
                        "text": f"🖼️ 우회 경로 이미지를 생성 중입니다...\n잠시 후 이미지가 추가로 전송됩니다."
                    }
                }
            else:
                # 콜백이 없으면 안내 메시지 추가
                response_text += "\n💡 상세한 이미지를 보려면 관리자에게 콜백 설정을 요청하세요."
            
            return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": response_text}}]}}
        
        else:
            # 텍스트만 전송
            return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": response_text}}]}}
        
    except Exception as e:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"노선 정보 조회 중 오류: {str(e)}"}}]}}

@app.post("/webhook/location_save", tags=["카카오톡"])
async def location_save_webhook(req: Request, background_tasks: BackgroundTasks):
    """사용자 위치 저장"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    user_id = body['userRequest']['user']['id']
    params = body.get('action', {}).get('params', {})
    location_name = params.get('location', '').strip()
    
    if not location_name:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "위치를 입력해주세요.\n예: 강남역, 홍대입구역, 명동"}}]}}
    
    location_info = get_location_info(location_name)
    
    if not location_info:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"'{location_name}' 위치를 찾을 수 없습니다.\n다른 키워드로 다시 시도해주세요."}}]}}
    
    background_tasks.add_task(save_user_location, user_id, location_info)
    
    response_text = f"📍 위치 저장 완료!\n\n"
    response_text += f"🏢 {location_info['name']}\n"
    response_text += f"📮 {location_info['address']}\n\n"
    response_text += "이제 '내 주변 확인'으로 주변 버스 통제 정보를 확인할 수 있습니다."
    
    quick_replies = [{"label": reply, "action": "message", "messageText": reply} for reply in ["내 주변 확인", "오늘 버스 정보", "노선 조회"]]
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": response_text}}],
            "quickReplies": quick_replies
        }
    }

@app.post("/webhook/nearby_check", tags=["카카오톡"])
async def nearby_check_webhook(req: Request):
    """사용자 주변 통제 정보 조회"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    user_id = body['userRequest']['user']['id']
    location = get_user_location(user_id)
    
    if not location:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "먼저 위치를 등록해주세요.\n'위치 등록' 메뉴를 이용하세요."}}]}}
    
    if not CRAWLER_AVAILABLE:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "현재 위치 기반 서비스를 사용할 수 없습니다."}}]}}
    
    try:
        tm_x, tm_y = wgs84_to_tm(location["x"], location["y"])
        today = korean_date_string()  # 한국 시간 기준 날짜
        
        request_data = PositionRequest(tm_x=tm_x, tm_y=tm_y, radius=500, target_date=today)
        result = await get_position_controls(request_data)
        
        if not result.success:
            return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"주변 정보 조회 실패: {result.message}"}}]}}
        
        data = result.data
        nearby_stations = data.get('nearby_stations', [])
        
        response_text = f"📍 {location['name']} 주변 500m\n\n"
        response_text += f"🚏 주변 정류소: {len(nearby_stations)}개\n"
        response_text += f"✅ 현재 주변에 통제 중인 정류소가 없습니다."
        
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": response_text}}]}}
        
    except Exception as e:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"주변 정보 조회 중 오류: {str(e)}"}}]}}

@app.post("/webhook/help", tags=["카카오톡"])
async def help_webhook(req: Request):
    """도움말"""
    help_text = """🚌 서울 버스 통제 알림봇 사용법

📋 주요 기능:
• 오늘 버스 정보 - 전체 통제 현황
• 노선 조회 - 특정 노선 통제 정보
• 노선 이미지 - 우회 경로 이미지 확인
• 위치 등록 - 내 위치 저장
• 내 주변 확인 - 주변 통제 정류소

💬 사용 예시:
• "406번 확인해줘"
• "406번 이미지"
• "강남역 등록"
• "내 주변 알려줘"

🔄 실시간 업데이트:
서울시 TOPIS 시스템과 연동하여
최신 버스 통제 정보를 제공합니다.

🖼️ 새로운 기능:
이제 노선별 우회 경로 이미지도
실시간으로 생성하여 제공합니다!
처음 요청 시 이미지 생성에 
약간의 시간이 걸릴 수 있습니다.

⏰ 모든 시간은 한국 표준시(KST) 기준입니다.
"""
    
    quick_replies = [{"label": reply, "action": "message", "messageText": reply} for reply in ["오늘 버스 정보", "위치 등록", "노선 조회"]]
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": help_text}}],
            "quickReplies": quick_replies
        }
    }

@app.get("/stats", tags=["통계"])
async def get_statistics():
    """시스템 통계 정보"""
    stats = {
        "total_notices": len(cached_notices),
        "last_update": last_update,
        "user_sessions": len(user_sessions),
        "crawler_available": CRAWLER_AVAILABLE,
        "kakao_api_configured": bool(KAKAO_REST_API_KEY),
        "current_time": get_korean_time().strftime("%Y-%m-%d %H:%M:%S KST")  # 한국 시간 추가
    }
    
    # 생성된 이미지 개수 통계 추가
    total_images = 0
    if cached_notices:
        control_types = {}
        for notice in cached_notices:
            control_type = notice.get('control_type', '기타')
            control_types[control_type] = control_types.get(control_type, 0) + 1
            
            # 이미지 개수 계산
            route_images = notice.get('route_images', {})
            total_images += len(route_images)
        
        stats["notices_by_type"] = control_types
        stats["total_generated_images"] = total_images
    
    return ControlResponse(
        success=True,
        message="통계 정보 조회 완료",
        data=stats,
        timestamp=get_korean_time()  # 한국 시간으로 변경
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
