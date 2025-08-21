"""
통합 API 서버 - 버스 API + 카카오톡 챗봇 (Cloudinary 자동 업로드 + 콜백 API)
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import json
from datetime import datetime
import asyncio
import logging
from contextlib import asynccontextmanager
import pytz
import aiohttp
import tempfile

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

# 기존 모듈 import
try:
    from restricted_bus import TOPISCrawler
    CRAWLER_AVAILABLE = True
except ImportError:
    print("⚠️ 크롤러 모듈을 찾을 수 없습니다.")
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

# Cloudinary 이미지 처리 함수들
def upload_image_to_cloudinary(image_path: str, route_number: str, notice_seq: str) -> Optional[str]:
    """이미지를 Cloudinary에 업로드하고 URL 반환"""
    if not cloudinary_configured:
        return None
    
    try:
        public_id = f"bus_routes/route_{route_number}_seq_{notice_seq}"
        
        result = cloudinary.uploader.upload(
            image_path,
            public_id=public_id,
            folder="seoul_bus",
            resource_type="image",
            format="png",
            overwrite=True
        )
        
        optimized_url, _ = cloudinary_url(
            result['public_id'],
            format="auto",
            quality="auto",
            crop="fit",
            width=1024,
            height=768,
            secure=True
        )
        
        logger.info(f"✅ Cloudinary 업로드 성공: 노선 {route_number}")
        return optimized_url
        
    except Exception as e:
        logger.error(f"❌ Cloudinary 업로드 실패: {e}")
        return None

def check_cloudinary_image(route_number: str, notice_seq: str) -> Optional[str]:
    """Cloudinary에서 기존 이미지 확인"""
    if not cloudinary_configured:
        return None
    
    try:
        public_id = f"seoul_bus/bus_routes/route_{route_number}_seq_{notice_seq}"
        result = cloudinary.api.resource(public_id)
        
        if result and result.get('secure_url'):
            return result['secure_url']
            
    except cloudinary.exceptions.NotFound:
        pass
    except Exception as e:
        logger.error(f"Cloudinary 확인 오류: {e}")
    
    return None

async def upload_all_images_to_cloudinary():
    """서버 시작 시 모든 노선 이미지를 Cloudinary에 업로드"""
    if not CRAWLER_AVAILABLE or not cloudinary_configured or not cached_notices:
        return
    
    logger.info("🚀 모든 노선 이미지를 Cloudinary에 자동 업로드 시작...")
    
    upload_count = 0
    skip_count = 0
    
    for notice in cached_notices:
        notice_seq = notice.get('seq')
        route_pages = notice.get('route_pages', {})
        route_images = notice.get('route_images', {})
        attachments = notice.get('attachments', [])
        
        if not attachments or not route_pages:
            continue
        
        for route_number in route_pages.keys():
            try:
                # 이미 Cloudinary URL이 있는지 확인
                existing_url = route_images.get(route_number)
                if existing_url and 'cloudinary.com' in str(existing_url):
                    skip_count += 1
                    continue
                
                # Cloudinary에서 확인
                cloudinary_url = check_cloudinary_image(route_number, notice_seq)
                if cloudinary_url:
                    if 'route_images' not in notice:
                        notice['route_images'] = {}
                    notice['route_images'][route_number] = cloudinary_url
                    skip_count += 1
                    continue
                
                # 로컬 이미지가 있으면 업로드
                local_image = route_images.get(route_number)
                if local_image and os.path.exists(local_image):
                    cloudinary_url = upload_image_to_cloudinary(local_image, route_number, notice_seq)
                    if cloudinary_url:
                        notice['route_images'][route_number] = cloudinary_url
                        upload_count += 1
                else:
                    # 이미지 생성 후 업로드 (Gemini 사용)
                    logger.info(f"노선 {route_number} 이미지 생성 중...")
                    # 여기서 실제 이미지 생성 로직 호출
                    # 생성된 이미지를 Cloudinary에 업로드
                    pass
                
                await asyncio.sleep(0.5)  # API 제한 방지
                
            except Exception as e:
                logger.error(f"노선 {route_number} 처리 오류: {e}")
    
    # 캐시 저장
    if upload_count > 0 and crawler:
        crawler._save_cache()
    
    logger.info(f"✅ Cloudinary 업로드 완료: 신규 {upload_count}개, 기존 {skip_count}개")

async def send_kakao_callback(callback_url: str, message_data: Dict):
    """카카오톡 콜백 전송"""
    try:
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'BusControlAPI/1.0'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                callback_url, 
                json=message_data, 
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    logger.info("✅ 카카오톡 콜백 전송 성공")
                    return True
                else:
                    logger.error(f"❌ 콜백 전송 실패: {response.status}")
                    return False
                    
    except Exception as e:
        logger.error(f"❌ 콜백 전송 오류: {e}")
        return False

async def process_route_image_callback(route_number: str, target_date: str, callback_url: str):
    """백그라운드에서 노선 이미지 처리 후 콜백 전송"""
    try:
        # 해당 날짜와 노선의 통제 정보 찾기
        filtered_notices = crawler.filter_by_date(cached_notices, target_date)
        
        route_info = None
        image_url = None
        
        for notice in filtered_notices:
            # 노선 정보 확인
            route_pages = notice.get('route_pages', {})
            if route_number in route_pages:
                # Cloudinary URL 확인
                route_images = notice.get('route_images', {})
                image_url = route_images.get(route_number)
                
                # 통제 정보 수집
                detour_routes = notice.get('detour_routes', {})
                route_info = {
                    'title': notice.get('title', ''),
                    'detour': detour_routes.get(route_number, ''),
                    'control_type': notice.get('control_type', '통제')
                }
                break
        
        # 콜백 메시지 구성
        if route_info and image_url:
            text = f"🚌 노선 {route_number}번 통제 정보\n"
            text += f"📅 {target_date}\n\n"
            text += f"📋 {route_info['title']}\n"
            text += f"🚧 통제유형: {route_info['control_type']}\n"
            if route_info['detour']:
                text += f"🔄 우회경로: {route_info['detour']}\n"
            
            callback_message = {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": text}},
                        {
                            "simpleImage": {
                                "imageUrl": image_url,
                                "altText": f"{route_number}번 버스 우회 경로"
                            }
                        }
                    ]
                }
            }
        else:
            callback_message = {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {
                            "simpleText": {
                                "text": f"❌ 노선 {route_number}번의 {target_date} 통제 정보를 찾을 수 없습니다."
                            }
                        }
                    ]
                }
            }
        
        # 콜백 전송
        await send_kakao_callback(callback_url, callback_message)
        
    except Exception as e:
        logger.error(f"콜백 처리 오류: {e}")
        error_message = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": f"❌ 처리 중 오류가 발생했습니다: {str(e)}"}}
                ]
            }
        }
        await send_kakao_callback(callback_url, error_message)

async def initialize_crawler():
    """크롤러 초기화 및 Cloudinary 자동 업로드"""
    global crawler, cached_notices, last_update
    
    if not CRAWLER_AVAILABLE:
        logger.warning("크롤러 모듈을 사용할 수 없습니다.")
        return
    
    try:
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("Gemini API Key가 설정되지 않았습니다.")
        
        crawler = TOPISCrawler(gemini_api_key=gemini_api_key)
        cached_notices, _ = crawler.crawl_notices()
        last_update = get_kst_now()
        
        logger.info(f"✅ 크롤러 초기화 완료. {len(cached_notices)}개 공지사항 로드")
        
        # Cloudinary 자동 업로드
        if cloudinary_configured:
            await upload_all_images_to_cloudinary()
        
    except Exception as e:
        logger.error(f"크롤러 초기화 실패: {e}")
        cached_notices = []
        last_update = get_kst_now()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 실행
    await initialize_crawler()
    yield

# FastAPI 앱 생성
app = FastAPI(
    title="서울 버스 통제 알림 API + 카카오톡 챗봇",
    description="Cloudinary 자동 업로드 + 카카오톡 콜백 API",
    version="3.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 기본 엔드포인트
@app.get("/", tags=["기본"])
async def root():
    """API 기본 정보"""
    return {
        "service": "서울 버스 통제 알림 API",
        "version": "3.0.0",
        "status": "running",
        "last_update": last_update,
        "cached_notices": len(cached_notices),
        "cloudinary": "enabled" if cloudinary_configured else "disabled",
        "current_time_kst": get_kst_now().isoformat(),
        "today_kst": get_kst_today()
    }

@app.get("/health", tags=["기본"])
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy",
        "timestamp": get_kst_now(),
        "cached_notices": len(cached_notices),
        "cloudinary": cloudinary_configured
    }

# 카카오톡 챗봇 엔드포인트
@app.post("/kakao/route", tags=["카카오톡"])
async def kakao_route_handler(req: Request, background_tasks: BackgroundTasks):
    """카카오톡 노선 조회 (콜백 API 사용)"""
    body = await req.json()
    
    # 파라미터 추출
    user_request = body.get('userRequest', {})
    params = body.get('action', {}).get('params', {})
    
    route_number = params.get('route_number', '').strip()
    target_date = params.get('date', '').strip()
    
    # 콜백 URL
    callback_url = user_request.get('callbackUrl')
    
    if not route_number:
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "노선 번호를 입력해주세요.\n예: 406, 143, 9401"}}
                ]
            }
        }
    
    # 날짜가 없으면 한국 시간 기준 오늘
    if not target_date:
        target_date = get_kst_today()
    
    # 날짜 형식 검증
    try:
        datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "날짜 형식이 잘못되었습니다.\nYYYY-MM-DD 형식으로 입력해주세요."}}
                ]
            }
        }
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "현재 서비스를 사용할 수 없습니다.\n잠시 후 다시 시도해주세요."}}
                ]
            }
        }
    
    # 콜백이 있으면 비동기 처리
    if callback_url:
        background_tasks.add_task(
            process_route_image_callback,
            route_number,
            target_date,
            callback_url
        )
        
        return {
            "version": "2.0",
            "useCallback": True,
            "data": {
                "text": f"🔍 노선 {route_number}번 정보를 조회 중입니다...\n잠시만 기다려주세요. (약 3-5초)"
            }
        }
    
    # 콜백이 없으면 즉시 응답 (이미지 없이)
    filtered_notices = crawler.filter_by_date(cached_notices, target_date)
    
    for notice in filtered_notices:
        route_pages = notice.get('route_pages', {})
        if route_number in route_pages:
            detour_routes = notice.get('detour_routes', {})
            
            text = f"🚌 노선 {route_number}번 통제 정보\n"
            text += f"📅 {target_date}\n\n"
            text += f"📋 {notice.get('title', '')}\n"
            text += f"🚧 통제유형: {notice.get('control_type', '통제')}\n"
            
            detour = detour_routes.get(route_number, '')
            if detour:
                text += f"🔄 우회경로: {detour}\n"
            
            text += "\n💡 이미지를 보려면 콜백 설정이 필요합니다."
            
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": text}}
                    ]
                }
            }
    
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {"simpleText": {"text": f"❌ 노선 {route_number}번의 {target_date} 통제 정보가 없습니다."}}
            ]
        }
    }

@app.post("/kakao/today", tags=["카카오톡"])
async def kakao_today_handler(req: Request):
    """오늘의 버스 통제 정보 요약"""
    today = get_kst_today()
    
    if not CRAWLER_AVAILABLE or not cached_notices:
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "현재 서비스를 사용할 수 없습니다."}}
                ]
            }
        }
    
    filtered_notices = crawler.filter_by_date(cached_notices, today)
    
    if not filtered_notices:
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": f"✅ 오늘({today}) 버스 통제 정보가 없습니다.\n정상 운행 중입니다!"}}
                ]
            }
        }
    
    # 통제 노선 수집
    all_routes = set()
    for notice in filtered_notices:
        route_pages = notice.get('route_pages', {})
        all_routes.update(route_pages.keys())
    
    text = f"📅 오늘({today}) 버스 통제 현황\n\n"
    text += f"🚨 총 {len(filtered_notices)}건의 통제\n"
    text += f"🚌 영향받는 노선: {len(all_routes)}개\n\n"
    
    # 주요 공지 2개
    for i, notice in enumerate(filtered_notices[:2], 1):
        title = notice.get('title', '')
        if len(title) > 30:
            title = title[:30] + "..."
        text += f"{i}. {title}\n"
    
    if len(filtered_notices) > 2:
        text += f"... 외 {len(filtered_notices)-2}건\n"
    
    text += "\n💡 특정 노선 조회: '노선 [번호]'"
    
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {"simpleText": {"text": text}}
            ]
        }
    }

# 관리 엔드포인트
@app.post("/admin/upload-all", tags=["관리"])
async def manual_upload_all():
    """모든 이미지 수동 Cloudinary 업로드"""
    if not cloudinary_configured:
        raise HTTPException(status_code=503, detail="Cloudinary가 설정되지 않았습니다.")
    
    await upload_all_images_to_cloudinary()
    return {"message": "업로드 작업이 시작되었습니다."}

@app.get("/stats/cloudinary", tags=["통계"])
async def cloudinary_stats():
    """Cloudinary 업로드 통계"""
    if not cached_notices:
        return {"total": 0, "uploaded": 0}
    
    total = 0
    uploaded = 0
    
    for notice in cached_notices:
        route_pages = notice.get('route_pages', {})
        route_images = notice.get('route_images', {})
        
        for route in route_pages.keys():
            total += 1
            if route in route_images and 'cloudinary.com' in str(route_images[route]):
                uploaded += 1
    
    return {
        "total_routes": total,
        "uploaded_routes": uploaded,
        "percentage": round((uploaded/total*100) if total > 0 else 0, 1),
        "timestamp": get_kst_now()
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
