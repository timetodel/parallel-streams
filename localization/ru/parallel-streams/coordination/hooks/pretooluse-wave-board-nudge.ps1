#Requires -Version 7
<#
Хук PreToolUse: правится план волны — напомнить, что до живой вкладки соседа это само не дойдёт.

Зачем. Дописать находку в план кажется достаточным действием — план ведь и есть закон волны. Но
закон он для СЛЕДУЮЩИХ вкладок: живая читала план один раз, на старте, и работает по копии в своём
рабочем дереве. 20.08.2026 так потерялись три находки. Сторож ловит ровно этот момент — когда
правка уже пишется, а адресат ещё не назван, — и показывает, какие деревья волны живы.

Живое дерево ≠ живая вкладка: дерево остаётся и после закрытия вкладки. Живость сторож берёт из
маячка, который сторож доставки обновляет в своём дереве на каждом ходу, и говорит ровно то, что
знает: «отмечалась недавно» — вкладка работала в последние часы и скорее всего жива (закрыться она
могла и час назад, маячок этого не заметит — снимать его при закрытии некому), «без свежей
отметки» — неизвестно (могла закрыться, а могла работать молча или стартовать до появления
сторожа), «дерева нет» — поток закрыт. Выдавать неизвестность за закрытый поток нельзя: находка
уйдёт в «Хвосты волны» мимо живого соседа. Обратное переобещание тоже вредно: «точно жива» снимает
с автора находки вопрос, не завести ли ей задание в хвостах, — потому сторож так и не говорит.

Список ограничен сверху и начинается с тех, о ком известно точно. Сторож подсказывает, а не
решает: он сужает выбор до нескольких имён, дальше смотрит человек за экраном.

Показывается один раз за сессию: правок плана в потоке много, а напоминание одно и то же.
Хук ничего не блокирует и при любой неожиданности молча выходит нулём.

Папку с планами волн берём из профиля проекта (`.parallel-streams.md`, раздел `## Plans`) — общим
разбором из `lib/wave-board-lib.ps1`, тем же, которым её читает отбор общих по устройству мест.
Зашитая папка одного проекта делала бы сторожа немым во всех остальных, а папка не названа — значит
планов в проекте нет и напоминать не о чем.
#>

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Больше имён — уже не подсказка, а стена текста, и вкладка платит за неё на каждом шаге.
$MaxNames = 8

function Get-StateDir {
    $dir = Join-Path $PWD '.claude/.cache'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    return $dir
}

