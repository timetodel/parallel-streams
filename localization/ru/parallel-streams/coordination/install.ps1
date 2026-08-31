#Requires -Version 7
<#
Разворачивает канал согласования между вкладками в проект: сторожа, профиль, переходник.

Зачем отдельный установщик. Сам канал лежит в папке скилла, но работать он начинает только от трёх
вещей ВНЕ её: записей в настройках проекта, раздела в профиле и короткого переходника в `scripts/`.
Собирать это руками в каждом новом проекте — сверка с чужим файлом настроек и четыре места, где
легко ошибиться, причём ошибка в любом из них выглядит одинаково безобидно: «соседу нечего
сказать». Поэтому одна команда и отчёт, в котором названо КАЖДОЕ действие.

Что делает установка:
  1. настройки  `<проект>/.claude/settings.json` — два сторожа: доставка находок и подсказка при
     правке плана волны;
  2. профиль    `<проект>/.parallel-streams.md`  — разделы `## Coordination` и `## Plans`;
  3. переходник `<проект>/scripts/wave-board.ps1` — чтобы команда запуска везде была одинаково
     короткой: `pwsh scripts/wave-board.ps1 ...`.

Чего установщик не делает НИКОГДА: не трогает чужие записи в настройках, не меняет ни байта в уже
написанном профиле и не переписывает чужой файл на месте переходника. Всё, что он делать не стал,
названо в отчёте — молчаливых пропусков здесь нет, иначе канал окажется наполовину подключённым, а
выглядеть это будет как тишина на доске.

Оговорка про файл настроек: чужие записи и их порядок целы, но собирает файл обратно сериализатор, и
раскладку (отступы, переносы) он ставит свою. Файл, написанный в другом стиле, после первой установки
окажется в диффе целиком. Установщик про это ГОВОРИТ — сверяется с прежним текстом заранее и печатает
предупреждение ровно тогда, когда переписывает стиль.

Повторный прогон безопасен: он не плодит дублей, а переехавшую папку скилла подхватывает правкой
уже стоящих записей. Разбор — в комментарии к Set-Guards.

Режимы: Install (по умолчанию), Uninstall (снять), Check (показать состояние).
#>

