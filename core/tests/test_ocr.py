import io
import json
import unittest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import User
from core.models import ExtractionBatch, Transaction
from core.services import ingest, ocr

from .test_parser import MPESA_PAYBILL

OCR_OK = ocr.is_available()


def sms_screenshot(text, dark=False, width=780):
    """Render a phone-style SMS bubble screenshot."""
    import textwrap

    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
        except OSError:
            font = ImageFont.load_default(size=30)
    bg, bubble, fg = ((0, 0, 0), (38, 38, 40), (235, 235, 240)) if dark else ((255, 255, 255), (233, 233, 235), (20, 20, 20))
    lines = textwrap.wrap(text, 34)
    h = 140 + 40 * len(lines)
    img = Image.new("RGB", (width, h), bg)
    d = ImageDraw.Draw(img)
    d.text((width // 2 - 60, 20), "MPESA", font=font, fill=fg)
    d.rounded_rectangle((24, 80, width - 120, 100 + 40 * len(lines)), radius=30, fill=bubble)
    for i, ln in enumerate(lines):
        d.text((48, 90 + 40 * i), ln, font=font, fill=fg)
    d.text((40, 110 + 40 * len(lines)), "11:46", font=font, fill=(140, 140, 150))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class OCRCleanupTests(SimpleTestCase):
    def test_noise_removed_and_amounts_repaired(self):
        raw = "< 12 MPESA\n\nSun, 2 Nov\nUK2LQ0BS3V Confirmed. Ksh1O,OOO.OO sent to KCB\n\nPaybill AC for account 1317734378 on\n2/11/26 at 11:46 AM\n11:46\nToday\n"
        text = ocr.regroup_messages(ocr.clean_ocr_text(raw))
        self.assertTrue(text.startswith("UK2LQ0BS3V Confirmed. Ksh10,000.00 sent to KCB Paybill AC"))
        self.assertNotIn("MPESA\n", text)
        self.assertNotIn("Today", text)
        self.assertEqual(text.count("\n\n"), 0)  # one message, even with a blank line inside the bubble

    def test_ocr_duplicate_folding(self):
        from core.services.validation import duplicate_key

        a, b = Transaction(mpesa_receipt="UK2LQ0BS3V"), Transaction(mpesa_receipt="UK2LQOBS3V")
        self.assertEqual(duplicate_key(a), duplicate_key(b))


@unittest.skipUnless(OCR_OK, "Tesseract OCR not installed")
class OCRIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("one@rhm.test", "S3cure-pass!")
        self.client.force_login(self.user)

    def test_ocr_image_light_and_dark(self):
        for dark in (False, True):
            text = ocr.ocr_image(sms_screenshot(MPESA_PAYBILL, dark=dark))
            self.assertIn("Confirmed", text)
            self.assertIn("Ksh10,000.00", text)
            self.assertIn("1317734378", text)

    def test_preview_endpoint(self):
        img = SimpleUploadedFile("shot.png", sms_screenshot(MPESA_PAYBILL), content_type="image/png")
        r = self.client.post(reverse("core:ocr_preview"), {"images": [img]})
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["results"][0]["count"], 1)
        bad = SimpleUploadedFile("evil.png", b"not an image", content_type="image/png")
        r = self.client.post(reverse("core:ocr_preview"), {"images": [bad]})
        self.assertIn("not a valid image", r.json()["results"][0]["error"])

    def test_extract_from_uploaded_images_flags_ocr(self):
        img = SimpleUploadedFile("shot.png", sms_screenshot(MPESA_PAYBILL, dark=True), content_type="image/png")
        r = self.client.post(reverse("core:extract"), {"images": [img], "source": "AUTO", "status": "Completed"})
        batch = ExtractionBatch.objects.get()
        self.assertRedirects(r, reverse("core:batch_detail", args=[batch.uid]))
        self.assertEqual(batch.input_method, "image")
        t = batch.transactions.get()
        self.assertEqual(str(t.amount), "10000.00")
        self.assertEqual(t.account_number, "1317734378")
        self.assertTrue(t.sms.from_image)
        self.assertTrue(any(ocr.OCR_WARNING == i["message"] for i in t.issues))

    def test_previewed_text_keeps_ocr_flag_and_detects_duplicate(self):
        ingest.run_batch(self.user, [MPESA_PAYBILL])  # the original SMS already exists
        r = self.client.post(reverse("core:ocr_preview"), {"images": [SimpleUploadedFile("s.png", sms_screenshot(MPESA_PAYBILL))]})
        msgs = r.json()["results"][0]["messages"]
        self.client.post(reverse("core:extract"), {
            "sms_text": "\n\n".join(msgs), "ocr_texts": json.dumps(msgs), "source": "AUTO", "status": "Completed",
        })
        t = Transaction.objects.order_by("-id").first()
        self.assertTrue(any(ocr.OCR_WARNING == i["message"] for i in t.issues))
        self.assertTrue(t.is_duplicate)  # even if OCR read 0 as O in the receipt
