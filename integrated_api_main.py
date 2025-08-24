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
# integrated_api_main.py 상단에 추가
import asyncio

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
                        image_url = f"{base_url}/static/route_images/{filename}"
                        
                        print(f"노선 {route_number} 이미지 생성 완료: {filename}")
                        return image_url
                
                break  # 첫 번째 첨부파일만 처리
        
        print(f"노선 {route_number} 이미지 생성 실패")
        return None
        
    except Exception as e:
        print(f"노선 {route_number} 이미지 생성 중 오류: {e}")
        return None
    
# 5. 수정된 콜백 함수들 (노선 번호 정규화 적용)
async def send_success_callback(callback_url: str, route_input: str, target_date: str, 
                              notice_title: str, detour_path: str, route_image_url: str):
    """성공 콜백 전송 (노선 번호 정규화 적용)"""
    route_number = normalize_route_number(route_input)
    
    info_text = f"✅ 이미지 생성 완료!\n\n"
    info_text += f"🚌 노선 {route_number}번 우회 경로\n"
    info_text += f"📅 {target_date}\n\n"
    if notice_title:
        title_short = notice_title[:50] + '...' if len(notice_title) > 50 else notice_title
        info_text += f"📄 {title_short}\n"
    if detour_path:
        detour_short = detour_path[:60] + '...' if len(detour_path) > 60 else detour_path
        info_text += f"🔄 우회: {detour_short}\n"
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
    
    await send_kakao_callback_message_fixed(callback_url, callback_message)


async def send_error_callback(callback_url: str, route_input: str, error_message: str):
    """오류 콜백 전송 (노선 번호 정규화 적용)"""
    route_number = normalize_route_number(route_input)
    
    callback_message = {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": f"❌ 노선 {route_number}번\n\n{error_message}\n\n다시 시도해주세요."
                    }
                }
            ]
        }
    }
    
    await send_kakao_callback_message_fixed(callback_url, callback_message)

async def handle_route_image_completely(route_number: str, body: dict, callback_url: str):
    """백그라운드에서 모든 이미지 처리 및 콜백 전송"""
    try:
        print(f"🔍 백그라운드 처리 시작: 노선 {route_number}")
        
        # 파라미터 추출
        params = body.get('action', {}).get('params', {})
        target_date = params.get('date', '').strip()
        if not target_date:
            target_date = korean_date_string()
        
        if not CRAWLER_AVAILABLE or not cached_notices:
            await send_error_callback(callback_url, route_number, "서비스를 사용할 수 없습니다.")
            return
        
        # 날짜별 공지사항 필터링
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        
        if not filtered_notices:
            await send_error_callback(callback_url, route_number, f"날짜 {target_date}에 통제 정보가 없습니다.")
            return
        
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
                    route_image_url = f"{base_url}/static/route_images/{filename}"
                    notice_title = notice.get('title', '제목 없음')
                    detour_routes = notice.get('detour_routes', {})
                    detour_path = detour_routes.get(route_number, '')
                    print(f"✅ 노선 {route_number} 기존 이미지 발견")
                    break
            
            # 해당 노선이 포함된 공지사항 찾기
            route_pages = notice.get('route_pages', {})
            if route_number in route_pages:
                target_notice = notice
                notice_title = notice.get('title', '제목 없음')
                detour_routes = notice.get('detour_routes', {})
                detour_path = detour_routes.get(route_number, '')
        
        # 2단계: 기존 이미지가 있으면 즉시 콜백
        if route_image_url:
            await send_success_callback(callback_url, route_number, target_date, notice_title, detour_path, route_image_url)
            return
        
        # 3단계: 이미지가 없으면 생성
        if target_notice:
            print(f"🔄 노선 {route_number} 이미지 생성 시작...")
            
            # 이미지 생성
            route_image_url = generate_route_image_realtime(route_number, target_notice)
            
            if route_image_url:
                await send_success_callback(callback_url, route_number, target_date, notice_title, detour_path, route_image_url)
            else:
                await send_error_callback(callback_url, route_number, "이미지 생성에 실패했습니다.")
        else:
            await send_error_callback(callback_url, route_number, f"날짜 {target_date}에 노선 {route_number}의 통제 정보가 없습니다.")
        
        print(f"✅ 노선 {route_number} 백그라운드 처리 완료")
        
    except Exception as e:
        print(f"❌ 백그라운드 처리 오류: {e}")
        await send_error_callback(callback_url, route_number, f"시스템 오류: {str(e)[:50]}")

