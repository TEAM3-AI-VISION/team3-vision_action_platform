"""
URL configuration for project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    # STEP 3: /analyses/ 로 시작하는 주소는 analyses/urls.py 가 처리한다
    path('analyses/', include('analyses.urls')),
    # STEP 4: /api/v1/ 로 시작하는 주소는 analyses/api_urls.py 가 처리한다
    path('api/v1/', include('analyses.api_urls')),
]
