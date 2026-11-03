import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.functional import LazyObject


class PrivateStorage(LazyObject):
    """Storage outside STATIC/MEDIA – files are only reachable via permission-checked views."""

    def _setup(self):
        self._wrapped = FileSystemStorage(location=str(settings.PRIVATE_MEDIA_ROOT), base_url=None)


private_storage = PrivateStorage()


def _private_storage():
    return private_storage


class TimeStamped(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# --------------------------------------------------------------------- settings
class AppSetting(models.Model):
    """Singleton row holding admin-configurable settings."""

    download_password_hash = models.CharField(max_length=256, blank=True)
    download_password_changed_at = models.DateTimeField(null=True, blank=True)
    default_status = models.CharField(max_length=40, default="Completed")
    default_report_type = models.CharField(max_length=80, blank=True)
    default_sheet_name = models.CharField(max_length=31, default="Extracted Transactions")
    include_total_row = models.BooleanField(default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "application setting"

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        if not obj.download_password_hash and settings.INITIAL_DOWNLOAD_PASSWORD:
            obj.set_download_password(settings.INITIAL_DOWNLOAD_PASSWORD)
            obj.save()
        return obj

    def set_download_password(self, raw):
        self.download_password_hash = make_password(raw)
        self.download_password_changed_at = timezone.now()

    def check_download_password(self, raw):
        if not self.download_password_hash or not raw:
            return False
        return check_password(raw, self.download_password_hash)

    @property
    def has_download_password(self):
        return bool(self.download_password_hash)


class Option(models.Model):
    """Configurable picklists (statuses, report types, item names)."""

    class Kind(models.TextChoices):
        STATUS = "status", "Status"
        REPORT_TYPE = "report_type", "Report type"
        ITEM_NAME = "item_name", "Item name"

    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    value = models.CharField(max_length=120)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "sort_order", "value"]
        constraints = [models.UniqueConstraint(fields=["kind", "value"], name="uniq_option_kind_value")]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.value}"

    @classmethod
    def values(cls, kind):
        return list(cls.objects.filter(kind=kind, is_active=True).values_list("value", flat=True))


class ParserRule(models.Model):
    """Admin-defined regex overrides so new SMS formats can be supported without code changes.

    The pattern must contain ONE capture group; the first match wins.
    """

    class Institution(models.TextChoices):
        ANY = "ANY", "Any"
        MPESA = "MPESA", "M-Pesa"
        KCB = "KCB", "KCB"

    class Field(models.TextChoices):
        AMOUNT = "amount", "Amount"
        REFERENCE = "reference", "Reference"
        MPESA_RECEIPT = "mpesa_receipt", "MPESA Receipt"
        ACCOUNT_NUMBER = "account_number", "Account Number"
        DATE = "date", "Date/time"
        CHANNEL = "channel", "Channel (fixed value)"
        BANK = "bank", "Bank (fixed value)"

    name = models.CharField(max_length=100)
    institution = models.CharField(max_length=10, choices=Institution.choices, default=Institution.ANY)
    field = models.CharField(max_length=20, choices=Field.choices)
    pattern = models.CharField(max_length=500, help_text="Python regex with one capture group (case-insensitive).")
    fixed_value = models.CharField(
        max_length=100, blank=True, help_text="For Channel/Bank: value to set when the pattern matches."
    )
    priority = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "id"]

    def __str__(self):
        return self.name


