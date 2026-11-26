from django.db import models


class InferenceRecord(models.Model):
    """STEP 5: 업로드한 영상 1개에 대한 분석 기록 (DB 테이블 1행)

    id 필드는 Django가 자동으로 만든다. (기록 번호)
    """

    # 처리 상태 값 목록
    # STEP 5: RECEIVED(업로드 받음)
    # STEP 8: FastAPI 추론 결과에 따라 COMPLETED(분석 완료) 또는 FAILED(분석 실패)
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', '업로드 받음'
        COMPLETED = 'COMPLETED', '분석 완료'
        FAILED = 'FAILED', '분석 실패'

    # 저장한 영상 파일 경로 (실제 파일은 MEDIA_ROOT/videos/ 에 저장된다)
    video_file = models.FileField(upload_to='videos/')

    # 처리 상태
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )

    # AI가 반환한 예측 동작 (STEP 8: 신뢰도 1등 사람의 action_id, 예: "A005")
    # 업로드 직후·분석 실패 시에는 비어 있다. (임의 값 금지)
    predicted_label = models.CharField(max_length=100, null=True, blank=True)

    # AI가 반환한 신뢰도 0~1 (STEP 8: 신뢰도 1등 사람의 confidence)
    # 업로드 직후·분석 실패 시에는 비어 있다. (임의 값 금지)
    confidence = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f'#{self.id} {self.video_file.name} ({self.status})'