[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Check')]
    [string]$Mode = 'Install',

    # Корень проекта, куда ставим. По умолчанию — корень репозитория, из которого запущен скрипт.
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ‼️ Снимаем git-переменные окружения ДО первого обращения к git: с заданным снаружи GIT_DIR корнем
# проекта окажется чужой репозиторий, и установка уедет туда молча. Разбор ловушки — в самом файле.
. (Join-Path $PSScriptRoot 'lib/git-env-clean.ps1')

# Папка скилла, от которой считаются все пути в настройках и в переходнике.
$CoordDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path

# Признак «запись наша»: команда ведёт в НАШ файл сторожа, лежащий в папке сторожей канала. Ни одной
# половины признака не хватает. По одной папке своим сочли бы чужой хук, случайно положенный в папку
# с таким же именем, — и снятие унесло бы его вместе с нашими. По одному имени файла не узнали бы
# СВОЮ прежнюю запись после переезда скилла, и рядом со старой (мёртвой) легла бы вторая.
$OurHookFiles = @('wave-board-deliver.ps1', 'pretooluse-wave-board-nudge.ps1')
$OurMark = 'coordination/hooks/(' +
    (($OurHookFiles | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')'

# Строка, по которой узнаётся наш переходник: чужой файл с тем же именем не трогаем.
$BridgeMark = 'переехал в папку скилла'

$ProfileName = '.parallel-streams.md'

function Report {
    param([string]$Text)
    # ‼️ Пишем в консоль, а не в поток вывода: отчёт печатают функции, у которых есть и возвращаемое
    # значение, и вызов из присваивания. Через поток вывода такой отчёт молча уехал бы в переменную.
    Write-Host $Text
}

function Deny {
    param([string]$Text)
    # Отказ читает человек: `throw` завернул бы текст в рамку исключения с путём и номером строки.
    [Console]::Error.WriteLine($Text)
    exit 1
}

function Get-SlashPath {
    param([string]$Path)
    return ($Path -replace '\\', '/')
}

function Test-SameText {
    param([string]$Left, [string]$Right)
    # Сравнение содержимого файла — посимвольное: обычное -eq в PowerShell не различает регистр, и
    # переехавший путь, отличающийся только регистром буквы, сошёл бы за «ничего не изменилось».
    return [string]::Equals($Left, $Right, [System.StringComparison]::Ordinal)
}

function Resolve-Root {
    param([string]$Given)
    if ($Given) {
        if (-not (Test-Path -LiteralPath $Given -PathType Container)) {
            Deny "Папки нет: $Given"
        }
        return (Resolve-Path -LiteralPath $Given).Path
    }
    $top = ''
    try { $top = (& git rev-parse --show-toplevel 2>$null) } catch { $top = '' }
    if ($top) { return (Resolve-Path -LiteralPath ([string]$top).Trim()).Path }
    Report "Это не репозиторий git — ставлю в текущую папку: $($PWD.Path)"
    return $PWD.Path
}

function Get-InsidePath {
    param([string]$Root, [string]$Target)
    # Путь от корня проекта, если цель лежит ВНУТРИ него, иначе пусто. Внутренний вид нужен затем,
    # чтобы настройки пережили переезд папки проекта и одинаково работали в каждом рабочем дереве.
    $rel = Get-SlashPath ([System.IO.Path]::GetRelativePath($Root, $Target))
    if ([System.IO.Path]::IsPathRooted($rel)) { return '' }
    if ($rel -eq '.' -or $rel -eq '..' -or $rel.StartsWith('../')) { return '' }
    return $rel
}

# ─── Разметка профиля ────────────────────────────────────────────────────────────────────────────

function Get-MarkdownSection {
    param([string]$Text, [string]$Name)
    # Раздел `## Имя` целиком, без хвостовых пустых строк. Пусто — раздела нет.
    #
    # Границей раздела считается заголовок и второго уровня, и ПЕРВОГО: `# Другая часть` стоит в
    # разметке выше, и, обрывая раздел только на `##`, мы утащили бы в него весь остаток документа —
    # вместе с чужими путями в обратных кавычках, из которых потом читается папка планов.
    # Подзаголовки (`### ...`) границей не считаются: они часть раздела.
    #
    # Имя сравнивается БЕЗ учёта регистра (в PowerShell `-eq` для строк так и работает): профиль
    # пишет человек, и `## plans` у него — тот же раздел планов, что `## Plans`. Так же его читает
    # сторож-подсказка, иначе профиль с другим регистром дал бы подключённого, но немого сторожа.
    if (-not $Text) { return '' }
    $found = $false
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match '^(#{1,2})\s+(.+?)\s*$') {
            if ($found) { break }
            if ($Matches[1].Length -eq 2 -and $Matches[2] -eq $Name) { $found = $true; $out.Add($line) }
            continue
        }
        if ($found) { $out.Add($line) }
    }
    if (-not $found) { return '' }
    while ($out.Count -gt 0 -and -not $out[$out.Count - 1].Trim()) { $out.RemoveAt($out.Count - 1) }
    return ($out -join "`n")
}

function Get-PlansFolder {
    param([string]$ProfileText)
    # Папку планов профиль называет в разделе `## Plans`, обратными кавычками — как путь. Берём
    # первое подходящее: раздел человек пишет словами, и в кавычки там попадает и имя файла, и
    # команда. Пусто — папка не объявлена, и сторож-подсказка не подключается вовсе.
    $section = Get-MarkdownSection -Text $ProfileText -Name 'Plans'
    if (-not $section) { return '' }
    foreach ($found in [regex]::Matches($section, '`([^`]+)`')) {
        $value = Get-SlashPath $found.Groups[1].Value.Trim()
        $value = $value -replace '^\./', ''
        if ($value -match '\s') { continue }
        # Место под заполнение папкой не считаем: в заготовках проекта незаполненное помечается
        # угловыми скобками (`<номер потока>`), и принять их за имя папки значит подключить сторожа
        # к папке, которой нет, — вместо честного «папка не названа».
        if ($value -match '[<>]') { continue }
        # Имя файла папкой планов не бывает: `.parallel-streams.md`, `README.md` и прочее.
        if ($value -notmatch '/$' -and $value -match '\.[A-Za-z0-9]{1,5}$') { continue }
        if (-not $value.EndsWith('/')) { $value += '/' }
        return $value
    }
    return ''
}

# ─── Чтение и запись файлов ──────────────────────────────────────────────────────────────────────

function Read-TextFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false))
}

