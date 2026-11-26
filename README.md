# team3-vision_action_platform

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

가이드 STEP 4의 요구사항은 "`csrfToken`은 Django가 발급한 실제 토큰을 읽어 전달하도록 구현"하는 것이다.
백엔드가 토큰을 어떻게 발급·검사할지는 가이드에 정해져 있지 않아서, 다음과 같이 정했다.

### 1. 토큰 발급: `index` 뷰에 `@ensure_csrf_cookie`

- `index.html`에 `{% csrf_token %}`이 없으면 Django는 `csrftoken` 쿠키를 발급하지 않는다.
  그러면 `analyze.js`가 빈 토큰을 보내게 된다.
- A1의 HTML은 건드리지 않고, `backend/analyses/views.py`의 `index` 뷰에서 쿠키를 발급한다.
- `analyze.js`는 이 쿠키를 읽어 `X-CSRFToken` 헤더로 보낸다.
- **대안:** A1이 `<form>` 안에 `{% csrf_token %}`을 넣어도 된다.
  `analyze.js`는 `csrfmiddlewaretoken` 입력칸을 먼저 찾도록 되어 있어 그대로 동작한다.
  이 방식으로 바꾸면 `@ensure_csrf_cookie`는 빼도 된다.

### 2. 토큰 검사: 지금은 하지 않음 (가이드 범위 유지)

- DRF의 `@api_view`는 **로그인하지 않은 요청에는 CSRF 토큰을 검사하지 않는다.**
  그래서 현재는 토큰 없이 POST 해도 요청이 성공한다.
- 가이드의 "다른 단계의 기능을 미리 추가하지 않는다" 원칙에 따라 검사를 따로 켜지 않았다.
- 로그인 기능이 생기면 DRF의 `SessionAuthentication`이 **로그인한 사용자의 요청부터 자동으로 CSRF를 검사**한다.
  그때부터 토큰이 없거나 틀리면 `403 Forbidden`이 된다.
- 로그인 전에도 검사가 필요하면 `@api_view(['POST'])` **아래(안쪽)**에 `@csrf_protect`를 붙인다.
  순서를 반대로 하면 검사가 꺼진 채로 남는다.
