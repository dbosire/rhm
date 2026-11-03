from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import UserCreateForm, UserEditForm
from accounts.models import User
from accounts.permissions import admin_required
from core.forms import AppSettingForm, DownloadPasswordForm, OptionForm, ParserRuleForm
from core.models import AppSetting, AuditLog, Option, ParserRule
from core.services import audit

from .common import paginate


@admin_required
def user_list(request):
    qs = User.objects.annotate(n_txn=Count("transactions")).order_by("email")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(email__icontains=q) | qs.filter(full_name__icontains=q)
    return render(request, "core/admin/user_list.html", {"users": qs, "q": q})


@admin_required
def user_form(request, pk=None):
    obj = get_object_or_404(User, pk=pk) if pk else None
    FormCls = UserEditForm if obj else UserCreateForm
    if request.method == "POST":
        form = FormCls(request.POST, instance=obj)
        if form.is_valid():
            if obj and obj.pk == request.user.pk and (not form.cleaned_data["is_active"] or form.cleaned_data["role"] != User.Role.ADMIN):
                form.add_error(None, "You cannot deactivate or demote your own account.")
            else:
                user = form.save()
                audit.log(request, "user.update" if obj else "user.create", user, role=user.role, active=user.is_active)
                messages.success(request, f"User {user.email} saved.")
                return redirect("core:user_list")
    else:
        form = FormCls(instance=obj)
    return render(request, "core/admin/user_form.html", {"form": form, "obj": obj})


@admin_required
@require_POST
def user_toggle(request, pk):
    obj = get_object_or_404(User, pk=pk)
    if obj.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
    else:
        obj.is_active = not obj.is_active
        obj.save(update_fields=["is_active"])
        audit.log(request, "user.activate" if obj.is_active else "user.deactivate", obj)
        messages.success(request, f"{obj.email} {'activated' if obj.is_active else 'deactivated'}.")
    return redirect("core:user_list")


@admin_required
def app_settings(request):
    setting = AppSetting.load()
    pw_form = DownloadPasswordForm(prefix="pw")
    st_form = AppSettingForm(instance=setting, prefix="st")
    opt_form = OptionForm(prefix="opt")
    rule_form = ParserRuleForm(prefix="rule")
    if request.method == "POST":
        section = request.POST.get("section")
        if section == "password":
            pw_form = DownloadPasswordForm(request.POST, prefix="pw")
            if pw_form.is_valid():
                setting.set_download_password(pw_form.cleaned_data["new_password"])
                setting.updated_by = request.user
                setting.save()
                audit.log(request, "settings.download_password_changed", setting)
                messages.success(request, "Download password updated.")
                return redirect("core:settings")
        elif section == "general":
            st_form = AppSettingForm(request.POST, instance=setting, prefix="st")
            if st_form.is_valid():
                s = st_form.save(commit=False)
                s.updated_by = request.user
                s.save()
                audit.log(request, "settings.update", setting)
                messages.success(request, "Settings saved.")
                return redirect("core:settings")
        elif section == "option":
            opt_form = OptionForm(request.POST, prefix="opt")
            if opt_form.is_valid():
                if Option.objects.filter(kind=opt_form.cleaned_data["kind"], value=opt_form.cleaned_data["value"]).exists():
                    opt_form.add_error("value", "That option already exists.")
                else:
                    opt_form.save()
                    messages.success(request, "Option added.")
                    return redirect("core:settings")
        elif section == "rule":
            rule_form = ParserRuleForm(request.POST, prefix="rule")
            if rule_form.is_valid():
                rule = rule_form.save()
                audit.log(request, "parser_rule.create", rule, pattern=rule.pattern)
                messages.success(request, "Parser rule added.")
                return redirect("core:settings")
    return render(request, "core/admin/settings.html", {
        "setting": setting, "pw_form": pw_form, "st_form": st_form, "opt_form": opt_form, "rule_form": rule_form,
        "options": Option.objects.all(), "rules": ParserRule.objects.all(),
    })


@admin_required
@require_POST
def option_toggle(request, pk):
    opt = get_object_or_404(Option, pk=pk)
    if request.POST.get("delete"):
        opt.delete()
    else:
        opt.is_active = not opt.is_active
        opt.save(update_fields=["is_active"])
    return redirect("core:settings")


@admin_required
@require_POST
def rule_toggle(request, pk):
    rule = get_object_or_404(ParserRule, pk=pk)
    if request.POST.get("delete"):
        audit.log(request, "parser_rule.delete", rule)
        rule.delete()
    else:
        rule.is_active = not rule.is_active
        rule.save(update_fields=["is_active"])
    return redirect("core:settings")


@admin_required
def audit_log(request):
    qs = AuditLog.objects.select_related("user")
    action = request.GET.get("action")
    if action:
        qs = qs.filter(action__startswith=action)
    if request.GET.get("failed"):
        qs = qs.filter(success=False)
    page, query = paginate(request, qs, 50)
    actions = sorted({a.split(".")[0] for a in AuditLog.objects.values_list("action", flat=True).distinct()})
    return render(request, "core/admin/audit_log.html", {"page": page, "query": query, "actions": actions, "action": action})
