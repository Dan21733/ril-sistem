from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("master", "0042_satuan_kelipatan"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="alias",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
