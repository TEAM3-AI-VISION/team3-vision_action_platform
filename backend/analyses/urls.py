from django.urls import path

from . import views

# 이 파일은 project/urls.py 의 path('analyses/', include('analyses.urls')) 에서 불러온다.
# 그래서 여기의 '' 는 실제로 /analyses/ 주소가 된다.
urlpatterns = [
    path('', views.index, name='index'),
]