async def send_kakao_callback_message_fixed(callback_url: str, message_data: Dict):
    """카카오톡 콜백 URL로 메시지 전송 (수정된 버전)"""
    print(f"📡 콜백 전송 시도: {callback_url}")
    
    try:
        # requests 라이브러리 사용 (더 안정적)
        import requests
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        response = requests.post(
            callback_url, 
            json=message_data, 
            headers=headers,
            timeout=10  # 타임아웃 단축
        )
        
        print(f"📨 콜백 응답 상태: {response.status_code}")
        print(f"📨 콜백 응답 내용: {response.text}")
        
        if response.status_code == 200:
            try:
                result = response.json()
                print(f"✅ 카카오톡 콜백 전송 성공: {result}")
            except:
                print(f"✅ 카카오톡 콜백 전송 성공 (JSON 파싱 실패)")
        else:
            print(f"❌ 카카오톡 콜백 전송 실패: {response.status_code}")
            
    except Exception as e:
        print(f"❌ 카카오톡 콜백 전송 오류: {e}")

async def generate_and_send_kakao_callback_fixed(route_number: str, target_date: str, 
                                               target_notice: Dict, callback_url: str, 
                                               notice_title: str, detour_path: str):
    """백그라운드에서 이미지 생성 후 카카오톡 콜백 전송 (수정된 버전)"""
    try:
        print(f"🚀 콜백 함수 시작: 노선 {route_number}")
        print(f"🔗 콜백 URL: {callback_url}")
        
        # 이미지 생성 (수정된 함수 사용)
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
        
        # 카카오톡 콜백 전송 (수정된 함수)
        await send_kakao_callback_message_fixed(callback_url, callback_message)
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
                            "text": f"❌ 시스템 오류\n\n🚌 노선 {route_number}번\n이미지 생성 중 오류가 발생했습니다.\n\n오류 상세: {str(e)[:100]}"
                        }
                    }
                ]
            }
        }
        try:
            await send_kakao_callback_message_fixed(callback_url, error_message)
        except Exception as e2:
            print(f"❌ 오류 콜백 전송도 실패: {e2}")

async def initialize_crawler():
    """크롤러 초기화 및 이미지 사전 생성"""
    global crawler, cached_notices, last_update
    
    if not CRAWLER_AVAILABLE:
        logger.warning("크롤러 모듈을 사용할 수 없습니다.")
        cached_notices = []
        last_update = get_korean_time()
        return
    
    try:
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("Gemini API Key가 설정되지 않았습니다.")
        
        crawler = TOPISCrawler(gemini_api_key=gemini_api_key)
        cached_notices, cache_hit = crawler.crawl_notices()
        last_update = get_korean_time()
        
        logger.info(f"크롤러 초기화 완료. {len(cached_notices)}개 공지사항 로드됨")
        
        # 🚀 모든 노선 이미지 사전 생성
        await generate_all_route_images_on_startup()
        
    except Exception as e:
        logger.error(f"크롤러 초기화 실패: {e}")
        cached_notices = []
        last_update = get_korean_time()


