"""Shared constants: the standard 13-parameter Excel output schema."""

# (key, Excel header) in the exact required sequence.
STANDARD_PARAMETERS = [
    ("user_email", "User Email"),
    ("report_type", "Report Type"),
    ("item_name", "Item Name"),
    ("quantity", "Quantity"),
    ("unit_cost", "Unit Cost"),
    ("amount", "Amount"),
    ("status", "Status"),
    ("reference", "Reference"),
    ("mpesa_receipt", "MPESA Receipt"),
    ("submission_date", "Submission Date"),
    ("account_number", "Account Number"),
    ("bank", "Bank"),
    ("channel", "Channel"),
]
PARAM_KEYS = [k for k, _ in STANDARD_PARAMETERS]
PARAM_LABELS = dict(STANDARD_PARAMETERS)

# Values stored/written as text (never coerced to numbers)
TEXT_PARAMS = {"reference", "mpesa_receipt", "account_number"}
MONEY_PARAMS = {"unit_cost", "amount"}

# Synonyms used to auto-map template headers to parameters.
HEADER_SYNONYMS = {
    "user_email": ["user email", "email", "user", "submitted by"],
    "report_type": ["report type", "type", "category"],
    "item_name": ["item name", "item", "description", "particulars"],
    "quantity": ["quantity", "qty", "units"],
    "unit_cost": ["unit cost", "unit price", "price", "rate"],
    "amount": ["amount", "total", "value", "amount (kes)", "amount kes"],
    "status": ["status", "state"],
    "reference": ["reference", "ref", "narration", "narrative", "details"],
    "mpesa_receipt": ["mpesa receipt", "m-pesa receipt", "receipt", "receipt no", "transaction id", "mpesa code", "mpesa ref"],
    "submission_date": ["submission date", "date", "transaction date", "date & time", "date time"],
    "account_number": ["account number", "account", "account no", "a/c", "acc no"],
    "bank": ["bank", "institution", "financial institution"],
    "channel": ["channel", "mode", "payment channel", "payment mode"],
}

DEFAULT_STATUS_OPTIONS = [
    "Completed", "Pending", "Extracted", "Awaiting Review", "Approved", "Exported", "Rejected", "Failed",
]
DEFAULT_REPORT_TYPES = ["Offering", "Tithe", "Missions", "Pledge", "Donation", "Thanksgiving", "Project", "Other"]

DATE_DISPLAY_FORMAT = "%d/%m/%Y %I:%M %p"
# Submission Date is shown and exported as a date only, MM/DD/YY.
SUBMISSION_DATE_FORMAT = "%m/%d/%y"
EXCEL_DATE_FORMAT = "mm/dd/yy"
SUBMISSION_DATE_INPUT_FORMATS = ["%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"]
EXCEL_MONEY_FORMAT = "#,##0.00"
