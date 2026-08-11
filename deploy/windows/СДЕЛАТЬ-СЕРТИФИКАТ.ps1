#Requires -Version 5.1
<#
.SYNOPSIS
    Выпустить сертификат для работы системы по HTTPS.

.DESCRIPTION
    Создаёт собственный сертификат сроком на два года и раскладывает его
    в три файла рядом с настройкой nginx:

        sklad.crt  — сертификат для nginx
        sklad.key  — закрытый ключ для nginx (никому не показывать)
        sklad-для-сотрудников.crt — тот же сертификат для установки на
                                    рабочие места

    Зачем последний. Сертификат выпущен предприятием, а не признанным
    удостоверяющим центром, и браузер про такой честно говорит: «этому
    сайту доверять нельзя». Каждый день нажимать «всё равно продолжить»
    — плохая привычка: однажды так же нажмут на настоящую подмену.
    Поэтому сертификат один раз ставят на рабочие места, и предупреждение
    пропадает.

    Купленный сертификат тут не нужен и чаще всего невозможен: их выдают
    на имена в интернете, а sklad.leko.local существует только внутри
    предприятия.

.PARAMETER Name
    Имя, по которому сотрудники открывают систему. По умолчанию —
    sklad.leko.local. Оно должно совпадать с адресом в браузере: иначе
    браузер ругается уже не на доверие, а на несовпадение имени.

.PARAMETER Address
    Адрес компьютера в сети, например 192.168.1.50. Добавляется в
    сертификат, чтобы систему можно было открыть и по адресу.

.PARAMETER Years
    Срок сертификата. По умолчанию 2 года.

.EXAMPLE
    .\СДЕЛАТЬ-СЕРТИФИКАТ.ps1
    .\СДЕЛАТЬ-СЕРТИФИКАТ.ps1 -Name sklad.leko.local -Address 192.168.1.50
#>
[CmdletBinding()]
param(
    [string]$Name = 'sklad.leko.local',
    [string]$Address,
    [int]$Years = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Target = Join-Path $ProjectRoot 'deploy\nginx'

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Складская система ООО «ЛЕКО»' -ForegroundColor White
Write-Host '  Сертификат для работы по HTTPS' -ForegroundColor White
Write-Host '  ---------------------------------------------------------'

if (-not (Test-Path $Target)) {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
}

$names = @($Name)
if ($Address) { $names += $Address }
Write-Host ''
Write-Host "  Имя в сертификате: $($names -join ', ')"
Write-Host "  Срок: $Years г."

try {
    $certificate = New-SelfSignedCertificate `
        -DnsName $names `
        -CertStoreLocation 'Cert:\LocalMachine\My' `
        -FriendlyName "Складской учёт ООО ЛЕКО ($Name)" `
        -NotAfter (Get-Date).AddYears($Years) `
        -KeyExportPolicy Exportable `
        -KeyLength 2048 `
        -KeyAlgorithm RSA `
        -HashAlgorithm SHA256
} catch {
    Fail @"
не удалось выпустить сертификат: $($_.Exception.Message)

Скорее всего не хватает прав. Запустите PowerShell от имени
администратора и повторите.
"@
}

# Пароль нужен только чтобы вынуть ключ из хранилища Windows: файл с
# ним тут же превращается в пару crt/key и удаляется.
$password = [System.Guid]::NewGuid().ToString()
$securePassword = ConvertTo-SecureString -String $password -Force -AsPlainText
$pfxPath = Join-Path $Target 'sklad-временный.pfx'

Export-PfxCertificate -Cert $certificate -FilePath $pfxPath `
    -Password $securePassword | Out-Null

# Сертификат для сотрудников: только открытая часть, ключа в нём нет
$publicPath = Join-Path $Target 'sklad-для-сотрудников.crt'
Export-Certificate -Cert $certificate -FilePath $publicPath -Type CERT | Out-Null

$crtPath = Join-Path $Target 'sklad.crt'
$keyPath = Join-Path $Target 'sklad.key'

# Сам сертификат кладём в PEM своими руками, без посторонних программ:
# это просто его содержимое в base64 между двумя строками-рамками.
# Раньше и его доставали openssl, и когда тот спотыкался, оставался
# пустой файл, а nginx на него отвечал загадочным «no start line».
$pem = New-Object System.Text.StringBuilder
[void]$pem.AppendLine('-----BEGIN CERTIFICATE-----')
$base64 = [Convert]::ToBase64String($certificate.Export('Cert'))
for ($i = 0; $i -lt $base64.Length; $i += 64) {
    [void]$pem.AppendLine($base64.Substring($i, [Math]::Min(64, $base64.Length - $i)))
}
[void]$pem.AppendLine('-----END CERTIFICATE-----')
# Без BOM и с переносами строк как в Unix: openssl и nginx читают
# именно такой PEM, а Set-Content в PowerShell 5.1 добавил бы BOM.
[IO.File]::WriteAllText($crtPath, ($pem.ToString() -replace "`r`n", "`n"),
    (New-Object System.Text.UTF8Encoding($false)))
