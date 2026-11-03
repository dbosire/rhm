from django.db import migrations

STATUSES = ["Completed", "Pending", "Extracted", "Awaiting Review", "Approved", "Exported", "Rejected", "Failed"]
REPORT_TYPES = ["Offering", "Tithe", "Missions", "Pledge", "Donation", "Thanksgiving", "Project", "Other"]


def seed(apps, schema_editor):
    Option = apps.get_model("core", "Option")
    AppSetting = apps.get_model("core", "AppSetting")
    for i, v in enumerate(STATUSES):
        Option.objects.get_or_create(kind="status", value=v, defaults={"sort_order": i})
    for i, v in enumerate(REPORT_TYPES):
        Option.objects.get_or_create(kind="report_type", value=v, defaults={"sort_order": i})
    AppSetting.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
