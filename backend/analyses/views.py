import requests
from django.conf import settings
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import InferenceRecord

# STEP 8: FastAPI 추론을 기다리는 최대 시간(초). 넘으면 FAILED 처리한다.
FASTAPI_TIMEOUT_SECONDS = 120


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
    """업로드된 영상을 저장하고, FastAPI로 추론한 결과를 DB에 기록해 돌려준다.

    STEP 4: 영상 도착 확인 (파일명·크기)
    STEP 5: 업로드 기록을 DB에 저장 (상태 RECEIVED)
    STEP 8: FastAPI 추론 → 결과 저장 (COMPLETED / FAILED)
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
    record = InferenceRecord.objects.create(
        video_file=video,
        status=InferenceRecord.Status.RECEIVED,
    )

    # STEP 8: 저장한 영상을 FastAPI(AI 서버)로 보내 실제 추론을 요청한다.
    # 받는 쪽: ai/main.py 의 @app.post("/predict/video")
    try:
        with record.video_file.open('rb') as f:
            res = requests.post(
                f'{settings.FASTAPI_URL}/predict/video',
                files={'video': (video.name, f, 'video/mp4')},  # 필드 이름 'video', .mp4 파일명
                timeout=FASTAPI_TIMEOUT_SECONDS,                 # 무한 대기 방지
            )
    except requests.RequestException as e:
        # FastAPI가 꺼져 있거나(연결 실패) 시간 초과 → Django는 죽지 않고 FAILED로 기록한다.
        record.status = InferenceRecord.Status.FAILED
        record.save()
        return Response(
            {
                'id': record.id,
                'status': record.status,
                'filename': video.name,
                'size': video.size,
                'error': f'AI 서버 연결 실패: {e}',
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    # FastAPI 응답(JSON)을 꺼낸다. JSON이 아니면(예: 500 HTML) 빈 dict로 둔다.
    is_json = res.headers.get('content-type', '').startswith('application/json')
    data = res.json() if is_json else {}
    predictions = data.get('predictions') or []

    if res.status_code == 200 and predictions:
        # 성공: 사람별 결과 중 신뢰도 1등을 고른다.
        # predictions 는 track_id(사람 번호) 순서라서 predictions[0]이 1등이 아니다.
        best = max(predictions, key=lambda p: p['confidence'])
        record.predicted_label = best['action_id']   # 예: "A005"
        record.confidence = best['confidence']       # 0~1
        record.status = InferenceRecord.Status.COMPLETED
    else:
        # 실패: 400(mp4 아님)·422(영상 열기 실패)·500, 또는 사람 없음·판단 불가(200 + 빈 리스트)
        # 예측값은 비워 둔다. (임의 값 금지)
        record.status = InferenceRecord.Status.FAILED
    record.save()

    # FastAPI가 준 안내 문구 (사람 없음 등). 오류일 때는 detail 에 들어 있다.
    message = data.get('message') or data.get('detail') or ''

    return Response({
        'id': record.id,
        'status': record.status,
        'filename': video.name,
        'size': video.size,
        'predicted_label': record.predicted_label,
        'confidence': record.confidence,
        'message': message if isinstance(message, str) else str(message),
    })
