"""
통합 API 서버 - 버스 API + 카카오톡 챗봇 (Cloudinary + 한국시간 적용)
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
import asyncio
import logging
from contextlib import asynccontextmanager
import requests
import tempfile
import pytz

# Cloudinary import
try:
    import cloudinary
    import cloudinary.uploader
    import cloudinary.api
    from cloudinary.utils import cloudinary_url
    CLOUDINARY_AVAILABLE = True
except ImportError:
    print("⚠️ Cloudinary 모듈을 찾을 수 없습니다. pip install cloudinary를 실행하세요.")
    CLOUDINARY_AVAILABLE = False

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

# 한국 시간대 설정
KST = pytz.timezone('Asia/Seoul')

def get_kst_now():
    """한국 시간으로 현재 시간 반환"""
    return datetime.now(KST)

def get_kst_today():
    """한국 시간으로 오늘 날짜 반환 (YYYY-MM-DD 형식)"""
    return get_kst_now().strftime("%Y-%m-%d")

# Cloudinary 설정
if CLOUDINARY_AVAILABLE:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True
    )
    
    # Cloudinary 설정 확인
    cloudinary_configured = bool(
        os.getenv("CLOUDINARY_CLOUD_NAME") and 
        os.getenv("CLOUDINARY_API_KEY") and 
        os.getenv("CLOUDINARY_API_SECRET")
    )
    
    if cloudinary_configured:
        logger.info("✅ Cloudinary 설정 완료")
    else:
        logger.warning("⚠️ Cloudinary 환경변수가 설정되지 않았습니다.")
        cloudinary_configured = False
else:
    cloudinary_configured = False

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
        "updatedAt": get_kst_now()
    }

def get_user_location(user_id: str) -> Optional[Dict]:
    """사용자 위치 정보 조회"""
    return user_sessions.get(user_id, {}).get("location")

# Cloudinary 이미지 처리 함수들
def upload_image_to_cloudinary(image_path: str, route_number: str, notice_seq: str) -> Optional[str]:
    """이미지를 Cloudinary에 업로드하고 URL 반환"""
    if not cloudinary_configured:
        print("❌ Cloudinary가 설정되지 않았습니다.")
        return None
    
    try:
        # 공개 ID 생성 (검색 가능하도록)
        public_id = f"bus_routes/route_{route_number}_seq_{notice_seq}"
        
        # Cloudinary에 업로드
        result = cloudinary.uploader.upload(
            image_path,
            public_id=public_id,
            folder="seoul_bus_routes",
            resource_type="image",
            format="png",
            overwrite=True  # 같은 파일이 있으면 덮어쓰기
        )
        
        # 최적화된 URL 생성
        optimized_url, _ = cloudinary_url(
            result['public_id'],
            format="auto",      # 자동 포맷 최적화
            quality="auto",     # 자동 품질 최적화
            crop="scale",
            width=800,          # 최대 너비 800px
            height=600,         # 최대 높이 600px
            secure=True
        )
        
        print(f"✅ Cloudinary 업로드 성공: {optimized_url}")
        return optimized_url
        
    except Exception as e:
        print(f"❌ Cloudinary 업로드 실패: {e}")
        return None

def check_existing_cloudinary_image(route_number: str, notice_seq: str) -> Optional[str]:
    """Cloudinary에서 기존 이미지 확인"""
    if not cloudinary_configured:
        return None
    
    try:
        public_id = f"seoul_bus_routes/bus_routes/route_{route_number}_seq_{notice_seq}"
        
        # 이미지 존재 여부 확인
        result = cloudinary.api.resource(public_id)
        
        if result and result.get('secure_url'):
            print(f"✅ Cloudinary에서 기존 이미지 발견: {route_number}")
            return result['secure_url']
            
    except cloudinary.exceptions.NotFound:
        print(f"📷 Cloudinary에 노선 {route_number} 이미지 없음")
    except Exception as e:
        print(f"❌ Cloudinary 이미지 확인 오류: {e}")
    
    return None

def generate_route_image_realtime_cloudinary(route_number: str, target_notice: Dict) -> Optional[str]:
    """실시간으로 노선 이미지 생성 후 Cloudinary 업로드"""
    try:
        if not CRAWLER_AVAILABLE or not cloudinary_configured:
            return None
        
        attachments = target_notice.get('attachments', [])
        if not attachments:
            print(f"노선 {route_number}: 첨부파일이 없습니다.")
            return None
        
        notice_seq = target_notice['seq']
        print(f"노선 {route_number} 이미지 생성 시작... (공지: {notice_seq})")
        
        # 임시 디렉토리에 이미지 생성
        with tempfile.TemporaryDirectory() as temp_dir:
            # Gemini로 이미지 생성 (임시 폴더 사용)
            original_images_folder = crawler.images_folder
            crawler.images_folder = temp_dir  # 임시로 변경
            
            extracted = crawler._extract_with_gemini(
                target_notice.get('content', ''),
                attachments,
                notice_seq,
                save_attachments=False  # 첨부파일은 임시로만 처리
            )
            
            # 원래 폴더 복원
            crawler.images_folder = original_images_folder
            
            # 생성된 이미지 확인 및 Cloudinary 업로드
            route_images = extracted.get('route_images', {})
            if route_number in route_images:
                temp_image_path = route_images[route_number]
                if temp_image_path and os.path.exists(temp_image_path):
                    # Cloudinary에 업로드
                    cloudinary_url = upload_image_to_cloudinary(
                        temp_image_path, route_number, notice_seq
                    )
                    
                    if cloudinary_url:
                        # 캐시 업데이트 (URL만 저장)
                        if 'route_images' not in target_notice:
                            target_notice['route_images'] = {}
                        target_notice['route_images'][route_number] = cloudinary_url
                        
                        # 전체 캐시 저장
                        crawler._save_cache()
                        
                        print(f"노선 {route_number} 이미지 업로드 완료: {cloudinary_url}")
                        return cloudinary_url
        
        print(f"노선 {route_number} 이미지 생성 실패")
        return None
        
    except Exception as e:
        print(f"노선 {route_number} 이미지 생성 중 오류: {e}")
        return None

async def send_kakao_callback_message(callback_url: str, message_data: Dict):
    """카카오톡 콜백 URL로 메시지 전송 (올바른 형식)"""
    try:
        # 카카오톡 콜백 API는 정확한 형식이 중요함
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'ChatbotServer/1.0'
        }
        
        # 콜백 요청 시도
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                callback_url, 
                json=message_data, 
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response_text = await response.text()
                
                if response.status == 200:
                    print(f"✅ 카카오톡 콜백 전송 성공")
                    try:
                        result = json.loads(response_text)
                        print(f"콜백 응답: {result}")
                        return True
                    except:
                        print(f"콜백 응답 (텍스트): {response_text}")
                        return True
                else:
                    print(f"❌ 카카오톡 콜백 전송 실패: {response.status}")
                    print(f"응답 내용: {response_text}")
                    return False
                    
    except Exception as e:
        print(f"❌ 카카오톡 콜백 전송 오류: {e}")
        # aiohttp가 없으면 requests로 fallback
        try:
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'ChatbotServer/1.0'
            }
            response = requests.post(
                callback_url, 
                json=message_data, 
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                print(f"✅ 카카오톡 콜백 전송 성공 (fallback)")
                try:
                    result = response.json()
                    print(f"콜백 응답: {result}")
                except:
                    print(f"콜백 응답 (텍스트): {response.text}")
                return True
            else:
                print(f"❌ 카카오톡 콜백 전송 실패 (fallback): {response.status_code}")
                print(f"응답 내용: {response.text}")
                return False
                
        except Exception as e2:
            print(f"❌ 카카오톡 콜백 전송 완전 실패: {e2}")
            return False

async def generate_and_send_kakao_callback_cloudinary(route_number: str, target_date: str, 
                                                    target_notice: Dict, callback_url: str, 
                                                    notice_title: str, detour_path: str):
    """백그라운드에서 Cloudinary 이미지 생성 후 카카오톡 콜백 전송"""
    try:
        print(f"🔄 백그라운드 Cloudinary 이미지 생성 시작: 노선 {route_number}")
        
        # Cloudinary에 이미지 생성 및 업로드
        route_image_url = generate_route_image_realtime_cloudinary(route_number, target_notice)
        
        if route_image_url:
            # 성공 메시지 + Cloudinary 이미지 구성
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
        
        # 카카오톡 콜백 전송
        success = await send_kakao_callback_message(callback_url, callback_message)
        
        if success:
            print(f"✅ 노선 {route_number} Cloudinary 이미지 생성 및 콜백 완료")
        else:
            print(f"❌ 노선 {route_number} 콜백 전송 실패")
        
    except Exception as e:
        # 오류 메시지 콜백
        error_message = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": f"❌ 이미지 생성 오류\n\n🚌 노선 {route_number}번\n시스템 오류로 이미지를 생성할 수 없습니다.\n관리자에게 문의해주세요."
                        }
                    }
                ]
            }
        }
        await send_kakao_callback_message(callback_url, error_message)
        print(f"❌ 노선 {route_number} Cloudinary 이미지 생성 오류: {e}")

async def initialize_crawler():
    """크롤러 초기화 + 자동 Cloudinary 업로드"""
    global crawler, cached_notices, last_update
    
    if not CRAWLER_AVAILABLE:
        logger.warning("크롤러 모듈을 사용할 수 없습니다.")
        cached_notices = []
        last_update = get_kst_now()
        return
    
    try:
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("Gemini API Key가 설정되지 않았습니다.")
        
        crawler = TOPISCrawler(gemini_api_key=gemini_api_key)
        cached_notices, cache_hit = crawler.crawl_notices()
        last_update = get_kst_now()
        
        logger.info(f"크롤러 초기화 완료. {len(cached_notices)}개 공지사항 로드됨")
        
        # 🔥 새로 추가: 자동 Cloudinary 업로드
        if cloudinary_configured:
            logger.info("🚀 자동 Cloudinary 업로드 시작...")
            await upload_all_cached_images_to_cloudinary()
        else:
            logger.warning("⚠️ Cloudinary가 설정되지 않아 자동 업로드를 건너뜁니다.")
        
    except Exception as e:
        logger.error(f"크롤러 초기화 실패: {e}")
        cached_notices = []
        last_update = get_kst_now()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 실행
    ensure_directories()
    await initialize_crawler()
    yield

# FastAPI 앱 생성
app = FastAPI(
    title="서울 버스 통제 알림 API + 카카오톡 챗봇",
    description="서울시 버스 운행 변경 및 통제 정보 조회 API + 카카오톡 인터페이스 (Cloudinary 연동)",
    version="2.2.0",
    lifespan=lifespan
)

# 디렉토리 생성 후 정적 파일 서빙 설정 (여전히 필요 - 다른 파일들용)
attachments_dir = ensure_directories()
try:
    app.mount("/static", StaticFiles(directory=attachments_dir), name="static")
    logger.info(f"✅ 정적 파일 서빙 설정 완료: {attachments_dir}")
except Exception as e:
    logger.error(f"❌ 정적 파일 서빙 설정 실패: {e}")

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
        "version": "2.2.0",
        "status": "running",
        "last_update": last_update,
        "cached_notices": len(cached_notices) if cached_notices else 0,
        "features": ["REST API", "카카오톡 챗봇", "위치 기반 서비스", "실시간 이미지 생성", "Cloudinary 연동"],
        "crawler_available": CRAWLER_AVAILABLE,
        "cloudinary_available": cloudinary_configured,
        "current_kst_time": get_kst_now().isoformat(),
        "current_kst_date": get_kst_today()
    }

@app.get("/health", tags=["기본"])
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy",
        "timestamp": get_kst_now(),
        "kakao_api": "configured" if KAKAO_REST_API_KEY else "missing",
        "cached_notices": len(cached_notices) if cached_notices else 0,
        "crawler_status": "available" if CRAWLER_AVAILABLE else "unavailable",
        "cloudinary_status": "configured" if cloudinary_configured else "missing",
        "current_kst_time": get_kst_now().isoformat(),
        "current_kst_date": get_kst_today()
    }

@app.get("/notices", tags=["공지사항"])
async def get_notices(date: Optional[str] = Query(None, description="조회할 날짜 (YYYY-MM-DD, 기본값: 오늘)")):
    """공지사항 목록 조회"""
    if not CRAWLER_AVAILABLE:
        raise HTTPException(status_code=503, detail="크롤러 모듈을 사용할 수 없습니다.")
    
    if not cached_notices:
        raise HTTPException(status_code=503, detail="데이터를 로드 중입니다.")
    
    # 날짜가 없으면 한국 시간 기준 오늘 날짜 사용
    if not date:
        date = get_kst_today()
    else:
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
    
    filtered_notices = crawler.filter_by_date(cached_notices, date)
    return filtered_notices

@app.get("/routes/{route_number}/controls", tags=["노선"])
async def get_route_controls(route_number: str, date: str = Query(None, description="조회할 날짜 (기본값: 오늘)")):
    """특정 노선의 통제 정보 조회"""
    if not CRAWLER_AVAILABLE:
        raise HTTPException(status_code=503, detail="크롤러 모듈을 사용할 수 없습니다.")
    
    # 날짜가 없으면 한국 시간 기준 오늘 날짜 사용
    if not date:
        date = get_kst_today()
    else:
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
            timestamp=get_kst_now()
        )
    
    try:
        service_key = crawler.service_key
        nearby_stations = get_stations_by_position(service_key, request.tm_x, request.tm_y, request.radius)
        
        return ControlResponse(
            success=True,
            message=f"반경 {request.radius}m 내 정류소 {len(nearby_stations)}개 발견",
            data={"nearby_stations": nearby_stations},
            timestamp=get_kst_now()
        )
        
    except Exception as e:
        return ControlResponse(
            success=False,
            message=f"위치 기반 조회 오류: {str(e)}",
            timestamp=get_kst_now()
        )

# 카카오톡 웹훅 엔드포인트들
@app.post("/webhook/bus_info", tags=["카카오톡"])
async def bus_info_webhook(req: Request):
    """버스 통제 정보 조회 웹훅"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    today = get_kst_today()  # 한국 시간 기준 오늘 날짜
    
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
    """노선 우회 경로 이미지 전송 (Cloudinary 버전)"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    params = body.get('action', {}).get('params', {})
    route_number = params.get('route_number', '').strip()
    target_date = params.get('date', '').strip()
    
    if not route_number:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "노선 번호를 입력해주세요.\n예: 406, 143, 9401"}}]}}
    
    # 날짜가 없으면 한국 시간 기준 오늘 날짜 사용
    if not target_date:
        target_date = get_kst_today()
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "현재 서비스를 사용할 수 없습니다."}}]}}
    
    try:
        # 해당 날짜의 노선 통제 정보 찾기
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        
        route_image_url = None
        notice_title = None
        detour_path = None
        target_notice = None
        
        # 1단계: 캐시된 Cloudinary URL 확인
        for notice in filtered_notices:
            route_images = notice.get('route_images', {})
            if route_number in route_images:
                cached_url = route_images[route_number]
                # URL이 Cloudinary URL인지 확인
                if cached_url and ('cloudinary.com' in cached_url or cached_url.startswith('http')):
                    route_image_url = cached_url
                    notice_title = notice.get('title', '제목 없음')
                    detour_routes = notice.get('detour_routes', {})
                    detour_path = detour_routes.get(route_number, '')
                    print(f"✅ 노선 {route_number} 캐시된 Cloudinary URL 발견")
                    break
            
            # 해당 노선이 포함된 공지사항 찾기 (이미지 생성용)
            route_pages = notice.get('route_pages', {})
            if route_number in route_pages:
                target_notice = notice
                notice_title = notice.get('title', '제목 없음')
                detour_routes = notice.get('detour_routes', {})
                detour_path = detour_routes.get(route_number, '')
                
                # Cloudinary에서 직접 확인
                if not route_image_url:
                    route_image_url = check_existing_cloudinary_image(route_number, notice['seq'])
                    if route_image_url:
                        # 캐시에도 저장
                        if 'route_images' not in notice:
                            notice['route_images'] = {}
                        notice['route_images'][route_number] = route_image_url
                        crawler._save_cache()
        
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
        
        # 3단계: 이미지가 없는 경우 - Cloudinary 업로드로 콜백 처리
        elif target_notice:
            callback_url = body.get('userRequest', {}).get('callbackUrl')
            
            if callback_url:
                print(f"🔄 콜백 URL 발견: {callback_url}")
                
                # 백그라운드에서 Cloudinary 업로드 시작
                background_tasks.add_task(
                    generate_and_send_kakao_callback_cloudinary,
                    route_number, target_date, target_notice, callback_url, notice_title, detour_path
                )
                
                # 카카오톡 콜백 활성화 응답 (중요: useCallback: true)
                storage_type = "Cloudinary" if cloudinary_configured else "임시 저장소"
                return {
                    "version": "2.0",
                    "useCallback": True,
                    "data": {
                        "text": f"🔄 노선 {route_number}번 이미지 생성 중...\n\n⏳ PDF에서 우회 경로 이미지를 생성하여 {storage_type}에 업로드하고 있습니다.\n잠시만 기다려주세요... (약 15-45초 소요)"
                    }
                }
            else:
                print(f"⚠️ 콜백 URL이 없음 - 일반 응답으로 처리")
                # 콜백이 없으면 간단한 정보만 제공
                info_text = f"🚌 노선 {route_number}번 통제 정보\n"
                info_text += f"📅 {target_date}\n\n"
                if notice_title:
                    title_short = notice_title[:50] + '...' if len(notice_title) > 50 else notice_title
                    info_text += f"📄 {title_short}\n"
                if detour_path:
                    detour_short = detour_path[:80] + '...' if len(detour_path) > 80 else detour_path
                    info_text += f"🔄 우회 경로: {detour_short}\n"
                
                info_text += "\n💡 상세한 이미지를 보려면 카카오톡 관리자에게 콜백 설정을 요청하세요."
                
                return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": info_text}}]}}
        
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
        print(f"❌ 노선 이미지 조회 오류: {e}")
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"이미지 조회 중 오류: {str(e)}"}}]}}

@app.post("/webhook/route_check", tags=["카카오톡"])
async def route_check_webhook(req: Request, background_tasks: BackgroundTasks):
    """특정 노선 통제 정보 조회 (Cloudinary 버전)"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    params = body.get('action', {}).get('params', {})
    route_number = params.get('route_number', '').strip()
    target_date = params.get('date', '').strip()
    
    if not route_number:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "노선 번호를 입력해주세요.\n예: 406, 143, 7016"}}]}}
    
    # 날짜가 없으면 한국 시간 기준 오늘 날짜 사용
    if not target_date:
        target_date = get_kst_today()
    
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
        
        # 이미지 확인 및 생성 (Cloudinary 버전)
        route_image_url = None
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        target_notice = None
        notice_title = None
        detour_path = None
        
        # 1단계: 캐시된 Cloudinary URL 확인
        for notice in filtered_notices:
            route_images = notice.get('route_images', {})
            if route_number in route_images:
                cached_url = route_images[route_number]
                if cached_url and ('cloudinary.com' in cached_url or cached_url.startswith('http')):
                    route_image_url = cached_url
                    break
            
            # 해당 노선이 포함된 공지사항 찾기
            route_pages = notice.get('route_pages', {})
            if route_number in route_pages:
                target_notice = notice
                notice_title = notice.get('title', '제목 없음')
                detour_routes = notice.get('detour_routes', {})
                detour_path = detour_routes.get(route_number, '')
                
                # Cloudinary에서 직접 확인
                if not route_image_url:
                    route_image_url = check_existing_cloudinary_image(route_number, notice['seq'])
                    if route_image_url:
                        # 캐시에도 저장
                        if 'route_images' not in notice:
                            notice['route_images'] = {}
                        notice['route_images'][route_number] = route_image_url
                        crawler._save_cache()
        
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
        
        # 3단계: 이미지가 없으면 처리 (Cloudinary 버전)
        elif target_notice:
            # 콜백 URL 확인
            callback_url = body.get('userRequest', {}).get('callbackUrl')
            
            if callback_url:
                print(f"🔄 노선 체크 - 콜백 URL 발견: {callback_url}")
                
                # 백그라운드에서 Cloudinary 업로드 후 콜백 전송
                background_tasks.add_task(
                    generate_and_send_kakao_callback_cloudinary,
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
        print(f"❌ 노선 정보 조회 오류: {e}")
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
        today = get_kst_today()  # 한국 시간 기준 오늘 날짜
        
        request_data = PositionRequest(tm_x=tm_x, tm_y=tm_y, radius=500, target_date=today)
        result = await get_position_controls(request_data)
        
        if not result.success:
            return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"주변 정보 조회 실패: {result.message}"}}]}}
        
        data = result.data
        nearby_stations = data.get('nearby_stations', [])
        
        response_text = f"📍 {location['name']} 주변 500m\n"
        response_text += f"📅 오늘 ({today})\n\n"
        response_text += f"🚏 주변 정류소: {len(nearby_stations)}개\n"
        response_text += f"✅ 현재 주변에 통제 중인 정류소가 없습니다."
        
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": response_text}}]}}
        
    except Exception as e:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"주변 정보 조회 중 오류: {str(e)}"}}]}}

