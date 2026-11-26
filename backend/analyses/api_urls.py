from django.urls import path

from . import views

# 이 파일은 project/urls.py 의 path('api/v1/', include('analyses.api_urls')) 에서 불러온다.
# 화면 주소(urls.py)와 API 주소(api_urls.py)를 파일로 나눠서 관리한다.
# 그래서 여기의 'analyze/' 는 실제로 /api/v1/analyze/ 주소가 된다.
#
# STEP 9: <int:pk> 는 주소의 숫자 부분을 정수로 읽어 pk 라는 이름으로 함수에 넘긴다.
#   /api/v1/analyze/3/ → project/urls.py → 이 파일 → views.get_result(request, pk=3)
urlpatterns = [
    path('analyze/', views.analyze, name='analyze'),                     # STEP 4~8: POST
    path('analyze/<int:pk>/', views.get_result, name='analyze-detail'),  # STEP 9: GET
]