function Save-TextFile {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function ConvertTo-FileJson {
    param($Data, [string]$Original)
    # ‼️ Сборка настроек обратно в текст. Три вещи, каждая из которых портит чужой файл молча:
    #   • разбор идёт БЕЗ -AsHashtable (см. Read-Settings): словарь теряет порядок ключей, и файл
    #     после сохранения перетасовывается целиком — чужие записи выглядят переписанными;
    #   • кириллица не должна уехать в \uXXXX: в файле есть русские подписи сторожей, их читает
    #     человек. Разворачиваем обратно всё, кроме того, что в JSON обязано быть экранированным;
    #   • переводы строк и хвостовой перевод берём у прежнего файла: иначе безобидный прогон
    #     переписывает КАЖДУЮ строку и тонет в разнице при слиянии.
    #
    # ‼️ Разворачиваем по РУНАМ обратных косых, а не по одиночному совпадению `\uXXXX`. Чужое
    # значение `путь C:\u0041pps\bin` записано в файле как `C:\\u0041pps\\bin`, и наивный поиск
    # принял бы ВТОРУЮ косую за начало escape-последовательности: в файл уехало бы `C:\Apps` —
    # незаконный escape, после которого файл настроек не разбирается ЦЕЛИКОМ, а с ним молча
    # отключаются ВСЕ хуки проекта, не только наши. Нечётное число косых перед `u` — последняя
    # открывает escape; чётное — все они парные, и перед нами обычный текст, который трогать нельзя.
    $text = $Data | ConvertTo-Json -Depth 20
    $text = [regex]::Replace($text, '(\\+)u([0-9a-fA-F]{4})', {
            param($found)
            $slashes = $found.Groups[1].Value
            if ($slashes.Length % 2 -eq 0) { return $found.Value }
            $code = [Convert]::ToInt32($found.Groups[2].Value, 16)
            if ($code -lt 0x20 -or $code -eq 0x22 -or $code -eq 0x5C) { return $found.Value }
            # Половинка суррогатной пары (эмодзи) сама по себе уже не символ — оставляем как есть.
            if ($code -ge 0xD800 -and $code -le 0xDFFF) { return $found.Value }
            return $slashes.Substring(0, $slashes.Length - 1) + [string][char]$code
        })
    $text = $text -replace "`r`n", "`n"
    $useCrlf = $Original -and ($Original -match "`r`n")
    if ($useCrlf) { $text = $text -replace "`n", "`r`n" }
    if (-not $Original -or $Original.EndsWith("`n")) {
        $text += $(if ($useCrlf) { "`r`n" } else { "`n" })
    }
    return $text
}

function Read-Settings {
    param([string]$Path)
    $raw = Read-TextFile -Path $Path
    if (-not $raw -or -not $raw.Trim()) {
        return [pscustomobject]@{ Data = $null; Raw = $raw }
    }
    $data = $null
    try {
        $data = $raw | ConvertFrom-Json
    } catch {
        Deny "Настройки проекта не разбираются как JSON: $Path. Почините файл и повторите."
    }
    return [pscustomobject]@{ Data = $data; Raw = $raw }
}

function New-Settings {
    return [pscustomobject][ordered]@{
        '$schema' = 'https://json.schemastore.org/claude-code-settings.json'
        hooks     = [pscustomobject]@{}
    }
}

# ─── Работа со свойствами разобранного JSON ──────────────────────────────────────────────────────

function Get-Prop {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $prop = $Object.PSObject.Properties[$Name]
    if (-not $prop) { return $null }
    return $prop.Value
}

function Set-Prop {
    param($Object, [string]$Name, $Value)
    $prop = $Object.PSObject.Properties[$Name]
    if ($prop) {
        $prop.Value = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

# ─── Опознание записей сторожей ──────────────────────────────────────────────────────────────────

function Test-OurHook {
    param($Hook)
    $command = Get-SlashPath ([string](Get-Prop $Hook 'command'))
    return $command -match $OurMark
}

function Get-HookWhat {
    param($Hook)
    # Как запись называется в отчёте — одними и теми же словами и при установке, и при показе.
    switch (Get-HookKind $Hook) {
        'deliver' { return 'доставка находок' }
        'nudge' { return 'подсказка при правке плана волны' }
        default { return 'запись канала' }
    }
}

function Get-HookKind {
    param($Hook)
    $command = Get-SlashPath ([string](Get-Prop $Hook 'command'))
    if ($command -match 'wave-board-deliver\.ps1') { return 'deliver' }
    if ($command -match 'pretooluse-wave-board-nudge\.ps1') { return 'nudge' }
    return 'other'
}

function Get-HookMark {
    param($Hook)
    # Различитель внутри вида: у доставки это стадия, у подсказки — инструмент из условия. По нему
    # повторный прогон узнаёт СВОЮ прежнюю запись и правит её, а не кладёт рядом вторую такую же.
    $kind = Get-HookKind $Hook
    if ($kind -eq 'deliver') {
        $stage = [regex]::Match([string](Get-Prop $Hook 'command'), '-Stage\s+(\w+)')
        if ($stage.Success) { return "deliver:$($stage.Groups[1].Value)" }
        return 'deliver:'
    }
    if ($kind -eq 'nudge') {
        $tool = [regex]::Match([string](Get-Prop $Hook 'if'), '^\s*(\w+)\s*\(')
        if ($tool.Success) { return "nudge:$($tool.Groups[1].Value)" }
        return 'nudge:'
    }
    return 'other'
}

function New-HookRecord {
    param($Wanted)
    # Порядок полей ровно тот, что в уже написанных руками настройках: так запись читается глазами
    # одинаково, где бы она ни стояла.
    $record = [ordered]@{ type = 'command'; shell = 'powershell'; command = $Wanted.Command }
    if ($Wanted.If) { $record['if'] = $Wanted.If }
    $record['timeout'] = $Wanted.Timeout
    if ($Wanted.Status) { $record['statusMessage'] = $Wanted.Status }
    return [pscustomobject]$record
}

function Update-HookRecord {
    param($Hook, $Wanted)
    # Правим НА МЕСТЕ: существующие поля сохраняют свой порядок, дописывается только недостающее.
    Set-Prop $Hook 'type' 'command'
    Set-Prop $Hook 'shell' 'powershell'
    Set-Prop $Hook 'command' $Wanted.Command
    if ($Wanted.If) { Set-Prop $Hook 'if' $Wanted.If }
    Set-Prop $Hook 'timeout' $Wanted.Timeout
    if ($Wanted.Status) { Set-Prop $Hook 'statusMessage' $Wanted.Status }
}

function Get-EventEntries {
    param($Settings, [string]$EventName)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return @() }
    $entries = Get-Prop $node $EventName
    if (-not $entries) { return @() }
    return @($entries)
}

function Set-EventEntries {
    param($Settings, [string]$EventName, $Entries)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) {
        $node = [pscustomobject]@{}
        Set-Prop $Settings 'hooks' $node
    }
    $kept = @($Entries)
    if ($kept.Count -eq 0) {
        # Опустевшее событие убираем целиком: пустой массив читают глазами и принимают за
        # подключённого сторожа.
        if ($node.PSObject.Properties[$EventName]) { $node.PSObject.Properties.Remove($EventName) }
        return
    }
    Set-Prop $node $EventName ([object[]]$kept)
}

function Get-Buckets {
    param($Settings, [string]$EventName)
    # Записи события вместе со своими списками хуков — в изменяемом виде.
    $buckets = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in (Get-EventEntries -Settings $Settings -EventName $EventName)) {
        $list = [System.Collections.Generic.List[object]]::new()
        foreach ($hook in @(Get-Prop $entry 'hooks')) { if ($hook) { $list.Add($hook) } }
        $buckets.Add([pscustomobject]@{ Entry = $entry; Hooks = $list })
    }
    # Запятая обязательна: без неё PowerShell разворачивает список в отдельные значения, и на том
    # конце вместо изменяемого списка окажется обычный массив.
    return , $buckets
}