function Get-WaveStreams {
    param([string]$WaveMarker)
    # Деревья волны, разложенные по живости: отметившиеся вкладки отдельно, молчащие деревья
    # отдельно. Главная папка репозитория потоком не является, себя тоже не предлагаем.
    $here = ($PWD.Path -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
    $alive = [System.Collections.Generic.List[string]]::new()
    $silent = [System.Collections.Generic.List[string]]::new()
    foreach ($tree in (Get-Worktrees)) {
        if ($tree.path -notmatch '/\.claude/worktrees/[^/]+$') { continue }
        if ($tree.path.ToLowerInvariant() -eq $here) { continue }
        $name = if ($tree.branch) { $tree.branch } else { Split-Path -Leaf $tree.path }
        if ($WaveMarker -and $name -notmatch $WaveMarker -and $tree.path -notmatch $WaveMarker) { continue }
        # Молчащее дерево из списка НЕ выбрасываем: маячка может не быть у вкладки, запущенной до
        # появления сторожа, а объявить её закрытой — значит увести находку в «Хвосты волны» мимо
        # живого соседа. Говорим то, что знаем: отметилась или неизвестно.
        if ($tree.live) { $alive.Add($name) } else { $silent.Add($name) }
    }
    return [pscustomobject]@{
        Alive  = @($alive | Sort-Object -Unique)
        Silent = @($silent | Sort-Object -Unique)
    }
}

try {
    . (Join-Path $PSScriptRoot '../lib/hook-io.ps1')
    # Общая часть подключается здесь, а не ниже по тексту: из неё берётся и папка планов, по
    # которой сторож решает, план ли перед ним, и список рабочих деревьев.
    . (Join-Path $PSScriptRoot '../lib/wave-board-lib.ps1')
    $raw = Read-HookInput
    if (-not $raw) { exit 0 }
    $call = $raw | ConvertFrom-Json
    $filePath = $call.tool_input.file_path
    if (-not $filePath) { exit 0 }
    $normalized = $filePath -replace '\\', '/'
    # Путь приходит и относительным («планы-волн/…»), а шаблон ниже требует ведущей
    # косой — на таком вызове сторож молча выходил, то есть не работал ровно там, где короче писать.
    if (-not [System.IO.Path]::IsPathRooted($normalized)) {
        $normalized = (Join-Path $PWD.Path $normalized) -replace '\\', '/'
    }
    # Папку планов называет профиль проекта. Не названа — планов в проекте нет, и напоминать не о
    # чем: молчим, а не сверяемся с папкой какого-то одного проекта.
    $plans = Get-ProfilePlansFolder
    if (-not $plans) { exit 0 }
    if ($normalized -notmatch [regex]::Escape("/$plans")) { exit 0 }
    # Архив — это уже закрытые волны: адресовать там некому.
    if ($normalized -match [regex]::Escape("/${plans}archive/")) { exit 0 }

    $sessionId = if ($call.session_id) { [string]$call.session_id } else { 'nosession' }
    $flag = Join-Path (Get-StateDir) "wave-board-nudge-$sessionId.flag"
    if (Test-Path $flag) { exit 0 }
    Get-ChildItem (Get-StateDir) -Filter 'wave-board-nudge-*.flag' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    # Маркер волны берём из имени плана (`2026-08-13-server-wave3-corp-tariff.md`), чтобы не
    # предлагать соседей из чужой волны. Номер в имени есть далеко не у всякого плана — тогда
    # показываем деревья проекта, но с оговоркой (волна не определена) и с потолком: без потолка
    # список вырождался во ВСЕ деревья репозитория, их два десятка.
    $marker = ''
    $matched = [regex]::Match((Split-Path -Leaf $normalized), 'wave\d+')
    if ($matched.Success) { $marker = $matched.Value }

    $streams = Get-WaveStreams -WaveMarker $marker
    New-Item -ItemType File -Path $flag -Force | Out-Null

    $waveWord = if ($marker) { "волны ($marker)" } else { 'проекта' }
    # Потолок общий на оба списка: сначала те, о ком знаем точно, потом остальные.
    $shownAlive = @($streams.Alive | Select-Object -First $MaxNames)
    $shownSilent = @($streams.Silent | Select-Object -First ($MaxNames - $shownAlive.Count))
    $total = $streams.Alive.Count + $streams.Silent.Count
    $rest = $total - $shownAlive.Count - $shownSilent.Count

    $head = [System.Collections.Generic.List[string]]::new()
    if ($total -eq 0) {
        $head.Add("Рабочих деревьев ${waveWord} нет вовсе — значит потоки закрыты.")
    } else {
        if (-not $marker) {
            $head.Add('Волну по имени плана не определить — ниже деревья всего проекта, сверьтесь с именем потока.')
        }
        if ($shownAlive.Count -gt 0) {
            $head.Add("Вкладки ${waveWord} отмечались за последние часы (скорее всего живы): $($shownAlive -join ', ')")
        }
        if ($shownSilent.Count -gt 0) {
            $head.Add("Деревья ${waveWord} без свежей отметки (жива вкладка или нет — неизвестно): $($shownSilent -join ', ')")
        }
        if ($rest -gt 0) { $head.Add("… и ещё $rest — весь список: git worktree list") }
    }
    $advice = if ($total -gt 0) {
        @(
            'Находка кому-то из них — И в план, И на доску волны, иначе не дойдёт:'
            '  pwsh scripts/wave-board.ps1 -Mode Add -To <волна/поток> -Title "<одна строка>" -Where "<где полный текст>"'
            'Адрес — НОМЕР потока в таблице плана (wave6/3): имена веток к середине волны расходятся'
            'с объявленными. Кто какой поток ведёт и в каком он состоянии:'
            '  pwsh scripts/wave-board.ps1 -Mode Streams'
            'Поток сдан — доска откажет: находке место в разделе «Хвосты волны», отдельным пунктом'
            'с готовым текстом запуска новой вкладки.'
        )
    } else {
        @(
            'Значит находка не может быть строкой в чужой задаче: её место — раздел «Хвосты волны»,'
            'отдельным пунктом с готовым текстом запуска новой вкладки.'
        )
    }

    $text = @(
        'Правится план волны. Дописка в план до ЖИВОЙ вкладки соседа сама не дойдёт: план читают'
        'один раз, на старте, и в её рабочем дереве файл остался той версии, что был при старте.'
        ''
    ) + $head + @('') + $advice | Join-String -Separator "`n"

    @{
        hookSpecificOutput = @{
            hookEventName     = 'PreToolUse'
            additionalContext = $text
        }
        suppressOutput     = $true
    } | ConvertTo-Json -Depth 5 -Compress
} catch {
    exit 0
}