async def generate_all_route_images_on_startup():
    """서버 시작 시 모든 노선 이미지 사전 생성"""
    try:
        logger.info("🖼️ 모든 노선 이미지 사전 생성 시작...")
        
        total_generated = 0
        total_existing = 0
        total_failed = 0
        
        for notice in cached_notices:
            route_pages = notice.get('route_pages', {})
            route_images = notice.get('route_images', {})
            attachments = notice.get('attachments', [])
            
            if not route_pages or not attachments:
                continue
            
            notice_seq = notice['seq']
            notice_title = notice.get('title', 'N/A')[:30]
            
            logger.info(f"📄 처리 중: {notice_title} ({len(route_pages)}개 노선)")
            
            for route_number in route_pages.keys():
                try:
                    # 이미 이미지가 있는지 확인
                    if route_number in route_images:
                        image_path = route_images[route_number]
                        if image_path and os.path.exists(image_path):
                            logger.info(f"  ✅ 노선 {route_number}: 기존 이미지 사용")
                            total_existing += 1
                            continue
                    
                    # 이미지 생성
                    logger.info(f"  🔄 노선 {route_number}: 이미지 생성 중...")
                    
                    # 동기 함수를 비동기로 실행
                    route_image_url = await asyncio.to_thread(
                        generate_route_image_realtime_startup,
                        route_number, notice
                    )
                    
                    if route_image_url:
                        logger.info(f"  ✅ 노선 {route_number}: 이미지 생성 완료")
                        total_generated += 1
                    else:
                        logger.warning(f"  ❌ 노선 {route_number}: 이미지 생성 실패")
                        total_failed += 1
                    
                    # CPU 부하 방지를 위한 잠시 대기
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    logger.error(f"  ❌ 노선 {route_number} 이미지 생성 오류: {e}")
                    total_failed += 1
        
        # 캐시 저장
        if total_generated > 0:
            await asyncio.to_thread(crawler._save_cache)
        
        logger.info(f"🎉 노선 이미지 사전 생성 완료!")
        logger.info(f"  📊 통계: 신규생성 {total_generated}개, 기존사용 {total_existing}개, 실패 {total_failed}개")
        
    except Exception as e:
        logger.error(f"❌ 노선 이미지 사전 생성 실패: {e}")


def generate_route_image_realtime_startup(route_number: str, target_notice: Dict) -> Optional[str]:
    """서버 시작용 노선 이미지 생성 (동기 버전)"""
    try:
        if not CRAWLER_AVAILABLE:
            return None
        
        attachments = target_notice.get('attachments', [])
        route_pages = target_notice.get('route_pages', {})
        
        if not attachments or route_number not in route_pages:
            return None
        
        notice_seq = target_notice['seq']
        page_num = route_pages[route_number]
        
        # 첫 번째 첨부파일만 처리
        attachment = attachments[0]
        file_path = crawler._download_attachment(attachment, save_to_folder=True)
        
        if file_path:
            # HWP 변환 (필요시)
            converted_path = crawler._convert_hwp_to_pdf(file_path)
            
            if converted_path.lower().endswith('.pdf'):
                # 이미지 생성
                image_path = crawler._convert_pdf_page_to_image(
                    converted_path, page_num - 1, route_number, notice_seq
                )
                
                if image_path and os.path.exists(image_path):
                    # 캐시 업데이트 (메모리에서만)
                    if 'route_images' not in target_notice:
                        target_notice['route_images'] = {}
                    target_notice['route_images'][route_number] = image_path
                    
                    # URL 생성
                    filename = os.path.basename(image_path)
                    base_url = os.getenv("RENDER_EXTERNAL_URL", "https://restricted-bus-notice.onrender.com")
                    image_url = f"{base_url}/static/route_images/{filename}"
                    
                    return image_url
        
        return None
        
    except Exception as e:
        print(f"❌ 시작용 이미지 생성 오류 (노선 {route_number}): {e}")
        return None

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


# 1. 노선 번호 정규화 함수 (새로 추가)
def normalize_route_number(route_input: str) -> str:
    """노선 번호 정규화 (9401번, 9401 버스 등 → 9401)"""
    if not route_input:
        return ""
    
    # 공백 제거 및 소문자 변환
    normalized = route_input.strip().lower()
    
    # '번', '버스', '번버스' 등 제거
    patterns_to_remove = [
        r'번\s*버스$',  # '번 버스', '번버스'
        r'번$',         # '번'
        r'\s*버스$',    # ' 버스', '버스'
    ]
    
    for pattern in patterns_to_remove:
        normalized = re.sub(pattern, '', normalized)
    
    # 공백 제거
    return normalized.strip()