Write-Host ''
Write-Host '  Сертификат записан.' -ForegroundColor Green

# А вот ключ вынуть без openssl нельзя: PowerShell 5.1 не умеет
# выгружать закрытый ключ в PEM. openssl идёт вместе с nginx для Windows.
$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    Write-Host ''
    Write-Host '  ВНИМАНИЕ: не найдена программа openssl.' -ForegroundColor Yellow
    Write-Host '  Она нужна только чтобы вынуть ключ. Обычно лежит рядом с nginx;'
    Write-Host '  добавьте её папку в PATH или выполните вручную:'
    Write-Host ''
    Write-Host "      openssl pkcs12 -legacy -in `"$pfxPath`" -nocerts -nodes -out `"$keyPath`""
    Write-Host "  Пароль от файла: $password"
    Write-Host ''
    Write-Host '  Временный файл оставлен: он понадобится для этой команды.'
    Write-Host "      $pfxPath"
    Write-Host ''
    exit 0
}

# Windows закрывает PFX старым шифрованием (RC2), а openssl версии 3
# считает его устаревшим и без ключа -legacy просто отказывается читать.
# Отказ этот тихий: файл он к тому времени уже создал, и остаётся пустым.
# Поэтому сначала пробуем как есть, потом с -legacy, и в обоих случаях
# смотрим не на наличие файла, а на его содержимое.
$keyMade = $false
foreach ($legacy in @(@(), @('-legacy'))) {
    $output = & openssl pkcs12 @legacy -in $pfxPath -nocerts -nodes `
        -out $keyPath -passin "pass:$password" 2>&1
    if ((Test-Path $keyPath) -and
        ((Get-Content $keyPath -Raw -ErrorAction SilentlyContinue) -match '-----BEGIN')) {
        $keyMade = $true
        break
    }
    $lastError = ($output | Out-String).Trim()
}

if (-not $keyMade) {
    if (Test-Path $keyPath) { Remove-Item $keyPath -Force }
    Fail @"
не удалось вынуть закрытый ключ. Ответ openssl:

$lastError

Сертификат при этом выпущен, он в файле:
    $pfxPath
Пароль от него: $password
"@
}

Remove-Item $pfxPath -Force

Write-Host ''
Write-Host '  Готово. Файлы:' -ForegroundColor Green
Write-Host "    $crtPath        — сертификат для nginx"
Write-Host "    $keyPath        — ключ для nginx (никому не показывать)"
Write-Host "    $publicPath  — поставить на рабочие места"
Write-Host ''
Write-Host '  Дальше:'
Write-Host '    1. Пропишите пути к sklad.crt и sklad.key в настройке nginx'
Write-Host '       (образец — deploy\nginx\warehouse-https.conf).'
Write-Host '    2. Поставьте sklad-для-сотрудников.crt на рабочие места:'
Write-Host '       двойное нажатие -> Установить сертификат -> Локальный компьютер'
Write-Host '       -> Поместить в: Доверенные корневые центры сертификации.'
Write-Host '    3. Впишите в .env строки USE_HTTPS=True и CSRF_TRUSTED_ORIGINS.'
Write-Host '    4. Проверьте: venv\Scripts\python.exe manage.py checksetup'
Write-Host ''
Write-Host '  Порядок целиком — в HTTPS.md.'
Write-Host ''
exit 0
