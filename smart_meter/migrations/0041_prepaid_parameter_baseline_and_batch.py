import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('smart_meter', '0040_meter_reading_balance_trace'),
    ]

    operations = [
        migrations.AddField(
            model_name='meterprepaidsettings',
            name='baseline_status',
            field=models.CharField(
                choices=[
                    ('local_only', 'Local values only'),
                    ('verified', 'Verified against meter'),
                    ('stale', 'Physical baseline needs re-verification'),
                    ('mismatch', 'Physical meter differs from verified baseline'),
                ],
                db_index=True,
                default='local_only',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='meterprepaidsettings',
            name='baseline_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='meterprepaidsettings',
            name='baseline_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='meterprepaidsettings',
            name='baseline_raw_read_hex',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='meterprepaidsettings',
            name='baseline_command_id',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='meterprepaidsettings',
            name='last_write_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='MeterPrepaidParameterBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch_key', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('action', models.CharField(choices=[('save_all', 'Save All')], default='save_all', max_length=20)),
                ('status', models.CharField(choices=[('previewed', 'Previewed'), ('processing', 'Processing'), ('completed', 'Completed'), ('partial', 'Partial / uncertain'), ('failed', 'Failed')], db_index=True, default='previewed', max_length=20)),
                ('target_count', models.PositiveIntegerField(default=0)),
                ('accepted_count', models.PositiveIntegerField(default=0)),
                ('warning_count', models.PositiveIntegerField(default=0)),
                ('rejected_count', models.PositiveIntegerField(default=0)),
                ('preview_changes', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='prepaid_parameter_batches', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
        migrations.AddField(
            model_name='meterprepaidwriteattempt',
            name='batch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='attempts', to='smart_meter.meterprepaidparameterbatch'),
        ),
        migrations.AddField(
            model_name='meterprepaidwriteattempt',
            name='before_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='meterprepaidwriteattempt',
            name='desired_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='meterprepaidwriteattempt',
            name='command_id',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='meterprepaidwriteattempt',
            name='result_state',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AlterField(
            model_name='meterprepaidwriteattempt',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending'), ('sent', 'Sent'), ('verified', 'Verified'), ('failed', 'Failed'), ('uncertain', 'Uncertain'), ('rolled_back', 'Rolled back')], default='pending', max_length=16),
        ),
    ]
