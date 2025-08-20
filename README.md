# 🚌 서울 버스 통제 알림 시스템 (Restricted Bus Notice)

서울시 버스 운행 변경 및 통제 정보를 자동으로 수집하고 조회할 수 있는 시스템입니다.

## 📋 주요 기능

### 🔍 데이터 수집
- **TOPIS 공지사항 크롤링**: 서울시 교통정보시스템(TOPIS)에서 버스 운행 변경 공지사항을 자동 수집
- **AI 기반 정보 추출**: Gemini API를 활용하여 PDF/HWP 첨부파일에서 상세 정보 자동 추출
- **스마트 캐싱**: 중복 처리 방지 및 성능 최적화를 위한 캐시 시스템

### 📊 정보 조회
- **날짜별 통제 현황**: 특정 날짜의 버스 통제 정보 조회
- **노선별 상세 정보**: 개별 버스 노선의 통제 및 우회 경로 확인
- **위치 기반 검색**: 좌표(TM) 기반 주변 통제 정류소 조회
- **실시간 정류소 정보**: 서울시 버스 API를 통한 정류소 및 노선 정보 연동

### 🎯 통제 정보 상세
- **통제 유형**: 우회, 폐쇄, 미정차, 단축운행 등
- **통제 기간**: 정확한 시작/종료 시간 정보
- **우회 경로**: 노선별 대체 경로 안내
- **영향 정류소**: 통제되는 정류소 목록 및 ARS ID
- **첨부파일 처리**: PDF/HWP 파일 자동 변환 및 이미지 추출

## 🛠️ 기술 스택

- **Python 3.8+**
- **데이터 수집**: requests, BeautifulSoup4, xml
- **AI 분석**: Google Gemini API
- **문서 처리**: PyMuPDF (PDF), win32com.client (HWP)
- **데이터 저장**: JSON 캐싱, pandas (CSV 내보내기)
- **이미지 처리**: PIL, matplotlib

## 🚀 설치 및 설정

### 1. 저장소 클론
```bash
git clone <repository-url>
cd restricted_bus_notice
```

### 2. 의존성 설치
```bash
pip install -r requirements.txt
```

### 3. 환경 변수 설정
```bash
# Gemini API 키 설정
export GOOGLE_API_KEY="your_gemini_api_key"
# 또는
export GEMINI_API_KEY="your_gemini_api_key"
```

### 4. HWP 변환 설정 (Windows 전용)
- 한글 2010 이상 설치 필요
- win32com 라이브러리를 통한 HWP → PDF 변환

## 🚀 API 서버 실행

### 1. 환경 설정
```bash
# .env 파일 생성
cp .env.example .env

# Gemini API 키 설정
echo "GOOGLE_API_KEY=your_api_key_here" >> .env
```

### 2. API 서버 실행
```bash
# 직접 실행
python api_main.py

# 또는 uvicorn 사용
uvicorn api_main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Docker 실행
```bash
# Docker Compose 사용
docker-compose up -d

# 또는 Docker 직접 실행
docker build -t bus-control-api .
docker run -p 8000:8000 -e GOOGLE_API_KEY=your_api_key bus-control-api
```

### 4. API 문서 확인
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 📡 API 엔드포인트

### 기본 정보
- `GET /`: 서비스 기본 정보
- `GET /health`: 헬스 체크
- `GET /stats`: 시스템 통계

### 공지사항
- `GET /notices`: 공지사항 목록 조회
- `GET /notices?date=2025-08-15`: 특정 날짜 공지사항 조회
- `GET /notices/{notice_id}`: 공지사항 상세 조회

### 노선 정보
- `GET /routes/{route_number}/controls?date=2025-08-15`: 특정 노선 통제 정보
- `GET /routes/controls?date=2025-08-15`: 전체 노선 통제 정보

### 위치 기반 조회
- `POST /position/controls`: 좌표 기반 통제 정류소 조회

### 정류소 검색
- `GET /stations/search?name=광화문`: 정류소명으로 검색
- `GET /stations/search?ars_id=01118`: ARS ID로 검색

### 관리 기능
- `POST /update`: 수동 데이터 업데이트
- `GET /export/csv`: CSV 내보내기

### API 사용 예시
```python
import requests

# 기본 정보 조회
response = requests.get("http://localhost:8000/")
print(response.json())

# 특정 날짜 공지사항 조회
response = requests.get("http://localhost:8000/notices?date=2025-08-15")
notices = response.json()

