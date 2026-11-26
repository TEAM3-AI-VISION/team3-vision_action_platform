from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import InferenceRecord


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
    """업로드된 영상을 받아 DB에 기록(InferenceRecord)을 저장하고 결과를 돌려준다.

    STEP 4: 영상 도착 확인 (파일명·크기)
    STEP 5: 업로드 기록을 DB에 저장 (상태 RECEIVED)
    이 단계에서는 FastAPI 호출(STEP 8)을 하지 않는다.
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

    # STEP 5: ORM으로 DB에 1행 저장한다.
    # models.py 에 모델을 만든 것만으로는 저장되지 않는다. 여기서 create()를 실행해야 저장된다.
    # 영상 파일은 MEDIA_ROOT/videos/ 에 저장되고, DB에는 파일 경로가 들어간다.
    # predicted_label·confidence 는 AI 결과가 없으므로 비워 둔다. (STEP 8에서 채움)
    record = InferenceRecord.objects.create(
        video_file=video,
        status=InferenceRecord.Status.RECEIVED,
    )

    # STEP 4 통과 기준: 파일명과 크기를 확인할 수 있다.
    # STEP 5: 저장된 기록 번호(id)와 상태도 함께 돌려준다. (STEP 9에서 id로 결과를 조회)
    return Response({
        'id': record.id,
        'status': record.status,
        'filename': video.name,
        'size': video.size,
    })
