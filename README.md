# 🚌 서울 버스 통제 알림 시스템 (Restricted Bus Notice)

서울시 버스 운행 변경 및 통제 정보를 자동으로 수집하고 조회할 수 있는 시스템입니다. FastAPI 기반의 REST API 서버로 웹, 모바일 등 다양한 플랫폼에서 활용 가능합니다.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104%2B-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 📋 주요 기능

### 🔍 데이터 수집 & 분석
- **TOPIS 공지사항 크롤링**: 서울시 교통정보시스템에서 버스 운행 변경 공지사항 자동 수집
- **AI 기반 정보 추출**: Gemini API를 활용하여 PDF/HWP 첨부파일에서 상세 정보 자동 추출
- **스마트 캐싱**: 중복 처리 방지 및 성능 최적화를 위한 지능형 캐시 시스템
- **문서 자동 변환**: HWP → PDF 변환 및 이미지 추출

### 📊 정보 조회 & 검색
- **날짜별 통제 현황**: 특정 날짜의 버스 통제 정보 조회
- **노선별 상세 정보**: 개별 버스 노선의 통제 및 우회 경로 확인
- **위치 기반 검색**: 좌표(TM) 기반 주변 통제 정류소 조회
- **정류소 통합 검색**: 정류소명, ARS ID를 통한 다각도 검색
- **실시간 정보 연동**: 서울시 버스 API를 통한 최신 정류소 및 노선 정보

### 🚀 API 서비스
- **RESTful API**: 표준 REST API 엔드포인트 제공
- **자동 문서화**: Swagger UI 및 ReDoc 자동 생성
- **비동기 처리**: 백그라운드 데이터 업데이트
- **CORS 지원**: 웹 브라우저에서 직접 호출 가능
- **Docker 지원**: 컨테이너 기반 배포

## 🛠️ 기술 스택

### Backend
- **Python 3.8+** - 메인 개발 언어
- **FastAPI** - 고성능 웹 프레임워크
- **Uvicorn** - ASGI 서버

### 데이터 처리
- **requests, aiohttp** - HTTP 클라이언트
- **BeautifulSoup4** - 웹 스크래핑
- **pandas** - 데이터 분석 및 CSV 처리
- **xml.etree** - XML 파싱

### AI & 문서 처리
- **Google Gemini API** - AI 기반 문서 분석
- **PyMuPDF** - PDF 처리
- **win32com.client** - HWP 변환 (Windows)
- **PIL, matplotlib** - 이미지 처리

### 배포 & 운영
- **Docker** - 컨테이너화
- **Docker Compose** - 멀티 컨테이너 관리

## 🚀 빠른 시작

### 📁 파일 구조
```
restricted_bus_notice/
├── 📄 README.md                 # 프로젝트 문서
├── 🐍 api_main.py              # FastAPI 메인 서버
├── 🐍 restricted_bus.py        # 크롤러 및 데이터 처리
├── 🐍 position_checker.py      # 위치 기반 조회
├── 🐍 api_client.py           # API 사용 예시
├── 🐍 env_setup.py            # 환경변수 설정 도우미
├── 🔧 hwpx2pdf.py             # HWP 변환 유틸리티
├── 🔧 extract_image.py        # PDF 이미지 추출
├── ⚙️ setup_windows.bat        # Windows 자동 설정
├── 📦 requirements.txt         # Python 의존성
├── 🐳 Dockerfile              # Docker 이미지 설정
├── 🐳 docker-compose.yml      # Docker Compose 설정
├── 🌍 .env.example            # 환경변수 템플릿
├── 💾 topis_cache.json        # 데이터 캐시
└── 📁 topis_attachments/      # 첨부파일 저장소
    └── 📁 route_images/       # 노선 이미지
```

### 🪟 Windows에서 빠른 설정 (추천)

#### 방법 1: 자동 설정 스크립트 사용 ⭐
```cmd
# 1. 자동 설정 스크립트 실행
setup_windows.bat

# 스크립트가 자동으로:
# - 필요한 폴더 생성
# - .env 파일 생성
# - 패키지 설치
# - 환경변수 테스트
# - API 서버 실행
```

#### 방법 2: 수동 설정
```cmd
# 1. 저장소 클론
git clone <repository-url>
cd restricted_bus_notice

# 2. .env 파일 생성
copy .env.example .env
notepad .env

# 3. .env 파일에서 API 키 수정
# GOOGLE_API_KEY=실제_지미나이_API_키_입력

# 4. 패키지 설치
pip install -r requirements.txt

# 5. 환경변수 테스트
python env_setup.py

# 6. API 서버 실행
python api_main.py
```