# 2. 수정된 route_check_webhook
@app.post("/webhook/route_check", tags=["카카오톡"])
async def route_check_webhook(req: Request, background_tasks: BackgroundTasks):
    """특정 노선 통제 정보 조회 (올바른 카카오톡 콜백)"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    params = body.get('action', {}).get('params', {})
    route_input = params.get('route_number', '').strip()
    target_date = params.get('date', '').strip()
    
    # 노선 번호 정규화
    route_number = normalize_route_number(route_input)
    
    if not route_number:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "노선 번호를 입력해주세요.\n예: 406, 143, 7016, 9401번, 406번 버스"}}]}}
    
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
            
            # 영향 정류소 표시
            stations = control.get('affected_stations', [])
            if stations:
                station_names = [s.get('station_name', '이름없음') for s in stations[:3]]
                response_text += f"🚏 영향정류소: {', '.join(station_names)}"
                if len(stations) > 3:
                    response_text += f" 외 {len(stations)-3}곳"
                response_text += "\n"
            
            # 우회 경로 표시 (수정됨)
            detour = control.get('detour_path', '')
            if detour:
                if len(detour) > 50:
                    detour = detour[:50] + "..."
                response_text += f"🔄 우회: {detour}\n"
            
            response_text += "\n"
        
        # 이미지 확인 및 생성 (기존 코드와 동일)
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


# 3. 수정된 route_image_webhook
@app.post("/webhook/route_image", tags=["카카오톡"])
async def route_image_webhook(req: Request):
    """노선 우회 경로 이미지 전송 (사전 생성된 이미지 사용)"""
    body = await req.json()
    
    if 'userRequest' not in body:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잘못된 요청입니다."}}]}}
    
    params = body.get('action', {}).get('params', {})
    route_input = params.get('route_number', '').strip()
    target_date = params.get('date', '').strip()
    
    # 노선 번호 정규화
    route_number = normalize_route_number(route_input)
    
    if not route_number:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "노선 번호를 입력해주세요.\n예: 406, 143, 9401번, 406번 버스"}}]}}
    
    if not target_date:
        target_date = korean_date_string()
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "현재 서비스를 사용할 수 없습니다."}}]}}
    
    try:
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        
        # 해당 노선 정보 및 이미지 찾기
        for notice in filtered_notices:
            route_pages = notice.get('route_pages', {})
            route_images = notice.get('route_images', {})
            
            if route_number in route_pages:
                notice_title = notice.get('title', '제목 없음')
                detour_routes = notice.get('detour_routes', {})
                detour_path = detour_routes.get(route_number, '')
                
                # 영향 정류소 정보 추가
                station_info = notice.get('station_info', {})
                affected_stations = []
                for station_id, info in station_info.items():
                    if route_number in info.get('affected_routes', []):
                        affected_stations.append(info.get('name', '이름미상'))
                
                # 이미지 URL 생성
                route_image_url = None
                if route_number in route_images:
                    image_path = route_images[route_number]
                    if image_path and os.path.exists(image_path):
                        filename = os.path.basename(image_path)
                        base_url = os.getenv("RENDER_EXTERNAL_URL", "https://restricted-bus-notice.onrender.com")
                        route_image_url = f"{base_url}/static/route_images/{filename}"
                
                # 응답 구성
                info_text = f"🚌 노선 {route_number}번 우회 경로\n"
                info_text += f"📅 {target_date}\n\n"
                
                if notice_title:
                    title_short = notice_title[:50] + '...' if len(notice_title) > 50 else notice_title
                    info_text += f"📄 {title_short}\n"
                
                # 영향 정류소 표시 (수정됨)
                if affected_stations:
                    stations_str = ', '.join(affected_stations[:3])
                    if len(affected_stations) > 3:
                        stations_str += f" 외 {len(affected_stations)-3}곳"
                    info_text += f"🚏 영향정류소: {stations_str}\n"
                
                # 우회 경로 표시 (수정됨)
                if detour_path:
                    detour_short = detour_path[:60] + '...' if len(detour_path) > 60 else detour_path
                    info_text += f"🔄 우회: {detour_short}\n"
                
                if route_image_url:
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
                    info_text += "\n⚠️ 이미지를 준비 중입니다. 잠시 후 다시 시도해주세요."
                    
                    return {
                        "version": "2.0",
                        "template": {
                            "outputs": [
                                {
                                    "simpleText": {
                                        "text": info_text
                                    }
                                }
                            ]
                        }
                    }
        
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


# 4. 새로운 엔드포인트 - 현재 통제되는 정류소 정보
@app.get("/controlled-stations", tags=["통제정보"])
async def get_controlled_stations(
    date: Optional[str] = Query(None, description="조회할 날짜 (YYYY-MM-DD, 기본값: 오늘)")
):
    """
    현재 통제되는 버스 정류소 정보 조회
    
    - **date**: 조회할 날짜 (선택사항, 기본값: 한국시간 기준 오늘)
    """
    try:
        if not CRAWLER_AVAILABLE or not cached_notices:
            raise HTTPException(status_code=503, detail="서비스를 사용할 수 없습니다.")
        
        target_date = date if date else korean_date_string()
        
        # 날짜 유효성 검사
        try:
            datetime.strptime(target_date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
        
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        
        if not filtered_notices:
            return ControlResponse(
                success=True,
                message=f"날짜 {target_date}에 통제되는 정류소가 없습니다.",
                data={
                    "date": target_date,
                    "total_stations": 0,
                    "controlled_stations": []
                },
                timestamp=get_korean_time()
            )
        
        controlled_stations = []
        station_coords = {}  # 정류소 좌표 캐시
        
        for notice in filtered_notices:
            station_info = notice.get('station_info', {})
            detour_routes = notice.get('detour_routes', {})
            
            for station_id, info in station_info.items():
                station_name = info.get('name', '이름미상')
                
                # 좌표 조회 (캐시 확인)
                coords = station_coords.get(station_id)
                if not coords and station_id.isdigit() and len(station_id) == 5:
                    try:
                        # 서울시 버스 API로 정류소 좌표 조회
                        url = 'http://ws.bus.go.kr/api/rest/stationinfo/getStationByUid'
                        params = {'serviceKey': crawler.service_key, 'arsId': station_id}
                        
                        response = requests.get(url, params=params, timeout=5)
                        if response.status_code == 200:
                            import xml.etree.ElementTree as ET
                            root = ET.fromstring(response.content.decode('utf-8'))
                            
                            tm_x = root.find('.//itemList/tmX')
                            tm_y = root.find('.//itemList/tmY')
                            
                            if tm_x is not None and tm_y is not None:
                                coords = {
                                    "tm_x": float(tm_x.text) if tm_x.text else 0,
                                    "tm_y": float(tm_y.text) if tm_y.text else 0
                                }
                                station_coords[station_id] = coords
                    except Exception as e:
                        logger.warning(f"정류소 {station_id} 좌표 조회 실패: {e}")
                
                # 우회 경로 정보
                affected_routes = info.get('affected_routes', [])
                station_detours = {}
                for route in affected_routes:
                    if route in detour_routes:
                        station_detours[route] = detour_routes[route]
                
                station_data = {
                    "station_id": station_id,
                    "station_name": station_name,
                    "coordinates": coords or {"tm_x": 0, "tm_y": 0},
                    "control_periods": info.get('periods', []),
                    "control_type": notice.get('control_type', '통제'),
                    "control_scope": info.get('control_scope', '전체통제'),
                    "affected_routes": affected_routes,
                    "detour_routes": station_detours,
                    "notice_title": notice.get('title', '제목 없음'),
                    "notice_seq": notice.get('seq', '')
                }
                
                controlled_stations.append(station_data)
        
        # 중복 제거 (station_id 기준)
        unique_stations = {}
        for station in controlled_stations:
            station_id = station['station_id']
            if station_id not in unique_stations:
                unique_stations[station_id] = station
            else:
                # 기존 데이터와 병합
                existing = unique_stations[station_id]
                # 기간 정보 병합
                all_periods = existing['control_periods'] + station['control_periods']
                existing['control_periods'] = list(set(all_periods))
                # 노선 정보 병합
                all_routes = existing['affected_routes'] + station['affected_routes']
                existing['affected_routes'] = list(set(all_routes))
                # 우회 정보 병합
                existing['detour_routes'].update(station['detour_routes'])
        
        final_stations = list(unique_stations.values())
        
        return ControlResponse(
            success=True,
            message=f"날짜 {target_date}에 통제되는 정류소 {len(final_stations)}개 조회 완료",
            data={
                "date": target_date,
                "total_stations": len(final_stations),
                "controlled_stations": final_stations
            },
            timestamp=get_korean_time()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"통제 정류소 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="통제 정류소 조회 중 오류가 발생했습니다.")
        
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
