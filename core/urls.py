from django.urls import path

from .views import admin_views, dashboard, extraction, reports, templates_views, transactions

app_name = "core"

urlpatterns = [
    path("", dashboard.dashboard, name="dashboard"),
    path("healthz", dashboard.healthz, name="healthz"),
    # extraction
    path("extract/", extraction.extract, name="extract"),
    path("batches/", extraction.batch_list, name="batch_list"),
    path("batches/merge/", extraction.batch_merge, name="batch_merge"),
    path("batches/<uuid:uid>/", extraction.batch_detail, name="batch_detail"),
    path("batches/<uuid:uid>/retry/", extraction.batch_retry, name="batch_retry"),
    path("batches/<uuid:uid>/generate/", reports.generate_from_batch, name="batch_generate"),
    path("sms/<int:pk>/retry/", extraction.sms_retry, name="sms_retry"),
    # transactions
    path("transactions/", transactions.transaction_list, name="transaction_list"),
    path("transactions/bulk/", transactions.transaction_bulk, name="transaction_bulk"),
    path("transactions/<int:pk>/", transactions.transaction_detail, name="transaction_detail"),
    # excel
    path("generate/", reports.generate, name="generate"),
    path("reports/", reports.report_list, name="report_list"),
    path("reports/<uuid:uid>/", reports.report_detail, name="report_detail"),
    path("reports/<uuid:uid>/download/", reports.report_download, name="report_download"),
    path("templates/", templates_views.template_list, name="template_list"),
    path("templates/<int:pk>/", templates_views.template_preview, name="template_preview"),
    path("templates/<int:pk>/mapping/", templates_views.template_mapping, name="template_mapping"),
    path("templates/<int:pk>/delete/", templates_views.template_delete, name="template_delete"),
    path("templates/<int:pk>/info/", templates_views.template_info_api, name="template_info"),
    path("mappings/<int:pk>/delete/", templates_views.mapping_delete, name="mapping_delete"),
    # administration
    path("manage/users/", admin_views.user_list, name="user_list"),
    path("manage/users/new/", admin_views.user_form, name="user_create"),
    path("manage/users/<int:pk>/", admin_views.user_form, name="user_edit"),
    path("manage/users/<int:pk>/toggle/", admin_views.user_toggle, name="user_toggle"),
    path("manage/settings/", admin_views.app_settings, name="settings"),
    path("manage/options/<int:pk>/", admin_views.option_toggle, name="option_toggle"),
    path("manage/rules/<int:pk>/", admin_views.rule_toggle, name="rule_toggle"),
    path("manage/audit/", admin_views.audit_log, name="audit_log"),
]
