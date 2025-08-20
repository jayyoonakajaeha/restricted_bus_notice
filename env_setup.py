"""
환경변수 설정 도우미
Windows에서 .env 파일을 자동으로 로드
"""

import os
from pathlib import Path

def load_env_file():
    """
    .env 파일을 찾아서 환경변수로 로드
    """
    # 현재 디렉토리에서 .env 파일 찾기
    env_file = Path(".env")
    
    if not env_file.exists():
        print("⚠️ .env 파일을 찾을 수 없습니다.")
        print("💡 .env.example을 복사해서 .env 파일을 만들어주세요:")
        print("   copy .env.example .env")
        print("   그리고 GOOGLE_API_KEY를 실제 값으로 변경하세요.")
        return False
    
    try:
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    os.environ[key] = value
                    print(f"✅ {key} 환경변수가 설정되었습니다.")
        
        return True
        
    except Exception as e:
        print(f"❌ .env 파일 로드 실패: {e}")
        return False

def check_required_env():
    """
    필수 환경변수가 설정되었는지 확인
    """
    required_keys = ['GOOGLE_API_KEY', 'GEMINI_API_KEY']
    missing_keys = []
    
    for key in required_keys:
        if not os.environ.get(key):
            missing_keys.append(key)
    
    if missing_keys:
        print(f"❌ 필수 환경변수가 누락되었습니다: {', '.join(missing_keys)}")
        print("💡 .env 파일에 다음과 같이 설정하세요:")
        for key in missing_keys:
            print(f"   {key}=your_actual_api_key_here")
        return False
    
    print("✅ 모든 필수 환경변수가 설정되었습니다.")
    return True

def setup_env():
    """
    환경변수 설정 전체 프로세스
    """
    print("🔧 환경변수 설정을 시작합니다...")
    
    # .env 파일 로드
    if load_env_file():
        # 필수 환경변수 확인
        return check_required_env()
    
    return False

if __name__ == "__main__":
    setup_env()