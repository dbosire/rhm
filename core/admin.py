from django.contrib import admin

from .models import (
    AppSetting, AuditLog, ColumnMapping, ExcelTemplate, ExtractionBatch, GeneratedReport, Option, ParserRule,
    SMSMessage, Transaction,
)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ["id", "mpesa_receipt", "reference", "amount", "bank", "channel", "status", "workflow", "is_duplicate", "created_by", "created_at"]
    list_filter = ["bank", "channel", "status", "workflow", "source", "is_duplicate"]
    search_fields = ["mpesa_receipt", "reference", "account_number", "item_name"]
    raw_id_fields = ["sms", "batch", "duplicate_of"]


@admin.register(SMSMessage)
class SMSMessageAdmin(admin.ModelAdmin):
    list_display = ["id", "batch", "detected_source", "parse_status", "attempts", "created_at"]
    list_filter = ["detected_source", "parse_status"]
    search_fields = ["raw_text"]


@admin.register(ExtractionBatch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ["short_uid", "created_by", "input_method", "submitted_count", "extracted_count", "failed_count", "duplicate_count", "state", "created_at"]
    list_filter = ["state", "input_method"]


@admin.register(GeneratedReport)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["name", "mode", "record_count", "total_amount", "created_by", "created_at", "download_count"]
    exclude = ["file"]
    filter_horizontal = ["transactions"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "user", "action", "object_type", "object_id", "success", "ip_address"]
    list_filter = ["action", "success"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


admin.site.register([ExcelTemplate, ColumnMapping, Option, ParserRule])


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    exclude = ["download_password_hash"]
    readonly_fields = ["download_password_changed_at"]
