from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0009_remove_paymentallocation_permissions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paymentdetail",
            name="payment",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="detail",
                to="payments.payment",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField(
                    model_name="allocationauditlog",
                    old_name="allocation",
                    new_name="payment_detail",
                ),
                migrations.AlterField(
                    model_name="allocationauditlog",
                    name="payment_detail",
                    field=models.ForeignKey(
                        db_column="allocation_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="audit_logs",
                        to="payments.paymentdetail",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