function Save-Buckets {
    param($Settings, [string]$EventName, $Buckets)
    $entries = [System.Collections.Generic.List[object]]::new()
    foreach ($bucket in $Buckets) {
        if ($bucket.Hooks.Count -eq 0) { continue }
        Set-Prop $bucket.Entry 'hooks' ([object[]]$bucket.Hooks)
        $entries.Add($bucket.Entry)
    }
    Set-EventEntries -Settings $Settings -EventName $EventName -Entries $entries
}

function Find-Bucket {
    param($Buckets, [string]$Matcher)
    foreach ($bucket in $Buckets) {
        $own = [string](Get-Prop $bucket.Entry 'matcher')
        if (-not $Matcher) {
            if (-not $own) { return $bucket }
            continue
        }
        if (-not $own) { continue }
        # `Write|Edit` и `Edit|Write` — один и тот же отбор: сравниваем составом, а не строкой.
        $mine = @($Matcher -split '\|' | ForEach-Object { $_.Trim() } | Sort-Object)
        $theirs = @($own -split '\|' | ForEach-Object { $_.Trim() } | Sort-Object)
        if (($mine -join '|') -eq ($theirs -join '|')) { return $bucket }
    }
    return $null
}

function New-Bucket {
    param($Buckets, [string]$Matcher)
    $entry = if ($Matcher) {
        [pscustomobject][ordered]@{ matcher = $Matcher; hooks = @() }
    } else {
        [pscustomobject][ordered]@{ hooks = @() }
    }
    $bucket = [pscustomobject]@{
        Entry = $entry
        Hooks = [System.Collections.Generic.List[object]]::new()
    }
    $Buckets.Add($bucket)
    return $bucket
}

function Get-OurEvents {
    param($Settings)
    # События, на которых УЖЕ стоят наши записи. Обходить только желаемые события мало: пропала
    # папка планов или переименовали раздел профиля — событие правки файлов в желаемое не попадёт
    # вовсе, прежняя запись сторожа-подсказки останется в настройках и будет указывать в мёртвое
    # место. Отчёт при этом говорил «не подключён», а показ состояния — «подключён»: врал отчёт.
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return @() }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($eventName in @($node.PSObject.Properties.Name)) {
        foreach ($bucket in (Get-Buckets -Settings $Settings -EventName $eventName)) {
            foreach ($hook in $bucket.Hooks) {
                if (Test-OurHook $hook) { $names.Add($eventName); break }
            }
        }
    }
    return @($names | Select-Object -Unique)
}