@app.post("/webhook/help", tags=["카카오톡"])
async def help_webhook(req: Request):
    """도움말"""
    current_time = get_kst_now().strftime("%H:%M")
    current_date = get_kst_today()
    
    help_text = f"""🚌 서울 버스 통제 알림봇 사용법

📅 현재 시간: {current_date} {current_time} (KST)

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
클라우드에 저장하여 안정적으로 제공합니다!
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

# 테스트 엔드포인트들
@app.get("/test/cloudinary", tags=["테스트"])
async def test_cloudinary():
    """Cloudinary 연결 테스트"""
    if not cloudinary_configured:
        return {
            "status": "not_configured",
            "message": "Cloudinary 환경변수가 설정되지 않았습니다.",
            "required_env_vars": ["CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"]
        }
    
    try:
        # Cloudinary API 테스트
        result = cloudinary.api.ping()
        
        return {
            "status": "success",
            "message": "Cloudinary 연결 성공",
            "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME"),
            "api_response": result,
            "timestamp": get_kst_now()
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Cloudinary 연결 실패: {str(e)}",
            "timestamp": get_kst_now()
        }

@app.get("/test/time", tags=["테스트"])
async def test_time():
    """시간 설정 테스트"""
    utc_now = datetime.utcnow()
    kst_now = get_kst_now()
    
    return {
        "utc_time": utc_now.isoformat(),
        "kst_time": kst_now.isoformat(),
        "kst_date": get_kst_today(),
        "timezone": "Asia/Seoul",
        "offset_hours": 9
    }

@app.get("/stats", tags=["통계"])
async def get_statistics():
    """시스템 통계 정보"""
    stats = {
        "total_notices": len(cached_notices),
        "last_update": last_update,
        "user_sessions": len(user_sessions),
        "crawler_available": CRAWLER_AVAILABLE,
        "cloudinary_available": cloudinary_configured,
        "kakao_api_configured": bool(KAKAO_REST_API_KEY),
        "current_kst_time": get_kst_now().isoformat(),
        "current_kst_date": get_kst_today()
    }
    
    # 생성된 이미지 개수 통계 추가
    total_cloudinary_images = 0
    if cached_notices:
        control_types = {}
        for notice in cached_notices:
            control_type = notice.get('control_type', '기타')
            control_types[control_type] = control_types.get(control_type, 0) + 1
            
            # Cloudinary 이미지 개수 계산
            route_images = notice.get('route_images', {})
            for image_url in route_images.values():
                if image_url and 'cloudinary.com' in str(image_url):
                    total_cloudinary_images += 1
        
        stats["notices_by_type"] = control_types
        stats["total_cloudinary_images"] = total_cloudinary_images
    
    return ControlResponse(
        success=True,
        message="통계 정보 조회 완료",
        data=stats,
        timestamp=get_kst_now()
    )

async def upload_all_cached_images_to_cloudinary():
    """캐시된 모든 공지사항의 노선 이미지를 Cloudinary에 업로드"""
    if not CRAWLER_AVAILABLE or not cloudinary_configured:
        logger.warning("크롤러 또는 Cloudinary가 설정되지 않아 자동 업로드를 건너뜁니다.")
        return
    
    if not cached_notices:
        logger.info("업로드할 공지사항이 없습니다.")
        return
    
    logger.info("🔄 모든 노선 이미지를 Cloudinary에 자동 업로드 시작...")
    
    upload_count = 0
    skip_count = 0
    error_count = 0
    
    for notice in cached_notices:
        notice_seq = notice.get('seq')
        notice_title = notice.get('title', '제목없음')
        
        # 첨부파일이 있고 route_pages 정보가 있는 공지사항만 처리
        attachments = notice.get('attachments', [])
        route_pages = notice.get('route_pages', {})
        
        if not attachments or not route_pages:
            continue
        
        logger.info(f"📄 공지사항 {notice_seq}: {notice_title[:50]}...")
        
        # 기존 route_images 확인
        route_images = notice.get('route_images', {})
        
        for route_number in route_pages.keys():
            try:
                # 1. 이미 Cloudinary URL이 있는지 확인
                existing_url = route_images.get(route_number)
                if existing_url and 'cloudinary.com' in str(existing_url):
                    logger.info(f"   노선 {route_number}: Cloudinary URL 이미 존재, 건너뜀")
                    skip_count += 1
                    continue
                
                # 2. Cloudinary에서 기존 이미지 확인
                cloudinary_url = check_existing_cloudinary_image(route_number, notice_seq)
                if cloudinary_url:
                    # 캐시 업데이트
                    if 'route_images' not in notice:
                        notice['route_images'] = {}
                    notice['route_images'][route_number] = cloudinary_url
                    logger.info(f"   노선 {route_number}: Cloudinary에서 기존 이미지 발견")
                    skip_count += 1
                    continue
                
                # 3. 새로 생성 및 업로드
                logger.info(f"   노선 {route_number}: 새 이미지 생성 및 업로드 중...")
                cloudinary_url = generate_route_image_realtime_cloudinary(route_number, notice)
                
                if cloudinary_url:
                    upload_count += 1
                    logger.info(f"   ✅ 노선 {route_number}: 업로드 완료")
                else:
                    error_count += 1
                    logger.warning(f"   ❌ 노선 {route_number}: 업로드 실패")
                
                # API 제한을 위한 짧은 대기
                await asyncio.sleep(0.5)
                
            except Exception as e:
                error_count += 1
                logger.error(f"   ❌ 노선 {route_number} 처리 중 오류: {e}")
        
        # 공지사항별 처리 간격
        await asyncio.sleep(1)
    
    # 캐시 저장
    if upload_count > 0:
        crawler._save_cache()
    
    logger.info(f"🎉 Cloudinary 자동 업로드 완료!")
    logger.info(f"   📊 업로드: {upload_count}개, 건너뜀: {skip_count}개, 오류: {error_count}개")

# 수동 업로드 엔드포인트도 추가
@app.post("/admin/upload-all-images", tags=["관리"])
async def manual_upload_all_images():
    """모든 노선 이미지를 수동으로 Cloudinary에 업로드"""
    try:
        if not cloudinary_configured:
            return ControlResponse(
                success=False,
                message="Cloudinary가 설정되지 않았습니다.",
                timestamp=get_kst_now()
            )
        
        await upload_all_cached_images_to_cloudinary()
        
        return ControlResponse(
            success=True,
            message="모든 노선 이미지 업로드가 완료되었습니다.",
            timestamp=get_kst_now()
        )
        
    except Exception as e:
        logger.error(f"수동 업로드 오류: {e}")
        return ControlResponse(
            success=False,
            message=f"업로드 중 오류가 발생했습니다: {str(e)}",
            timestamp=get_kst_now()
        )

# 통계에 업로드 현황 추가
@app.get("/stats/cloudinary", tags=["통계"])
async def get_cloudinary_statistics():
    """Cloudinary 업로드 통계"""
    if not cloudinary_configured:
        return ControlResponse(
            success=False,
            message="Cloudinary가 설정되지 않았습니다.",
            timestamp=get_kst_now()
        )
    
    try:
        total_routes = 0
        uploaded_routes = 0
        pending_routes = []
        
        for notice in cached_notices:
            route_pages = notice.get('route_pages', {})
            route_images = notice.get('route_images', {})
            
            for route_number in route_pages.keys():
                total_routes += 1
                
                # Cloudinary URL이 있는지 확인
                image_url = route_images.get(route_number)
                if image_url and 'cloudinary.com' in str(image_url):
                    uploaded_routes += 1
                else:
                    pending_routes.append({
                        'route': route_number,
                        'notice_seq': notice.get('seq'),
                        'notice_title': notice.get('title', '')[:50]
                    })
        
        upload_percentage = (uploaded_routes / total_routes * 100) if total_routes > 0 else 0
        
        return ControlResponse(
            success=True,
            message="Cloudinary 통계 조회 완료",
            data={
                "total_routes": total_routes,
                "uploaded_routes": uploaded_routes,
                "pending_routes": len(pending_routes),
                "upload_percentage": round(upload_percentage, 1),
                "pending_details": pending_routes[:10],  # 처음 10개만
                "cloudinary_configured": cloudinary_configured
            },
            timestamp=get_kst_now()
        )
        
    except Exception as e:
        return ControlResponse(
            success=False,
            message=f"통계 조회 오류: {str(e)}",
            timestamp=get_kst_now()
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
