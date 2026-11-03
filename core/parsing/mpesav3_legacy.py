"""
Integration of the existing desktop script `mpesav3.py`.

The original script builds a Tkinter GUI at import time, so it cannot be
imported directly inside a web server. Instead we read the file, pull ONLY the
`parse_mpesa_message` function definition out of its syntax tree and compile
that function on its own. If the script cannot be found (e.g. the app runs on
another machine) the vendored copy below – taken verbatim from mpesav3.py – is
used, so behaviour is identical either way.

The legacy function returns: [receipt, amount, paybill, account, date, time]
"""
import ast
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("rhm")


# ---------------------------------------------------------------------------
# Vendored copy of parse_mpesa_message() from mpesav3.py (unchanged logic)
# ---------------------------------------------------------------------------
def _vendored_parse_mpesa_message(message):
    # --- Patterns ---
    receipt_pattern_start = r"^([A-Z0-9]{8,12})\s+Confirmed"  # Receipt at start
    receipt_pattern_ref = r"Ref[.:]?\s*([A-Z0-9]+)"  # Receipt after 'Ref'
    amount_pattern1 = r"Ksh\s*([\d,]+\.\d{2})"  # Ksh500.00 or Ksh 500.00
    amount_pattern2 = r"([\d,]+\.\d{2})\s*KES"  # 500.00 KES
    paybill_pattern = r"(?:Pay[\s-]?Bill|Paybill\s*AC)\s*(\d+)?"  # Pay Bill 522522 OR Paybill AC
    account_pattern = r"account\s+(\S+)"
    date_time_pattern = r"on\s*([\d/]+\s*\w*\s*\d*)\s*at\s*([\d:]+\s*[APM]{0,2})"

    receipt = None
    match_start = re.search(receipt_pattern_start, message, re.IGNORECASE)
    if match_start:
        receipt = match_start.group(1)
    else:
        match_ref = re.search(receipt_pattern_ref, message, re.IGNORECASE)
        if match_ref:
            receipt = match_ref.group(1)

    amount = 0.0
    match_amt1 = re.search(amount_pattern1, message, re.IGNORECASE)
    match_amt2 = re.search(amount_pattern2, message, re.IGNORECASE)
    if match_amt1:
        amount = float(match_amt1.group(1).replace(",", ""))
    elif match_amt2:
        amount = float(match_amt2.group(1).replace(",", ""))

    paybill = ""
    account = ""
    match_paybill = re.search(paybill_pattern, message, re.IGNORECASE)
    if match_paybill:
        paybill = match_paybill.group(1) if match_paybill.group(1) else "KCB"

    match_account = re.search(account_pattern, message, re.IGNORECASE)
    if match_account:
        account = match_account.group(1)

    date = ""
    time = ""
    match_dt = re.search(date_time_pattern, message, re.IGNORECASE)
    if match_dt:
        date = match_dt.group(1).replace(" ", "")
        time = match_dt.group(2)

    return [receipt or "", amount, paybill, account, date, time]


def _load_from_script(path: Path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    func = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse_mpesa_message"), None
    )
    if func is None:
        raise LookupError("parse_mpesa_message() not found in script")
    module = ast.Module(body=[func], type_ignores=[])
    namespace = {"re": re, "__builtins__": __builtins__}
    exec(compile(module, str(path), "exec"), namespace)  # defines the function only – no GUI code runs
    return namespace["parse_mpesa_message"]


@lru_cache(maxsize=1)
def get_legacy_parser():
    """Return (callable, origin_description)."""
    path = Path(getattr(settings, "MPESAV3_SCRIPT_PATH", "") or "")
    if path and path.is_file():
        try:
            fn = _load_from_script(path)
            logger.info("Loaded parse_mpesa_message from %s", path)
            return fn, str(path)
        except Exception as exc:  # pragma: no cover - depends on local file
            logger.warning("Could not load mpesav3.py (%s); using vendored copy", exc)
    return _vendored_parse_mpesa_message, "vendored copy of mpesav3.py"


def legacy_parse(message: str) -> dict:
    fn, _ = get_legacy_parser()
    try:
        receipt, amount, paybill, account, date, time = fn(message)
    except Exception as exc:  # never let the legacy pass break extraction
        logger.warning("Legacy parser error: %s", exc)
        return {}
    return {
        "receipt": (receipt or "").strip(),
        "amount": amount or 0.0,
        "paybill": (paybill or "").strip(),
        "account": (account or "").strip().rstrip(".,;"),
        "date": (date or "").strip(),
        "time": (time or "").strip(),
    }
