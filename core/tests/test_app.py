import io

import openpyxl
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import AppSetting, AuditLog, ExcelTemplate, GeneratedReport, Transaction
from core.services import excel, ingest
from core.services.excel_safety import safe_text

from .test_parser import KCB_CREDIT, KCB_DEBIT, MPESA_PAYBILL


def make_template_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Extracted Transactions"
    ws.append(["Unit Cost", "Amount", "Status", "Reference", "MPESA Receipt", "Submission Date", "Account Number", "Bank"])
    ws.append([None, 1000, "Completed", "Existing", "UJT8ZBVD8R", "29/10/2026 04:29 PM", "131***4378", "KCB"])
    ws.append(["Total", "=SUM(B2:B2)"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(DOWNLOAD_MAX_ATTEMPTS=3)
class AppTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@rhm.test", "S3cure-pass!")
        self.u1 = User.objects.create_user("one@rhm.test", "S3cure-pass!")
        self.u2 = User.objects.create_user("two@rhm.test", "S3cure-pass!")
        s = AppSetting.load()
        s.set_download_password("Download#2026")
        s.save()

    def test_batch_extraction_and_duplicates(self):
        b = ingest.run_batch(self.u1, [MPESA_PAYBILL, KCB_CREDIT, MPESA_PAYBILL, "random text that is not a transaction"])
        self.assertEqual((b.submitted_count, b.extracted_count, b.failed_count, b.duplicate_count), (4, 3, 1, 1))
        t = b.transactions.get(mpesa_receipt="UK2PH0KJOG")
        self.assertEqual(t.user_email, "one@rhm.test")
        dup = b.transactions.filter(is_duplicate=True).get()
        self.assertEqual(dup.duplicate_decision, "pending")
        self.assertTrue(Transaction.objects.filter(pk=dup.duplicate_of_id).exists())

    def test_amount_from_quantity_when_missing(self):
        b = ingest.run_batch(self.u1, ["Payment confirmed for account 12345 on 02/11/2026 Ref UK2PH0KJOX received"],
                             defaults={"quantity": "2", "unit_cost": "250"})
        t = b.transactions.get()
        self.assertEqual(str(t.amount), "500.00")

    def test_access_control(self):
        ingest.run_batch(self.u1, [KCB_DEBIT])
        self.client.force_login(self.u2)
        r = self.client.get(reverse("core:transaction_list"))
        self.assertNotContains(r, "Wallet Debit")
        t = Transaction.objects.get()
        self.assertEqual(self.client.get(reverse("core:transaction_detail", args=[t.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("core:user_list")).status_code, 403)

    def test_pages_render(self):
        b = ingest.run_batch(self.admin, [MPESA_PAYBILL, KCB_CREDIT, "nonsense message without amount here"])
        self.client.force_login(self.admin)
        for name, args in [("core:dashboard", []), ("core:extract", []), ("core:batch_list", []),
                           ("core:batch_detail", [b.uid]), ("core:transaction_list", []), ("core:generate", []),
                           ("core:report_list", []), ("core:template_list", []), ("core:user_list", []),
                           ("core:settings", []), ("core:audit_log", []), ("core:user_create", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_extract_view_paste_and_csv(self):
        self.client.force_login(self.u1)
        r = self.client.post(reverse("core:extract"), {"sms_text": MPESA_PAYBILL + "\n\n" + KCB_CREDIT, "source": "AUTO", "status": "Completed"})
        self.assertEqual(r.status_code, 302)
        csv_file = SimpleUploadedFile("sms.csv", ("id,message\n1,\"" + KCB_DEBIT + "\"\n").encode())
        r = self.client.post(reverse("core:extract"), {"upload": csv_file, "source": "AUTO"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Transaction.objects.filter(created_by=self.u1).count(), 3)

    def test_generate_new_and_download_with_password(self):
        ingest.run_batch(self.u1, [MPESA_PAYBILL, KCB_CREDIT])
        self.client.force_login(self.u1)
        ids = list(Transaction.objects.values_list("pk", flat=True))
        r = self.client.post(reverse("core:transaction_bulk"), {"action": "generate", "ids": ids})
        self.assertRedirects(r, reverse("core:generate"))
        r = self.client.post(reverse("core:generate"), {"mode": "new", "report_name": "Test", "sheet_name": "Data", "include_total": "on", "acknowledge": "on"})
        report = GeneratedReport.objects.get()
        self.assertRedirects(r, reverse("core:report_detail", args=[report.uid]))
        url = reverse("core:report_download", args=[report.uid])
        # wrong password
        r = self.client.post(url, {"password": "nope"})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b"Download#2026", r.content)
        # GET not allowed
        self.assertEqual(self.client.get(url).status_code, 405)
        r = self.client.post(url, {"password": "Download#2026"})
        self.assertEqual(r.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(b"".join(r.streaming_content)))
        ws = wb["Data"]
        self.assertEqual([c.value for c in ws[1]], [l for _, l in __import__("core.constants", fromlist=["x"]).STANDARD_PARAMETERS])
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertTrue(ws.auto_filter.ref)
        self.assertEqual(ws["K2"].data_type, "s")
        self.assertTrue(AuditLog.objects.filter(action="report.download", success=True).exists())
        self.assertTrue(AuditLog.objects.filter(action="report.download.denied").exists())
        self.assertEqual(Transaction.objects.filter(workflow="exported").count(), 2)

    def test_download_rate_limit_and_unauthenticated(self):
        ingest.run_batch(self.u1, [MPESA_PAYBILL])
        t = Transaction.objects.get()
        rep = GeneratedReport.objects.create(name="R", created_by=self.u1, mode="new", sheet_name="S")
        rep.file.save("r.xlsx", ContentFile(excel.build_new_workbook([t])))
        url = reverse("core:report_download", args=[rep.uid])
        self.assertEqual(self.client.post(url, {"password": "x"}).status_code, 302)  # login required
        self.client.force_login(self.u1)
        for _ in range(3):
            self.client.post(url, {"password": "bad"})
        self.assertEqual(self.client.post(url, {"password": "Download#2026"}).status_code, 429)
        self.client.force_login(self.u2)
        self.assertEqual(self.client.post(url, {"password": "Download#2026"}).status_code, 404)

    def test_populate_template_preserves_and_extends_total(self):
        ingest.run_batch(self.u1, [MPESA_PAYBILL, KCB_CREDIT])
        tpl = ExcelTemplate(name="T", owner=self.u1, original_filename="t.xlsx")
        tpl.file.save("t.xlsx", ContentFile(make_template_bytes()))
        original = make_template_bytes()
        mapping = excel.auto_map(excel.read_headers(excel.load_template(tpl.file).active, 1))
        data, first = excel.populate_template(tpl.file, "Extracted Transactions", 1, 2, mapping, Transaction.objects.order_by("id"))
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        self.assertEqual(first, 3)
        self.assertEqual(ws["D2"].value, "Existing")  # existing data preserved
        self.assertEqual(ws["B3"].value, 10000)
        self.assertEqual(ws["A5"].value, "Total")
        self.assertEqual(ws["B5"].value, "=SUM(B2:B4)")
        tpl.file.open("rb")
        self.assertEqual(len(tpl.file.read()), len(original))  # original untouched

    def test_formula_injection_neutralised(self):
        self.assertEqual(safe_text("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(safe_text("@SUM(1)"), "'@SUM(1)")
        self.assertEqual(safe_text("-500"), "-500")
        self.assertEqual(safe_text("Normal text"), "Normal text")

    def test_template_upload_rejects_non_xlsx(self):
        self.client.force_login(self.u1)
        bad = SimpleUploadedFile("evil.xlsx", b"not a zip at all")
        r = self.client.post(reverse("core:template_list"), {"name": "x", "upload": bad})
        self.assertContains(r, "not a valid .xlsx")
        good = SimpleUploadedFile("t.xlsx", make_template_bytes())
        r = self.client.post(reverse("core:template_list"), {"name": "Good", "upload": good})
        tpl = ExcelTemplate.objects.get()
        self.assertRedirects(r, reverse("core:template_preview", args=[tpl.pk]))
        self.assertEqual(self.client.get(reverse("core:template_mapping", args=[tpl.pk])).status_code, 200)
        r = self.client.get(reverse("core:template_info", args=[tpl.pk]))
        self.assertEqual(r.json()["auto_mapping"]["amount"], "B")

    def test_password_hashed(self):
        s = AppSetting.load()
        self.assertNotIn("Download#2026", s.download_password_hash)
        self.assertTrue(s.check_download_password("Download#2026"))

    def test_login_with_email(self):
        r = self.client.post(reverse("accounts:login"), {"username": "ONE@rhm.test", "password": "S3cure-pass!"})
        self.assertEqual(r.status_code, 302)
