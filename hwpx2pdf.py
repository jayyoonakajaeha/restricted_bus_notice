import os
import win32com.client as win32
import time

def convert_hwpx_to_pdf_simple(folder_path):
    """
    지정된 폴더 내의 모든 .hwpx 파일을 .pdf 파일로 변환합니다.
    (간소화된 버전)
    """
    if not os.path.isdir(folder_path):
        print(f"❌ 오류: '{folder_path}' 폴더를 찾을 수 없습니다.")
        return False

    success_count = 0
    error_count = 0
    
    # HWP(X) 파일 목록 가져오기
    hwpx_files = [f for f in os.listdir(folder_path) if (f.lower().endswith('.hwpx') or f.lower().endswith('.hwp'))]
    
    if not hwpx_files:
        print("❌ HWP 및 HWPX 파일을 찾을 수 없습니다.")
        return False
        
    print(f"총 {len(hwpx_files)}개의 HWP 및 HWPX 파일을 발견했습니다.")

    for i, filename in enumerate(hwpx_files, 1):
        hwp = None
        try:
            print(f"[{i}/{len(hwpx_files)}] 변환 중: {filename}")
            
            hwpx_path = os.path.join(folder_path, filename)
            pdf_filename = os.path.splitext(filename)[0] + ".pdf"
            pdf_path = os.path.join(folder_path, pdf_filename)
            
            # 각 파일마다 새로운 한글 객체 생성
            print("  한글 프로그램 시작...")
            hwp = win32.Dispatch("HWPFrame.HwpObject")
            hwp.RegisterModule("FilePathCheckDLL", "SecurityModule") # 팝업 없애는 부분. 두번쨰 인자 값이 레지스터 등록 이름
            
            # 한글 창 숨기기
            hwp.XHwpWindows.Item(0).Visible = False
            
            # HWPX 파일 열기
            print(f"  파일 열기: {filename}")
            result = hwp.Open(hwpx_path)
            
            if not result:
                raise Exception("파일을 열 수 없습니다.")
            
            time.sleep(1)  # 파일 로딩 대기
            
            # PDF로 저장 시도
            print("  PDF로 변환 중...")
            try:
                # 방법 1: SaveAs 사용
                hwp.SaveAs(pdf_path, "PDF")
                time.sleep(1)
                
            except Exception as save_error:
                print(f"  SaveAs 실패: {save_error}")
                print("  다른 방법으로 시도...")
                
                try:
                    # 방법 2: HAction 사용
                    act = hwp.CreateAction("FileSaveAsPdf")
                    pset = act.CreateSet()
                    pset.SetItem("filename", pdf_path)
                    pset.SetItem("Format", "PDF")
                    act.Execute(pset)
                    time.sleep(1)
                    
                except Exception as action_error:
                    print(f"  HAction도 실패: {action_error}")
                    
                    # 방법 3: 마지막 시도
                    try:
                        hwp.HAction.GetDefault("FileSaveAsPdf", hwp.HParameterSet.HFileOpenSave.HSet)
                        hwp.HParameterSet.HFileOpenSave.filename = pdf_path
                        hwp.HParameterSet.HFileOpenSave.Format = "PDF"
                        hwp.HAction.Execute("FileSaveAsPdf", hwp.HParameterSet.HFileOpenSave.HSet)
                        time.sleep(1)
                    except:
                        raise Exception("모든 PDF 저장 방법 실패")
            
            # 변환 완료 확인
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                print(f"  ✅ 변환 완료: {pdf_filename}")
                success_count += 1
            else:
                print(f"  ❌ PDF 파일이 제대로 생성되지 않았습니다.")
                error_count += 1

        except Exception as e:
            print(f"  ❌ 변환 실패: {e}")
            error_count += 1
        
        finally:
            # 한글 프로그램 정리
            if hwp:
                try:
                    hwp.Clear(1)  # 문서 닫기
                    hwp.Quit()    # 한글 종료
                    time.sleep(0.5)
                except:
                    pass  # 종료 시 오류 무시
            
            print(f"  한글 프로그램 종료\n")

    print(f"🎉 작업 완료!")
    print(f"  - 성공: {success_count}개")
    print(f"  - 실패: {error_count}개")
    
    return error_count == 0

def main():
    print("=" * 50)
    print("HWPX to PDF 변환기 (간소화 버전)")
    print("=" * 50)
    
    # 폴더 경로 설정
    target_folder = r"C:\Users\613ja\Documents\KT디지털인재장학생\집회알리미\topis_attachments"
    
    # 폴더 존재 확인
    if not os.path.exists(target_folder):
        print(f"❌ 폴더가 존재하지 않습니다: {target_folder}")
        target_folder = input("올바른 폴더 경로를 입력하세요: ").strip()
    
    print(f"대상 폴더: {target_folder}")
    print("변환을 시작합니다...\n")
    
    # 변환 실행
    success = convert_hwpx_to_pdf_simple(target_folder)
    
    if success:
        print("\n🎉 모든 파일이 성공적으로 변환되었습니다!")
    else:
        print("\n⚠️ 일부 파일 변환에 실패했습니다.")
        print("\n문제 해결 방법:")
        print("1. 한글 2010 이상이 설치되어 있는지 확인")
        print("2. 변환할 파일이 다른 프로그램에서 열려있지 않은지 확인")  
        print("3. 스크립트를 관리자 권한으로 실행")
        print("4. 한글 파일에 암호가 걸려있지 않은지 확인")
    

if __name__ == "__main__":
    main()