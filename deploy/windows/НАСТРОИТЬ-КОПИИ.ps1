#Requires -Version 5.1
<#
.SYNOPSIS
    Настроить ежедневное снятие резервных копий.

.DESCRIPTION
    Заводит задание в планировщике Windows, которое каждую ночь снимает
    копию базы, фотографий и настроек. Запускается один раз; дальше
    копии делаются сами.

    Задание выполняется от имени текущего пользователя и только когда
    компьютер включён. Пропущенный запуск (компьютер был выключен)
    выполняется при следующем включении.

    Права администратора не нужны.

.PARAMETER At
    Во сколько снимать копию. По умолчанию 23:30 — после рабочего дня.
    Формат: ЧЧ:ММ.

.PARAMETER To
    Куда складывать копии. По умолчанию — папка backups в программе.
    Лучше указать другой диск или сетевую папку.

.PARAMETER Keep
    Сколько дней хранить копии. По умолчанию 30.

.PARAMETER Remove
    Убрать задание вместо создания.

.EXAMPLE
    .\НАСТРОИТЬ-КОПИИ.ps1
    .\НАСТРОИТЬ-КОПИИ.ps1 -At 02:00 -To D:\Копии\Склад -Keep 60
    .\НАСТРОИТЬ-КОПИИ.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$At = '23:30',
    [string]$To,
    [int]$Keep = 30,
    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$TaskName = 'Складской учёт ЛЕКО - резервная копия'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$BackupScript = Join-Path $PSScriptRoot 'backup.ps1'

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Ежедневные резервные копии' -ForegroundColor Cyan
Write-Host '  ---------------------------------------------------------'

# --- убрать задание ---------------------------------------------------------
if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host '    Задание убрано. Копии больше не снимаются сами.' -ForegroundColor Yellow
        Write-Host '    Снять копию вручную: .\backup.ps1'
    } else {
        Write-Host '    Задания и не было — убирать нечего.'
    }
    Write-Host ''
    exit 0
}

# --- проверки перед созданием -----------------------------------------------
if (-not (Test-Path $BackupScript)) {
    Fail "не найден $BackupScript."
}
try {
    $time = [datetime]::ParseExact($At, 'HH:mm', $null)
} catch {
    Fail "не разобрать время «$At». Нужен формат ЧЧ:ММ, например 23:30."
}

# Проверяем на деле, что копия снимается: заводить задание, которое
# каждую ночь молча падает, — хуже, чем не заводить вовсе.
Write-Host '    Пробное снятие копии...'
$probe = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $BackupScript, '-Keep', $Keep)
if ($To) { $probe += @('-To', $To) }
& powershell @probe
if ($LASTEXITCODE -ne 0) {
    Fail 'пробная копия не снялась. Задание не создано — сначала разберитесь с причиной выше.'
}

# --- само задание -----------------------------------------------------------
# -NoMedia для ежедневных: фотографии занимают больше всего места, а
# меняются редко. Полная копия с фотографиями снимается при обновлении.
$taskArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$BackupScript`" -Keep $Keep -NoMedia -Quiet"
if ($To) { $taskArguments += " -To `"$To`"" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument $taskArguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description `
        'Ежедневная резервная копия складской системы ООО «ЛЕКО»' `
        -Force | Out-Null
} catch {
    Fail "не удалось создать задание: $($_.Exception.Message)"
}

$where = if ($To) { $To } else { Join-Path $ProjectRoot 'backups' }

Write-Host ''
Write-Host '    Готово.' -ForegroundColor Green
Write-Host "    Копия снимается каждый день в $At"
Write-Host "    Складывается в: $where"
Write-Host "    Хранится дней: $Keep"
Write-Host ''
Write-Host '    Проверить задание: планировщик заданий Windows,' -ForegroundColor DarkGray
Write-Host "    задание «$TaskName»" -ForegroundColor DarkGray
Write-Host '    Убрать: .\НАСТРОИТЬ-КОПИИ.ps1 -Remove' -ForegroundColor DarkGray
Write-Host ''

if (-not $To) {
    Write-Host '    ВНИМАНИЕ: копии лежат рядом с базой, на том же диске.' -ForegroundColor Yellow
    Write-Host '    От отказа диска это не спасёт — а ради него копии и делают.'
    Write-Host '    Укажите другой диск или сетевую папку:'
    Write-Host '        .\НАСТРОИТЬ-КОПИИ.ps1 -To D:\Копии\Склад' -ForegroundColor DarkGray
    Write-Host ''
}