function Set-Guards {
    param($Settings, $Wanted)
    # Подключение сторожей. Идемпотентность держится на трёх правилах:
    #   • «наша» запись узнаётся по тому, что её команда ведёт в наш файл сторожа, — значит
    #     переехавший скилл правит СВОЮ прежнюю запись, а не заводит вторую;
    #   • внутри вида запись узнаётся по различителю (стадия у доставки, инструмент у подсказки),
    #     поэтому изменившееся условие тоже правится на месте;
    #   • всё, что осталось нашим и лишним, убирается — повторный прогон не копит дубли.
    # Чужие записи не читаются дальше поля команды и не двигаются ни при каком исходе.
    $events = @(@($Wanted | ForEach-Object { $_.Event }) + (Get-OurEvents -Settings $Settings)) |
        Select-Object -Unique
    foreach ($eventName in $events) {
        $here = @($Wanted | Where-Object { $_.Event -eq $eventName })
        $buckets = Get-Buckets -Settings $Settings -EventName $eventName

        $ours = [System.Collections.Generic.List[object]]::new()
        foreach ($bucket in $buckets) {
            foreach ($hook in $bucket.Hooks) {
                if (-not (Test-OurHook $hook)) { continue }
                $ours.Add([pscustomobject]@{
                        Bucket = $bucket
                        Hook   = $hook
                        Kind   = (Get-HookKind $hook)
                        Mark   = (Get-HookMark $hook)
                        Used   = $false
                    })
            }
        }

        foreach ($want in $here) {
            $hit = $ours | Where-Object { -not $_.Used -and $_.Mark -eq $want.Mark } | Select-Object -First 1
            if (-not $hit) {
                # Различитель не сошёлся, а вид тот же — это наша запись с устаревшим условием или
                # путём. Её надо поправить, иначе рядом ляжет вторая и сторож заговорит дважды.
                $hit = $ours | Where-Object { -not $_.Used -and $_.Kind -eq $want.Kind } | Select-Object -First 1
            }
            if ($hit) {
                $hit.Used = $true
                $before = ($hit.Hook | ConvertTo-Json -Depth 5 -Compress)
                Update-HookRecord -Hook $hit.Hook -Wanted $want
                $after = ($hit.Hook | ConvertTo-Json -Depth 5 -Compress)
                if (Test-SameText -Left $before -Right $after) {
                    Report "  уже подключено: $($want.Title)"
                } else {
                    Report "  поправлена запись (путь или условие устарели): $($want.Title)"
                }
                continue
            }
            $bucket = Find-Bucket -Buckets $buckets -Matcher $want.Matcher
            if (-not $bucket) { $bucket = New-Bucket -Buckets $buckets -Matcher $want.Matcher }
            $bucket.Hooks.Add((New-HookRecord -Wanted $want))
            Report "  подключено: $($want.Title)"
        }

        # Всё наше, что не сошлось ни с одним желаемым, — лишнее, и вида это не касается: запись
        # сторожа-подсказки после пропавшей папки планов не «другого вида», а просто больше не
        # нужна. Оставленная, она указывала бы в мёртвое место столько, сколько живёт проект.
        foreach ($extra in $ours) {
            if ($extra.Used) { continue }
            $extra.Bucket.Hooks.Remove($extra.Hook) | Out-Null
            Report "  снята наша прежняя запись, больше не нужная: $(Get-HookWhat $extra.Hook) (событие $eventName)"
        }

        Save-Buckets -Settings $Settings -EventName $eventName -Buckets $buckets
    }
}

function Remove-Guards {
    param($Settings)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return }
    foreach ($eventName in @($node.PSObject.Properties.Name)) {
        $buckets = Get-Buckets -Settings $Settings -EventName $eventName
        $removed = 0
        foreach ($bucket in $buckets) {
            foreach ($hook in @($bucket.Hooks)) {
                if (-not (Test-OurHook $hook)) { continue }
                $bucket.Hooks.Remove($hook) | Out-Null
                $removed++
            }
        }
        if ($removed -eq 0) { continue }
        Save-Buckets -Settings $Settings -EventName $eventName -Buckets $buckets
        Report "  снято записей на событии ${eventName}: $removed"
    }
}

# ─── Переходник ──────────────────────────────────────────────────────────────────────────────────

function Get-BridgeText {
    param([string]$Root)
    $target = Join-Path $CoordDir 'wave-board.ps1'
    $inside = Get-InsidePath -Root $Root -Target $CoordDir
    if ($inside) {
        $where = "$inside/"
        $rel = Get-SlashPath ([System.IO.Path]::GetRelativePath((Join-Path $Root 'scripts'), $target))
        $call = "`$target = Join-Path `$PSScriptRoot '$rel'"
    } else {
        # Скилл лежит вне проекта — относительного пути до него не построить, пишем полный.
        $where = (Get-SlashPath $CoordDir) + '/'
        $call = "`$target = '$(Get-SlashPath $target)'"
    }
    $lines = @(
        '#Requires -Version 7'
        '<#'
        "Переходник: инструмент $BridgeMark (``$where``),"
        'здесь только вызов — чтобы команда запуска во всех проектах со скиллом была одинаково короткой:'
        '`pwsh scripts/wave-board.ps1 ...`. Логика и правки — только в скрипте скилла, не здесь.'
        '#>'
        ''
        $call
        '& $target @args'
        'exit $LASTEXITCODE'
    )
    return (($lines -join "`n") + "`n")
}