# 노선 통제 정보 조회
response = requests.get("http://localhost:8000/routes/406/controls?date=2025-08-15")
controls = response.json()

# 위치 기반 조회
data = {
    "tm_x": 196769.0,
    "tm_y": 451475.0,
    "radius": 500,
    "target_date": "2025-08-15"
}
response = requests.post("http://localhost:8000/position/controls", json=data)
result = response.json()
```

## 📖 사용법

### 기본 실행 (스크립트)
```python
from restricted_bus import TOPISCrawler

# 크롤러 초기화
crawler = TOPISCrawler(gemini_api_key="your_api_key")

# 공지사항 수집
notices, cache_hit = crawler.crawl_notices()

# 특정 날짜 통제 정보 조회
target_date = "2025-08-15"
filtered_notices = crawler.filter_by_date(notices, target_date)

# 특정 노선 정보 조회
route_controls = crawler.get_control_info_by_route(notices, target_date, "406")
```

### 위치 기반 조회
```python
from position_checker import check_control_by_position

# TM 좌표 기준 주변 통제 정류소 확인
check_control_by_position(
    crawler=crawler,
    notices=notices, 
    tm_x=196769.0,
    tm_y=451475.0,
    radius=500,
    target_date="2025-08-15"
)
```

### CSV 내보내기
```python
# 수집된 데이터를 CSV로 내보내기
csv_file = crawler.export_to_csv(notices, "bus_controls_20250815.csv")
```

## 🔧 설정 파일

### 캐시 설정
- **캐시 파일**: `topis_cache.json`
- **자동 정리**: 30일 이상된 데이터 자동 삭제
- **파일 관리**: 첨부파일 30개 제한으로 자동 정리

### 폴더 구조
```
restricted_bus_notice/
├── restricted_bus.py          # 메인 크롤러
├── position_checker.py        # 위치 기반 조회
├── hwpx2pdf.py               # HWP 변환 유틸리티
├── extract_image.py          # PDF 이미지 추출
├── topis_cache.json          # 캐시 데이터
├── topis_attachments/        # 첨부파일 저장소
│   └── route_images/         # 노선 이미지
└── README.md
```

## 📊 데이터 구조

### 공지사항 정보
```json
{
  "seq": "5204",
  "title": "8/15(금) 강남구 관내 집회 대비 시내버스 정류소 무정차 안내",
  "create_date": "2025-08-14 09:44:22",
  "control_type": "미정차",
  "general_periods": ["2025-08-15 15:00~2025-08-15 18:15"],
  "station_info": {
    "23285": {
      "name": "강남역11번출구",
      "periods": ["2025-08-15 15:00~2025-08-15 18:15"],
      "affected_routes": ["3412", "4312", "서초03", "8541"],
      "control_scope": "특정노선"
    }
  },
  "detour_routes": {
    "3412": "대체경로 정보..."
  }
}
```

## 🔍 주요 클래스 및 메서드

### TOPISCrawler 클래스
- `crawl_notices()`: 공지사항 수집
- `filter_by_date()`: 날짜별 필터링
- `get_control_info_by_route()`: 노선별 통제 정보
- `show_route_control_info()`: 노선 통제 정보 출력
- `download_attachments_for_filtered_notices()`: 첨부파일 다운로드

### 위치 기반 기능
- `get_stations_by_position()`: 좌표 기준 정류소 조회
- `check_control_by_position()`: 위치 기반 통제 정보 확인

## ⚡ 성능 최적화

- **캐시 시스템**: 중복 크롤링 방지
- **재시도 로직**: 네트워크 오류 자동 복구
- **파일 관리**: 자동 정리로 디스크 사용량 최적화
- **API 제한**: 요청 간격 조절로 서버 부하 방지

## 🤝 기여하기

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 라이선스

이 프로젝트는 MIT 라이선스 하에 배포됩니다. 자세한 내용은 `LICENSE` 파일을 참고하세요.

## 🙋‍♂️ 문의 및 지원

- **이슈**: GitHub Issues를 통해 버그 리포트 및 기능 요청
- **문서**: 자세한 API 문서는 Wiki 참고

---

**⚠️ 주의사항**
- Gemini API 키가 필요합니다
- Windows 환경에서 HWP 변환 시 한글 프로그램 설치 필요
- 서울시 TOPIS 시스템의 정책 변경에 따라 일부 기능이 영향받을 수 있습니다
