from django import forms
from django.conf import settings
from django.contrib.auth import password_validation

from .models import AppSetting, ExcelTemplate, Option, ParserRule, Transaction
from .services.excel import clean_sheet_name

def parse_submission_date(raw):
    """Parse a date typed as YYYY-MM-DD (date picker) or MM/DD/YY(YY)."""
    from datetime import datetime

    from .constants import SUBMISSION_DATE_INPUT_FORMATS

    for fmt in SUBMISSION_DATE_INPUT_FORMATS:
        try:
            return datetime.strptime((raw or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def combine_date(d, previous=None):
    """Aware datetime for date ``d``; keeps the time-of-day of ``previous`` (SMS time) if any."""
    from datetime import datetime, time

    from django.utils import timezone

    t = timezone.localtime(previous).time() if previous else time(12, 0)
    return timezone.make_aware(datetime.combine(d, t))


SOURCE_CHOICES = [("AUTO", "Auto-detect"), ("MPESA", "M-Pesa"), ("KCB", "KCB")]
IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/bmp,image/gif,image/tiff"


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": IMAGE_ACCEPT}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


class ExtractForm(forms.Form):
    sms_text = forms.CharField(
        required=False, label="Paste SMS messages",
        widget=forms.Textarea(attrs={"rows": 9, "placeholder": "Paste one or many M-Pesa / KCB messages here.\nSeparate messages with a blank line or put each on its own line."}),
    )
    upload = forms.FileField(required=False, label="…or upload a file (.txt, .csv, .xlsx)")
    images = MultipleFileField(required=False, label="…or SMS screenshots / photos")
    ocr_texts = forms.CharField(required=False, widget=forms.HiddenInput)  # set by the page after an OCR preview
    source = forms.ChoiceField(choices=SOURCE_CHOICES, initial="AUTO", label="Institution")
    report_type = forms.CharField(required=False, max_length=80)
    item_name = forms.CharField(required=False, max_length=200)
    quantity = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)
    unit_cost = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=2)
    status = forms.CharField(required=False, max_length=40)
    submission_date = forms.DateField(
        required=False, label="Submission date override",
        widget=forms.DateInput(attrs={"type": "date"}),
        input_formats=["%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"],
        help_text="Date only (shown as MM/DD/YY). Leave blank to use the SMS date, or today if the SMS has none.",
    )

    def clean_upload(self):
        f = self.cleaned_data.get("upload")
        if f:
            from .services.ocr import is_image_name

            if not f.name.lower().endswith((".txt", ".csv", ".xlsx")) and not is_image_name(f.name):
                raise forms.ValidationError("Supported: .txt, .csv, .xlsx or an image (PNG, JPG, WEBP…).")
            if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                raise forms.ValidationError(f"File exceeds {settings.MAX_UPLOAD_MB} MB.")
        return f

    def clean_images(self):
        from .services.ocr import is_image_name

        files = self.cleaned_data.get("images") or []
        if len(files) > settings.MAX_IMAGES_PER_UPLOAD:
            raise forms.ValidationError(f"At most {settings.MAX_IMAGES_PER_UPLOAD} images per upload.")
        for f in files:
            if not is_image_name(f.name):
                raise forms.ValidationError(f"{f.name} is not a supported image type.")
            if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                raise forms.ValidationError(f"{f.name} exceeds {settings.MAX_UPLOAD_MB} MB.")
        return files

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("sms_text", "").strip() and not cleaned.get("upload") and not cleaned.get("images"):
            raise forms.ValidationError("Paste at least one SMS, upload a file or add a screenshot.")
        return cleaned


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = [
            "user_email", "report_type", "item_name", "quantity", "unit_cost", "amount", "status", "reference",
            "mpesa_receipt", "submission_date", "account_number", "bank", "channel",
        ]
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        initial_date = None
        if self.instance and self.instance.submission_date:
            from django.utils import timezone

            initial_date = timezone.localtime(self.instance.submission_date).date()
        self.fields["submission_date"] = forms.DateField(
            required=False, label="Submission Date (MM/DD/YY)", initial=initial_date,
            widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            input_formats=["%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"],
        )
        if initial_date:
            self.initial["submission_date"] = initial_date
        if user is not None and not user.is_admin_role:
            # Email always comes from the authenticated owner
            self.fields["user_email"].disabled = True

    def clean_submission_date(self):
        d = self.cleaned_data.get("submission_date")
        if d is None:
            return None
        previous = self.instance.submission_date if self.instance and self.instance.pk else None
        return combine_date(d, previous)


class TemplateUploadForm(forms.ModelForm):
    upload = forms.FileField(label="Excel template (.xlsx)")

    class Meta:
        model = ExcelTemplate
        fields = ["name", "is_shared"]

    def clean_upload(self):
        f = self.cleaned_data["upload"]
        if not f.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Only .xlsx workbooks are supported.")
        if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"File exceeds {settings.MAX_UPLOAD_MB} MB.")
        return f


class GenerateForm(forms.Form):
    MODE_CHOICES = [("new", "Generate a new workbook"), ("template", "Populate an existing template")]
    mode = forms.ChoiceField(choices=MODE_CHOICES, initial="new", widget=forms.RadioSelect)
    report_name = forms.CharField(max_length=150)
    sheet_name = forms.CharField(max_length=31, required=False)
    include_total = forms.BooleanField(required=False, initial=True)
    template = forms.IntegerField(required=False)
    template_sheet = forms.CharField(required=False, max_length=31)
    mapping = forms.IntegerField(required=False)
    header_row = forms.IntegerField(required=False, min_value=1, max_value=1000)
    data_start_row = forms.IntegerField(required=False, min_value=2, max_value=100000)
    acknowledge = forms.BooleanField(required=False)

    def clean_sheet_name(self):
        return clean_sheet_name(self.cleaned_data.get("sheet_name"))


class DownloadPasswordForm(forms.Form):
    new_password = forms.CharField(widget=forms.PasswordInput, strip=False, min_length=8, label="New download password")
    confirm_password = forms.CharField(widget=forms.PasswordInput, strip=False, label="Confirm password")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password") != cleaned.get("confirm_password"):
            raise forms.ValidationError("Passwords do not match.")
        if cleaned.get("new_password"):
            password_validation.validate_password(cleaned["new_password"])
        return cleaned


class AppSettingForm(forms.ModelForm):
    class Meta:
        model = AppSetting
        fields = ["default_status", "default_report_type", "default_sheet_name", "include_total_row"]


class OptionForm(forms.ModelForm):
    class Meta:
        model = Option
        fields = ["kind", "value", "sort_order"]


class ParserRuleForm(forms.ModelForm):
    class Meta:
        model = ParserRule
        fields = ["name", "institution", "field", "pattern", "fixed_value", "priority", "is_active"]

    def clean_pattern(self):
        import re

        p = self.cleaned_data["pattern"]
        try:
            compiled = re.compile(p)
        except re.error as exc:
            raise forms.ValidationError(f"Invalid regex: {exc}")
        if compiled.groups < 1 and self.cleaned_data.get("field") not in ("channel", "bank"):
            raise forms.ValidationError("Pattern needs one capture group, e.g. Ref[:\\s]+([A-Z0-9]{10}).")
        return p
