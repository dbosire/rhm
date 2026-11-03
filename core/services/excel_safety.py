"""Defensive checks before any uploaded workbook is opened, plus formula-injection protection."""
import re
import zipfile

import openpyxl

try:  # openpyxl uses defusedxml automatically when it is installed
    import defusedxml  # noqa: F401
except ImportError:  # pragma: no cover
    defusedxml = None

MAX_UNCOMPRESSED = 60 * 1024 * 1024
MAX_RATIO = 150
MAX_ENTRIES = 2000


class UnsafeWorkbook(Exception):
    pass


def check_xlsx(fileobj):
    """Validate an .xlsx container: real zip, Office structure, no macros/external links, no zip bomb."""
    pos = fileobj.tell() if hasattr(fileobj, "tell") else 0
    head = fileobj.read(4)
    fileobj.seek(pos)
    if head != b"PK\x03\x04":
        raise UnsafeWorkbook("The file is not a valid .xlsx workbook.")
    try:
        with zipfile.ZipFile(fileobj) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            if len(infos) > MAX_ENTRIES:
                raise UnsafeWorkbook("Workbook contains too many parts.")
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise UnsafeWorkbook("The file is not a valid Excel workbook.")
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                raise UnsafeWorkbook("Macro-enabled workbooks are not allowed.")
            if any(n.startswith("xl/externalLinks/") for n in names):
                raise UnsafeWorkbook("Workbooks with external data links are not allowed.")
            if any(n.lower().endswith((".bin", ".exe", ".dll")) and "printerSettings" not in n for n in names):
                raise UnsafeWorkbook("Workbook contains embedded binary objects.")
            total = sum(i.file_size for i in infos)
            packed = sum(i.compress_size for i in infos) or 1
            if total > MAX_UNCOMPRESSED or total / packed > MAX_RATIO:
                raise UnsafeWorkbook("Workbook is too large or abnormally compressed.")
    except zipfile.BadZipFile:
        raise UnsafeWorkbook("The file is corrupted or not a valid .xlsx workbook.")
    finally:
        fileobj.seek(pos)


def open_safe_workbook(fileobj, **kwargs):
    check_xlsx(fileobj)
    try:
        return openpyxl.load_workbook(fileobj, keep_vba=False, keep_links=False, **kwargs)
    except Exception as exc:
        raise UnsafeWorkbook(f"Could not read workbook: {exc}")


_DANGEROUS = re.compile(r"^[=+@\t\r]|^-(?![\d.,\s]*$)")


def safe_text(value):
    """Neutralise spreadsheet formula injection in user/SMS-derived text."""
    if value is None:
        return None
    s = str(value)
    if _DANGEROUS.match(s):
        return "'" + s
    return s
