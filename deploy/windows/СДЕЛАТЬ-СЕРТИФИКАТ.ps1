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

# --- перевод в PEM ----------------------------------------------------------
#
# nginx читает PEM: содержимое в base64 между строками-рамками. Windows
# хранит и то и другое по-своему, и раньше перекладывать звали openssl.
# Это оказалось ошибкой: openssl вместе с nginx для Windows не идёт, на
# складском компьютере его попросту нет, и выпуск сертификата
# останавливался на полпути.
#
# Теперь всё делается средствами самого Windows. Посторонних программ не
# нужно, и связи с интернетом тоже.

function ConvertTo-Pem([string]$Label, [byte[]]$Bytes) {
    $text = New-Object System.Text.StringBuilder
    [void]$text.Append("-----BEGIN $Label-----`n")
    $base64 = [Convert]::ToBase64String($Bytes)
    for ($i = 0; $i -lt $base64.Length; $i += 64) {
        [void]$text.Append(
            $base64.Substring($i, [Math]::Min(64, $base64.Length - $i)) + "`n")
    }
    [void]$text.Append("-----END $Label-----`n")
    return $text.ToString()
}

function Save-Text([string]$Path, [string]$Text) {
    # Без BOM: с ним nginx не узнаёт строку-рамку и говорит «no start
    # line». Set-Content в PowerShell 5.1 BOM добавляет, поэтому пишем
    # напрямую.
    [IO.File]::WriteAllText($Path, $Text,
        (New-Object System.Text.UTF8Encoding($false)))
}

# --- сборка DER для закрытого ключа -----------------------------------------
#
# Ключ Windows отдаёт числами, а PEM хранит их в виде записи ASN.1 DER.
# Запись простая: каждое число — метка 0x02, длина, само число; всё
# вместе завёрнуто в последовательность с меткой 0x30. Порядок чисел
# задан стандартом PKCS#1 и менять его нельзя.

function Get-DerLength([int]$Length) {
    if ($Length -lt 0x80) { return [byte[]]@($Length) }
    $bytes = [System.Collections.Generic.List[byte]]::new()
    $rest = $Length
    while ($rest -gt 0) {
        $bytes.Insert(0, [byte]($rest -band 0xFF))
        $rest = $rest -shr 8
    }
    # Старший байт говорит, сколько байтов занимает сама длина
    return [byte[]](@([byte](0x80 -bor $bytes.Count)) + $bytes.ToArray())
}

function Get-DerInteger([byte[]]$Value) {
    # Ведущие нули не нужны, но если старший бит единица, ноль спереди
    # обязателен: иначе число прочтут как отрицательное.
    $start = 0
    while ($start -lt ($Value.Length - 1) -and $Value[$start] -eq 0) { $start++ }
    $body = $Value[$start..($Value.Length - 1)]
    if ($body[0] -ge 0x80) { $body = [byte[]](@([byte]0) + $body) }
    return [byte[]](@([byte]0x02) + (Get-DerLength $body.Length) + $body)
}

function Get-DerSequence([byte[]]$Content) {
    return [byte[]](@([byte]0x30) + (Get-DerLength $Content.Length) + $Content)
}

function ConvertTo-Pkcs1([System.Security.Cryptography.RSAParameters]$Key) {
    $body = [byte[]]@()
    $body += Get-DerInteger ([byte[]]@(0))       # версия
    foreach ($part in @($Key.Modulus, $Key.Exponent, $Key.D, $Key.P, $Key.Q,
                        $Key.DP, $Key.DQ, $Key.InverseQ)) {
        $body += Get-DerInteger $part
    }
    return Get-DerSequence $body
}

# Сертификат для сотрудников: только открытая часть, ключа в нём нет
$publicPath = Join-Path $Target 'sklad-для-сотрудников.crt'
Export-Certificate -Cert $certificate -FilePath $publicPath -Type CERT | Out-Null

$crtPath = Join-Path $Target 'sklad.crt'
$keyPath = Join-Path $Target 'sklad.key'

Save-Text $crtPath (ConvertTo-Pem 'CERTIFICATE' $certificate.Export('Cert'))

try {
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($certificate)
    if (-not $rsa) { throw 'закрытый ключ недоступен' }
    $parameters = $rsa.ExportParameters($true)
    Save-Text $keyPath (ConvertTo-Pem 'RSA PRIVATE KEY' (ConvertTo-Pkcs1 $parameters))
} catch {
    Fail @"
не удалось выгрузить закрытый ключ: $($_.Exception.Message)

Сертификат выпущен и лежит в хранилище Windows, но файлов для nginx
из него не получилось. Запустите PowerShell от имени администратора и
повторите: без прав администратора ключ не отдаётся.
"@
}

# Проверяем то, что получилось, а не то, что задумывалось: пустой или
# неполный файл nginx встретит невнятным «no start line», и разбираться
# придётся уже там.
foreach ($path in @($crtPath, $keyPath)) {
    $head = Get-Content $path -TotalCount 1 -ErrorAction SilentlyContinue
    if (-not $head -or $head -notlike '-----BEGIN*') {
        Fail "файл $path получился неполным. Повторите выпуск сертификата."
    }
}

# Ключ читается только тем, кто его выпустил. Иначе он лежит рядом с
# настройкой nginx с правами по умолчанию — то есть доступен всем.
try {
    $rights = Get-Acl $keyPath
    $rights.SetAccessRuleProtection($true, $false)
    $rights.Access | ForEach-Object { [void]$rights.RemoveAccessRule($_) }
    foreach ($who in @('BUILTIN\Administrators', 'NT AUTHORITY\SYSTEM')) {
        $rights.AddAccessRule((New-Object `
            System.Security.AccessControl.FileSystemAccessRule(
                $who, 'FullControl', 'Allow')))
    }
    Set-Acl $keyPath $rights
} catch {
    Write-Host ''
    Write-Host '  Замечание: не удалось ограничить доступ к файлу ключа.' `
        -ForegroundColor Yellow
    Write-Host "  Ограничьте его вручную: $keyPath"
}

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
