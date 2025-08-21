"""
통합 API 서버 - 버스 API + 카카오톡 챗봇 (Render 배포 수정 버전)
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

class KakaoResponse:
    """카카오톡 응답 생성 헬퍼 (이미지 전송 기능 추가)"""
    
    @staticmethod
    def simple_text(text: str) -> Dict:
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}]
            }
        }
    
    @staticmethod
    def simple_image(image_url: str, alt_text: str = "이미지") -> Dict:
        """단순 이미지 전송"""
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleImage": {
                            "imageUrl": image_url,
                            "altText": alt_text
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def text_with_image(text: str, image_url: str, alt_text: str = "이미지") -> Dict:
        """텍스트와 이미지 함께 전송"""
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": text}},
                    {
                        "simpleImage": {
                            "imageUrl": image_url,
                            "altText": alt_text
                        }
                    }
                ]
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

def generate_route_image_realtime(route_number: str, target_notice: Dict) -> Optional[str]:
    """실시간으로 노선 이미지 생성 및 URL 반환"""
    try:
        if not CRAWLER_AVAILABLE:
            return None
        
        attachments = target_notice.get('attachments', [])
        if not attachments:
            print(f"노선 {route_number}: 첨부파일이 없습니다.")
            return None
        
        notice_seq = target_notice['seq']
        print(f"노선 {route_number} 이미지 생성 시작... (공지: {notice_seq})")
        
        # Gemini로 이미지 생성
        extracted = crawler._extract_with_gemini(
            target_notice.get('content', ''),
            attachments,
            notice_seq,
            save_attachments=True  # 첨부파일 저장 및 이미지 생성
        )
        
        # 생성된 이미지 확인
        route_images = extracted.get('route_images', {})
        if route_number in route_images:
            image_path = route_images[route_number]
            if image_path and os.path.exists(image_path):
                # 캐시 업데이트
                if 'route_images' not in target_notice:
                    target_notice['route_images'] = {}
                target_notice['route_images'][route_number] = image_path
                
                # 전체 캐시 저장
                crawler._save_cache()
                
                # URL 생성 (Render 배포 URL로 수정)
                filename = os.path.basename(image_path)
                base_url = os.getenv("RENDER_EXTERNAL_URL", "https://restricted-bus-notice.onrender.com")
                image_url = f"{base_url}/static/route_images/{filename}"
                
                print(f"노선 {route_number} 이미지 생성 완료: {filename}")
                return image_url
        
        print(f"노선 {route_number} 이미지 생성 실패")
        return None
        
    except Exception as e:
        print(f"노선 {route_number} 이미지 생성 중 오류: {e}")
        return None

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
    # 시작 시 실행
    ensure_directories()  # 디렉토리 생성
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
        "environment": "production" if os.getenv("RENDER_EXTERNAL_URL") else "development"
    }

@app.get("/health", tags=["기본"])
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(),
        "kakao_api": "configured" if KAKAO_REST_API_KEY else "missing",
        "cached_notices": len(cached_notices) if cached_notices else 0,
        "crawler_status": "available" if CRAWLER_AVAILABLE else "unavailable",
        "directories": {
            "attachments": os.path.exists("topis_attachments"),
            "images": os.path.exists("topis_attachments/route_images")
        }
    }

# 나머지 엔드포인트들은 원래 코드와 동일...
# (기존 코드의 나머지 부분을 여기에 포함)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
