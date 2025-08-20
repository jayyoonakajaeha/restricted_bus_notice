@echo off
chcp 65001 >nul
echo 🚌 서울 버스 통제 알림 API - Windows 설정 스크립트

echo.
echo 📁 필요한 폴더 생성 중...
if not exist "topis_attachments" mkdir topis_attachments
if not exist "topis_attachments\route_images" mkdir topis_attachments\route_images

echo.
echo 📄 .env 파일 확인 중...
if not exist ".env" (
    if exist ".env.example" (
        echo ✅ .env.example을 복사하여 .env 파일을 생성합니다...
        copy .env.example .env >nul
        echo ⚠️  .env 파일에서 GOOGLE_API_KEY를 실제 값으로 변경해주세요!
        echo    파일 위치: %CD%\.env
        pause
        notepad .env
    ) else (
        echo ❌ .env.example 파일이 없습니다.
        echo 💡 다음 내용으로 .env 파일을 생성해주세요:
        echo.
        echo GOOGLE_API_KEY=your_gemini_api_key_here
        echo GEMINI_API_KEY=your_gemini_api_key_here
        echo.
        pause
        exit /b 1
    )
) else (
    echo ✅ .env 파일이 이미 존재합니다.
)

echo.
echo 📦 Python 패키지 설치 중...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo 🧪 환경변수 테스트 중...
python env_setup.py

echo.
echo 🚀 API 서버를 시작하시겠습니까? (y/n)
set /p choice="선택: "
if /i "%choice%"=="y" (
    echo 📡 API 서버 시작 중...
    echo 🌐 브라우저에서 http://localhost:8000/docs 에서 API 문서를 확인하세요
    echo ⏹️  서버를 중지하려면 Ctrl+C를 누르세요
    echo.
    python api_main.py
) else (
    echo.
    echo ✅ 설정이 완료되었습니다!
    echo 💡 API 서버를 시작하려면: python api_main.py
    echo 📖 API 문서 확인: http://localhost:8000/docs
)

pause