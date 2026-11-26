# team3-vision_action_platform

## 진행 상황

기능을 아래 STEP 순서로 하나씩 끝까지 연결해 보고, 확인되면 다음 단계로 넘어간다.

| STEP | 내용 | 담당 | 상태 |
|---|---|---|---|
| 1 | HTML에 AI 분석 버튼 만들기 | A1 | ✅ 완료 (PR #1) |
| 2 | JavaScript에서 submit 이벤트 받기 | A2 | ✅ 완료 (PR #1) |
| 3 | Django에서 `/analyses/` 화면 열기 | B1 + A1 | ✅ 완료 (PR #2, #3) |
| 4 | JavaScript → Django API로 영상 전송 | A2 + B1 | ✅ 완료 (`hu` 브랜치 PR) |
| 5 | Django → DB에 업로드 기록 저장 (`InferenceRecord`) | B2 + B1 | ✅ 완료 (PR #7) |
| 6 | 17동작 모델로 실제 영상 추론 | C1 | ✅ 완료 (PR #6) |
| 7 | FastAPI에서 추론 결과 반환 | C2 + C1 | ✅ 완료 (PR #9) |
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

## STEP 5: 업로드 기록을 PostgreSQL에 저장

상태: ✅ 완료 (B2 + B1)

### 요약

- `InferenceRecord` 모델을 만들고, `views.analyze`가 영상을 받을 때마다 DB에 1행을 저장한다.
- 영상 파일은 `backend/media/videos/`에 저장되고, DB에는 파일 경로가 들어간다.
- 처리 상태는 `RECEIVED`(업로드 받음)로 저장한다.
- `predicted_label`(예측 동작)과 `confidence`(신뢰도)는 AI 결과가 없으므로 비워 둔다. STEP 8에서 실제 값으로 채운다.
- 응답에 저장된 기록 번호 `id`와 `status`가 추가된다. STEP 9에서 이 `id`로 결과를 조회한다.

```
analyze.js fetch POST
 → views.analyze
 → InferenceRecord.objects.create(video_file=..., status=RECEIVED)
 → PostgreSQL  analyses_inferencerecord 테이블에 1행
 → {"id": 1, "status": "RECEIVED", "filename": "...", "size": ...}
```

### `InferenceRecord` 필드

| 필드 | 타입 | 내용 |
|---|---|---|
| `id` | 자동 증가 정수 | 기록 번호 (Django가 자동 생성) |
| `video_file` | `FileField(upload_to='videos/')` | 저장한 영상 파일 경로 |
| `status` | `CharField` | 처리 상태. 현재 `RECEIVED`만 사용 |
| `predicted_label` | `CharField`, 빈 값 허용 | 예측 동작 (STEP 8에서 저장) |
| `confidence` | `FloatField`, 빈 값 허용 | 신뢰도 (STEP 8에서 저장) |

### PostgreSQL 준비

Django 6.1은 **PostgreSQL 15 이상**이 필요하다. (14에서는 접속 시 `NotSupportedError`)

1. PostgreSQL 15 이상을 설치하고 프로젝트용 사용자·DB를 만든다.

   ```sql
   CREATE ROLE vision_user LOGIN CREATEDB PASSWORD '비밀번호';
   CREATE DATABASE vision_action OWNER vision_user;
   ```

2. `backend/.env.example`을 `backend/.env`로 복사하고 실제 값을 넣는다. `.env`는 git에 올리지 않는다.

   ```
   DB_NAME=vision_action
   DB_USER=vision_user
   DB_PASSWORD=비밀번호
   DB_HOST=localhost
   DB_PORT=5432
   ```

   PostgreSQL 14와 16을 함께 설치한 경우 16은 보통 `5433` 포트를 쓴다. `pg_lsclusters`로 확인한다.

3. 패키지 설치와 테이블 생성

   ```bash
   cd backend
   source venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py runserver
   ```

### 확인 방법

```bash
# 영상 업로드 후 DB 확인
psql -h localhost -p 5432 -U vision_user -d vision_action \
     -c "select id, video_file, status, predicted_label, confidence from analyses_inferencerecord;"
```

| 테스트 | 기대 결과 |
|---|---|
| 영상 업로드 | `200`, 응답에 `id`·`status: RECEIVED`, DB에 1행 추가, `media/videos/`에 파일 저장 |
| 영상 없이 POST | `400`, DB에 저장되지 않음 |
| 저장된 행 | `predicted_label`·`confidence`는 빈 값 |

### STEP 5에서 바뀐 부분

STEP 4까지의 내용 중 STEP 5에서 코드가 바뀐 부분이다.

| 항목 | STEP 4 | STEP 5 |
|---|---|---|
| DB | SQLite (`db.sqlite3`) | PostgreSQL (`.env`에서 접속 정보 읽기) |
| `/api/v1/analyze/` 응답 | `{"filename", "size"}` | `{"id", "status", "filename", "size"}` |
| `requirements.txt` | Django, DRF | + `psycopg[binary]`, `python-dotenv` |
| 실행 준비 | `pip install` 후 바로 `runserver` | `.env` 작성 → `migrate` → `runserver` |
