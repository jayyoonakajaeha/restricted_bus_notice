from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
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

# .env 파일 로드 (Windows 지원)
try:
    from dotenv import load_dotenv
    load_dotenv()  # .env 파일 자동 로드
except ImportError:
    # python-dotenv가 없으면 수동으로 로드
    from env_setup import setup_env
    setup_env()

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

# Pydantic 모델들
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
    title="서울 버스 통제 알림 API",
    description="서울시 버스 운행 변경 및 통제 정보 조회 API",
    version="1.0.0",
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

@app.get("/", tags=["기본"])
async def root():
    """API 기본 정보"""
    return {
        "service": "서울 버스 통제 알림 API",
        "version": "1.0.0",
        "status": "running",
        "last_update": last_update,
        "cached_notices": len(cached_notices) if cached_notices else 0
    }

@app.get("/health", tags=["기본"])
async def health_check():
    """헬스 체크"""
    return {"status": "healthy", "timestamp": datetime.now()}

@app.get("/notices", response_model=List[Notice], tags=["공지사항"])
async def get_notices(
    date: Optional[str] = Query(None, description="조회할 날짜 (YYYY-MM-DD)")
):
    """
    공지사항 목록 조회
    
    - **date**: 특정 날짜의 공지사항만 조회 (선택사항)
    """
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

@app.get("/notices/{notice_id}", response_model=Notice, tags=["공지사항"])
async def get_notice(notice_id: str):
    """
    특정 공지사항 상세 조회
    
    - **notice_id**: 공지사항 ID (seq)
    """
    try:
        for notice in cached_notices:
            if notice.get('seq') == notice_id:
                return notice
        
        raise HTTPException(status_code=404, detail="해당 공지사항을 찾을 수 없습니다.")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"공지사항 상세 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="공지사항 조회 중 오류가 발생했습니다.")

@app.get("/routes/{route_number}/controls", response_model=List[RouteControlInfo], tags=["노선"])
async def get_route_controls(
    route_number: str,
    date: str = Query(..., description="조회할 날짜 (YYYY-MM-DD)")
):
    """
    특정 노선의 통제 정보 조회
    
    - **route_number**: 버스 노선 번호
    - **date**: 조회할 날짜 (YYYY-MM-DD)
    """
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

