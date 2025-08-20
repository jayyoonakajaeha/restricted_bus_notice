"""
서울 버스 통제 알림 API 클라이언트 (수정된 버전)
"""

import requests
import json
from datetime import datetime
from typing import List, Dict, Optional

class BusControlAPIClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
    
    def _request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """API 요청 헬퍼 메서드"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"API 요청 실패: {e}")
            raise
    
    def get_health(self) -> Dict:
        """헬스 체크"""
        return self._request("GET", "/health")
    
    def get_service_info(self) -> Dict:
        """서비스 기본 정보"""
        return self._request("GET", "/")
    
    def get_notices(self, date: Optional[str] = None) -> List[Dict]:
        """공지사항 목록 조회"""
        params = {}
        if date:
            params['date'] = date
        
        return self._request("GET", "/notices", params=params)
    
    def get_notice_detail(self, notice_id: str) -> Dict:
        """특정 공지사항 상세 조회"""
        return self._request("GET", f"/notices/{notice_id}")
    
    def get_route_controls(self, route_number: str, date: str) -> List[Dict]:
        """특정 노선의 통제 정보 조회"""
        params = {'date': date}
        return self._request("GET", f"/routes/{route_number}/controls", params=params)
    
    def get_all_route_controls(self, date: str) -> Dict:
        """특정 날짜의 모든 노선 통제 정보"""
        params = {'date': date}
        return self._request("GET", "/routes/controls", params=params)
    
    def get_position_controls(self, tm_x: float, tm_y: float, 
                            radius: int = 500, target_date: Optional[str] = None) -> Dict:
        """위치 기반 통제 정류소 조회"""
        data = {
            "tm_x": tm_x,
            "tm_y": tm_y,
            "radius": radius
        }
        if target_date:
            data["target_date"] = target_date
        
        return self._request("POST", "/position/controls", json=data)
    
    def search_stations(self, name: Optional[str] = None, 
                       ars_id: Optional[str] = None) -> Dict:
        """정류소 검색"""
        params = {}
        if name:
            params['name'] = name
        if ars_id:
            params['ars_id'] = ars_id
        
        return self._request("GET", "/stations/search", params=params)
    
    def get_route_image_info(self, route_number: str, date: str, download: bool = False) -> Dict:
        """노선 이미지 정보 조회 (JSON 응답)"""
        params = {'date': date, 'download': download}
        return self._request("GET", f"/routes/{route_number}/image", params=params)
    
    def download_route_image_file(self, route_number: str, date: str, save_path: str = None) -> str:
        """노선 이미지 파일 직접 다운로드 (PNG 파일)"""
        params = {'date': date}
        
        try:
            response = self.session.get(f"{self.base_url}/routes/{route_number}/image/file", params=params)
            response.raise_for_status()
            
            if not save_path:
                save_path = f"route_{route_number}_{date}.png"
            
            with open(save_path, 'wb') as f:
                f.write(response.content)
            
            print(f"✅ 노선 {route_number} 이미지 저장: {save_path}")
            return save_path
            
        except requests.exceptions.RequestException as e:
            print(f"❌ 이미지 다운로드 실패: {e}")
            raise
    
    def get_route_image_complete(self, route_number: str, date: str, save_path: str = None) -> Dict:
        """노선 이미지 완전 처리 (정보 조회 + 파일 다운로드)"""
        
        # 1단계: 이미지 정보 조회 (자동 생성 포함)
        print(f"🔍 노선 {route_number} 이미지 정보 조회 중...")
        image_info = self.get_route_image_info(route_number, date, download=True)
        
        if not image_info.get('success'):
            return image_info
        
        data = image_info['data']
        
        # 2단계: 이미지가 존재하면 파일 다운로드
        if data.get('image_exists'):
            print(f"📥 노선 {route_number} 이미지 파일 다운로드 중...")
            try:
                local_path = self.download_route_image_file(route_number, date, save_path)
                data['local_file_path'] = local_path
                print(f"✅ 완료: {local_path}")
            except Exception as e:
                data['download_error'] = str(e)
                print(f"❌ 파일 다운로드 실패: {e}")
        else:
            print(f"⚠️ 노선 {route_number}의 이미지가 존재하지 않습니다.")
        
        return image_info
    
    def list_route_images(self) -> Dict:
        """생성된 모든 노선 이미지 목록 조회"""
        return self._request("GET", "/images/list")
    
    def generate_route_images(self, date: str, routes: Optional[List[str]] = None) -> Dict:
        """특정 날짜의 노선 이미지 일괄 생성"""
        params = {'date': date}
        if routes:
            # 여러 routes 파라미터를 URL에 추가
            route_params = '&'.join([f'routes={route}' for route in routes])
            url = f"/routes/images/generate?{route_params}&date={date}"
            response = self.session.post(f"{self.base_url}{url}")
            response.raise_for_status()
            return response.json()
        else:
            return self._request("POST", "/routes/images/generate", params=params)
    
    def manual_update(self) -> Dict:
        """수동 업데이트 요청"""
        return self._request("POST", "/update")
    
    def get_statistics(self) -> Dict:
        """시스템 통계"""
        return self._request("GET", "/stats")
    
    def export_csv(self, date: Optional[str] = None, save_path: str = "bus_controls.csv"):
        """CSV 내보내기"""
        params = {}
        if date:
            params['date'] = date
        
        response = self.session.get(f"{self.base_url}/export/csv", params=params)
        response.raise_for_status()
        
        with open(save_path, 'wb') as f:
            f.write(response.content)
        
        print(f"CSV 파일이 저장되었습니다: {save_path}")


def test_basic_functionality():
    """기본 기능 테스트"""
    print("🧪 === 기본 기능 테스트 ===\n")
    
    client = BusControlAPIClient()
    
    try:
        # 1. 헬스 체크
        print("1. 헬스 체크")
        health = client.get_health()
        print(f"   상태: {health['status']}")
        print(f"   시간: {health['timestamp']}\n")
        
        # 2. 서비스 정보
        print("2. 서비스 정보")
        info = client.get_service_info()
        print(f"   서비스: {info['service']}")
        print(f"   버전: {info.get('version', 'N/A')}")
        print(f"   캐시된 공지사항: {info['cached_notices']}개")
        print(f"   마지막 업데이트: {info.get('last_update', 'N/A')}\n")
        
        # 3. 특정 날짜 공지사항 조회
        target_date = "2025-08-15"
        print(f"3. {target_date} 공지사항 조회")
        notices = client.get_notices(date=target_date)
        print(f"   발견된 공지사항: {len(notices)}개")
        
        if notices:
            for i, notice in enumerate(notices[:2], 1):  # 처음 2개만 출력
                print(f"   {i}. {notice['title']}")
                print(f"      통제유형: {notice.get('control_type', 'N/A')}")
                print(f"      생성일: {notice.get('create_date', 'N/A')}")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ 기본 기능 테스트 실패: {e}")
        return False


def test_route_image_functionality():
    """노선 이미지 기능 테스트"""
    print("🖼️ === 노선 이미지 기능 테스트 ===\n")
    
    client = BusControlAPIClient()
    route_number = "406"
    target_date = "2025-08-15"
    
    try:
        # 1. 이미지 정보 조회
        print(f"1. 노선 {route_number} 이미지 정보 조회")
        image_info = client.get_route_image_info(route_number, target_date, download=True)
        
        if image_info.get('success'):
            data = image_info['data']
            print(f"   ✅ 성공!")
            print(f"   공지제목: {data.get('notice_title', 'N/A')}")
            print(f"   이미지 존재: {data.get('image_exists', False)}")
            print(f"   페이지 정보: {data.get('page_info', 'N/A')}")
            
            if data.get('image_exists'):
                print(f"   서버 경로: {data.get('image_path', 'N/A')}")
                
                # 2. 이미지 파일 다운로드
                print(f"\n2. 노선 {route_number} 이미지 파일 다운로드")
                local_file = client.download_route_image_file(route_number, target_date)
                
                import os
                if os.path.exists(local_file):
                    file_size = os.path.getsize(local_file)
                    print(f"   ✅ 다운로드 성공: {local_file} ({file_size} bytes)")
                    return True
            else:
                print(f"   ⚠️ 노선 {route_number}의 이미지가 없습니다.")
        else:
            print(f"   ❌ 실패: {image_info.get('message', 'Unknown error')}")
        
    except Exception as e:
        print(f"❌ 노선 이미지 테스트 실패: {e}")
        return False
    
    return False


def main():
    """메인 실행 함수"""
    print("🚌 === 서울 버스 통제 알림 API 클라이언트 ===\n")
    
    # 기본 기능 테스트
    if test_basic_functionality():
        print("✅ 기본 기능 테스트 완료\n")
        
        # 노선 이미지 기능 테스트
        test_route_image_functionality()
    else:
        print("❌ 기본 기능 테스트 실패")
        print("💡 API 서버가 실행 중인지 확인하세요: python api_main.py")


if __name__ == "__main__":
    main()