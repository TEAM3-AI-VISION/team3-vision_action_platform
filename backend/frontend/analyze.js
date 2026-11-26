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

    // 4. Django가 돌려준 JSON 응답 확인하기
    const data = await response.json();
    console.log('Django 응답:', data);
    resultDiv.textContent = JSON.stringify(data);
});