### 🐧 Linux/Mac에서 설정

```bash
# 1. 저장소 클론
git clone <repository-url>
cd restricted_bus_notice

# 2. 가상환경 생성 (권장)
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. 패키지 설치
pip install -r requirements.txt

# 4. 환경변수 설정
export GOOGLE_API_KEY="your_gemini_api_key_here"

# 5. API 서버 실행
python api_main.py
```

### 🐳 Docker로 실행

```bash
# Docker Compose 사용 (추천)
docker-compose up -d

# 또는 Docker 직접 실행
docker build -t bus-control-api .
docker run -p 8000:8000 -e GOOGLE_API_KEY=your_api_key bus-control-api
```

## 🔑 환경변수 설정 방법

### Windows 환경변수 설정

#### 1. .env 파일 방법 (가장 쉬움) ⭐⭐⭐
```bash
# .env 파일 생성 (프로젝트 루트에)
GOOGLE_API_KEY=your_actual_gemini_api_key_here
GEMINI_API_KEY=your_actual_gemini_api_key_here
```

#### 2. 명령 프롬프트에서 임시 설정
```cmd
# Command Prompt
set GOOGLE_API_KEY=your_api_key_here
python api_main.py

# PowerShell
$env:GOOGLE_API_KEY="your_api_key_here"
python api_main.py
```

#### 3. 시스템 환경변수로 영구 설정
```
1. Win + R → sysdm.cpl → 엔터
2. 고급 탭 → 환경 변수
3. 새로 만들기 → GOOGLE_API_KEY 입력
4. 값에 실제 API 키 입력
5. 확인 후 재시작
```

### 환경변수 확인
```cmd
# Windows
echo %GOOGLE_API_KEY%

# Python에서 확인
python -c "import os; print('API Key:', os.environ.get('GOOGLE_API_KEY', 'NOT_SET'))"

# 환경변수 테스트 도구 실행
python env_setup.py
```

## 📡 API 문서 및 엔드포인트

### 📖 자동 생성 API 문서
서버 실행 후 브라우저에서 확인:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### 🔗 주요 엔드포인트

#### 기본 정보
```
GET  /                     # 서비스 기본 정보
GET  /health              # 헬스 체크
GET  /stats               # 시스템 통계
```

#### 공지사항
```
GET  /notices                           # 전체 공지사항 목록
GET  /notices?date=2025-08-15          # 특정 날짜 공지사항
GET  /notices/{notice_id}              # 공지사항 상세 조회
```

#### 노선 정보
```
GET  /routes/{route_number}/controls?date=2025-08-15  # 특정 노선 통제 정보
GET  /routes/controls?date=2025-08-15                 # 전체 노선 통제 정보
```

#### 위치 기반 조회
```
POST /position/controls    # 좌표 기반 통제 정류소 조회
```

#### 정류소 검색
```
GET  /stations/search?name=광화문      # 정류소명으로 검색
GET  /stations/search?ars_id=01118     # ARS ID로 검색
```

#### 관리 기능
```
POST /update               # 수동 데이터 업데이트
GET  /export/csv          # CSV 파일 내보내기
```

## 💻 API 사용 예시

### Python으로 API 호출
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

# 위치 기반 조회 (광화문 근처)
data = {
    "tm_x": 196769.0,
    "tm_y": 451475.0,
    "radius": 500,
    "target_date": "2025-08-15"
}
response = requests.post("http://localhost:8000/position/controls", json=data)
result = response.json()
```

### 클라이언트 예시 코드 실행
```python
# 포함된 클라이언트 코드 실행
python api_client.py

# 다양한 API 사용 예시를 실제로 확인할 수 있습니다
```

### curl로 API 호출
```bash
# 공지사항 조회
curl "http://localhost:8000/notices?date=2025-08-15"

# 노선 통제 정보
curl "http://localhost:8000/routes/406/controls?date=2025-08-15"

