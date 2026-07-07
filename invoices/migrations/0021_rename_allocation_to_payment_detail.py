from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0010_rename_allocation_relations_to_payment_detail"),
        ("invoices", "0020_securitydeposittransaction_payment_detail_fk_state"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField(
                    model_name="securitydeposittransaction",
                    old_name="allocation",
                    new_name="payment_detail",
                ),
                migrations.AlterField(
                    model_name="securitydeposittransaction",
                    name="payment_detail",
                    field=models.OneToOneField(
                        blank=True,
                        db_column="allocation_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="security_amt",
                        to="payments.paymentdetail",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
