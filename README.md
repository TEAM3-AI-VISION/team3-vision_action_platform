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
| 8 | Django ↔ FastAPI HTTP 연결 | B1 + C2 | ✅ 완료 (PR #11) |
| 9 | 저장된 결과를 JavaScript로 표시 | B1 + B2 + A2 | 🔶 진행 중 (조회 API·URL 완료, A2 화면 표시 남음) |
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

## STEP 6·7: 17동작 추론 + FastAPI

상태: ✅ 완료 (C1 + C2)

### 요약

- `ai/inference.py` 한 파일이 MP4 하나를 받아 **사람별로 17동작 중 하나와 신뢰도**를 돌려준다. Django 없이 혼자 실행할 수 있다.
- `ai/main.py`의 `POST /predict/video`는 받은 영상을 임시 파일로 저장하고 `inference.predict_video()` 결과를 그대로 돌려준다.

```
MP4
 → YOLO11m-pose + ByteTrack: Frame마다 사람별 관절 17개 (사람 번호를 끝까지 유지)
 → 관절 → 값 40개 (몸 크기로 나눈 좌표 34 + 팔꿈치·무릎 각도 4 + 엉덩이 화면 위치 2)
 → 8초 창(4초씩 이동)을 30칸으로 맞추고 변화량·통계·움직임 요약을 더해 2,646개
 → ExtraTrees 확률과 1D-CNN 확률의 평균 → 창이 여러 개면 다시 평균
 → 사람별 1등 · top3 · uncertain(0.3 미만)
```

### 실행

```bash
cd ai
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python inference.py data/sample.mp4   # FastAPI 없이 바로 확인
uvicorn main:app --port 8001          # FastAPI (http://localhost:8001/docs)
```

- 모델 파일은 `ai/models/`에 들어 있어 따로 받을 필요가 없다.

### 응답

| 이름 | 뜻 |
|---|---|
| `predictions` | 판단한 사람마다 1개 (`track_id` 순서) |
| `action_id` / `action_name_ko` | 동작 번호 / 한글 이름 (예: `A001` 키오스크 두드리기) |
| `confidence` | 1등 동작의 확률 (0~1) |
| `uncertain` | `confidence`가 0.3보다 낮으면 `true` |
| `top3` | 확률이 높은 동작 3개 |
| `message` | 정상이면 `""`, 사람을 못 찾았거나 판단을 못 하면 그 이유 |
| `processing_seconds` | 처리에 걸린 시간(초). 7초 영상이 GPU 약 5초, CPU 약 32초 |

| 요청 | 응답 |
|---|---|
| MP4 | `200` |
| MP4가 아닌 파일 | `400` |
| 읽을 수 없는 영상 | `422` |

### 왜 이렇게 했나

- **몸 크기로 나눈 좌표**: 카메라와의 거리가 달라도 같은 동작이면 비슷한 값이 나오게 했다.
- **8초 창**: 학습 영상 길이(중간값 5.4초)를 덮는 길이다. 긴 영상은 4초씩 밀며 여러 창으로 보고 평균한다.
- **두 모델 평균**: ExtraTrees는 자세 모양을, 1D-CNN은 시간에 따른 움직임을 잘 본다. 둘을 평균한 것이 하나만 쓸 때보다 정확했다.
- **카메라 각도를 바꿔 가며 학습**: 실제 매장 카메라 구도가 학습 영상과 달라서, 관절 좌표를 돌려 다른 각도에서 찍은 것처럼 만들어 함께 학습했다.

### 모델 결과

| 평가 | 결과 |
|---|---|
| 학습에 안 쓴 사람(S05) 테스트 | 93.5% |
| 학습에 안 쓴 현장 영상 (다른 카메라 구도) | 15개 중 15개 |

### 처리 시간 (7초 영상)

| 컴퓨터 | 첫 요청 | 두 번째부터 |
|---|---|---|
| GPU | 약 7.5초 | 약 5초 |
| CPU만 | 약 32초 | 약 32초 |

- 첫 요청은 모델을 읽느라 조금 더 걸린다.
- CPU만 있으면 영상 길이의 4~5배가 걸린다. Django 타임아웃이 120초라서 CPU에서는 약 25초가 넘는 영상이 `FAILED`가 될 수 있다. 시연은 GPU 컴퓨터에서 한다.

## STEP 8: Django ↔ FastAPI 연결

상태: ✅ 완료 (B1 + C2)

### 요약

- `views.analyze`가 기록을 저장한 뒤, 저장한 영상을 `requests.post()`로 FastAPI `POST /predict/video`에 보낸다.
- FastAPI가 돌려준 사람별 결과 중 **신뢰도 1등**의 `action_id`와 `confidence`를 `InferenceRecord`에 저장하고 상태를 `COMPLETED`로 바꾼다.
- FastAPI 연결 실패·시간 초과(120초)·오류 응답·사람 없음이면 `FAILED`로 저장하고 예측값은 비워 둔다.

```
analyze.js fetch POST
 → views.analyze
 → InferenceRecord 저장 (RECEIVED)
 → requests.post(FASTAPI_URL + '/predict/video', files={'video': ...}, timeout=120)
 → FastAPI: YOLO Pose → 모델 추론 → 사람별 결과 JSON
 → 신뢰도 1등 → predicted_label·confidence 저장 (COMPLETED) / 실패 시 FAILED
 → {"id", "status", "filename", "size", "predicted_label", "confidence", "message"}
```

### Django ↔ FastAPI 약속

| 항목 | 값 |
|---|---|
| FastAPI 주소 | `http://localhost:8001` (`backend/.env`의 `FASTAPI_URL`) |
| 엔드포인트 | `POST /predict/video` (`ai/main.py`) |
| 파일 필드 이름 | `video` (`.mp4`만) |
| 저장할 결과 | 신뢰도가 가장 높은 사람 1명 (`predictions`는 `track_id` 순서라 `[0]`이 1등이 아님) |
| `predicted_label` | `action_id` (예: `A005`) |
| 상태 | `RECEIVED` → `COMPLETED` / `FAILED` |
| 타임아웃 | 120초 |

### 성공·실패 판단

| FastAPI 결과 | 상태 | 예측값 |
|---|---|---|
| 200 + `predictions` 1개 이상 | `COMPLETED` | 1등의 `action_id`, `confidence` |
| 200 + `predictions` 빈 리스트 (사람 없음·판단 불가) | `FAILED` | 비움 |
| 400 / 422 / 500 | `FAILED` | 비움 |
| 연결 실패·120초 초과 | `FAILED` (Django 응답 `502`) | 비움 |

### 실행 (터미널 2개)

```bash
# 1) FastAPI (포트 8001)
cd ai
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8001
```

```bash
# 2) Django (포트 8000)
cd backend
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

- `backend/.env`에 `FASTAPI_URL=http://localhost:8001`을 추가한다. (`.env.example` 참고)
- 추론은 동기 방식이라 영상이 길면 응답이 오래 걸린다. 테스트는 10~15초 이하 영상으로 한다.

### 검증 결과

| 테스트 | 결과 |
|---|---|
| 정상 영상 (A005 밀치기, 3.3초) | `200`, `COMPLETED`, `predicted_label=A005`, `confidence=0.9576` |
| 같은 영상을 FastAPI에 직접 요청 | `A005`, `0.9576`로 DB 값과 동일 |
| 사람 없는 영상 (회색 화면 5초) | `200`, `FAILED`, 예측값 없음, `message: 영상에서 사람을 찾지 못했습니다.` |
| FastAPI에 연결 불가 | `502`, `FAILED`, Django는 계속 동작 |

### STEP 8에서 바뀐 부분

STEP 5까지의 내용 중 STEP 8에서 코드가 바뀐 부분이다.

| 항목 | STEP 5 | STEP 8 |
|---|---|---|
| `status` 값 | `RECEIVED` | `RECEIVED`, `COMPLETED`, `FAILED` (마이그레이션 `0002`) |
| `predicted_label`, `confidence` | 항상 빈 값 | 성공 시 FastAPI가 준 실제 값 |
| `/api/v1/analyze/` 응답 | `{"id", "status", "filename", "size"}` | + `predicted_label`, `confidence`, `message` (연결 실패 시 `502`와 `error`) |
| `requirements.txt` | Django, DRF, psycopg, python-dotenv | + `requests` |
| `.env` | DB 접속 정보 | + `FASTAPI_URL` |

## STEP 9: 저장된 결과를 다시 조회하기

상태: 🔶 조회 API(B2)와 URL 연결(B1) 완료, 화면 표시(A2)는 진행 중이다.

### 요약

- `GET /api/v1/analyze/{id}/`는 PostgreSQL에 저장된 분석 기록 1건을 다시 꺼내 돌려준다.
- POST 응답은 방금 계산한 값이고, GET 응답은 DB에 저장된 값이다. 두 값이 같으면 저장이 제대로 됐다는 뜻이다.
- B2: `views.get_result()`가 `get_object_or_404()`로 기록을 조회한다. 읽기만 하고 저장하지 않는다.
- B1: `analyses/api_urls.py`에 `analyze/<int:pk>/`를 추가했다. `project/urls.py`는 바꾸지 않았다.

### 흐름

```
GET /api/v1/analyze/3/
 → project/urls.py  path('api/v1/', include('analyses.api_urls'))
 → analyses/api_urls.py  path('analyze/<int:pk>/', views.get_result)   # 숫자 3 → pk=3
 → views.get_result(request, pk=3)
 → get_object_or_404(InferenceRecord, pk=3)   # SELECT ... WHERE id = 3
 → {"id": 3, "status": ..., "predicted_label": ..., "confidence": ...}
```

### 응답 명세

| 경우 | HTTP | 응답 |
|---|---|---|
| 기록 있음 | `200` | `{"id", "status", "predicted_label", "confidence"}` |
| 없는 id | `404` | `{"detail": "..."}` |
| 숫자가 아닌 id (`/analyze/abc/`) | `404` | URL이 맞지 않음 (`<int:pk>`) |
| GET이 아닌 요청 | `405` | `{"detail": "..."}` |

`predicted_label`, `confidence`는 `COMPLETED`가 아니면 `null`이다.

### 검증 결과

| 테스트 | 결과 |
|---|---|
| 있는 기록 조회 (`COMPLETED`) | `200`, `{"id": 3, "status": "COMPLETED", "predicted_label": "A005", "confidence": 0.9576}` |
| 있는 기록 조회 (`FAILED`) | `200`, `predicted_label`·`confidence`가 `null` |
| 없는 id (`99999`) | `404` |
| 숫자가 아닌 id (`abc`) | `404` |
| 실제 영상 POST 후 그 `id`로 GET | POST와 GET 모두 `A005`, `0.9576` |
| GET 결과와 DB 테이블(`analyses_inferencerecord`) 비교 | 값이 같다 |
