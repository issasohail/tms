import json
from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader, PdfWriter

from leases.models import Lease, LeaseDocument, LeaseDocumentCategory
from leases.models_renewal import LeaseRenewal
from properties.models import Property, Unit
from punjab_estamp.models import (
    LeaseEStampWorkflow,
    PunjabEStampDistrict,
    PunjabEStampPurpose,
    PunjabEStampRelation,
    PunjabEStampTehsil,
)
from punjab_estamp.services.workflow import (
    applicant_snapshot,
    mark_invalid,
    mark_paid,
    mark_stamp_issued,
    mark_uploaded,
    missing_configuration,
    payment_message,
    prepare_replacement,
    prepare_workflow,
    record_challan,
    workflow_card_context,
    workflow_comment,
)
from tenants.models import Tenant


class PunjabEStampWorkflowServiceTests(TestCase):
    def setUp(self):
        # Phase 7/seed migrations populate these portal reference rows in the
        # test database. Reuse/update them rather than inserting duplicates.
        self.relation, _ = PunjabEStampRelation.objects.update_or_create(
            portal_value="33",
            defaults={"name": "S/O", "active": True, "sort_order": 1},
        )
        self.district, _ = PunjabEStampDistrict.objects.update_or_create(
            portal_value="18",
            defaults={"name": "Rawalpindi", "active": True, "sort_order": 1},
        )
        self.tehsil, _ = PunjabEStampTehsil.objects.update_or_create(
            district=self.district,
            portal_value="72",
            defaults={"name": "Rawalpindi", "active": True, "sort_order": 1},
        )
        self.purpose, _ = PunjabEStampPurpose.objects.update_or_create(
            portal_value="208",
            defaults={
                "name": "Lease Agreement",
                "denomination": 100,
                "default_continuation_sheets": 1,
                "active": True,
                "is_default_for_lease": True,
                "sort_order": 1,
            },
        )
        self.owner = Tenant.objects.create(
            first_name="Owner",
            last_name="Father",
            relation=self.relation,
            cnic="3520212345671",
            phone="03001234567",
            email="owner@example.com",
            permanent_address="Owner Address",
        )
        self.caretaker = Tenant.objects.create(
            first_name="Caretaker",
            last_name="Guardian",
            relation=self.relation,
            cnic="3520212345672",
            phone="03007654321",
            email="caretaker@example.com",
            permanent_address="Caretaker Address",
        )
        self.primary_tenant = Tenant.objects.create(
            first_name="Primary",
            last_name="Tenant",
            cnic="3520212345673",
        )
        self.property = Property.objects.create(
            property_name="F56",
            owner_tenant=self.owner,
            owner_name="Legacy Owner",
            owner_father_name="Legacy Father",
            relation="S/O",
            owner_cnic="3520212345671",
            owner_phone="03001234567",
            owner_address="Legacy Owner Address",
            property_address1="F56 Street",
            property_city="Rawalpindi",
            property_state="Punjab",
            property_zipcode="46000",
            zila=self.district,
            tehsil=self.tehsil,
            type="house",
            property_type="house",
            total_units=1,
        )
        self.unit = Unit.objects.create(property=self.property, unit_number="Flat 2")
        self.lease = Lease.objects.create(
            tenant=self.primary_tenant,
            unit=self.unit,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_rent="25000.00",
        )
        self.history = LeaseRenewal.objects.create(
            lease=self.lease,
            renewal_number=1,
            is_original=True,
            start_date=self.lease.start_date,
            end_date=self.lease.end_date,
            monthly_rent=self.lease.monthly_rent,
        )
        self.user = get_user_model().objects.create_superuser(
            username="estamp-admin",
            email="estamp@example.com",
            password="test-password",
        )

    def test_applicant_priority_uses_linked_tenants(self):
        self.assertEqual(applicant_snapshot(self.lease)["source"], "linked_owner")

        self.property.caretaker_name = "Legacy Caretaker"
        self.property.caretaker_father_name = "Legacy Guardian"
        self.property.caretaker_relation = "S/O."
        self.property.caretaker_cnic = "3520212345674"
        self.property.caretaker_phone = "03001111111"
        self.property.caretaker_address = "Legacy Caretaker Address"
        self.property.save()
        legacy = applicant_snapshot(self.lease)
        self.assertEqual(legacy["source"], "linked_owner")
        self.assertEqual(legacy["relation_portal_value"], "33")

        self.property.caretaker_tenant = self.caretaker
        self.property.save(update_fields=["caretaker_tenant"])
        linked = applicant_snapshot(self.lease)
        self.assertEqual(linked["source"], "linked_owner")
        self.assertEqual(linked["record_id"], self.owner.pk)

        self.property.caretaker_tenant = None
        self.property.caretaker_name = ""
        self.property.caretaker_cnic = ""
        self.property.caretaker_phone = ""
        self.property.caretaker_address = ""
        self.property.owner_tenant = None
        self.property.save()
        self.assertEqual(applicant_snapshot(self.lease)["source"], "")

    def test_missing_relation_mapping_blocks_launch(self):
        self.owner.relation = None
        self.owner.relation_legacy = "Unknown relation"
        self.owner.save(update_fields=["relation", "relation_legacy"])
        self.assertIn(
            "active Punjab Applicant Relation mapping",
            missing_configuration(self.lease),
        )
        with self.assertRaises(ValidationError):
            prepare_workflow(self.lease, self.history, self.user)

    def test_comment_and_repeated_prepare_are_idempotent(self):
        self.assertEqual(
            workflow_comment(self.lease),
            "F56 Flat 2 - Primary Tenant",
        )
        first, created = prepare_workflow(self.lease, self.history, self.user)
        second, created_again = prepare_workflow(self.lease, self.history, self.user)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            LeaseEStampWorkflow.objects.filter(
                lease=self.lease, lease_history=self.history
            ).count(),
            1,
        )

    def test_first_challan_freezes_snapshot_and_reuses_identifiers(self):
        workflow, _ = prepare_workflow(self.lease, self.history, self.user)
        workflow, created = record_challan(
            workflow, "2026F123", "40170000000000001", self.user
        )
        self.assertTrue(created)
        self.assertEqual(workflow.applicant_snapshot["name"], "Owner")
        self.assertEqual(workflow.property_snapshot["district_portal_value"], "18")
        self.assertEqual(workflow.portal_snapshot["purpose_portal_value"], "208")
        self.assertEqual(
            workflow.portal_snapshot["reason"],
            "F56 Flat 2 - Primary Tenant",
        )

        self.owner.first_name = "Changed Owner"
        self.owner.phone = "03009999999"
        self.owner.relation = None
        self.owner.save(update_fields=["first_name", "phone", "relation"])
        self.property.zila = None
        self.property.tehsil = None
        self.property.save(update_fields=["zila", "tehsil"])
        same, created_again = record_challan(
            workflow, "2026F123", "40170000000000001", self.user
        )
        self.assertFalse(created_again)
        self.assertEqual(same.applicant_snapshot["name"], "Owner")
        prepared_again, prepare_created = prepare_workflow(
            self.lease, self.history, self.user
        )
        self.assertFalse(prepare_created)
        self.assertEqual(prepared_again.pk, workflow.pk)
        self.assertEqual(workflow_card_context(self.lease, self.history)["missing"], [])
        with self.assertRaises(ValidationError):
            record_challan(workflow, "DIFFERENT", "999", self.user)

    def test_new_renewal_uses_current_identity(self):
        first, _ = prepare_workflow(self.lease, self.history, self.user)
        record_challan(first, "FIRST", "11111111111111111", self.user)
        self.owner.first_name = "Current Owner"
        self.owner.save(update_fields=["first_name"])
        renewal = LeaseRenewal.objects.create(
            lease=self.lease,
            renewal_number=2,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
            monthly_rent="27500.00",
        )
        second, _ = prepare_workflow(self.lease, renewal, self.user)
        second, _ = record_challan(second, "SECOND", "22222222222222222", self.user)
        self.assertEqual(second.applicant_snapshot["name"], "Current Owner")
        self.assertNotEqual(first.pk, second.pk)

    def test_state_transitions_replacement_audit_and_whatsapp(self):
        self.property.caretaker_tenant = self.caretaker
        self.property.save(update_fields=["caretaker_tenant"])
        workflow, _ = prepare_workflow(self.lease, self.history, self.user)
        workflow, _ = record_challan(
            workflow, "CHALLAN-1", "40170000000000001", self.user
        )
        message = payment_message(workflow)
        self.assertIn("Punjab e-Stamp Challan", message)
        self.assertIn("Tenant: Primary Tenant", message)
        self.assertIn(
            "PSID - Copy this number: `40170000000000001`",
            message,
        )
        self.assertEqual(workflow.applicant_snapshot["phone"], "+923001234567")

        workflow, changed = mark_paid(workflow, self.user)
        self.assertTrue(changed)
        workflow, changed = mark_stamp_issued(workflow, "STAMP-1", self.user)
        self.assertTrue(changed)
        document = LeaseDocument.objects.create(
            lease=self.lease,
            lease_history=self.history,
            category="estamp_paper",
            file=SimpleUploadedFile("stamp.pdf", b"%PDF-1.4\n%%EOF"),
            original_filename="stamp.pdf",
            display_name="stamp.pdf",
            uploaded_by=self.user,
        )
        workflow, changed = mark_uploaded(workflow, document, self.user)
        self.assertTrue(changed)
        with self.assertRaises(ValidationError):
            mark_invalid(workflow, "not found", self.user)

        workflow.status = workflow.STATUS_STAMP_ISSUED
        workflow.lease_document = None
        workflow.save(update_fields=["status", "lease_document"])
        workflow = mark_invalid(workflow, "Portal says invalid", self.user)
        workflow = prepare_replacement(workflow, self.user)
        self.assertEqual(workflow.status, workflow.STATUS_READY)
        self.assertEqual(workflow.replacement_count, 1)
        self.assertEqual(workflow.challan_number, "")
        self.assertEqual(workflow.challan_history[0]["challan_number"], "CHALLAN-1")
        self.assertEqual(workflow.applicant_snapshot["name"], "Owner")

    def test_state_api_exposes_no_otp_or_pin_fields(self):
        workflow, _ = prepare_workflow(self.lease, self.history, self.user)
        record_challan(workflow, "API-CHALLAN", "40170000000000001", self.user)
        self.client.force_login(self.user)
        response = self.client.get(
            reverse(
                "punjab_estamp:state",
                args=[self.lease.pk, self.history.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        def keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key.lower()
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)

        self.assertNotIn("otp", set(keys(payload)))
        self.assertNotIn("pin", set(keys(payload)))

    def test_portal_launch_is_dry_run_and_has_no_secret_fields(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                "punjab_estamp:launch",
                args=[self.lease.pk, self.history.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["flow"]["dryRun"])
        self.assertEqual(payload["flow"]["stage"], "challan")
        self.assertTrue(payload["flow"]["launchToken"])
        self.assertIn("AddChallanForWhitePaper", payload["portal_url"])
        self.assertIn("name=GenerateChallan", payload["portal_url"])
        self.assertIn("agree=true", payload["portal_url"])
        serialized = str(payload).lower()
        self.assertNotIn("'otp'", serialized)
        self.assertNotIn("'pin'", serialized)

    def test_signed_launch_snapshot_and_exact_challan_event(self):
        self.client.force_login(self.user)
        launch = self.client.post(
            reverse(
                "punjab_estamp:launch",
                args=[self.lease.pk, self.history.pk],
            )
        ).json()
        workflow_id = launch["flow"]["workflowId"]
        event_url = reverse("punjab_estamp:event", args=[workflow_id])
        self.owner.first_name = "Changed After Launch"
        self.owner.save(update_fields=["first_name"])
        response = self.client.post(
            event_url,
            data={
                "action": "challan_generated",
                "challan_number": "2026F123",
                "psid": "40170000000000001",
                "launch_token": launch["flow"]["launchToken"],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        workflow = LeaseEStampWorkflow.objects.get(pk=workflow_id)
        self.assertEqual(workflow.applicant_snapshot["name"], "Owner")

        mismatch = self.client.post(
            event_url,
            data={
                "action": "stamp_found",
                "challan_number": "ANOTHER-CHALLAN",
                "stamp_number": "STAMP-1",
            },
            content_type="application/json",
        )
        self.assertEqual(mismatch.status_code, 422)
        workflow.refresh_from_db()
        self.assertEqual(workflow.status, workflow.STATUS_CHALLAN_GENERATED)

        matched = self.client.post(
            event_url,
            data={
                "action": "stamp_found",
                "challan_number": " 2026f123 ",
                "stamp_number": "STAMP-1",
            },
            content_type="application/json",
        )
        self.assertEqual(matched.status_code, 200)
        workflow.refresh_from_db()
        self.assertEqual(workflow.status, workflow.STATUS_STAMP_ISSUED)

    def test_applicant_default_is_owner_and_explicit_caretaker_is_supported(self):
        self.property.caretaker_tenant = self.caretaker
        self.property.save(update_fields=["caretaker_tenant"])

        default_applicant = applicant_snapshot(self.lease)
        self.assertEqual(default_applicant["source"], "linked_owner")
        self.assertEqual(default_applicant["record_id"], self.owner.pk)

        owner_applicant = applicant_snapshot(self.lease, "owner")
        self.assertEqual(owner_applicant["source"], "linked_owner")
        self.assertEqual(owner_applicant["record_id"], self.owner.pk)

        caretaker_applicant = applicant_snapshot(self.lease, "caretaker")
        self.assertEqual(caretaker_applicant["source"], "linked_caretaker")
        self.assertEqual(caretaker_applicant["record_id"], self.caretaker.pk)


    def test_applicant_default_falls_back_to_caretaker_when_owner_missing(self):
        self.property.owner_tenant = None
        self.property.caretaker_tenant = self.caretaker
        self.property.save(
            update_fields=[
                "owner_tenant",
                "caretaker_tenant",
            ]
        )

        applicant = applicant_snapshot(self.lease)

        self.assertEqual(applicant["source"], "linked_caretaker")
        self.assertEqual(applicant["record_id"], self.caretaker.pk)


    def test_explicit_unavailable_caretaker_cannot_launch(self):
        self.client.force_login(self.user)
        self.property.caretaker_tenant = None
        self.property.save(update_fields=["caretaker_tenant"])

        response = self.client.post(
            reverse(
                "punjab_estamp:launch",
                args=[self.lease.pk, self.history.pk],
            ),
            data=json.dumps(
                {
                    "applicant_source": "caretaker",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 422)


    def test_explicit_caretaker_launch_uses_caretaker_snapshot(self):
        self.client.force_login(self.user)
        self.property.caretaker_tenant = self.caretaker
        self.property.save(update_fields=["caretaker_tenant"])

        response = self.client.post(
            reverse(
                "punjab_estamp:launch",
                args=[self.lease.pk, self.history.pk],
            ),
            data=json.dumps(
                {
                    "applicant_source": "caretaker",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        payload = response.json()
        flow = payload["flow"]

        self.assertEqual(
            flow["applicant"]["source"],
            "linked_caretaker",
        )
        self.assertEqual(
            flow["applicant"]["record_id"],
            self.caretaker.pk,
        )


    def test_signed_launch_snapshot_freezes_selected_caretaker(self):
        self.client.force_login(self.user)
        self.property.caretaker_tenant = self.caretaker
        self.property.save(update_fields=["caretaker_tenant"])

        launch = self.client.post(
            reverse(
                "punjab_estamp:launch",
                args=[self.lease.pk, self.history.pk],
            ),
            data=json.dumps(
                {
                    "applicant_source": "caretaker",
                }
            ),
            content_type="application/json",
        ).json()

        workflow_id = launch["flow"]["workflowId"]

        self.caretaker.first_name = "Changed After Launch"
        self.caretaker.save(update_fields=["first_name"])

        event_url = reverse(
            "punjab_estamp:event",
            args=[workflow_id],
        )

        response = self.client.post(
            event_url,
            data=json.dumps(
                {
                    "action": "challan_generated",
                    "challan_number": "CARETAKER-TEST-1",
                    "psid": "40170000000000009",
                    "launch_token": launch["flow"]["launchToken"],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        workflow = LeaseEStampWorkflow.objects.get(
            pk=workflow_id
        )

        self.assertEqual(
            workflow.applicant_snapshot["source"],
            "linked_caretaker",
        )
        self.assertEqual(
            workflow.applicant_snapshot["name"],
            "Caretaker",
        )


    def test_existing_challan_ignores_opposite_applicant_source(self):
        self.client.force_login(self.user)
        self.property.caretaker_tenant = self.caretaker
        self.property.save(update_fields=["caretaker_tenant"])

        workflow, _ = prepare_workflow(
            self.lease,
            self.history,
            self.user,
        )

        workflow, _ = record_challan(
            workflow,
            "LOCKED-OWNER-1",
            "40170000000000010",
            self.user,
        )

        self.assertEqual(
            workflow.applicant_snapshot["source"],
            "linked_owner",
        )

        response = self.client.post(
            reverse(
                "punjab_estamp:launch",
                args=[self.lease.pk, self.history.pk],
            ),
            data=json.dumps(
                {
                    "applicant_source": "caretaker",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        workflow.refresh_from_db()

        self.assertEqual(
            workflow.applicant_snapshot["source"],
            "linked_owner",
        )


    def test_card_context_exposes_both_applicants_and_locks_existing_challan(self):
        self.property.caretaker_tenant = self.caretaker
        self.property.save(update_fields=["caretaker_tenant"])

        context = workflow_card_context(
            self.lease,
            self.history,
        )

        self.assertEqual(
            context["selected_applicant_source"],
            "owner",
        )
        self.assertFalse(context["applicant_locked"])

        self.assertEqual(
            context["applicant_options"]["owner"]["record_id"],
            self.owner.pk,
        )
        self.assertEqual(
            context["applicant_options"]["caretaker"]["record_id"],
            self.caretaker.pk,
        )

        workflow, _ = prepare_workflow(
            self.lease,
            self.history,
            self.user,
        )

        workflow, _ = record_challan(
            workflow,
            "CARD-LOCK-1",
            "40170000000000011",
            self.user,
        )

        context = workflow_card_context(
            self.lease,
            self.history,
        )

        self.assertTrue(context["applicant_locked"])
        self.assertEqual(
            context["selected_applicant_source"],
            "owner",
        )


    def test_automatic_pdf_uses_existing_processor_and_is_idempotent(self):
        workflow, _ = prepare_workflow(self.lease, self.history, self.user)
        workflow, _ = record_challan(
            workflow, "PDF-CHALLAN", "40170000000000001", self.user
        )
        workflow, _ = mark_paid(workflow, self.user)
        workflow, _ = mark_stamp_issued(workflow, "PDF-STAMP", self.user)
        LeaseDocumentCategory.objects.get_or_create(
            code="estamp_paper", defaults={"name": "E-Stamp Paper"}
        )
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("123456")
        encrypted = BytesIO()
        writer.write(encrypted)
        upload_url = reverse("leases:lease_file_upload", args=[self.lease.pk])
        self.client.force_login(self.user)
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                upload_url,
                data={
                    "category": "estamp_paper",
                    "punjab_workflow_id": workflow.pk,
                    "history": self.history.pk,
                    "estamp_password": "123456",
                    "file": SimpleUploadedFile(
                        "Punjab-eStamp.pdf",
                        encrypted.getvalue(),
                        content_type="application/pdf",
                    ),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            self.assertEqual(response.status_code, 200)
            workflow.refresh_from_db()
            self.assertEqual(workflow.status, workflow.STATUS_UPLOADED)
            self.assertEqual(
                LeaseDocument.objects.filter(
                    lease=self.lease,
                    lease_history=self.history,
                    category="estamp_paper",
                ).count(),
                1,
            )
            document = workflow.lease_document
            with document.file.open("rb") as stored:
                self.assertFalse(PdfReader(stored).is_encrypted)

            repeat = self.client.post(
                upload_url,
                data={
                    "category": "estamp_paper",
                    "punjab_workflow_id": workflow.pk,
                    "estamp_password": "654321",
                    "file": SimpleUploadedFile(
                        "duplicate.pdf", b"not-used", content_type="application/pdf"
                    ),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            self.assertEqual(repeat.status_code, 200)
            self.assertTrue(repeat.json()["idempotent"])
            self.assertEqual(
                LeaseDocument.objects.filter(
                    lease=self.lease,
                    lease_history=self.history,
                    category="estamp_paper",
                ).count(),
                1,
            )

    def test_browser_helper_enforces_dry_run_and_session_only_otp(self):
        helper_path = finders.find("punjab_estamp/punjab_portal_helper.user.js")
        self.assertTrue(helper_path)
        source = Path(helper_path).read_text(encoding="utf-8")
        self.assertEqual(
            source.count(
                'setNative("MobileSearchBox", formatPakistanMobile(flow.applicant.phone));'
            ),
            2,
        )
        self.assertNotIn(
            'setNative("MobileSearchBox", flow.applicant.phone);',
            source,
        )
        self.assertIn("TMS dry run complete", source)
        self.assertIn("flow.dryRun !== true", source)
        self.assertNotIn("localStorage", source)
        self.assertIn("sessionStorage", source)
        self.assertIn('setMasked("PersonCnic", applicantCnic)', source)
        self.assertIn("formatPakistanMobile", source)
        self.assertIn("Applicant Email is missing in TMS", source)
        self.assertIn("installPdfCapture", source)
        self.assertIn("navigator.credentials.get", source)
        self.assertNotIn('getElementById("btnNext")', source)
        specific = source.index("/stamp/stampretrievalbycnic")
        download = source.index("/stamp/searchchallan")
        generic = source.index('/stamp/stampretrieval"')
        self.assertLess(specific, download)
        self.assertLess(download, generic)

    def test_edit_clause_card_renders_all_six_states(self):
        workflow, _ = prepare_workflow(self.lease, self.history, self.user)
        expected = {
            workflow.STATUS_READY: "Create Stamp Paper",
            workflow.STATUS_CHALLAN_GENERATED: "Check / Continue",
            workflow.STATUS_PAID: "Continue Stamp Process",
            workflow.STATUS_STAMP_ISSUED: "Download / Continue",
            workflow.STATUS_UPLOADED: "View E-Stamp",
            workflow.STATUS_INVALID: "Create Replacement Challan",
        }
        (
            workflow.applicant_snapshot,
            workflow.property_snapshot,
            workflow.portal_snapshot,
        ) = (
            applicant_snapshot(self.lease),
            {
                "property_name": "F56",
                "unit_number": "Flat 2",
                "district_name": "Rawalpindi",
                "tehsil_name": "Rawalpindi",
            },
            {"purpose_name": "Lease Agreement"},
        )
        workflow.challan_number = "UI-CHALLAN"
        workflow.psid = "40170000000000001"
        workflow.stamp_number = "UI-STAMP"
        for state, button_text in expected.items():
            workflow.status = state
            if state == workflow.STATUS_READY:
                workflow.challan_number = ""
                workflow.psid = ""
            else:
                workflow.challan_number = "UI-CHALLAN"
                workflow.psid = "40170000000000001"
            workflow.save()
            html = render_to_string(
                "leases/edit_clause.html",
                {
                    "master_lease": self.lease,
                    "lease": self.lease,
                    "history": self.history,
                    "histories": [self.history],
                    "clauses": [],
                    "placeholders": [],
                    "role_tenants": [],
                    "relationship_types": [],
                    "punjab_estamp_relations": [self.relation],
                    "punjab_estamp": workflow_card_context(self.lease, self.history),
                    "estamp_status": SimpleNamespace(
                        document=None,
                        is_over_age=False,
                        age_days=None,
                    ),
                    "agreement_photo_settings": {
                        "include_photos": False,
                        "layout": "4up",
                        "selection_mode": "selected",
                        "selected_photo_ids": [],
                        "selected_count": 0,
                        "eligible_count": 0,
                        "estimated_photo_pages": 0,
                    },
                    "current_history_photos": [],
                    "general_lease_photos": [],
                    "filter_properties": [],
                    "filter_units": [],
                    "filter_tenants": [],
                    "filter_lease_choices": [],
                },
            )
            self.assertIn(button_text, html)
            self.assertIn(f'<option value="{self.relation.pk}">S/O</option>', html)
            self.assertNotIn(">OTP<", html)
            self.assertNotIn(">PDF PIN<", html)