# 정류소 검색
curl "http://localhost:8000/stations/search?name=광화문"
```

## 📊 데이터 구조

### 공지사항 정보 예시
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

### 통제 정보 분류
- **통제 유형**: 우회, 폐쇄, 미정차, 단축운행 등
- **통제 범위**: 특정노선, 전체통제
- **시간 정보**: 정확한 시작/종료 시간
- **공간 정보**: 정류소, 노선, 우회경로

## 🎯 고급 기능

### 🔄 자동 업데이트
- 백그라운드에서 자동으로 새로운 공지사항 수집
- 캐시 시스템으로 중복 처리 방지
- 30일 이상된 데이터 자동 정리

### 🌍 위치 기반 서비스
- TM 좌표계 지원
- 반경 기반 정류소 검색
- 통제 정류소 실시간 매칭

### 📄 문서 처리
- PDF에서 이미지 자동 추출
- HWP → PDF 자동 변환 (Windows)
- 노선별 상세 정보 이미지 생성

### 📈 모니터링 & 통계
- 시스템 상태 모니터링
- 통제 유형별 통계
- 캐시 사용량 및 성능 지표

## 🔧 설정 및 커스터마이징

### 캐시 설정
```python
# topis_cache.json 파일로 캐시 관리
# 30일 이상된 데이터 자동 정리
# 메모리 사용량 최적화
```

### 파일 관리
```python
# 첨부파일 자동 정리 (30개 제한)
# PDF 이미지 자동 생성
# 임시 파일 자동 삭제
```

### API 설정
```python
# CORS 설정 커스터마이징
# 요청 제한 및 타임아웃 설정
# 로그 레벨 조정
```

## 🐛 문제 해결

### 자주 발생하는 문제

#### 1. Gemini API 키 오류
```
❌ 오류: Gemini API Key가 설정되지 않았습니다.
✅ 해결: .env 파일에 GOOGLE_API_KEY 설정
```

#### 2. HWP 변환 실패 (Windows)
```
❌ 오류: HWP 변환 모듈을 찾을 수 없습니다.
✅ 해결: 한글 2010 이상 설치 또는 PDF 파일 직접 사용
```

#### 3. 포트 충돌
```
❌ 오류: Port 8000이 이미 사용 중
✅ 해결: uvicorn api_main:app --port 8001
```

#### 4. 권한 오류 (Windows)
```
❌ 오류: 파일 쓰기 권한 없음
✅ 해결: 관리자 권한으로 cmd 실행
```

### 디버깅 도구
```python
# 환경변수 확인
python env_setup.py

# API 상태 확인
curl http://localhost:8000/health

# 로그 확인
python api_main.py --log-level DEBUG
```

## 🚀 배포 가이드

### 로컬 개발 환경
```bash
uvicorn api_main:app --reload --host 0.0.0.0 --port 8000
```

### 프로덕션 배포
```bash
# Gunicorn 사용
gunicorn api_main:app -w 4 -k uvicorn.workers.UvicornWorker

# Docker 프로덕션
docker-compose -f docker-compose.prod.yml up -d
```

### 클라우드 배포
- **AWS**: ECS, Lambda
- **Google Cloud**: Cloud Run, App Engine  
- **Azure**: Container Instances, App Service
- **Heroku**: 직접 배포 지원

## 🤝 기여하기

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

### 개발 가이드라인
- 코드 스타일: PEP 8 준수
- 테스트 코드 작성 권장
- API 문서 업데이트 필수
- 변경사항은 CHANGELOG.md에 기록

## 📄 라이선스

이 프로젝트는 MIT 라이선스 하에 배포됩니다. 자세한 내용은 `LICENSE` 파일을 참고하세요.

## 🙋‍♂️ 지원 및 문의

- **GitHub Issues**: 버그 리포트 및 기능 요청
- **GitHub Discussions**: 질문 및 토론
- **Wiki**: 상세한 사용법 및 튜토리얼

## ⚠️ 주의사항

- **API 키 보안**: Gemini API 키를 공개 저장소에 올리지 마세요
- **서울시 정책**: TOPIS 시스템 정책 변경에 따라 일부 기능이 영향받을 수 있습니다
- **Windows 전용**: HWP 변환 기능은 Windows + 한글 프로그램이 필요합니다
- **API 제한**: Gemini API 사용량 제한을 고려하여 사용하세요

---

## 🎉 시작하기

지금 바로 시작해보세요! Windows 사용자라면:

```cmd
setup_windows.bat
```

한 번의 실행으로 모든 설정이 완료됩니다! 🚀

**API 문서**: http://localhost:8000/docs 에서 실시간으로 API를 테스트해보세요! 📊
