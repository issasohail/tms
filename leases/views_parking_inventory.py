from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST

from leases.forms_parking_inventory import (
    LeaseParkingAllocationForm,
    ParkingPolicyForm,
    ParkingSpaceForm,
)
from leases.models import Lease
from leases.models_parking_inventory import (
    InventoryItemDefinition,
    LeaseInventoryItem,
    LeaseParkingAllocation,
    ParkingPolicy,
    ParkingSpace,
    PropertyInventoryItem,
    UnitInventoryItem,
)
from leases.services.inventory_parking import (
    copy_inventory_defaults,
    effective_inventory,
    effective_parking_policy,
    policy_value,
    sync_lease_field_from_inventory_item,
    sync_parking_recurring_charge,
)
from properties.models import Property, Unit


def _scope_object(scope, pk):
    if scope == "property":
        return get_object_or_404(Property, pk=pk)
    if scope == "unit":
        return get_object_or_404(Unit.objects.select_related("property"), pk=pk)
    if scope == "lease":
        return get_object_or_404(Lease.objects.select_related("unit", "unit__property"), pk=pk)
    raise ValueError("Invalid scope")


def _scope_return_url(scope, obj):
    if scope == "property":
        return "properties:property_detail", obj.pk
    if scope == "unit":
        return "properties:unit_detail", obj.pk
    return "leases:lease_detail", obj.pk


def _inventory_model_target(scope, obj):
    if scope == "property":
        return PropertyInventoryItem, {"property": obj}
    if scope == "unit":
        return UnitInventoryItem, {"unit": obj}
    return LeaseInventoryItem, {"lease": obj}


@login_required
@require_http_methods(["GET", "POST"])
def global_inventory_manage(request):
    if request.method == "POST":
        action = request.POST.get("action") or "save_definition"
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "Enter an inventory item name.")
            return redirect("leases:global_inventory_manage")

        try:
            quantity = max(0, int(request.POST.get("quantity") or 0))
        except ValueError:
            quantity = 0
        try:
            sort_order = max(0, int(request.POST.get("sort_order") or 0))
        except ValueError:
            sort_order = 0

        if action == "add_definition":
            if InventoryItemDefinition.objects.filter(name__iexact=name).exists():
                messages.error(request, f'An inventory item named "{name}" already exists.')
                return redirect("leases:global_inventory_manage")
            base_code = slugify(name).replace("-", "_")[:90] or "inventory_item"
            code = base_code
            suffix = 2
            while InventoryItemDefinition.objects.filter(code=code).exists():
                code = f"{base_code[:96]}_{suffix}"
                suffix += 1
            InventoryItemDefinition.objects.create(
                name=name,
                code=code,
                unit_label=(request.POST.get("unit_label") or "item").strip() or "item",
                default_quantity=quantity,
                default_condition=(request.POST.get("condition") or "").strip(),
                include_in_clause=request.POST.get("include_in_clause") in (
                    "1", "on", "true"
                ),
                sort_order=sort_order,
                is_active=True,
            )
            messages.success(request, f"Inventory item {name} added.")
            return redirect("leases:global_inventory_manage")

        item = get_object_or_404(
            InventoryItemDefinition, pk=request.POST.get("item_id")
        )
        if InventoryItemDefinition.objects.filter(name__iexact=name).exclude(pk=item.pk).exists():
            messages.error(request, f'An inventory item named "{name}" already exists.')
            return redirect("leases:global_inventory_manage")
        item.name = name
        item.unit_label = (request.POST.get("unit_label") or "item").strip() or "item"
        item.default_quantity = quantity
        item.default_condition = (request.POST.get("condition") or "").strip()
        item.include_in_clause = request.POST.get("include_in_clause") in (
            "1", "on", "true"
        )
        item.sort_order = sort_order
        item.is_active = request.POST.get("is_active") in ("1", "on", "true")
        item.save(update_fields=[
            "name", "unit_label", "default_quantity", "default_condition",
            "include_in_clause", "sort_order", "is_active",
        ])
        messages.success(request, f"Inventory item {item.name} updated.")
        return redirect("leases:global_inventory_manage")
    return render(request, "leases/global_inventory_manage.html", {
        "items": InventoryItemDefinition.objects.order_by("sort_order", "name"),
    })


