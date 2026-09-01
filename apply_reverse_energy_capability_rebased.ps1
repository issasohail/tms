$ErrorActionPreference = "Stop"

function Require-Text($Text, $Needle, $File) {
    if (-not $Text.Contains($Needle)) { throw "Expected text was not found in $File. No files were changed." }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$modelsPath = Join-Path $root "smart_meter\models.py"
$formsPath = Join-Path $root "smart_meter\forms_reconciliation.py"
$viewsPath = Join-Path $root "smart_meter\views_reconciliation.py"
$listenerPath = Join-Path $root "smart_meter\management\commands\meter_listener.py"
$detailPath = Join-Path $root "smart_meter\templates\smart_meter\energy_system_detail.html"
$migrationPath = Join-Path $root "smart_meter\migrations\0033_meter_reverse_energy_capability.py"

$models = Get-Content -Raw $modelsPath
$forms = Get-Content -Raw $formsPath
$views = Get-Content -Raw $viewsPath
$listener = Get-Content -Raw $listenerPath
$detail = Get-Content -Raw $detailPath

Require-Text $models '    unit = models.ForeignKey(' $modelsPath
Require-Text $models '    power_status = models.CharField(' $modelsPath
Require-Text $forms '    def __init__(self, *args, group=None, energy_system=None, **kwargs):' $formsPath
Require-Text $views '        messages.success(request, "Energy System created with linked input and output meters.")' $viewsPath
Require-Text $views '        messages.success(request, "Energy System meter links updated.")' $viewsPath
Require-Text $listener '        if di == "028011FF" and data.get("balance") is not None:' $listenerPath
Require-Text $detail '<strong>{{ row.meter.meter_number }}</strong>{% if row.reading %}' $detailPath

if ($models.Contains('REVERSE_CAPABILITY_SUPPORTED')) { throw "Reverse capability code is already present. No files were changed." }
if (Test-Path $migrationPath) { throw "Migration 0033 already exists. No files were changed." }

$constants = @'
    REVERSE_CAPABILITY_UNKNOWN = "unknown"
    REVERSE_CAPABILITY_SUPPORTED = "supported"
    REVERSE_CAPABILITY_NOT_SUPPORTED = "not_supported"
    REVERSE_CAPABILITY_CHOICES = [
        (REVERSE_CAPABILITY_UNKNOWN, "Not yet verified"),
        (REVERSE_CAPABILITY_SUPPORTED, "Supported"),
        (REVERSE_CAPABILITY_NOT_SUPPORTED, "Not supported"),
    ]

'@
$field = @'
    reverse_energy_capability = models.CharField(
        max_length=16,
        choices=REVERSE_CAPABILITY_CHOICES,
        default=REVERSE_CAPABILITY_UNKNOWN,
        help_text="Updated to Supported after a valid reverse-energy register response. Set Not supported only for meters known not to provide that register.",
    )
'@
$models = $models.Replace('    unit = models.ForeignKey(', $constants + '    unit = models.ForeignKey(')
$models = $models.Replace('    power_status = models.CharField(', $field + '    power_status = models.CharField(')

$formField = @'
    output_reverse_capability = forms.ChoiceField(
        required=False,
        choices=[("", "Leave each meter unchanged"), (Meter.REVERSE_CAPABILITY_NOT_SUPPORTED, "Not supported - hide reverse reading"), (Meter.REVERSE_CAPABILITY_SUPPORTED, "Supported")],
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Apply this reverse-register status to the selected output meters. Use Not supported for the H9 single-phase output meters.",
    )

'@
$forms = $forms.Replace('    def __init__(self, *args, group=None, energy_system=None, **kwargs):', $formField + '    def __init__(self, *args, group=None, energy_system=None, **kwargs):')

$update = @'
            if form.cleaned_data["output_reverse_capability"]:
                form.cleaned_data["output_meters"].update(reverse_energy_capability=form.cleaned_data["output_reverse_capability"])
'@
$views = [regex]::Replace($views, '(?m)^            \]\)\r?\n(?=        messages\.success\(request, "Energy System)', "            ])`r`n" + $update)

$listenerUpdate = @'
        if (
            di == "00020000"
            and data.get("reverse_active_energy_kwh") is not None
            and meter.reverse_energy_capability != Meter.REVERSE_CAPABILITY_SUPPORTED
        ):
            meter.reverse_energy_capability = Meter.REVERSE_CAPABILITY_SUPPORTED
            meter.save(update_fields=["reverse_energy_capability"])

'@
$listener = $listener.Replace('        if di == "028011FF" and data.get("balance") is not None:', $listenerUpdate + '        if di == "028011FF" and data.get("balance") is not None:')

$badge = @'
 <span class="badge {% if row.meter.reverse_energy_capability == 'supported' %}bg-success{% elif row.meter.reverse_energy_capability == 'not_supported' %}bg-secondary{% else %}bg-warning text-dark{% endif %}">Reverse {% if row.meter.reverse_energy_capability == 'supported' %}supported{% elif row.meter.reverse_energy_capability == 'not_supported' %}not supported{% else %}unverified{% endif %}</span>
'@
$detail = $detail.Replace('<strong>{{ row.meter.meter_number }}</strong>{% if row.reading %}', '<strong>{{ row.meter.meter_number }}</strong>' + $badge.TrimEnd() + '{% if row.reading %}')
$detail = $detail.Replace('Forward: {{ row.forward|default_if_none:"Unavailable" }} kWh;Reverse: {{ row.reverse|default_if_none:"Unavailable" }} kWh', 'Forward: {{ row.forward|default_if_none:"Unavailable" }} kWh{% if row.meter.reverse_energy_capability == ''supported'' %}; Reverse: {{ row.reverse|default_if_none:"Awaiting read" }} kWh{% endif %}')
$detail = $detail.Replace('Forward: {{ row.forward|default_if_none:"Unavailable" }} kWh; Reverse: {{ row.reverse|default_if_none:"Unavailable" }} kWh', 'Forward: {{ row.forward|default_if_none:"Unavailable" }} kWh{% if row.meter.reverse_energy_capability == ''supported'' %}; Reverse: {{ row.reverse|default_if_none:"Awaiting read" }} kWh{% endif %}')

$migration = @'
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("smart_meter", "0032_auto_energy_poll_source")]

    operations = [
        migrations.AddField(
            model_name="meter",
            name="reverse_energy_capability",
            field=models.CharField(
                choices=[("unknown", "Not yet verified"), ("supported", "Supported"), ("not_supported", "Not supported")],
                default="unknown",
                help_text="Updated to Supported after a valid reverse-energy register response. Set Not supported only for meters known not to provide that register.",
                max_length=16,
            ),
        ),
    ]
'@

Set-Content -Path $modelsPath -Value $models -Encoding utf8
Set-Content -Path $formsPath -Value $forms -Encoding utf8
Set-Content -Path $viewsPath -Value $views -Encoding utf8
Set-Content -Path $listenerPath -Value $listener -Encoding utf8
Set-Content -Path $detailPath -Value $detail -Encoding utf8
Set-Content -Path $migrationPath -Value $migration -Encoding utf8

Write-Host "Reverse-energy capability update applied successfully."
