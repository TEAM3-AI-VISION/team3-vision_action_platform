# team3-vision_action_platform

## 진행 상황

기능을 아래 STEP 순서로 하나씩 끝까지 연결해 보고, 확인되면 다음 단계로 넘어간다.

| STEP | 내용 | 담당 | 상태 |
|---|---|---|---|
| 1 | HTML에 AI 분석 버튼 만들기 | A1 | ✅ 완료 (PR #1) |
| 2 | JavaScript에서 submit 이벤트 받기 | A2 | ✅ 완료 (PR #1) |
| 3 | Django에서 `/analyses/` 화면 열기 | B1 + A1 | ✅ 완료 (PR #2, #3) |
| 4 | JavaScript → Django API로 영상 전송 | A2 + B1 | ✅ 완료 (`hu` 브랜치 PR) |
| 5 | Django → DB에 업로드 기록 저장 (`InferenceRecord`) | B2 + B1 | ⬜ 다음 단계 |
| 6 | 17동작 모델로 실제 영상 추론 | C1 | 🔄 진행 중 (`hyesung` 브랜치) |
| 7 | FastAPI에서 추론 결과 반환 | C2 + C1 | ⬜ |
| 8 | Django ↔ FastAPI HTTP 연결 | B1 + C2 | ⬜ |
| 9 | 저장된 결과를 JavaScript로 표시 | B1 + B2 + A2 | ⬜ |
| 10 | 기능 파이프라인 추적·정리 | 전원 | ⬜ |

### STEP 4 요약

- A2: `analyze.js`가 영상을 `FormData`의 `video` 필드에 담아 `POST /api/v1/analyze/`로 보낸다. CSRF 토큰은 `X-CSRFToken` 헤더로 보낸다.
- B1: DRF 설치, `analyses/api_urls.py` 추가, `views.analyze`가 파일명과 크기를 JSON으로 돌려준다.
- 응답 예: `{"filename": "sample.mp4", "size": 204800}`. 영상이 없으면 `400`.
- DB 저장(STEP 5)과 FastAPI 호출(STEP 8)은 아직 하지 않는다.
- CSRF 처리 방침은 아래 "CSRF 처리 (STEP 4)" 참고.

## Backend (Django) 실행

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py runserver
```

- 분석 화면: http://localhost:8000/analyses/
- 영상 업로드 API: `POST http://localhost:8000/api/v1/analyze/` (파일 필드 이름: `video`)

## CSRF 처리 (STEP 4)

STEP 4에서 `analyze.js`는 Django가 발급한 실제 CSRF 토큰을 읽어 보낸다.
백엔드가 토큰을 어떻게 발급·검사할지는 다음과 같이 정했다.

### 1. 토큰 발급: `index` 뷰에 `@ensure_csrf_cookie`

- `index.html`에 `{% csrf_token %}`이 없으면 Django는 `csrftoken` 쿠키를 발급하지 않는다.
  그러면 `analyze.js`가 빈 토큰을 보내게 된다.
- A1의 HTML은 건드리지 않고, `backend/analyses/views.py`의 `index` 뷰에서 쿠키를 발급한다.
- `analyze.js`는 이 쿠키를 읽어 `X-CSRFToken` 헤더로 보낸다.
- **대안:** A1이 `<form>` 안에 `{% csrf_token %}`을 넣어도 된다.
  `analyze.js`는 `csrfmiddlewaretoken` 입력칸을 먼저 찾도록 되어 있어 그대로 동작한다.
  이 방식으로 바꾸면 `@ensure_csrf_cookie`는 빼도 된다.

### 2. 토큰 검사: 지금은 하지 않음

- DRF의 `@api_view`는 **로그인하지 않은 요청에는 CSRF 토큰을 검사하지 않는다.**
  그래서 현재는 토큰 없이 POST 해도 요청이 성공한다.
- 한 STEP에 다른 단계의 기능을 미리 넣지 않는다는 원칙에 따라 검사를 따로 켜지 않았다.
- 로그인 기능이 생기면 DRF의 `SessionAuthentication`이 **로그인한 사용자의 요청부터 자동으로 CSRF를 검사**한다.
  그때부터 토큰이 없거나 틀리면 `403 Forbidden`이 된다.
- 로그인 전에도 검사가 필요하면 `@api_view(['POST'])` **아래(안쪽)**에 `@csrf_protect`를 붙인다.
  순서를 반대로 하면 검사가 꺼진 채로 남는다.