@login_required
@require_http_methods(["GET", "POST"])
def inventory_manage(request, scope, pk):
    obj = _scope_object(scope, pk)
    model, target = _inventory_model_target(scope, obj)
    if request.method == "POST":
        action = request.POST.get("action")
        item_id = request.POST.get("item_id")
        if action in ("copy_item", "copy_all"):
            copy_inventory_defaults(obj, item_id=item_id if action == "copy_item" else None)
            messages.success(request, "Default inventory copied successfully.")
            return redirect("leases:inventory_manage", scope=scope, pk=pk)
        item = get_object_or_404(InventoryItemDefinition, pk=item_id)
        try:
            quantity = max(0, int(request.POST.get("quantity") or 0))
        except ValueError:
            quantity = 0
        defaults = {
            "quantity": quantity,
            "condition": (request.POST.get("condition") or "").strip(),
            "is_included": request.POST.get("is_included") in ("1", "on", "true"),
        }
        if model is LeaseInventoryItem:
            defaults["snapshot_source"] = "lease"
        model.objects.update_or_create(item=item, **target, defaults=defaults)
        if model is LeaseInventoryItem:
            sync_lease_field_from_inventory_item(obj, item, quantity)
        messages.success(request, f"{item.name} inventory updated.")
        return redirect("leases:inventory_manage", scope=scope, pk=pk)

    if scope == "property":
        rows = effective_inventory(property_obj=obj)
        parent_label = "Global Defaults"
    elif scope == "unit":
        rows = effective_inventory(unit=obj)
        parent_label = "Property Defaults"
    else:
        rows = effective_inventory(lease=obj)
        parent_label = "Unit Defaults"
    return render(request, "leases/inventory_manage.html", {
        "scope": scope, "scope_object": obj, "rows": rows,
        "parent_label": parent_label, "return_target": _scope_return_url(scope, obj),
    })


def _policy_target(scope, obj):
    return {scope: obj}


def _parent_parking_policy(scope, obj):
    if scope == "property":
        return effective_parking_policy()
    if scope == "unit":
        return effective_parking_policy(property_obj=obj.property)
    return effective_parking_policy(unit=obj.unit)


@login_required
@require_http_methods(["GET", "POST"])
def parking_manage(request, scope, pk):
    obj = _scope_object(scope, pk)
    target = _policy_target(scope, obj)
    policy = ParkingPolicy.objects.filter(**target).first()
    parent_policy = _parent_parking_policy(scope, obj)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "copy_policy":
            policy, _ = ParkingPolicy.objects.update_or_create(**target, defaults={
                "enabled": policy_value(parent_policy, "enabled"),
                "monthly_rate": policy_value(parent_policy, "monthly_rate"),
                "unauthorized_parking_penalty": policy_value(parent_policy, "unauthorized_parking_penalty"),
            })
            messages.success(request, "Parent parking defaults copied.")
        elif action == "save_policy":
            form = ParkingPolicyForm(request.POST, instance=policy)
            if form.is_valid():
                policy = form.save(commit=False)
                for key, value in target.items():
                    setattr(policy, key, value)
                policy.save()
                messages.success(request, "Parking policy updated.")
            else:
                messages.error(request, "Please correct the parking policy values.")
        elif action == "add_space" and scope == "property":
            form = ParkingSpaceForm(request.POST)
            if form.is_valid():
                space = form.save(commit=False)
                space.property = obj
                space.save()
                messages.success(request, f"Parking space {space.label} added.")
            else:
                messages.error(request, "Parking space could not be added.")
        elif action == "allocate" and scope == "lease":
            form = LeaseParkingAllocationForm(request.POST, lease=obj)
            if form.is_valid():
                allocation = form.save(commit=False)
                allocation.lease = obj
                allocation.save()
                sync_parking_recurring_charge(allocation)
                messages.success(request, f"Parking space {allocation.parking_space.label} allocated.")
            else:
                messages.error(request, "Parking allocation could not be saved.")
        return redirect("leases:parking_manage", scope=scope, pk=pk)

    effective_policy = effective_parking_policy(
        lease=obj if scope == "lease" else None,
        unit=obj if scope == "unit" else None,
        property_obj=obj if scope == "property" else None,
    )
    policy_initial = {
        "enabled": policy_value(effective_policy, "enabled"),
        "monthly_rate": policy_value(effective_policy, "monthly_rate"),
        "unauthorized_parking_penalty": policy_value(effective_policy, "unauthorized_parking_penalty"),
    }
    property_obj = obj if scope == "property" else (obj.property if scope == "unit" else obj.unit.property)
    allocation_form = None
    allocations = []
    if scope == "lease":
        allocation_form = LeaseParkingAllocationForm(
            lease=obj,
            initial={"agreed_monthly_rate": policy_value(effective_policy, "monthly_rate"), "start_date": timezone.localdate()},
        )
        allocations = obj.parking_allocations.select_related("parking_space", "vehicle", "recurring_charge")
    return render(request, "leases/parking_manage.html", {
        "scope": scope, "scope_object": obj,
        "policy": policy, "effective_policy": effective_policy,
        "policy_form": ParkingPolicyForm(instance=policy, initial=policy_initial),
        "space_form": ParkingSpaceForm() if scope == "property" else None,
        "spaces": property_obj.parking_spaces.select_related("vehicle_type").all(),
        "allocation_form": allocation_form, "allocations": allocations,
        "return_target": _scope_return_url(scope, obj),
    })


@login_required
@require_POST
def parking_allocation_end(request, pk):
    allocation = get_object_or_404(LeaseParkingAllocation, pk=pk)
    allocation.is_active = False
    allocation.end_date = timezone.localdate()
    allocation.save()
    sync_parking_recurring_charge(allocation)
    messages.success(request, f"Parking allocation {allocation.parking_space.label} ended.")
    return redirect("leases:parking_manage", scope="lease", pk=allocation.lease_id)
