#Requires -Version 5.1
<#
.SYNOPSIS
    Обновление работающей складской системы.

.DESCRIPTION
    Проводит обновление целиком и в правильном порядке: снимает копию базы
    и фотографий, обновляет код и библиотеки, применяет миграции, собирает
    статику и проверяет результат.

    Если проверка не прошла, база возвращается из копии, снятой в начале.
    Код при этом остаётся обновлённым — откатить его скрипт не берётся,
    чтобы не потерять правки, если они были; как это сделать вручную,
    он подскажет.

    Данные обновление не трогает: файл базы, каталог media с фотографиями
    и файл .env с настройками остаются на месте. Заменяется только код.

.PARAMETER SkipMedia
    Не копировать фотографии товаров. Полезно, когда их много и копия
    занимает время, а сами файлы обновление не затрагивает.

.PARAMETER NoPull
    Не выполнять git pull. Используйте, если обновляете распаковкой архива
    поверх: тогда сначала распакуйте файлы, потом запустите скрипт.

.EXAMPLE
    .\update.ps1
    .\update.ps1 -SkipMedia
    .\update.ps1 -NoPull
#>
[CmdletBinding()]
param(
    [switch]$SkipMedia,
    [switch]$NoPull
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# Корень проекта: скрипт лежит в deploy\windows, поднимаемся на два уровня
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$VenvPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
$Database = Join-Path $ProjectRoot 'db.sqlite3'
$MediaDir = Join-Path $ProjectRoot 'media'
$BackupRoot = Join-Path $ProjectRoot 'backups'

$Stamp = Get-Date -Format 'yyyy-MM-dd_HH-mm'
$BackupDir = Join-Path $BackupRoot $Stamp
$DatabaseBackup = Join-Path $BackupDir 'db.sqlite3'


# ============================================================================
#  Вспомогательное
# ============================================================================
function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Green
}

function Write-Note([string]$Text) {
    Write-Host "    $Text" -ForegroundColor DarkGray
}

function Write-Warn([string]$Text) {
    Write-Host "    ВНИМАНИЕ: $Text" -ForegroundColor Yellow
}

function Stop-WithError([string]$Text, [switch]$Restore) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red

    if ($Restore -and (Test-Path $DatabaseBackup)) {
        Write-Host ''
        Write-Host 'Возвращаю базу из копии...' -ForegroundColor Yellow
        Copy-Item $DatabaseBackup $Database -Force
        Write-Host 'База восстановлена на состояние до обновления.' `
            -ForegroundColor Green
        Write-Host ''
        Write-Host 'Код при этом остался обновлённым. Чтобы вернуть и его:' `
            -ForegroundColor DarkGray
        Write-Host '    git log --oneline -5        # найти предыдущий коммит' `
            -ForegroundColor DarkGray
        Write-Host '    git checkout <его-номер>' -ForegroundColor DarkGray
        Write-Host '    pip install -r requirements.txt --upgrade' `
            -ForegroundColor DarkGray
    }

    Write-Host ''
    Write-Host "Копия обновления: $BackupDir" -ForegroundColor DarkGray
    exit 1
}

