from dataclasses import dataclass
from django.urls import reverse
from leases.models import Lease, LeaseFamilyMember, LeaseRenewal

@dataclass
class RoleHistoryRow:
    role_type: str; lease: object; renewal: object; related_tenant: object; property: object; unit: object
    lease_start: object; lease_end: object; balance: object; status: str; status_date: object; detail_url: str
    @property
    def lease_label(self):
        return f"Lease #{self.lease.pk}" + (f" — Renewal #{self.renewal.renewal_number}" if self.renewal else "")


def _balance(lease):
    for name in ("annotated_balance", "balance", "current_balance"):
        value = getattr(lease, name, None)
        if value is not None:
            return value
    try: return lease.get_balance()
    except Exception: return 0


def tenant_role_history(tenant, role=None):
    rows=[]
    leases=Lease.objects.filter(tenant=tenant).select_related("tenant","unit__property")
    if not role:
        for lease in leases:
            rows.append(RoleHistoryRow("Primary Tenant",lease,None,lease.tenant,lease.unit.property,lease.unit,lease.start_date,lease.end_date,_balance(lease),lease.get_status_display(),lease.updated_at,reverse("leases:lease_detail",args=[lease.pk])))
    if role in (None,"family_member"):
        for link in LeaseFamilyMember.objects.filter(family_member=tenant).select_related("lease__tenant","lease__unit__property"):
            l=link.lease; rows.append(RoleHistoryRow("Family Member",l,None,l.tenant,l.unit.property,l.unit,l.start_date,l.end_date,_balance(l),l.get_status_display(),link.updated_at,reverse("leases:lease_detail",args=[l.pk])))
    for key,label,lookup in (("proposer","Proposer","proposer"),("seconder","Seconder","seconder"),("witness","Witness","witness1_tenant"),("witness","Witness","witness2_tenant")):
        if role not in (None,key): continue
        for l in Lease.objects.filter(**{lookup:tenant}).select_related("tenant","unit__property"):
            rows.append(RoleHistoryRow(label,l,None,l.tenant,l.unit.property,l.unit,l.start_date,l.end_date,_balance(l),l.get_status_display(),l.updated_at,reverse("leases:lease_detail",args=[l.pk])))
    if role in (None,"witness"):
        for lookup in ("witness1_tenant","witness2_tenant"):
            for r in LeaseRenewal.objects.filter(**{lookup:tenant}).select_related("lease__tenant","lease__unit__property"):
                l=r.lease; rows.append(RoleHistoryRow("Witness",l,r,l.tenant,l.unit.property,l.unit,r.start_date,r.end_date,_balance(l),"Renewal",r.updated_at,reverse("leases:lease_history_detail",args=[l.pk,r.pk])))
    return rows


def role_counts(tenant):
    rows=tenant_role_history(tenant)
    counts={"family_member":0,"proposer":0,"seconder":0,"witness":0}
    for r in rows:
        key=r.role_type.lower().replace(" ","_")
        if key in counts: counts[key]+=1
    return counts
