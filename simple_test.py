"""
간단한 노선 이미지 테스트
"""

import requests
import os

def test_api_server():
    """API 서버 연결 테스트"""
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        if response.status_code == 200:
            print("✅ API 서버 연결 성공")
            result = response.json()
            print(f"   상태: {result['status']}")
            return True
        else:
            print(f"❌ API 서버 응답 오류: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ API 서버에 연결할 수 없습니다.")
        print("💡 먼저 API 서버를 실행하세요: python api_main.py")
        return False
    except Exception as e:
        print(f"❌ 연결 테스트 오류: {e}")
        return False

def test_route_image():
    """노선 이미지 직접 테스트"""
    print("\n🖼️ === 노선 이미지 테스트 ===")
    
    route = "9401"
    date = "2025-08-15"
    
    # 1단계: 이미지 정보 조회 (자동 생성 포함)
    print(f"1️⃣ 노선 {route} 이미지 정보 조회...")
    try:
        response = requests.get(f"http://localhost:8000/routes/{route}/image?date={date}&download=true")
        
        if response.status_code == 200:
            result = response.json()
            print(f"   응답: {result}")
            
            if result.get('success'):
                data = result['data']
                print(f"   ✅ 성공!")
                print(f"   📄 공지제목: {data.get('notice_title', 'N/A')}")
                print(f"   🖼️ 이미지 존재: {data.get('image_exists', False)}")
                
                if data.get('image_exists'):
                    print(f"   📁 서버 경로: {data.get('image_path', 'N/A')}")
                    
                    # 2단계: 이미지 파일 다운로드
                    print(f"2️⃣ 노선 {route} 이미지 파일 다운로드...")
                    file_response = requests.get(f"http://localhost:8000/routes/{route}/image/file?date={date}")
                    
                    if file_response.status_code == 200:
                        filename = f"route_{route}_{date}.png"
                        with open(filename, 'wb') as f:
                            f.write(file_response.content)
                        
                        if os.path.exists(filename):
                            file_size = os.path.getsize(filename)
                            print(f"   ✅ 다운로드 성공: {filename} ({file_size} bytes)")
                            return True
                        else:
                            print(f"   ❌ 파일이 생성되지 않았습니다.")
                    else:
                        print(f"   ❌ 파일 다운로드 실패: {file_response.status_code}")
                        print(f"   오류: {file_response.text}")
                else:
                    print(f"   ⚠️ 노선 {route}의 이미지가 없습니다.")
                    print(f"   💡 해당 날짜에 통제 정보가 있는지 확인하세요.")
            else:
                print(f"   ❌ 실패: {result.get('message', 'Unknown error')}")
        else:
            print(f"   ❌ API 호출 실패: {response.status_code}")
            print(f"   오류: {response.text}")
    
    except Exception as e:
        print(f"   ❌ 오류: {e}")
    
    return False

def test_notices():
    """공지사항 테스트"""
    print("\n📋 === 공지사항 테스트 ===")
    
    try:
        # 전체 공지사항
        response = requests.get("http://localhost:8000/notices")
        if response.status_code == 200:
            notices = response.json()
            print(f"1️⃣ 전체 공지사항: {len(notices)}개")
            
            if notices:
                latest = notices[0]
                print(f"   최신: {latest.get('title', 'N/A')}")
        
        # 특정 날짜 공지사항
        response = requests.get("http://localhost:8000/notices?date=2025-08-15")
        if response.status_code == 200:
            date_notices = response.json()
            print(f"2️⃣ 2025-08-15 공지사항: {len(date_notices)}개")
            
            for i, notice in enumerate(date_notices[:2], 1):
                print(f"   {i}. {notice.get('title', 'N/A')}")
                print(f"      통제유형: {notice.get('control_type', 'N/A')}")
        
        return True
        
    except Exception as e:
        print(f"❌ 공지사항 테스트 오류: {e}")
        return False

def main():
    """메인 테스트"""
    print("🧪 === 간단한 API 테스트 ===")
    
    # 1. 서버 연결 확인
    if not test_api_server():
        return
    
    # 2. 공지사항 테스트
    if test_notices():
        print("✅ 공지사항 테스트 성공")
    
    # 3. 노선 이미지 테스트
    if test_route_image():
        print("✅ 노선 이미지 테스트 성공")
    else:
        print("❌ 노선 이미지 테스트 실패")
    
    print("\n🎉 === 테스트 완료 ===")

if __name__ == "__main__":
    main()