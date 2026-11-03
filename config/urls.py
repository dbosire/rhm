from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "RHM Extractor administration"

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("core.urls")),
]
