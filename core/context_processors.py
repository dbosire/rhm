from django.conf import settings
from django.urls import resolve, Resolver404


def app_context(request):
    try:
        url_name = resolve(request.path_info).url_name
    except Resolver404:
        url_name = ""
    return {"APP_NAME": "RHM Extractor", "current_url_name": url_name or "-", "MAX_UPLOAD_MB": settings.MAX_UPLOAD_MB}
