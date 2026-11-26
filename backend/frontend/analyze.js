const form = document.getElementById('analysis-form');
const videoInput = document.getElementById('video-input');
const resultDiv = document.getElementById('result');

// Django가 브라우저 쿠키에 발급한 실제 csrftoken 값을 읽어오는 함수
function getCookie(name) {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        const [key, value] = cookie.trim().split('=');
        if (key === name) {
            return decodeURIComponent(value);
        }
    }
    return '';
}

// async: 안에서 await(서버 응답 기다리기)를 쓰기 위해 추가
form.addEventListener('submit', async function (event) {
    event.preventDefault();

    console.log('AI 분석 버튼이 눌렸습니다!');

    const file = videoInput.files[0];
    if (!file) {
        console.log('선택한 영상이 없습니다.');
        return;
    }
    console.log('선택한 영상:', file.name, '/', file.size, 'bytes');

    // --- [STEP 4] Django API로 영상 파일 전송 ---

    // 1. 영상 파일을 FormData에 'video'라는 이름으로 담기
    const formData = new FormData();
    formData.append('video', file);

    // 2. Django가 발급한 실제 CSRF 토큰 읽기
    //    (HTML의 {% csrf_token %} 또는 브라우저 쿠키에서 읽음)
    const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');
    const csrfToken = csrfInput ? csrfInput.value : getCookie('csrftoken');

    // 3. Django API로 POST 요청 보내기
    const response = await fetch('/api/v1/analyze/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrfToken,
        },
        body: formData,
    });

    // 4. POST 응답 JSON 꺼내기
    const postData = await response.json();
    console.log('POST 응답:', postData);

    // POST 에러 처리 (예: 400 파일 없음, 502 FastAPI 서버 꺼짐)
    if (!response.ok) {
        resultDiv.textContent = `요청 실패 (${response.status}): ${postData.error || '서버 오류'}`;
        return;
    }

    // --- [STEP 9] 저장된 결과를 기록 ID로 다시 조회(GET)해서 화면에 표시 ---

    // 5. POST 응답에서 DB 기록 번호(id) 꺼내기
    const recordId = postData.id;

    // 6. 기록 번호로 Django에 GET 요청 보내기 (읽기 전용이라 CSRF 토큰 필요 없음)
    const getResponse = await fetch(`/api/v1/analyze/${recordId}/`);
    const record = await getResponse.json();
    console.log('DB에서 다시 가져온 결과:', record);

    // GET 에러 처리 (예: 404 없는 기록)
    if (!getResponse.ok) {
        resultDiv.textContent = `결과 조회 실패 (${getResponse.status}): ${record.detail || '오류'}`;
        return;
    }

    // 7. DB에서 가져온 상태(status)에 따라 화면(#result)에 표시
    if (record.status === 'COMPLETED') {
        const percent = (record.confidence * 100).toFixed(1);
        resultDiv.textContent = `예측 동작: ${record.predicted_label} (신뢰도 ${percent}%)`;
    } else {
        resultDiv.textContent = '분석에 실패했습니다.';
    }
});