@app.get("/routes/controls", tags=["노선"])
async def get_all_route_controls(
    date: str = Query(..., description="조회할 날짜 (YYYY-MM-DD)")
):
    """
    특정 날짜의 모든 노선 통제 정보 조회
    
    - **date**: 조회할 날짜 (YYYY-MM-DD)
    """
    try:
        # 날짜 유효성 검사
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
        
        filtered_notices = crawler.filter_by_date(cached_notices, date)
        
        if not filtered_notices:
            raise HTTPException(
                status_code=404, 
                detail=f"날짜 {date}에 통제 정보가 없습니다."
            )
        
        # 모든 노선 수집
        all_routes = set()
        for notice in filtered_notices:
            station_info = notice.get('station_info', {})
            for info in station_info.values():
                all_routes.update(info.get('affected_routes', []))
        
        # 각 노선별 정보 수집
        result = {}
        for route_number in sorted(all_routes):
            controls = crawler.get_control_info_by_route(cached_notices, date, route_number)
            if controls:
                result[route_number] = controls
        
        return {
            "date": date,
            "total_routes": len(result),
            "routes": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"전체 노선 통제 정보 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="노선 통제 정보 조회 중 오류가 발생했습니다.")

@app.post("/position/controls", tags=["위치"])
async def get_position_controls(request: PositionRequest):
    """
    위치 기반 통제 정류소 조회
    
    - **tm_x**: TM X 좌표
    - **tm_y**: TM Y 좌표  
    - **radius**: 검색 반경 (미터, 기본값: 500)
    - **target_date**: 조회할 날짜 (선택사항, YYYY-MM-DD)
    """
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
            # 날짜 유효성 검사
            try:
                datetime.strptime(request.target_date, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
            
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

@app.get("/stations/search", tags=["정류소"])
async def search_stations(
    name: Optional[str] = Query(None, description="정류소명으로 검색"),
    ars_id: Optional[str] = Query(None, description="ARS ID로 검색")
):
    """
    정류소 검색
    
    - **name**: 정류소명
    - **ars_id**: ARS ID (5자리 숫자)
    """
    try:
        if not name and not ars_id:
            raise HTTPException(status_code=400, detail="정류소명 또는 ARS ID 중 하나는 필수입니다.")
        
        result = {}
        
        if ars_id:
            # ARS ID로 정류소명 조회
            station_name = crawler.get_station_name_by_ars_id(ars_id)
            routes = crawler.get_routes_by_ars_id(ars_id)
            result['by_ars_id'] = {
                'ars_id': ars_id,
                'station_name': station_name,
                'routes': routes
            }
        
        if name:
            # 정류소명으로 ARS ID 조회
            found_ars_id = crawler.get_ars_id_by_station_name(name)
            routes = []
            if found_ars_id:
                routes = crawler.get_routes_by_ars_id(found_ars_id)
            result['by_name'] = {
                'station_name': name,
                'ars_id': found_ars_id,
                'routes': routes
            }
        
        return ControlResponse(
            success=True,
            message="정류소 검색 완료",
            data=result,
            timestamp=datetime.now()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"정류소 검색 오류: {e}")
        raise HTTPException(status_code=500, detail="정류소 검색 중 오류가 발생했습니다.")

@app.post("/update", tags=["관리"])
async def manual_update(background_tasks: BackgroundTasks):
    """
    수동으로 공지사항 업데이트
    """
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

@app.get("/export/csv", tags=["내보내기"])
async def export_csv(
    date: Optional[str] = Query(None, description="특정 날짜만 내보내기 (YYYY-MM-DD)")
):
    """
    CSV 파일로 데이터 내보내기
    
    - **date**: 특정 날짜만 내보내기 (선택사항)
    """
    try:
        notices_to_export = cached_notices
        
        if date:
            # 날짜 유효성 검사
            try:
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
            
            notices_to_export = crawler.filter_by_date(cached_notices, date)
        
        # 타임스탬프 추가한 파일명
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        date_suffix = f"_{date}" if date else ""
        filename = f"bus_controls{date_suffix}_{timestamp}.csv"
        
        csv_path = crawler.export_to_csv(notices_to_export, filename)
        
        return FileResponse(
            path=csv_path,
            filename=filename,
            media_type='text/csv'
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CSV 내보내기 오류: {e}")
        raise HTTPException(status_code=500, detail="CSV 내보내기 중 오류가 발생했습니다.")

@app.get("/routes/{route_number}/image", tags=["노선"])
async def get_route_image(
    route_number: str,
    date: str = Query(..., description="조회할 날짜 (YYYY-MM-DD)"),
    download: bool = Query(False, description="첨부파일 다운로드 여부")
):
    """
    특정 노선의 이미지 정보 조회 및 다운로드
    
    - **route_number**: 버스 노선 번호
    - **date**: 조회할 날짜 (YYYY-MM-DD)
    - **download**: 첨부파일을 다운로드하여 이미지 생성할지 여부
    """
    try:
        # 날짜 유효성 검사
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
        
        filtered_notices = crawler.filter_by_date(cached_notices, date)
        
        if not filtered_notices:
            raise HTTPException(
                status_code=404, 
                detail=f"날짜 {date}에 통제 정보가 없습니다."
            )
        
        # 해당 노선이 포함된 공지사항 찾기
        route_notice = None
        for notice in filtered_notices:
            route_pages = notice.get('route_pages', {})
            if route_number in route_pages:
                route_notice = notice
                break
        
        if not route_notice:
            raise HTTPException(
                status_code=404, 
                detail=f"날짜 {date}에 노선 {route_number}의 정보가 없습니다."
            )
        
        # 기존 이미지 확인
        route_images = route_notice.get('route_images', {})
        existing_image = route_images.get(route_number)
        
        # 다운로드 요청이 있고 기존 이미지가 없는 경우
        if download and (not existing_image or not os.path.exists(existing_image)):
            # 첨부파일 다운로드 및 이미지 생성
            attachments = route_notice.get('attachments', [])
            if attachments:
                print(f"노선 {route_number} 이미지 생성을 위해 첨부파일 다운로드 중...")
                
                for attachment in attachments:
                    file_path = crawler._download_attachment(attachment, save_to_folder=True)
                    if file_path:
                        # HWP → PDF 변환
                        converted_path = crawler._convert_hwp_to_pdf(file_path)
                        
                        # PDF에서 이미지 추출
                        route_pages = route_notice.get('route_pages', {})
                        page_num = route_pages.get(route_number)
                        
                        if page_num and converted_path.lower().endswith('.pdf'):
                            image_path = crawler._convert_pdf_page_to_image(
                                converted_path, page_num - 1, route_number, route_notice['seq']
                            )
                            if image_path:
                                # 캐시 업데이트
                                if 'route_images' not in route_notice:
                                    route_notice['route_images'] = {}
                                route_notice['route_images'][route_number] = image_path
                                existing_image = image_path
                                
                                # 캐시 저장
                                crawler._save_cache()
                                break
        
        result = {
            "route_number": route_number,
            "date": date,
            "notice_title": route_notice['title'],
            "page_info": route_notice.get('route_pages', {}).get(route_number),
            "image_path": existing_image,
            "image_exists": existing_image and os.path.exists(existing_image) if existing_image else False,
            "attachment_info": route_notice.get('attachments', [])
        }
        
        return ControlResponse(
            success=True,
            message=f"노선 {route_number} 이미지 정보 조회 완료",
            data=result,
            timestamp=datetime.now()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"노선 이미지 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="노선 이미지 조회 중 오류가 발생했습니다.")

@app.get("/routes/{route_number}/image/file", tags=["노선"])
async def get_route_image_file(
    route_number: str,
    date: str = Query(..., description="조회할 날짜 (YYYY-MM-DD)")
):
    """
    특정 노선의 이미지 파일 직접 다운로드
    
    - **route_number**: 버스 노선 번호
    - **date**: 조회할 날짜 (YYYY-MM-DD)
    """
    try:
        # 이미지 정보 먼저 조회
        image_info_response = await get_route_image(route_number, date, download=True)
        image_info = image_info_response.data
        
        image_path = image_info.get('image_path')
        
        if not image_path or not os.path.exists(image_path):
            raise HTTPException(
                status_code=404, 
                detail=f"노선 {route_number}의 이미지 파일을 찾을 수 없습니다."
            )
        
        return FileResponse(
            path=image_path,
            filename=f"route_{route_number}_{date}.png",
            media_type='image/png'
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"노선 이미지 파일 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="노선 이미지 파일 조회 중 오류가 발생했습니다.")

@app.get("/images/list", tags=["이미지"])
async def list_route_images():
    """
    생성된 모든 노선 이미지 목록 조회
    """
    try:
        images_folder = crawler.images_folder
        image_files = []
        
        if os.path.exists(images_folder):
            for filename in os.listdir(images_folder):
                if filename.lower().endswith('.png'):
                    file_path = os.path.join(images_folder, filename)
                    file_stat = os.stat(file_path)
                    
                    # 파일명에서 정보 추출 (route_노선번호_seq_공지번호_page_페이지.png)
                    parts = filename.replace('.png', '').split('_')
                    route_info = {}
                    if len(parts) >= 4:
                        route_info = {
                            'route_number': parts[1],
                            'notice_seq': parts[3] if len(parts) > 3 else '',
                            'page_number': parts[5] if len(parts) > 5 else ''
                        }
                    
                    image_files.append({
                        'filename': filename,
                        'file_path': file_path,
                        'size': file_stat.st_size,
                        'created_time': datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                        'route_info': route_info
                    })
        
        # 생성 시간 기준 내림차순 정렬
        image_files.sort(key=lambda x: x['created_time'], reverse=True)
        
        return ControlResponse(
            success=True,
            message=f"노선 이미지 목록 조회 완료 ({len(image_files)}개)",
            data={
                "total_images": len(image_files),
                "images_folder": images_folder,
                "images": image_files
            },
            timestamp=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"이미지 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="이미지 목록 조회 중 오류가 발생했습니다.")

@app.post("/routes/images/generate", tags=["이미지"])
async def generate_route_images(
    date: str = Query(..., description="조회할 날짜 (YYYY-MM-DD)"),
    routes: Optional[List[str]] = Query(None, description="특정 노선만 생성 (선택사항)")
):
    """
    특정 날짜의 모든 노선 이미지 일괄 생성
    
    - **date**: 조회할 날짜 (YYYY-MM-DD)
    - **routes**: 특정 노선만 생성할 경우 노선 번호 리스트
    """
    try:
        # 날짜 유효성 검사
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용해주세요.")
        
        filtered_notices = crawler.filter_by_date(cached_notices, date)
        
        if not filtered_notices:
            raise HTTPException(
                status_code=404, 
                detail=f"날짜 {date}에 통제 정보가 없습니다."
            )
        
        # 첨부파일이 있는 공지사항들 처리
        generated_images = {}
        
        for notice in filtered_notices:
            attachments = notice.get('attachments', [])
            route_pages = notice.get('route_pages', {})
            
            if not attachments or not route_pages:
                continue
            
            # 특정 노선만 처리하는 경우 필터링
            if routes:
                route_pages = {k: v for k, v in route_pages.items() if k in routes}
            
            if not route_pages:
                continue
            
            print(f"공지사항 '{notice['title']}'에서 {len(route_pages)}개 노선 이미지 생성 중...")
            
            # 첨부파일 다운로드
            for attachment in attachments:
                file_path = crawler._download_attachment(attachment, save_to_folder=True)
                if file_path:
                    converted_path = crawler._convert_hwp_to_pdf(file_path)
                    
                    if converted_path.lower().endswith('.pdf'):
                        # 각 노선별 이미지 생성
                        for route_number, page_num in route_pages.items():
                            image_path = crawler._convert_pdf_page_to_image(
                                converted_path, page_num - 1, route_number, notice['seq']
                            )
                            if image_path:
                                generated_images[route_number] = {
                                    'image_path': image_path,
                                    'notice_title': notice['title'],
                                    'page_number': page_num
                                }
                                
                                # 캐시 업데이트
                                if 'route_images' not in notice:
                                    notice['route_images'] = {}
                                notice['route_images'][route_number] = image_path
                    
                    break  # 첫 번째 첨부파일만 처리
        
        # 캐시 저장
        if generated_images:
            crawler._save_cache()
        
        return ControlResponse(
            success=True,
            message=f"노선 이미지 생성 완료 ({len(generated_images)}개)",
            data={
                "date": date,
                "generated_count": len(generated_images),
                "generated_routes": list(generated_images.keys()),
                "images": generated_images
            },
            timestamp=datetime.now()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"노선 이미지 일괄 생성 오류: {e}")
        raise HTTPException(status_code=500, detail="노선 이미지 생성 중 오류가 발생했습니다.")

@app.get("/stats", tags=["통계"])
async def get_statistics():
    """
    시스템 통계 정보
    """
    try:
        stats = {
            "total_notices": len(cached_notices),
            "last_update": last_update,
            "cache_file_size": 0,
            "notices_by_type": {},
            "recent_notices": []
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
                "control_type": notice.get('control_type')
            }
            for notice in cached_notices[:5]
        ]
        
        return ControlResponse(
            success=True,
            message="통계 정보 조회 완료",
            data=stats,
            timestamp=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"통계 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="통계 조회 중 오류가 발생했습니다.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)