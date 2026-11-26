from django.db import models


class InferenceRecord(models.Model):
    """STEP 5: 업로드한 영상 1개에 대한 분석 기록 (DB 테이블 1행)

    id 필드는 Django가 자동으로 만든다. (기록 번호)
    """

    # 처리 상태 값 목록. STEP 5에서는 RECEIVED(업로드 받음)만 사용한다.
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', '업로드 받음'

    # 저장한 영상 파일 경로 (실제 파일은 MEDIA_ROOT/videos/ 에 저장된다)
    video_file = models.FileField(upload_to='videos/')

    # 처리 상태
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )

    # 나중에(STEP 8) AI가 반환할 예측 동작. 지금은 비워 둔다. (임의 값 금지)
    predicted_label = models.CharField(max_length=100, null=True, blank=True)

    # 나중에(STEP 8) AI가 반환할 신뢰도. 지금은 비워 둔다. (임의 값 금지)
    confidence = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f'#{self.id} {self.video_file.name} ({self.status})'
