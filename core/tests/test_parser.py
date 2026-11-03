from decimal import Decimal

from django.test import SimpleTestCase

from core.parsing.extractor import detect_source, extract, split_messages
from core.parsing.mpesav3_legacy import legacy_parse

MPESA_PAYBILL = ("UK2LQ0BS3V Confirmed. Ksh10,000.00 sent to KCB Paybill AC for account 1317734378 on 2/11/26 "
                 "at 11:46 AM New M-PESA balance is Ksh1,234.00. Transaction cost, Ksh0.00.")
KCB_CREDIT = ("Dear DENNIS, your KCB A/C 131***4378 has been credited with KES 500.00 on 02/11/2026 at 11:08 AM. "
              "M-PESA Ref UK2PH0KJOG. Narration: REPENTANCE AND HOLINESS MISSIONS. Avail Bal KES 20,000.00")
KCB_DEBIT = ("Dear Customer, KES 5,000.00 has been debited from A/C 1317734378 on 27/10/2026 at 10:34 PM. "
             "Wallet Debit to Paybill 522522. Ref FT26300ABCDE")


class ParserTests(SimpleTestCase):
    def test_legacy_mpesav3_logic_still_runs(self):
        out = legacy_parse(MPESA_PAYBILL)
        self.assertEqual(out["receipt"], "UK2LQ0BS3V")
        self.assertEqual(out["amount"], 10000.0)
        self.assertEqual(out["account"], "1317734378")

    def test_mpesa_paybill(self):
        r = extract(MPESA_PAYBILL)
        self.assertEqual(r.source, "MPESA")
        self.assertEqual(r.amount, Decimal("10000.00"))
        self.assertEqual(r.mpesa_receipt, "UK2LQ0BS3V")
        self.assertEqual(r.account_number, "1317734378")
        self.assertEqual(r.bank, "KCB")
        self.assertEqual(r.channel, "M-PESA Paybill")
        self.assertEqual(r.reference, "KCB Paybill AC")
        self.assertEqual((r.transaction_date.day, r.transaction_date.month, r.transaction_date.hour), (2, 11, 11))

    def test_kcb_credit(self):
        r = extract(KCB_CREDIT)
        self.assertEqual(r.source, "KCB")
        self.assertEqual(r.amount, Decimal("500.00"))  # not the balance
        self.assertEqual(r.mpesa_receipt, "UK2PH0KJOG")
        self.assertEqual(r.reference, "REPENTANCE AND HOLINESS MISSIONS")
        self.assertEqual(r.account_number, "131***4378")
        self.assertEqual(r.bank, "KCB")

    def test_kcb_debit_without_mpesa_receipt(self):
        r = extract(KCB_DEBIT)
        self.assertEqual(r.amount, Decimal("5000.00"))
        self.assertEqual(r.mpesa_receipt, "")  # FT bank refs are not M-Pesa receipts
        self.assertEqual(r.reference, "Wallet Debit to Paybill 522522")
        self.assertEqual(r.channel, "Paybill")

    def test_unrecognized_left_blank(self):
        r = extract("Hello, please call me back when you are free tomorrow evening.")
        self.assertFalse(r.recognized)
        self.assertTrue(r.errors)
        self.assertIsNone(r.amount)

    def test_manual_source(self):
        self.assertEqual(extract(KCB_DEBIT, "KCB").source, "KCB")
        self.assertEqual(detect_source(MPESA_PAYBILL), "MPESA")

    def test_split_bulk(self):
        blob = MPESA_PAYBILL + "\n\n" + KCB_CREDIT + "\n" + KCB_DEBIT
        self.assertEqual(len(split_messages(blob)), 3)
        self.assertEqual(len(split_messages(MPESA_PAYBILL + " " + MPESA_PAYBILL.replace("UK2LQ0BS3V", "UK2LQ0BS3W"))), 2)