function Test-SystemRunning([int]$Port = 8000) {
    <#
        Занят ли порт — косвенный признак того, что система ещё работает.

        Обновлять на ходу нельзя: библиотеки меняются под работающим
        процессом, а копия базы может оказаться без последних документов.
    #>
    try {
        $connections = Get-NetTCPConnection -State Listen -LocalPort $Port `
            -ErrorAction Stop
        return $null -ne $connections
    } catch {
        return $false
    }
}

function Invoke-Django([string[]]$Arguments, [string]$FailureText) {
    Push-Location $ProjectRoot
    try {
        & $VenvPython 'manage.py' @Arguments
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -ne 0) {
        Stop-WithError $FailureText -Restore
    }
}


# ============================================================================
#  Обновление
# ============================================================================
Write-Host ''
Write-Host '  Складская система ООО «ЛЕКО»' -ForegroundColor White
Write-Host '  Обновление' -ForegroundColor White
Write-Host '  ---------------------------------------------------------'
Write-Note "Папка программы: $ProjectRoot"

# --- 0. Проверки перед началом ---------------------------------------------
Write-Step 'Проверка перед обновлением'

if (-not (Test-Path (Join-Path $ProjectRoot 'manage.py'))) {
    Stop-WithError @'
скрипт запущен не из папки программы.

Файл update.ps1 должен лежать в подпапке deploy\windows внутри папки
с системой, рядом с которой находится manage.py.
'@
}

if (-not (Test-Path $VenvPython)) {
    Stop-WithError @'
не найдено виртуальное окружение (папка venv).

Похоже, система ещё не установлена. Установка описана в README.md.
'@
}
Write-Ok 'папка программы и окружение на месте'

if (Test-SystemRunning) {
    Write-Host ''
    Write-Warn 'похоже, система сейчас работает (порт 8000 занят).'
    Write-Host '    Обновлять на ходу нельзя: библиотеки меняются под' `
        -ForegroundColor Yellow
    Write-Host '    работающим процессом, а копия базы может выйти неполной.' `
        -ForegroundColor Yellow
    Write-Host ''
    Write-Host '    Закройте окно с запущенным сервером (Ctrl+C) и' `
        -ForegroundColor Yellow
    Write-Host '    запустите обновление заново.' -ForegroundColor Yellow
    Write-Host ''
    $answer = Read-Host '    Продолжить всё равно? (напишите ДА)'
    if ($answer -cne 'ДА') {
        Write-Host '    Отменено — ничего не тронуто.' -ForegroundColor Green
        exit 0
    }
}

# --- 1. Резервная копия -----------------------------------------------------
Write-Step 'Резервная копия'

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null

if (Test-Path $Database) {
    # Копия снимается средством самой SQLite, а не копированием файла:
    # часть свежих записей может находиться в служебном журнале рядом с
    # базой, и обычная копия рискует остаться без последних документов.
    #
    # Код кладётся во временный файл, а не передаётся через `python -c`:
    # PowerShell на Windows разбирает строку с кавычками по своим правилам
    # и до Python она доходит уже без них — получается SyntaxError вместо
    # копии. Файл этой разборки не касается.
    $code = @'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
try:
    connection.execute("VACUUM INTO ?", (sys.argv[2],))
finally:
    connection.close()
'@
    $scriptFile = Join-Path ([System.IO.Path]::GetTempPath()) `
        ("leko_backup_{0}.py" -f [System.Guid]::NewGuid().ToString('N'))
    Set-Content -Path $scriptFile -Value $code -Encoding ASCII

    try {
        & $VenvPython $scriptFile $Database $DatabaseBackup
        $backupCode = $LASTEXITCODE
    } finally {
        Remove-Item $scriptFile -Force -ErrorAction SilentlyContinue
    }

    if ($backupCode -ne 0 -or -not (Test-Path $DatabaseBackup)) {
        Stop-WithError 'не удалось скопировать базу. Обновление отменено.'
    }
    $sizeMb = [math]::Round((Get-Item $DatabaseBackup).Length / 1MB, 2)
    Write-Ok "база данных — $sizeMb МБ"
} else {
    Write-Note 'файла базы нет — вероятно, используется PostgreSQL'
    Write-Warn 'копию базы PostgreSQL снимите отдельно, командой pg_dump'
}

if (-not $SkipMedia -and (Test-Path $MediaDir)) {
    Copy-Item $MediaDir (Join-Path $BackupDir 'media') -Recurse -Force
    $count = @(Get-ChildItem (Join-Path $BackupDir 'media') -Recurse -File `
        -ErrorAction SilentlyContinue).Count
    Write-Ok "фотографии товаров — $count файлов"
} elseif ($SkipMedia) {
    Write-Note 'копирование фотографий пропущено (-SkipMedia)'
}

# .env копируем всегда: он маленький, а восстанавливать настройки руками
# по памяти — дело неприятное
$EnvFile = Join-Path $ProjectRoot '.env'
if (Test-Path $EnvFile) {
    Copy-Item $EnvFile (Join-Path $BackupDir '.env') -Force
    Write-Ok 'файл настроек .env'
}

