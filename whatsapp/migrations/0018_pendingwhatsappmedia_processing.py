# Generated manually on 2026-07-23

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp', '0017_seed_tenant_simulator_staff_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingwhatsappmedia',
            name='processing',
            field=models.BooleanField(
                default=False,
                help_text='True while a video/audio file is still downloading in the background.',
            ),
        ),
    ]