# --------------------------------------------------------------------- batches & SMS
class ExtractionBatch(TimeStamped):
    class Method(models.TextChoices):
        PASTE = "paste", "Pasted text"
        TXT = "txt", "TXT file"
        CSV = "csv", "CSV file"
        XLSX = "xlsx", "Excel file"

    class State(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Completed with errors"
        FAILED = "failed", "Failed"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="batches")
    input_method = models.CharField(max_length=10, choices=Method.choices, default=Method.PASTE)
    source_hint = models.CharField(max_length=10, default="AUTO")
    filename = models.CharField(max_length=255, blank=True)
    defaults = models.JSONField(default=dict, blank=True)
    submitted_count = models.PositiveIntegerField(default=0)
    extracted_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=12, choices=State.choices, default=State.PROCESSING, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "extraction batches"

    def __str__(self):
        return f"Batch {self.short_uid}"

    @property
    def short_uid(self):
        return str(self.uid).split("-")[0].upper()

    def refresh_counts(self):
        msgs = self.messages.all()
        self.submitted_count = msgs.count()
        self.extracted_count = msgs.filter(parse_status=SMSMessage.ParseStatus.PARSED).count()
        self.failed_count = self.submitted_count - self.extracted_count
        self.duplicate_count = self.transactions.filter(is_duplicate=True).count()
        if self.submitted_count and self.failed_count == self.submitted_count:
            self.state = self.State.FAILED
        elif self.failed_count:
            self.state = self.State.PARTIAL
        else:
            self.state = self.State.COMPLETED
        self.save(update_fields=[
            "submitted_count", "extracted_count", "failed_count", "duplicate_count", "state", "updated_at",
        ])


class SMSMessage(TimeStamped):
    class Source(models.TextChoices):
        MPESA = "MPESA", "M-Pesa"
        KCB = "KCB", "KCB"
        UNKNOWN = "UNKNOWN", "Unknown"

    class ParseStatus(models.TextChoices):
        PARSED = "parsed", "Parsed"
        FAILED = "failed", "Failed"
        UNRECOGNIZED = "unrecognized", "Unrecognized format"

    batch = models.ForeignKey(ExtractionBatch, on_delete=models.CASCADE, related_name="messages")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sms_messages")
    raw_text = models.TextField()
    text_hash = models.CharField(max_length=64, db_index=True)
    detected_source = models.CharField(max_length=10, choices=Source.choices, default=Source.UNKNOWN)
    selected_source = models.CharField(max_length=10, default="AUTO")
    parse_status = models.CharField(max_length=14, choices=ParseStatus.choices, db_index=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=1)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["batch", "position"]

    def __str__(self):
        return self.raw_text[:60]