function Get-BridgeTarget {
    param([string]$Text)
    # Строка вызова — единственное, что в переходнике важно на деле. Сравнивать целиком годится для
    # решения «переписать», но не для рапорта: подправленная шапка заготовки — не повод пугать
    # человека тем, что переходник ведёт не туда.
    if (-not $Text) { return '' }
    $found = [regex]::Match($Text, '(?m)^\$target\s*=.*$')
    if ($found.Success) { return $found.Value.Trim() }
    return ''
}

# ─── Состояние проекта ───────────────────────────────────────────────────────────────────────────

function Get-State {
    param([string]$Root)
    $profilePath = Join-Path $Root $ProfileName
    $bridgePath = Join-Path $Root 'scripts/wave-board.ps1'
    $profileText = Read-TextFile -Path $profilePath
    $bridgeText = Read-TextFile -Path $bridgePath
    $plans = Get-PlansFolder -ProfileText $profileText
    return [pscustomobject]@{
        SettingsPath = (Join-Path $Root '.claude/settings.json')
        ProfilePath  = $profilePath
        BridgePath   = $bridgePath
        ProfileText  = $profileText
        BridgeText   = $bridgeText
        BridgeIsOurs = [bool]($bridgeText -and ($bridgeText -match [regex]::Escape($BridgeMark)))
        Plans        = $plans
        PlansExists  = [bool]($plans -and (Test-Path -LiteralPath (Join-Path $Root $plans) -PathType Container))
    }
}

function Get-Wanted {
    param([string]$Root, [string]$Plans)
    # Как записывается путь к сторожу. Внутри проекта — от рабочей папки (`$PWD`), ровно так же, как
    # написаны остальные хуки проекта: тогда настройки переживают переезд папки и одинаково работают
    # в каждом рабочем дереве. Снаружи — полным путём, другого способа нет.
    $inside = Get-InsidePath -Root $Root -Target $CoordDir
    $base = if ($inside) { '$PWD/' + $inside } else { Get-SlashPath $CoordDir }
    $deliver = "& `"$base/hooks/wave-board-deliver.ps1`""
    $nudge = "& `"$base/hooks/pretooluse-wave-board-nudge.ps1`""

    $wanted = @(
        [pscustomobject]@{
            Event   = 'SessionStart'
            Matcher = ''
            Kind    = 'deliver'
            Mark    = 'deliver:Start'
            Command = "$deliver -Stage Start"
            Timeout = 20
            Status  = 'Смотрю доску волны'
            If      = ''
            Title   = 'доставка находок в начале сессии'
        }
        [pscustomobject]@{
            Event   = 'UserPromptSubmit'
            Matcher = ''
            Kind    = 'deliver'
            Mark    = 'deliver:Prompt'
            Command = "$deliver -Stage Prompt"
            Timeout = 15
            Status  = ''
            If      = ''
            Title   = 'доставка находок перед каждым обращением человека'
        }
    )
    # Сторож-подсказка держится на папке планов: не зная её, он не отличит план волны от любого
    # другого файла и будет молчать всегда. Нет папки в профиле — записи не заводим вовсе.
    if (-not $Plans) { return $wanted }
    foreach ($tool in @('Edit', 'Write')) {
        $wanted += [pscustomobject]@{
            Event   = 'PreToolUse'
            Matcher = 'Write|Edit'
            Kind    = 'nudge'
            Mark    = "nudge:$tool"
            Command = $nudge
            Timeout = 20
            Status  = ''
            If      = "$tool($Plans**)"
            Title   = "подсказка при правке плана волны ($tool, папка $Plans)"
        }
    }
    return $wanted
}

# ─── Режимы ──────────────────────────────────────────────────────────────────────────────────────

