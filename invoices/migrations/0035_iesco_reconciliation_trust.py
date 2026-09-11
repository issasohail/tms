from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('invoices', '0034_iescobillreading_reminder_error_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='iescobillreading',
            name='trust_status',
            field=models.CharField(choices=[('fetched', 'Fetched'), ('parsed', 'Parsed'), ('verified', 'Verified'), ('confirmed', 'Confirmed for reconciliation')], db_index=True, default='fetched', max_length=16),
        ),
        migrations.AddField(
            model_name='iescobillreading',
            name='verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='iescobillreading',
            name='verified_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='iesco_bill_readings_verified', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='iescobillreading',
            name='confirmed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='iescobillreading',
            name='confirmed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='iesco_bill_readings_confirmed', to=settings.AUTH_USER_MODEL),
        ),
    ]
