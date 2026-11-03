"""
SMS transaction extraction for M-Pesa and KCB messages.

Pipeline per message:
  1. normalise text (strip invisible Unicode marks, collapse whitespace)
  2. detect institution (or use the user's manual selection)
  3. run admin-defined ParserRules (highest priority)
  4. run the legacy mpesav3.py parser
  5. fill remaining gaps with the extended M-Pesa / KCB patterns below
  6. derive Bank + Channel

Anything that cannot be found confidently is left blank (never guessed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .mpesav3_legacy import legacy_parse

MPESA = "MPESA"
KCB = "KCB"
UNKNOWN = "UNKNOWN"

INVISIBLE = dict.fromkeys(map(ord, "‎‏​‌‍﻿‪‫‬‭‮"), None)

# Well-known bank paybills on M-Pesa
BANK_PAYBILLS = {
    "522522": "KCB", "522533": "KCB", "247247": "Equity", "400200": "Co-op Bank", "880100": "NCBA",
    "303030": "Absa", "600100": "Stanbic", "222111": "Family Bank", "542542": "I&M", "516600": "DTB",
}
BANK_KEYWORDS = [
    (r"\bKCB\b", "KCB"), (r"\bEQUITY\b", "Equity"), (r"\bCO-?OP(?:ERATIVE)?\b", "Co-op Bank"),
    (r"\bNCBA\b", "NCBA"), (r"\bABSA\b", "Absa"), (r"\bSTANBIC\b", "Stanbic"),
    (r"\bFAMILY\s+BANK\b", "Family Bank"), (r"\bI\s?&\s?M\b", "I&M"), (r"\bDTB\b", "DTB"),
]

CUR = r"(?:Ksh|KES|Kshs|KSH)\.?"
NUM = r"([\d,]+(?:\.\d{1,2})?)"
RECEIPT_TOKEN = r"[A-Z0-9]{8,12}"
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
STOP = r"(?=\s+(?:on\s+\d|at\s+\d|Ref\b|M-?PESA\s+Ref|Avail|Bal|New\s|Call\b|Dial\b|Download|Transaction\s+cost)|\.\s|\.$|$)"


@dataclass
class ExtractionResult:
    source: str = UNKNOWN
    amount: Decimal | None = None
    mpesa_receipt: str = ""
    reference: str = ""
    account_number: str = ""
    transaction_date: datetime | None = None
    channel: str = ""
    bank: str = ""
    direction: str = ""
    counterparty: str = ""
    paybill: str = ""
    recognized: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    text: str = ""

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ------------------------------------------------------------------ helpers
def normalize(text: str) -> str:
    text = (text or "").translate(INVISIBLE).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def to_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return d.quantize(Decimal("0.01")) if d >= 0 else None


def looks_like_receipt(token: str) -> bool:
    token = (token or "").strip()
    return bool(
        re.fullmatch(RECEIPT_TOKEN, token)
        and re.search(r"[A-Z]", token)
        and re.search(r"\d", token)
        and token[0].isalpha()
    )


def parse_datetime(date_str: str, time_str: str = "") -> datetime | None:
    date_str = (date_str or "").strip().rstrip(".,")
    time_str = (time_str or "").strip().upper().replace(".", "")
    if not date_str:
        return None
    date_fmts = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y",
                 "%d-%b-%Y", "%d-%b-%y", "%d %b %y", "%d %B, %Y", "%d %b, %Y", "%d.%m.%Y"]
    d = None
    clean = re.sub(r"\s+", " ", date_str.replace("Sept", "Sep"))
    for fmt in date_fmts:
        try:
            d = datetime.strptime(clean, fmt)
            break
        except ValueError:
            continue
    if d is None:
        return None
    if d.year < 2000 or d.year > 2100:
        return None
    t = None
    if time_str:
        time_str = re.sub(r"\s+", "", time_str)
        for fmt in ("%I:%M%p", "%I:%M:%S%p", "%H:%M", "%H:%M:%S"):
            try:
                t = datetime.strptime(time_str, fmt).time()
                break
            except ValueError:
                continue
    if t:
        d = d.replace(hour=t.hour, minute=t.minute, second=t.second)
    try:
        return timezone.make_aware(d)
    except Exception:  # pragma: no cover
        return d


# ------------------------------------------------------------------ detection
def detect_source(text: str) -> str:
    t = normalize(text)
    if re.match(rf"^{RECEIPT_TOKEN}\s+Confirmed", t, re.I) or re.search(
        r"M-?PESA balance|Transaction cost|Fuliza M-?PESA|Amount you can transact", t, re.I
    ):
        return MPESA
    if re.search(r"\bKCB\b", t, re.I) and re.search(r"\b(A/C|account|acc|credited|debited|deposit|wallet)\b", t, re.I):
        return KCB
    if re.search(r"\bKCB\b", t, re.I):
        return KCB
    if re.search(r"\bConfirmed\b", t, re.I) and re.search(CUR, t, re.I):
        return MPESA
    return UNKNOWN


def looks_like_bank_sms(t: str) -> bool:
    return bool(
        re.search(r"\b(credited|debited)\b", t, re.I)
        and re.search(r"\b(A/C|account|acc|acct)\b", t, re.I)
    )


# ------------------------------------------------------------------ field finders
def find_amount(t: str) -> Decimal | None:
    verb = r"(?:has\s+been\s+|was\s+|is\s+)?(?:sent|paid|received|credited|debited|deposited|withdrawn|transferred|transfered)"
    m = re.search(rf"{CUR}\s*{NUM}\s*{verb}", t, re.I)
    if m:
        return to_decimal(m.group(1))
    m = re.search(rf"(?:credited|debited|received|deposit(?:ed)?|payment|amount|transfer)\s+(?:with|of|for)?\s*{CUR}\s*{NUM}", t, re.I)
    if m:
        return to_decimal(m.group(1))
    m = re.search(rf"{NUM}\s*KES\b", t)
    candidates = [(mm.start(), mm.group(1)) for mm in re.finditer(rf"{CUR}\s*{NUM}", t, re.I)]
    if m:
        candidates.append((m.start(), m.group(1)))
    for start, raw in sorted(candidates):
        before = t[max(0, start - 30):start].lower()
        if any(w in before for w in ("balance", "bal", "cost", "charge", "fee", "limit", "transact")):
            continue
        return to_decimal(raw)
    return None


def find_receipt(t: str, source: str) -> str:
    m = re.match(rf"^({RECEIPT_TOKEN})\s+Confirmed", t)
    if m and looks_like_receipt(m.group(1)):
        return m.group(1)
    label = (r"(?:M-?PESA\s*(?:Ref(?:erence)?|Receipt|Code|Trans(?:action)?\s*(?:ID|Code))|"
             r"Ref(?:erence)?(?:\s*(?:No|Number|Code))?|Receipt(?:\s*No)?|Trans(?:action)?\s*(?:ID|Code|Ref))")
    for m in re.finditer(rf"{label}\s*[.:#-]?\s*({RECEIPT_TOKEN})\b", t, re.I):
        if looks_like_receipt(m.group(1)) and not m.group(1).startswith("FT"):
            return m.group(1)
    if source == MPESA or re.search(r"M-?PESA", t, re.I):
        for m in re.finditer(r"\b([A-Z][A-Z0-9]{9})\b", t):
            tok = m.group(1)
            if looks_like_receipt(tok) and len(re.findall(r"[A-Z]", tok)) >= 3 and not tok.startswith("FT"):
                return tok
    return ""


def find_bank_reference(t: str) -> str:
    m = re.search(r"\b(FT[A-Z0-9]{8,14})\b", t)
    return m.group(1) if m else ""


def clean_phrase(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip(" .,;:-")
    return s[:255]


def find_reference(t: str, source: str) -> tuple[str, str]:
    """Return (reference, counterparty)."""
    counterparty = ""
    # Explicit labels
    m = re.search(
        rf"(?:Narration|Narrative|Description|Desc|Details|Reason|Purpose|Remarks?|Being|Memo)\s*[:\-]\s*(.+?){STOP}",
        t, re.I,
    )
    if m:
        ref = clean_phrase(m.group(1))
    else:
        ref = ""
    if not ref:
        m = re.search(rf"\b(Towards\s+.+?){STOP}", t, re.I)
        if m:
            ref = clean_phrase(m.group(1))
    if not ref:
        m = re.search(
            r"((?:Wallet|Mobile|M-?PESA|App|Internet)\s+(?:Debit|Credit|Transfer|Payment)\s+(?:to|from)\s+[A-Za-z][A-Za-z\- ]*?(?:\s+\d{4,})?)(?=\s+(?:on|at|Ref|via)\b|[.,]|$)",
            t, re.I,
        )
        if m:
            ref = clean_phrase(m.group(1))

    # Counterparty (M-Pesa style)
    patterns = [
        rf"sent to\s+(.+?)\s+for account",
        rf"paid to\s+(.+?)(?:\.\s|\s+on\s+\d|$)",
        rf"sent to\s+(.+?)\s+(?:\+?254|0)[17]\d{{8}}",
        rf"sent to\s+(.+?)\s+on\s+\d",
        rf"received\s+{CUR}\s*{NUM}\s+from\s+(.+?)(?:\s+(?:\+?254|0)[17]\d{{8}}|\s+on\s+\d|\.\s)",
        rf"from\s+(.+?)\s+(?:via|through)\s+M-?PESA",
        rf"(?:credited|deposited).*?\bfrom\s+(?!your\b)(.+?)(?:\s+(?:\+?254|0)[17]\d{{8}}|\s+on\s+\d|\s+via\b|\s+Ref\b|\.\s|$)",
    ]
    for p in patterns:
        m = re.search(p, t, re.I)
        if m:
            grp = m.groups()[-1]
            counterparty = clean_phrase(grp)
            if counterparty and not re.fullmatch(r"[\d,.]+", counterparty):
                break
            counterparty = ""
    if not ref and source == KCB:
        m = re.search(rf"\bfor\s+(?!account\b|A/C\b)([A-Za-z].+?){STOP}", t, re.I)
        if m:
            ref = clean_phrase(m.group(1))
    if not ref and counterparty:
        ref = counterparty
    if not ref:
        ref = find_bank_reference(t)
    return ref, counterparty


def find_account(t: str, source: str) -> str:
    m = re.search(r"for account\s+(.+?)(?=\s+on\s+\d|\s+at\s+\d|\.\s|\.$|$)", t, re.I)
    if m:
        return clean_phrase(m.group(1))[:60]
    m = re.search(
        r"(?:A/C|Acc(?:oun)?t|Acct)\.?\s*(?:No\.?|Number|#)?\s*[:\-]?\s*((?:[0-9]|[X*•]){4,20})", t, re.I
    )
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).upper()
    return ""


def _fix_time(raw):
    raw = (raw or "").strip()
    if re.match(r"^\d{1,2}\.\d{2}", raw):
        raw = raw.replace(".", ":", 1)
    return raw


def find_datetime(t: str) -> datetime | None:
    tp = r"(\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:[AP]\.?M\.?)?)"
    m = re.search(rf"(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})\s*(?:,|at|@)?\s*{tp}?", t, re.I)
    if m:
        dt = parse_datetime(m.group(1), _fix_time(m.group(2)))
        if dt:
            return dt
    m = re.search(rf"(\d{{4}}-\d{{2}}-\d{{2}})[ T]?{tp}?", t, re.I)
    if m:
        dt = parse_datetime(m.group(1), _fix_time(m.group(2)))
        if dt:
            return dt
    m = re.search(rf"(\d{{1,2}}[-\s]{MONTHS}[-\s,]+\d{{2,4}})\s*(?:at|,)?\s*{tp}?", t, re.I)
    if m:
        dt = parse_datetime(m.group(1).replace("-", " "), _fix_time(m.group(2)))
        if dt:
            return dt
    m = re.search(rf"(\d{{1,2}}-\d{{1,2}}-\d{{2,4}})\s*(?:at|,)?\s*{tp}?", t, re.I)
    if m:
        return parse_datetime(m.group(1), _fix_time(m.group(2)))
    return None


def find_direction(t: str) -> str:
    if re.search(r"\b(credited|received|deposit(?:ed)?|reversal)\b", t, re.I):
        return "Credit"
    if re.search(r"\b(debited|sent to|paid to|withdraw|withdrawn|bought|purchase)\b", t, re.I):
        return "Debit"
    return ""


def find_channel(t: str, source: str) -> str:
    if source == MPESA:
        if re.search(r"for account|Pay\s?-?bill", t, re.I):
            return "M-PESA Paybill"
        if re.search(r"paid to", t, re.I):
            return "M-PESA Till"
        if re.search(r"withdraw", t, re.I):
            return "M-PESA Withdrawal"
        if re.search(r"airtime", t, re.I):
            return "M-PESA Airtime"
        if re.search(r"received", t, re.I):
            return "M-PESA Receive"
        if re.search(r"sent to", t, re.I):
            return "M-PESA Send Money"
        if re.search(r"deposit", t, re.I):
            return "M-PESA Deposit"
        return "M-PESA"
    if source == KCB:
        rules = [
            (r"pesa\s?link", "PesaLink"), (r"\bATM\b", "ATM"), (r"\b(POS|card)\b", "Card/POS"),
            (r"\b(RTGS|EFT|SWIFT)\b", "RTGS/EFT"), (r"pay\s?-?bill|\b522522\b|\b522533\b", "Paybill"),
            (r"M-?PESA", "M-PESA"), (r"\b(KCB App|mobile app|app)\b", "KCB App"),
            (r"\b(branch|teller|cash deposit|cheque)\b", "Branch"), (r"transfer", "Bank Transfer"),
        ]
        for pat, label in rules:
            if re.search(pat, t, re.I):
                return label
        return "KCB"
    return ""


def find_bank(t: str, source: str, paybill: str, counterparty: str) -> str:
    if source == KCB:
        return "KCB"
    if paybill and paybill in BANK_PAYBILLS:
        return BANK_PAYBILLS[paybill]
    for num, bank in BANK_PAYBILLS.items():
        if re.search(rf"\b{num}\b", t):
            return bank
    for pat, bank in BANK_KEYWORDS:
        if re.search(pat, counterparty or "", re.I) or re.search(pat + r"\s+Pay\s?bill", t, re.I):
            return bank
    if paybill == "KCB":  # legacy fallback for "KCB Paybill AC"
        return "KCB"
    if source == MPESA:
        return "M-PESA"
    return ""


# ------------------------------------------------------------------ custom rules
def apply_rules(t: str, source: str, rules) -> dict:
    found = {}
    for rule in rules or []:
        if rule.institution not in ("ANY", source) or rule.field in found:
            continue
        try:
            m = re.search(rule.pattern, t, re.I)
        except re.error:
            continue
        if not m:
            continue
        if rule.field in ("channel", "bank"):
            found[rule.field] = rule.fixed_value or (m.group(1) if m.groups() else "")
        elif m.groups():
            found[rule.field] = m.group(1).strip()
    return found


# ------------------------------------------------------------------ main entry
def extract(text: str, selected_source: str = "AUTO", rules=None) -> ExtractionResult:
    t = normalize(text)
    res = ExtractionResult(text=t)
    if not t:
        res.errors.append("Empty message.")
        return res
    if len(t) < 20:
        res.errors.append("Message is too short to be a transaction notification.")
        return res

    detected = detect_source(t)
    assumed_kcb = False
    if detected == UNKNOWN and selected_source not in (MPESA, KCB) and looks_like_bank_sms(t):
        detected, assumed_kcb = KCB, True
    res.source = selected_source if selected_source in (MPESA, KCB) else detected
    if selected_source in (MPESA, KCB) and detected not in (UNKNOWN, selected_source):
        res.warnings.append(f"Source manually set to {selected_source} but message looks like {detected}.")

    if assumed_kcb:
        res.warnings.append("Bank SMS without an explicit bank name – assumed KCB. Please confirm.")

    custom = apply_rules(t, res.source, rules)
    legacy = legacy_parse(t)

    # Amount
    res.amount = to_decimal(custom.get("amount")) if custom.get("amount") else None
    if res.amount is None:
        res.amount = find_amount(t)
    if res.amount is None and legacy.get("amount"):
        res.amount = to_decimal(legacy["amount"])

    # Receipt
    if custom.get("mpesa_receipt"):
        res.mpesa_receipt = custom["mpesa_receipt"].upper()
    elif legacy.get("receipt") and looks_like_receipt(legacy["receipt"]) and not legacy["receipt"].startswith("FT"):
        res.mpesa_receipt = legacy["receipt"].upper()
    else:
        res.mpesa_receipt = find_receipt(t, res.source)

    # Reference / counterparty
    ref, counterparty = find_reference(t, res.source)
    res.reference = custom.get("reference") or ref
    res.counterparty = counterparty

    # Account
    res.account_number = custom.get("account_number") or find_account(t, res.source) or legacy.get("account", "")
    if res.account_number.lower() in {"no", "number", "balance"}:
        res.account_number = ""

    # Date
    if custom.get("date"):
        res.transaction_date = find_datetime(custom["date"])
    if not res.transaction_date:
        res.transaction_date = find_datetime(t)
    if not res.transaction_date and legacy.get("date"):
        res.transaction_date = parse_datetime(legacy["date"], legacy.get("time", ""))

    # Paybill / bank / channel / direction
    m = re.search(r"Pay\s?-?bill\s*(?:No\.?|number)?\s*(\d{5,7})", t, re.I)
    res.paybill = m.group(1) if m else legacy.get("paybill", "")
    res.direction = find_direction(t)
    res.channel = custom.get("channel") or find_channel(t, res.source)
    res.bank = custom.get("bank") or find_bank(t, res.source, res.paybill, counterparty)

    # Recognition
    if res.amount is None and not res.mpesa_receipt:
        res.errors.append(
            "Unrecognized SMS format: no transaction amount or receipt number could be found. "
            "Check the message or set the source manually and retry."
        )
    elif res.source == UNKNOWN:
        res.warnings.append("Institution could not be detected automatically – please confirm Bank/Channel.")
        res.recognized = True
    else:
        res.recognized = True
    if res.recognized and res.amount is None:
        res.warnings.append("Amount not found in SMS.")
    if res.recognized and not res.transaction_date:
        res.warnings.append("Transaction date/time not found in SMS.")
    return res


# ------------------------------------------------------------------ splitting
_START = re.compile(
    rf"(?=(?:^|(?<=\s))(?:{RECEIPT_TOKEN}\s+Confirmed\b|Dear\s+(?:Customer|Valued|[A-Z])|KCB:|Your\s+KCB))"
)


def split_messages(blob: str) -> list[str]:
    """Split a pasted blob into individual messages.

    Messages may be separated by blank lines, by line breaks, or simply run
    together; a new message is recognised by an M-Pesa receipt followed by
    'Confirmed', or a typical bank SMS opening such as 'Dear ...'.
    """
    blob = (blob or "").translate(INVISIBLE).replace("\r\n", "\n").replace("\r", "\n")
    chunks = [c for c in re.split(r"\n\s*\n", blob) if c.strip()]
    messages = []
    for chunk in chunks:
        lines = [ln.strip() for ln in chunk.split("\n") if ln.strip()]
        # If each line is itself a full message, treat lines as messages
        starts = [bool(_START.match(ln)) for ln in lines]
        if len(lines) > 1 and sum(starts) >= 2:
            current = []
            for ln, is_start in zip(lines, starts):
                if is_start and current:
                    messages.append(" ".join(current))
                    current = []
                current.append(ln)
            if current:
                messages.append(" ".join(current))
        else:
            messages.append(" ".join(lines))
    # Split run-together messages on one line
    out = []
    for msg in messages:
        parts = [p.strip() for p in _START.split(msg) if p.strip()]
        out.extend(parts or [msg])
    return [normalize(m) for m in out if normalize(m)]
