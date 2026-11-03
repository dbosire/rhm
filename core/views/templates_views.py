from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.constants import STANDARD_PARAMETERS
from core.forms import TemplateUploadForm
from core.models import ColumnMapping
from core.services import audit, excel
from core.services.access import visible_mappings, visible_templates
from core.services.excel_safety import UnsafeWorkbook, check_xlsx, open_safe_workbook


@login_required
def template_list(request):
    if request.method == "POST":
        form = TemplateUploadForm(request.POST, request.FILES)
        if form.is_valid():
            up = form.cleaned_data["upload"]
            try:
                check_xlsx(up.file)
                wb = open_safe_workbook(up.file, read_only=True)
                sheets = list(wb.sheetnames)
                wb.close()
                up.file.seek(0)
            except UnsafeWorkbook as exc:
                form.add_error("upload", str(exc))
            else:
                tpl = form.save(commit=False)
                tpl.owner = request.user
                tpl.original_filename = up.name[:255]
                tpl.size_bytes = up.size
                tpl.sheet_names = sheets
                if not request.user.is_admin_role:
                    tpl.is_shared = False
                tpl.file.save("template.xlsx", up, save=False)
                tpl.save()
                audit.log(request, "template.upload", tpl, filename=tpl.original_filename)
                messages.success(request, f"Template '{tpl.name}' uploaded.")
                return redirect("core:template_preview", pk=tpl.pk)
    else:
        form = TemplateUploadForm()
    return render(request, "core/template_list.html", {
        "form": form, "templates": visible_templates(request.user), "mappings": visible_mappings(request.user),
    })


@login_required
def template_preview(request, pk):
    tpl = get_object_or_404(visible_templates(request.user), pk=pk)
    try:
        wb = excel.load_template(tpl.file, read_only=False)
    except (UnsafeWorkbook, FileNotFoundError) as exc:
        messages.error(request, f"Cannot open template: {exc}")
        return redirect("core:template_list")
    sheet = request.GET.get("sheet") or wb.sheetnames[0]
    if sheet not in wb.sheetnames:
        sheet = wb.sheetnames[0]
    ws = wb[sheet]
    rows, cols = excel.preview_rows(ws)
    header_row = excel.guess_header_row(ws)
    headers = excel.read_headers(ws, header_row)
    return render(request, "core/template_preview.html", {
        "tpl": tpl, "sheets": wb.sheetnames, "sheet": sheet, "rows": rows, "cols": cols,
        "header_row": header_row, "headers": headers, "mappings": tpl.mappings.all(),
    })


@login_required
def template_mapping(request, pk):
    tpl = get_object_or_404(visible_templates(request.user), pk=pk)
    wb = excel.load_template(tpl.file, read_only=False)
    existing = None
    if request.GET.get("mapping"):
        existing = visible_mappings(request.user).filter(pk=request.GET["mapping"], template=tpl).first()
    sheet = request.POST.get("sheet") or request.GET.get("sheet") or (existing.sheet_name if existing else wb.sheetnames[0])
    if sheet not in wb.sheetnames:
        sheet = wb.sheetnames[0]
    ws = wb[sheet]

    def _int(v, default):
        try:
            return max(int(v), 1)
        except (TypeError, ValueError):
            return default

    header_row = _int(request.POST.get("header_row") or request.GET.get("header_row"),
                      existing.header_row if existing else excel.guess_header_row(ws))
    data_start_row = _int(request.POST.get("data_start_row") or request.GET.get("data_start_row"),
                          existing.data_start_row if existing else header_row + 1)
    headers = excel.read_headers(ws, header_row)
    rows, cols = excel.preview_rows(ws, max_rows=max(header_row + 5, 12))

    if request.method == "POST" and request.POST.get("save"):
        mapping = {}
        valid_cols = set(cols) | set(headers.keys())
        for key, _ in STANDARD_PARAMETERS:
            col = (request.POST.get(f"map_{key}") or "").strip().upper()
            if col and col in valid_cols:
                mapping[key] = col
        name = (request.POST.get("name") or "").strip()[:120] or f"{tpl.name} – {sheet}"
        if not mapping:
            messages.error(request, "Map at least one parameter to a column.")
        elif len(set(mapping.values())) != len(mapping):
            messages.error(request, "Each column can only be mapped to one parameter.")
        else:
            try:
                obj = existing if existing and existing.owner_id == request.user.pk else ColumnMapping(owner=request.user, template=tpl)
                obj.name, obj.sheet_name, obj.header_row, obj.data_start_row, obj.mapping = (
                    name, sheet, header_row, max(data_start_row, header_row + 1), mapping)
                obj.save()
            except IntegrityError:
                messages.error(request, "You already have a mapping with that name for this template.")
            else:
                audit.log(request, "mapping.save", obj, template=tpl.pk)
                messages.success(request, f"Mapping '{obj.name}' saved.")
                return redirect("core:template_preview", pk=tpl.pk)
        current = mapping
    else:
        current = existing.mapping if existing and existing.sheet_name == sheet and existing.header_row == header_row else excel.auto_map(headers)

    params = [(k, label, current.get(k, "")) for k, label in STANDARD_PARAMETERS]
    return render(request, "core/template_mapping.html", {
        "tpl": tpl, "sheets": wb.sheetnames, "sheet": sheet, "header_row": header_row,
        "data_start_row": data_start_row, "headers": headers, "params": params, "existing": existing,
        "rows": rows, "cols": cols,
    })


@login_required
@require_POST
def template_delete(request, pk):
    tpl = get_object_or_404(visible_templates(request.user), pk=pk)
    if tpl.owner_id != request.user.pk and not request.user.is_admin_role:
        messages.error(request, "Only the owner or an administrator can delete this template.")
        return redirect("core:template_list")
    name = tpl.name
    tpl.reports.update(template=None)
    file_name = tpl.file.name
    tpl.delete()
    try:
        from core.models import private_storage

        private_storage.delete(file_name)
    except Exception:
        pass
    audit.log(request, "template.delete", None, name=name)
    messages.success(request, f"Template '{name}' deleted (generated reports are kept).")
    return redirect("core:template_list")


@login_required
@require_POST
def mapping_delete(request, pk):
    obj = get_object_or_404(visible_mappings(request.user), pk=pk)
    if obj.owner_id != request.user.pk and not request.user.is_admin_role:
        messages.error(request, "Only the owner can delete this mapping.")
    else:
        obj.delete()
        messages.success(request, "Mapping deleted.")
    return redirect("core:template_list")


@login_required
def template_info_api(request, pk):
    """JSON: sheets, guessed header row, headers and auto-mapping for the generate page."""
    tpl = get_object_or_404(visible_templates(request.user), pk=pk)
    wb = excel.load_template(tpl.file, read_only=False)
    sheet = request.GET.get("sheet") or wb.sheetnames[0]
    if sheet not in wb.sheetnames:
        sheet = wb.sheetnames[0]
    ws = wb[sheet]
    try:
        header_row = max(int(request.GET.get("header_row")), 1)
    except (TypeError, ValueError):
        header_row = excel.guess_header_row(ws)
    headers = excel.read_headers(ws, header_row)
    mapping = excel.auto_map(headers)
    return JsonResponse({
        "sheets": wb.sheetnames, "sheet": sheet, "header_row": header_row, "headers": headers,
        "auto_mapping": {k: v for k, v in mapping.items()},
        "mappings": [
            {"id": m.pk, "name": m.name, "sheet": m.sheet_name}
            for m in visible_mappings(request.user).filter(template=tpl)
        ],
    })