function Update-Profile {
    param([string]$Root, $State, $Told)
    # Профиль разбирается ПЕРВЫМ (из него берётся папка планов, без которой не решить, подключать ли
    # сторож-подсказку), а печатается вторым — отчёт идёт в том порядке, в котором про канал думают.
    # Поэтому строки отчёта складываются в $Told, а не печатаются на месте.
    $profileText = $State.ProfileText
    $template = Read-TextFile -Path (Join-Path $CoordDir 'templates/profile.md')
    $extra = Read-TextFile -Path (Join-Path $CoordDir 'templates/profile-coordination.md')
    if (-not $template -or -not $extra) {
        Deny "Не нашлись заготовки профиля — комплект неполон: $CoordDir/templates"
    }
    $blocks = @()
    foreach ($name in @('Coordination', 'Plans')) {
        $block = Get-MarkdownSection -Text $extra -Name $name
        if (-not $block) {
            Deny "В заготовке разделов нет «## $name» — комплект неполон: $CoordDir/templates"
        }
        $blocks += [pscustomobject]@{ Name = $name; Text = $block }
    }

    if (-not $profileText) {
        $profileText = $template.TrimEnd() + "`n`n" + (($blocks | ForEach-Object { $_.Text }) -join "`n`n") + "`n"
        Save-TextFile -Path $State.ProfilePath -Text $profileText
        $Told.Add("  заведён профиль $ProfileName, в нём разделы «## Coordination» и «## Plans»")
        $Told.Add('  ‼️ править под свой проект: команды проверок, ревью и папку планов волн')
    } else {
        $missing = @($blocks | Where-Object { -not (Get-MarkdownSection -Text $profileText -Name $_.Name) })
        if ($missing.Count -eq 0) {
            $Told.Add("  профиль $ProfileName уже описывает канал — не тронут")
        } else {
            # Дописываем только в конец и только недостающее: в уже написанном тексте нельзя менять
            # ни байта, там слова человека про его проект.
            $eol = if ($profileText -match "`r`n") { "`r`n" } else { "`n" }
            $gap = ''
            if (-not $profileText.EndsWith("`n")) { $gap = $eol + $eol }
            elseif (-not $profileText.EndsWith($eol + $eol)) { $gap = $eol }
            $body = (($missing | ForEach-Object { $_.Text }) -join "`n`n") + "`n"
            if ($eol -ne "`n") { $body = $body -replace "`n", $eol }
            $profileText = $profileText + $gap + $body
            Save-TextFile -Path $State.ProfilePath -Text $profileText
            $Told.Add("  дописано в профиль: $(($missing | ForEach-Object { "«## $($_.Name)»" }) -join ', ')")
        }
    }

    $plans = Get-PlansFolder -ProfileText $profileText
    if (-not $plans) {
        $Told.Add('  папка планов волн в разделе «## Plans» не названа — сторож-подсказка не подключён')
        $Told.Add('  (впишите её туда обратными кавычками — строкой вида «Планы волн: `docs/plans/`» — и повторите установку)')
    } elseif (-not (Test-Path -LiteralPath (Join-Path $Root $plans) -PathType Container)) {
        $Told.Add("  папки планов «$plans», названной в профиле, в проекте нет — сторож-подсказка не подключён")
        $Told.Add('  (заведите папку или поправьте раздел «## Plans» и повторите установку)')
        $plans = ''
    } else {
        $Told.Add("  папка планов волн: $plans")
    }
    return $plans
}

function Invoke-Install {
    param([string]$Root)
    $state = Get-State -Root $Root
    $told = [System.Collections.Generic.List[string]]::new()
    $plans = Update-Profile -Root $Root -State $state -Told $told

    Report ''
    Report '1. Сторожа в настройках проекта'
    $settings = Read-Settings -Path $state.SettingsPath
    $data = $settings.Data
    $restyle = $false
    if (-not $data) {
        $data = New-Settings
        Report "  файла настроек не было — завожу $($state.SettingsPath)"
    } else {
        # Обратно в текст файл собирает сериализатор, и раскладку он берёт свою: отступы, переносы,
        # расстановку скобок. Содержание и порядок записей при этом целы, а вот файл в диффе будет
        # виден целиком — про это надо сказать заранее, а не обещать «ничего не тронуто».
        $restyle = -not (Test-SameText `
                -Left (ConvertTo-FileJson -Data $data -Original $settings.Raw) -Right $settings.Raw)
    }
    Set-Guards -Settings $data -Wanted (Get-Wanted -Root $Root -Plans $plans)
    $text = ConvertTo-FileJson -Data $data -Original $settings.Raw
    if (Test-SameText -Left $text -Right $settings.Raw) {
        Report '  настройки не изменились'
    } else {
        Save-TextFile -Path $state.SettingsPath -Text $text
        if ($restyle) {
            Report '  настройки сохранены; чужие записи и их порядок целы, но ‼️ файл переписан'
            Report '  единым стилем — отступы и раскладка стали такими, какими их пишет установщик'
        } else {
            Report '  настройки сохранены — чужие записи и их порядок не тронуты'
        }
    }

    Report ''
    Report '2. Профиль проекта'
    foreach ($line in $told) { Report $line }

    Report ''
    Report '3. Переходник scripts/wave-board.ps1'
    $bridge = Get-BridgeText -Root $Root
    if (-not $state.BridgeText) {
        Save-TextFile -Path $state.BridgePath -Text $bridge
        Report "  положен переходник: $($state.BridgePath)"
    } elseif (-not $state.BridgeIsOurs) {
        Report '  ‼️ на месте переходника лежит ЧУЖОЙ файл — не тронут'
        Report "  ($($state.BridgePath)); зовите инструмент полным путём или уберите чужой файл сами"
    } elseif (Test-SameText -Left $state.BridgeText -Right $bridge) {
        Report '  переходник уже на месте'
    } else {
        Save-TextFile -Path $state.BridgePath -Text $bridge
        $target = Get-BridgeTarget $bridge
        Report "  переходник обновлён — теперь он зовёт: $target"
    }

    Report ''
    Report 'Готово. Что подключено — `-Mode Check`, снять — `-Mode Uninstall`.'
}

function Invoke-Uninstall {
    param([string]$Root)
    $state = Get-State -Root $Root

    Report ''
    Report '1. Сторожа в настройках проекта'
    $settings = Read-Settings -Path $state.SettingsPath
    if (-not $settings.Data) {
        Report '  настроек проекта нет — снимать нечего'
    } else {
        # Та же оговорка, что при установке: сохранение переписывает файл раскладкой сериализатора.
        $restyle = -not (Test-SameText `
                -Left (ConvertTo-FileJson -Data $settings.Data -Original $settings.Raw) -Right $settings.Raw)
        Remove-Guards -Settings $settings.Data
        $text = ConvertTo-FileJson -Data $settings.Data -Original $settings.Raw
        if (Test-SameText -Left $text -Right $settings.Raw) {
            Report '  наших записей в настройках не было'
        } elseif ($restyle) {
            Save-TextFile -Path $state.SettingsPath -Text $text
            Report '  настройки сохранены; чужие записи целы, но ‼️ файл переписан единым стилем'
        } else {
            Save-TextFile -Path $state.SettingsPath -Text $text
            Report '  настройки сохранены — чужие записи оставлены как были'
        }
    }

    Report ''
    Report '2. Профиль проекта'
    Report "  $ProfileName оставлен как есть — там текст человека про его проект"

    Report ''
    Report '3. Переходник scripts/wave-board.ps1'
    if (-not $state.BridgeText) {
        Report '  переходника не было'
    } elseif (-not $state.BridgeIsOurs) {
        Report '  на месте переходника лежит чужой файл — не тронут'
    } else {
        Remove-Item -LiteralPath $state.BridgePath -Force
        Report "  убран: $($state.BridgePath)"
    }

    Report ''
    Report 'Канал снят. Папка скилла на месте — поставить обратно можно этой же командой.'
}

function Invoke-Check {
    param([string]$Root)
    $state = Get-State -Root $Root

    Report ''
    Report '1. Сторожа в настройках проекта'
    $settings = Read-Settings -Path $state.SettingsPath
    if (-not $settings.Data) {
        Report "  настроек нет вовсе: $($state.SettingsPath)"
    } else {
        $seen = 0
        foreach ($eventName in @('SessionStart', 'UserPromptSubmit', 'PreToolUse')) {
            foreach ($bucket in (Get-Buckets -Settings $settings.Data -EventName $eventName)) {
                foreach ($hook in $bucket.Hooks) {
                    if (-not (Test-OurHook $hook)) { continue }
                    $seen++
                    $what = Get-HookWhat $hook
                    $cond = [string](Get-Prop $hook 'if')
                    $tail = if ($cond) { ", условие $cond" } else { '' }
                    Report "  ${eventName}: $what$tail"
                    Report "    ведёт в: $(Get-Prop $hook 'command')"
                }
            }
        }
        if ($seen -eq 0) { Report '  ни одной нашей записи — канал не подключён' }
    }

    Report ''
    Report '2. Профиль проекта'
    if (-not $state.ProfileText) {
        Report "  профиля нет: $($state.ProfilePath)"
    } else {
        foreach ($name in @('Coordination', 'Plans')) {
            $has = [bool](Get-MarkdownSection -Text $state.ProfileText -Name $name)
            Report "  раздел «## $name»: $(if ($has) { 'есть' } else { 'нет' })"
        }
        if ($state.Plans) {
            $where = if ($state.PlansExists) { 'есть в проекте' } else { '‼️ в проекте не заведена' }
            Report "  папка планов волн: $($state.Plans) — $where"
        } else {
            Report '  папка планов волн не объявлена — сторожу-подсказке не с чем работать'
        }
    }

    Report ''
    Report '3. Переходник scripts/wave-board.ps1'
    if (-not $state.BridgeText) {
        Report "  переходника нет: $($state.BridgePath)"
    } elseif (-not $state.BridgeIsOurs) {
        Report '  на этом месте лежит чужой файл — инструмент зовут полным путём'
    } elseif (Test-SameText -Left (Get-BridgeTarget $state.BridgeText) -Right (Get-BridgeTarget (Get-BridgeText -Root $Root))) {
        Report '  на месте и ведёт в папку скилла'
    } else {
        Report '  ‼️ на месте, но ведёт не туда, где сейчас лежит скилл — нужна установка заново'
        Report "    сейчас: $(Get-BridgeTarget $state.BridgeText)"
    }
    Report ''
}

# ─── Ход ─────────────────────────────────────────────────────────────────────────────────────────

$root = Resolve-Root -Given $ProjectRoot
$title = switch ($Mode) {
    'Install' { 'установка' }
    'Uninstall' { 'снятие' }
    'Check' { 'состояние' }
}
Report "Канал согласования между вкладками — $title"
Report "  проект:       $root"
Report "  папка скилла: $CoordDir"

switch ($Mode) {
    'Install' { Invoke-Install -Root $root }
    'Uninstall' { Invoke-Uninstall -Root $root }
    'Check' { Invoke-Check -Root $root }
}
