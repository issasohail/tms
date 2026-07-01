from io import BytesIO

from django.core.files.base import ContentFile
from django.db import migrations


DEFAULT_CATEGORIES = [
    ("Electrician", 10),
    ("Carpenter", 20),
    ("Plumber", 30),
    ("Painter", 40),
    ("Weldor", 50),
    ("Labor", 60),
    ("Maid", 70),
]


def _png(label, size=(360, 360), background=(219, 234, 254), foreground=(30, 64, 175)):
    try:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", size, background)
        draw = ImageDraw.Draw(image)
        width, height = size
        if width == height:
            draw.ellipse((width * 0.32, height * 0.18, width * 0.68, height * 0.54), fill=(226, 232, 240), outline=foreground, width=4)
            draw.rounded_rectangle((width * 0.22, height * 0.58, width * 0.78, height * 0.86), radius=28, fill=(191, 219, 254), outline=foreground, width=4)
        else:
            draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=18, fill=(248, 250, 252), outline=foreground, width=4)
            draw.rectangle((40, 62, 150, 172), fill=(219, 234, 254), outline=foreground, width=3)
            draw.line((180, 78, width - 48, 78), fill=foreground, width=4)
            draw.line((180, 112, width - 70, 112), fill=foreground, width=3)
            draw.line((180, 146, width - 92, 146), fill=foreground, width=3)
        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except Exception:
            font = ImageFont.load_default()
        draw.text((24, height - 48), label, fill=foreground, font=font)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
            b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )


def seed_handyman_data(apps, schema_editor):
    HandymanCategory = apps.get_model("handyman", "HandymanCategory")
    HandymanProfile = apps.get_model("handyman", "HandymanProfile")

    categories = []
    for name, sort_order in DEFAULT_CATEGORIES:
        category, created = HandymanCategory.objects.get_or_create(
            name=name,
            defaults={"sort_order": sort_order, "is_active": True},
        )
        if not created and category.sort_order == 50:
            category.sort_order = sort_order
            category.save(update_fields=["sort_order"])
        categories.append(category)

    profile, _created = HandymanProfile.objects.get_or_create(
        full_name="Sample Handyman",
        defaults={
            "phone": "+923001234567",
            "whatsapp_number": "+923001234567",
            "address": "Sample City",
            "notes": "Sample handyman profile for layout preview.",
            "is_preferred": True,
            "is_active": True,
        },
    )
    profile.categories.add(*categories[:3])

    if not profile.photo:
        profile.photo.save("sample-handyman-photo.png", ContentFile(_png("Photo")), save=False)
    if not profile.id_card_front:
        profile.id_card_front.save("sample-handyman-id-front.png", ContentFile(_png("ID Front", size=(480, 300))), save=False)
    if not profile.id_card_back:
        profile.id_card_back.save("sample-handyman-id-back.png", ContentFile(_png("ID Back", size=(480, 300))), save=False)
    profile.save()


def unseed_handyman_data(apps, schema_editor):
    HandymanProfile = apps.get_model("handyman", "HandymanProfile")
    HandymanProfile.objects.filter(full_name="Sample Handyman", phone="+923001234567").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("handyman", "0002_remove_maintenancehandymanassignment_one_current_handyman_assignment_per_request"),
    ]

    operations = [
        migrations.RunPython(seed_handyman_data, unseed_handyman_data),
    ]
