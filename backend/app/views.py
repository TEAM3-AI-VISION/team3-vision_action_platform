from django.shortcuts import render


def index(request):
    """/analyses/ 에 접속하면 frontend/index.html 화면을 돌려준다."""
    # 'index.html' 은 settings.py TEMPLATES 의 DIRS(frontend/) 에서 찾는다.
    return render(request, 'index.html')
