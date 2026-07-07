from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("payments", "0007_backfill_missing_security_transactions"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameModel(
                    old_name="PaymentAllocation",
                    new_name="PaymentDetail",
                ),
                migrations.AlterModelTable(
                    name="paymentdetail",
                    table="payments_paymentallocation",
                ),
            ],
            database_operations=[],
        ),
        migrations.AlterField(
            model_name="paymentdetail",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payment_detail_updates",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