# --------------------------------------------------------------------- transactions
class Transaction(TimeStamped):
    class Workflow(models.TextChoices):
        PENDING = "pending", "Pending"
        EXTRACTED = "extracted", "Extracted"
        AWAITING_REVIEW = "awaiting_review", "Awaiting Review"
        APPROVED = "approved", "Approved"
        EXPORTED = "exported", "Exported"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    class DuplicateDecision(models.TextChoices):
        NONE = "", "—"
        PENDING = "pending", "Pending review"
        KEEP = "keep", "Retain"
        SKIP = "skip", "Skip"

    sms = models.OneToOneField(SMSMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name="transaction")
    batch = models.ForeignKey(ExtractionBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transactions")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    # ---- 13 standard parameters ------------------------------------
    user_email = models.EmailField()
    report_type = models.CharField(max_length=80, blank=True, db_index=True)
    item_name = models.CharField(max_length=200, blank=True, db_index=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=40, blank=True, db_index=True)
    reference = models.CharField(max_length=255, blank=True, db_index=True)
    mpesa_receipt = models.CharField(max_length=40, blank=True, db_index=True)
    submission_date = models.DateTimeField(null=True, blank=True, db_index=True)
    account_number = models.CharField(max_length=60, blank=True, db_index=True)
    bank = models.CharField(max_length=60, blank=True, db_index=True)
    channel = models.CharField(max_length=60, blank=True, db_index=True)

    # ---- extraction metadata ----------------------------------------
    source = models.CharField(max_length=10, choices=SMSMessage.Source.choices, default=SMSMessage.Source.UNKNOWN, db_index=True)
    extracted_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    transaction_date = models.DateTimeField(null=True, blank=True)
    direction = models.CharField(max_length=10, blank=True)  # Credit / Debit
    counterparty = models.CharField(max_length=200, blank=True)
    workflow = models.CharField(max_length=20, choices=Workflow.choices, default=Workflow.EXTRACTED, db_index=True)

    # ---- validation & duplicates --------------------------------------
    issues = models.JSONField(default=list, blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    dup_key = models.CharField(max_length=64, blank=True, db_index=True)
    is_duplicate = models.BooleanField(default=False, db_index=True)
    duplicate_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates")
    duplicate_decision = models.CharField(max_length=10, choices=DuplicateDecision.choices, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["created_by", "created_at"]),
            models.Index(fields=["bank", "channel"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__isnull=True) | Q(quantity__gte=0), name="txn_quantity_non_negative"),
            models.CheckConstraint(condition=Q(unit_cost__isnull=True) | Q(unit_cost__gte=0), name="txn_unit_cost_non_negative"),
            models.CheckConstraint(condition=Q(amount__isnull=True) | Q(amount__gte=0), name="txn_amount_non_negative"),
        ]

    def __str__(self):
        return f"{self.mpesa_receipt or self.reference or self.pk} – {self.amount}"

    @property
    def calculated_amount(self):
        if self.quantity is not None and self.unit_cost is not None:
            return (Decimal(self.quantity) * Decimal(self.unit_cost)).quantize(Decimal("0.01"))
        return None

    @property
    def error_count(self):
        return sum(1 for i in self.issues or [] if i.get("level") == "error")

    @property
    def warning_count(self):
        return sum(1 for i in self.issues or [] if i.get("level") == "warning")

    def param_value(self, key):
        return getattr(self, key)


# --------------------------------------------------------------------- Excel templates
def template_upload_to(instance, filename):
    return f"templates/{uuid.uuid4().hex}.xlsx"


def report_upload_to(instance, filename):
    return f"reports/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.xlsx"


class ExcelTemplate(TimeStamped):
    name = models.CharField(max_length=150)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="templates")
    file = models.FileField(upload_to=template_upload_to, storage=_private_storage, max_length=255)
    original_filename = models.CharField(max_length=255)
    size_bytes = models.PositiveIntegerField(default=0)
    sheet_names = models.JSONField(default=list)
    is_shared = models.BooleanField(default=False, help_text="Visible to all users")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ColumnMapping(TimeStamped):
    name = models.CharField(max_length=120)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mappings")
    template = models.ForeignKey(ExcelTemplate, null=True, blank=True, on_delete=models.CASCADE, related_name="mappings")
    sheet_name = models.CharField(max_length=31)
    header_row = models.PositiveIntegerField(default=1)
    data_start_row = models.PositiveIntegerField(default=2)
    mapping = models.JSONField(default=dict, help_text="{parameter_key: column_letter}")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "template", "name"], name="uniq_mapping_name")]

    def __str__(self):
        return self.name


# --------------------------------------------------------------------- generated reports
class GeneratedReport(TimeStamped):
    class Mode(models.TextChoices):
        NEW = "new", "New workbook"
        TEMPLATE = "template", "Populated template"

    class State(models.TextChoices):
        GENERATED = "generated", "Generated"
        FAILED = "failed", "Failed"
        PURGED = "purged", "File purged"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=200)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports")
    mode = models.CharField(max_length=10, choices=Mode.choices)
    template = models.ForeignKey(ExcelTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    mapping = models.ForeignKey(ColumnMapping, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    batch = models.ForeignKey(ExtractionBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    sheet_name = models.CharField(max_length=31)
    file = models.FileField(upload_to=report_upload_to, storage=_private_storage, max_length=255, blank=True)
    record_count = models.PositiveIntegerField(default=0)
    total_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    report_type = models.CharField(max_length=80, blank=True, db_index=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.GENERATED, db_index=True)
    transactions = models.ManyToManyField(Transaction, related_name="reports", blank=True)
    download_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def filename(self):
        safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in self.name).strip() or "report"
        return f"{safe}.xlsx"


# --------------------------------------------------------------------- audit
class AuditLog(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    success = models.BooleanField(default=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["action", "created_at"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