Write-Note "Копия: $BackupDir"

# --- 2. Обновление кода -----------------------------------------------------
Write-Step 'Обновление кода'

$IsGitRepo = Test-Path (Join-Path $ProjectRoot '.git')

if ($NoPull) {
    Write-Note 'получение кода пропущено (-NoPull)'
    Write-Note 'предполагается, что новые файлы уже распакованы'
} elseif (-not $IsGitRepo) {
    Write-Note 'это не репозиторий git — код получить неоткуда'
    Write-Note 'распакуйте новую версию поверх папки и запустите с -NoPull'
} else {
    Push-Location $ProjectRoot
    try {
        $before = (& git rev-parse --short HEAD 2>$null)
        & git pull
        $pullCode = $LASTEXITCODE
        $after = (& git rev-parse --short HEAD 2>$null)
    } finally {
        Pop-Location
    }

    if ($pullCode -ne 0) {
        Stop-WithError @'
не удалось получить обновление (git pull завершился с ошибкой).

Частые причины: нет связи с сервером, либо в папке есть изменённые
вручную файлы, мешающие обновлению. Посмотрите вывод выше.
'@
    }

    if ($before -eq $after) {
        Write-Ok "код уже последней версии ($after)"
    } else {
        Write-Ok "код обновлён: $before -> $after"
    }
}

# --- 3. Библиотеки ----------------------------------------------------------
Write-Step 'Обновление библиотек'
Write-Note 'самый долгий шаг, обычно меньше минуты...'

& $VenvPython '-m' 'pip' 'install' '-r' `
    (Join-Path $ProjectRoot 'requirements.txt') '--upgrade' '--quiet'
if ($LASTEXITCODE -ne 0) {
    Stop-WithError @'
не удалось обновить библиотеки.

Чаще всего причина — отсутствие интернета или блокировка антивирусом.
Проверьте подключение и запустите обновление заново.
'@ -Restore
}
Write-Ok 'библиотеки обновлены'

# --- 4. База данных ---------------------------------------------------------
Write-Step 'Обновление структуры базы'
Invoke-Django @('migrate', '--noinput') `
    'не удалось применить миграции. База возвращена из копии.'
Write-Ok 'структура базы обновлена'

# --- 5. Оформление ----------------------------------------------------------
Write-Step 'Сборка файлов оформления'
Invoke-Django @('collectstatic', '--noinput') `
    'не удалось собрать статику.'
Write-Ok 'оформление собрано'

# --- 6. Проверка ------------------------------------------------------------
Write-Step 'Проверка системы'

Push-Location $ProjectRoot
try {
    & $VenvPython 'manage.py' 'checksetup'
    $checkCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($checkCode -ne 0) {
    Stop-WithError @'
проверка нашла то, что мешает работе (подробности выше).

База возвращена в состояние до обновления.
'@ -Restore
}

# --- Итог -------------------------------------------------------------------
Write-Host ''
Write-Host '  ---------------------------------------------------------'
Write-Host '  Обновление завершено' -ForegroundColor Green
Write-Host '  ---------------------------------------------------------'
Write-Host ''
Write-Host '  Запустите систему как обычно и откройте пару рабочих'
Write-Host '  страниц — приход, отчёты, витрину. Проверка не увидит,'
Write-Host '  если что-то поехало в вёрстке.'
Write-Host ''
Write-Note "Копия до обновления: $BackupDir"
Write-Note 'Её можно удалить, когда убедитесь, что всё работает.'
Write-Host ''

# Старые копии не удаляем автоматически: решать, что уже не нужно,
# должен человек. Но подскажем, если их накопилось много.
$backups = @(Get-ChildItem $BackupRoot -Directory -ErrorAction SilentlyContinue)
if ($backups.Count -gt 10) {
    Write-Note "Копий обновлений накопилось: $($backups.Count). Старые можно"
    Write-Note "удалить вручную из папки backups."
    Write-Host ''
}
