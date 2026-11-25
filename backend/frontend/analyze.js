const form = document.getElementById('analysis-form');
const videoInput = document.getElementById('video-input');

form.addEventListener('submit', function (event) {

    event.preventDefault();

    
    console.log('AI 분석 버튼이 눌렸습니다!');

    
    const file = videoInput.files[0];   
    if (!file) {
        console.log('선택한 영상이 없습니다.');
        return;
    }
    console.log('선택한 영상:', file.name, '/', file.size, 'bytes');

    
});
