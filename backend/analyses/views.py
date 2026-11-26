from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


# STEP 4: ensure_csrf_cookie
# 화면을 열 때 Django가 브라우저 쿠키에 csrftoken 을 꼭 발급하도록 한다.
# analyze.js 는 이 쿠키 값을 읽어서 X-CSRFToken 헤더로 보낸다.
@ensure_csrf_cookie
def index(request):
    """/analyses/ 에 접속하면 frontend/index.html 화면을 돌려준다."""
    # 'index.html' 은 settings.py TEMPLATES 의 DIRS(frontend/) 에서 찾는다.
    return render(request, 'index.html')


# STEP 4: 영상 업로드 API
# 호출하는 쪽: frontend/analyze.js 의 fetch('/api/v1/analyze/', { method: 'POST' })
# 주소 연결:   project/urls.py → analyses/api_urls.py → 이 함수
# 참고: 로그인 기능이 없는 지금은 DRF가 CSRF 토큰을 검사하지 않는다. (README 참고)
@api_view(['POST'])
def analyze(request):
    """업로드된 영상이 Django에 도착했는지 확인하고 파일명과 크기를 돌려준다.

    이 단계에서는 DB 저장(STEP 5)과 FastAPI 호출(STEP 8)을 하지 않는다.
    """
    # JS의 formData.append('video', file) 에서 붙인 이름 'video' 로 파일을 꺼낸다.
    video = request.FILES.get('video')

    if video is None:
        return Response(
            {'error': "영상 파일이 없습니다. 'video' 필드로 파일을 보내 주세요."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 서버 터미널에서도 도착 여부를 확인할 수 있게 출력한다.
    print(f'[STEP 4] 영상 도착: {video.name} / {video.size} bytes')

    # STEP 4 통과 기준: 파일명과 크기를 확인할 수 있다.
    return Response({
        'filename': video.name,
        'size': video.size,
    })
