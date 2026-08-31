#Requires -Version 7
<#
Сторож доставки: приносит вкладке то, что положили на доску волны ЕЙ.

Зачем. План волны вкладка читает один раз, на старте, из своего рабочего дерева. Дописка в план
до неё не доходит ни через файл (в её дереве он остался прежним), ни через перечитывание (его нет).
Сторож закрывает этот зазор: сосед кладёт находку на общую доску, а вкладка получает её сама.

Два события, потому что дыры две:
  • Start  — начало сессии И восстановление после сжатия контекста. Показывает ВСЁ открытое,
             адресованное этому потоку, и обнуляет журнал показанного: после сжатия напоминание
             обязано вернуться, иначе оно теряется ровно там, где длинная работа.
  • Prompt — обычный ход. Показывает только то, чего вкладка ещё не видела в этой сессии.

Цена для контекста считана заранее и держится тремя предохранителями: одна запись показывается
вкладке один раз (журнал), за раз не больше пяти, чужому потоку не показывается вовсе. Потолок
задерживает, но не съедает: непоказанное приходит следующими ходами.

Заодно сторож отмечает своё рабочее дерево живым (маячок `.claude/.cache/wave-board-alive.txt`):
по нему инструмент и напоминание при правке плана отличают работающую вкладку от брошенного
дерева. Отметка ставится на каждом ходу и до любых ранних выходов — см. комментарий у неё.

Хук НИЧЕГО не блокирует и при любой неожиданности молча выходит нулём: сорванный сторож не должен
мешать работе.
#>

param(
    # Ни обязательности, ни набора допустимых значений средствами PowerShell: разбор параметров
    # идёт ДО тела скрипта, и неверный запуск отдавал бы ненулевой код с чужим текстом наружу —
    # ровно вопреки обещанию молча выйти нулём. Значение проверяем сами, уже внутри.
    [string]$Stage,

    # Только для тестов: доска в стороне от рабочей. В работе не задаётся — путь берётся у git.
    [string]$BoardPath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Больше — стена текста, которую перестают читать; остаток вкладка досмотрит показом доски.
$MaxRecords = 5

function Get-StateDir {
    # .claude/.cache уже в .gitignore — состояние хуков там не мусорит в репозитории.
    $dir = Join-Path $PWD '.claude/.cache'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    return $dir
}

function Get-OverlapBlock {
    param($Claims, $MyClaim, [string]$StateDir, [string]$SessionId)
    # Сосед правит те же файлы. Говорим об этом ОДИН раз за сессию на каждого соседа: предупреждение
    # одно и то же, а контекст вкладки переотправляется на каждом шаге.
    #
    # Зачем вообще. Вкладку тянет прихватить соседнюю задачу; она предлагает её владельцу, а
    # владелец не знает, что задачу планировали другому потоку, и подтверждает. Пересечение по
    # файлам — единственный признак этого, видимый машине, и виден он ДО конфликта слияния.
    try {
        $found = @(Get-Overlaps -Claims $Claims -MyClaim $MyClaim)
        if ($found.Count -eq 0) { return $null }
        $said = Join-Path $StateDir "wave-board-overlap-$SessionId.txt"
        $seen = if (Test-Path $said) { @(Get-Content $said -Encoding utf8 | Where-Object { $_.Trim() }) } else { @() }
        $fresh = @($found | Where-Object { "$($_.Claim.Record.wave)/$($_.Claim.Record.stream)" -notin $seen })
        if ($fresh.Count -eq 0) { return $null }
        Add-Content -Path $said -Encoding utf8 -Value (
            @($fresh | ForEach-Object { "$($_.Claim.Record.wave)/$($_.Claim.Record.stream)" }) -join "`n")
        $lines = foreach ($overlap in $fresh) {
            $names = @($overlap.Files | Select-Object -First 3) -join ', '
            $more = if ($overlap.Files.Count -gt 3) { " … и ещё $($overlap.Files.Count - 3)" } else { '' }
            $who = $overlap.Claim.Record
            "  • поток $($who.wave)/$($who.stream)$(if ($who.name) { " «$($who.name)»" }) — общие файлы: $names$more"
        }
        return @(
            'Соседний поток правит те же файлы прямо сейчас:'
            ($lines -join "`n")
            'Прежде чем предлагать владельцу работу за пределами своих задач — посмотрите, чей это кусок:'
            '  pwsh scripts/wave-board.ps1 -Mode Streams -Task <номер задачи>'
            'Владелец не знает, что задачу планировали другому потоку, и подтвердит её вам.'
        ) | Where-Object { $_ } | Join-String -Separator "`n"
    } catch {
        return $null
    }
}

function Get-StuckBlock {
    param([string]$Board, $Claims, $MyClaim)
    # Сводка застрявшего — ТОЛЬКО в главной папке репозитория: там сидит владелец, там нет потока и
    # нечего забивать. В рабочем дереве это был бы шум о чужих находках.
    #
    # Это единственное место, где механизм признаётся, что доставка не состоялась. Без него неудача
    # выглядит как тишина, а тишина — как «соседу нечего сказать».
    try {
        $gitDir = (& git rev-parse --git-dir 2>$null)
        $common = (& git rev-parse --git-common-dir 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $gitDir -or -not $common) { return $null }
        if ($gitDir.Trim() -ne $common.Trim()) { return $null }
        if (-not (Test-Path $Board)) { return $null }
        $records = @(Get-OpenRecords -Path $Board)
        $stuck = @(Get-StuckRecords -Records $records -Claims $Claims -KnownKeys (Get-KnownStreamKeys -AliveOnly))
        if ($stuck.Count -eq 0) { return $null }
        $shown = @($stuck | Select-Object -First $MaxRecords)
        $lines = foreach ($item in $shown) {
            "  • «$($item.Record.title)» — кому: $($item.Record.to), $($item.Reason)"
        }
        $tail = if ($stuck.Count -gt $shown.Count) { "  … и ещё $($stuck.Count - $shown.Count)" } else { $null }
        # Куда девать находку — через ту же общую проверку, что и у инструмента: в проекте без волн
        # раздел плана это указание в пустоту, и находка после него не попадает никуда вовсе.
        # Хватает одной находки, у чьего адресата план есть: строка про раздел плана нужна ей.
        $withPlan = @($stuck | Where-Object {
                Test-AddresseeHasPlan -Claims $Claims -Raw ([string]$_.Record.to) `
                    -Address (Get-StreamAddress -Raw ([string]$_.Record.to)) -Mine $MyClaim
            })
        $advice = if ($withPlan.Count -gt 0) {
            'Их место — раздел «Хвосты волны» плана, отдельным пунктом с готовым текстом запуска новой вкладки.'
        } else {
            'Плана волны нет — назовите находку в ответе владельцу.'
        }
        return @(
            "На доске волны застряло записей: $($stuck.Count) — адресат их не получит."
            ($lines -join "`n")
            $tail
            $advice
            'Вся доска: pwsh scripts/wave-board.ps1 -Mode Show'
        ) | Where-Object { $_ } | Join-String -Separator "`n"
    } catch {
        return $null
    }
}

function Send-Context {
    param([string]$Text, [string]$HookEvent)
    @{
        hookSpecificOutput = @{
            hookEventName     = $HookEvent
            additionalContext = $Text
        }
        suppressOutput     = $true
    } | ConvertTo-Json -Depth 5 -Compress
}

try {
    . (Join-Path $PSScriptRoot '../lib/hook-io.ps1')
    $raw = Read-HookInput
    if ($Stage -notin @('Start', 'Prompt')) { exit 0 }
    # Ни $input, ни $event: обе — автоматические переменные PowerShell.
    $call = if ($raw) { $raw | ConvertFrom-Json } else { $null }
    $sessionId = if ($call -and $call.session_id) { [string]$call.session_id } else { 'nosession' }

    . (Join-Path $PSScriptRoot '../lib/wave-board-lib.ps1')

    $stateDir = Get-StateDir
    $shownFile = Join-Path $stateDir "wave-board-shown-$sessionId.txt"
    $shownName = Split-Path -Leaf $shownFile
    # Журнал сказанного о пересечении — тоже свой у сессии, и чистка обязана щадить его так же:
    # время правки у него обновляется только когда есть что сказать, поэтому на сессии длиннее
    # суток он попал бы под чистку и то же предупреждение вернулось бы, хотя обещано «один раз».
    $overlapName = "wave-board-overlap-$sessionId.txt"

    # Маячок живой вкладки — по нему сосед отличает работающую вкладку от брошенного дерева.
    # Ставится на КАЖДОМ ходу, обеих стадий и до любых ранних выходов: иначе живой считалась бы
    # ровно та вкладка, которой уже что-то положили, а остальные выглядели бы закрытыми. Стоит это
    # одной записи в файл — папку состояния сторож всё равно трогает.
    Set-Content -Path (Get-AliveBeaconPath -TreePath $PWD.Path) `
        -Value "$((Get-Date).ToString('s')) $sessionId" -Encoding utf8

    # Та же отметка, но в заявке потока: маячок говорит о ПАПКЕ, заявка — о ПОТОКЕ, и переживает
    # удаление папки. Заявки нет (вкладка не объявлялась) — тихо ничего не делаем.
    $registry = Get-RegistryDir -BoardOverride $BoardPath
    Update-ClaimSeen -Dir $registry -TreePath $PWD.Path

    # Отметка живой сессии: время правки журнала обновляем на КАЖДОМ ходу, а не только когда есть
    # что показать. Иначе долгая сессия, которой сутками не приходило находок, попадала под чистку
    # вместе с закрытыми — и всё показанное приходило ей заново.
    if (Test-Path $shownFile) { (Get-Item $shownFile).LastWriteTime = Get-Date }

    # Чистка идёт ДО любых ранних выходов: раньше она стояла ниже них и при пустой доске не
    # выполнялась вовсе — то есть чаще всего не выполнялась никогда.
    Get-ChildItem $stateDir -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -like 'wave-board-shown-*.txt' -or $_.Name -like 'wave-board-overlap-*.txt') -and
            $_.Name -notin @($shownName, $overlapName) -and $_.LastWriteTime -lt (Get-Date).AddDays(-1)
        } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    # Список тронутых файлов в своей заявке — по нему соседи видят пересечение работы. Считается
    # не чаще раза в несколько минут и молчит при любой неудаче.
    Update-ClaimFiles -Dir $registry -TreePath $PWD.Path

    $board = Get-BoardPath -Override $BoardPath
    $claims = @(Get-Claims -Dir $registry)
    # Имена волн из реестра — разбору адреса: волна зовётся не только «wave6», там, где волн нет,
    # её подставляет заявка (датой или словом). Без этого адрес такой волны не разобрался бы, и
    # находка не дошла бы до вкладки, которая её ждёт.
    Set-KnownWaves -Keys @($claims | ForEach-Object { $_.WaveKey })
    $claim = Get-CurrentClaim -Dir $registry

    # Блоки, которые сторож кладёт в контекст. Их три, и появляются они независимо: пришедшее с
    # доски, пересечение с соседом, сводка застрявшего у владельца. Раньше сторож выходил сразу,
    # если на доске пусто, — тогда двух последних не было бы видно никогда.
    $blocks = [System.Collections.Generic.List[string]]::new()

    $overlapText = Get-OverlapBlock -Claims $claims -MyClaim $claim -StateDir $stateDir -SessionId $sessionId
    if ($overlapText) { $blocks.Add($overlapText) }

    if ($Stage -eq 'Start') {
        $stuckText = Get-StuckBlock -Board $board -Claims $claims -MyClaim $claim
        if ($stuckText) { $blocks.Add($stuckText) }
    }

    if (-not (Test-Path $board)) {
        if ($blocks.Count -gt 0) {
            Send-Context -Text ($blocks -join "`n`n") -HookEvent $(if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' })
        }
        exit 0
    }

    # Ключи вкладки нужны дважды: по ним отбираем адресованное ей и по ним же прячем то, что она
    # уже учла. У записи «всем» закрытие персональное — общего «закрыто» у неё нет.
    # ‼️ Имена потока — из общего места, а не «те, что видно прямо сейчас». Прежде сторож брал
    # только текущие, и находка, адресованная именем ветки, до вкладки не доходила НИКОГДА, стоило
    # ветку переименовать, переключить или открепиться от неё. Приём её при этом принимал и обещал
    # автору «поток ведёт вкладка — скорее всего, дойдёт сама».
    $keys = @(Get-StreamNames -Claim $claim -Claims $claims)
    # ‼️ Сданному потоку находок не носим. Вкладки, которая его вела, больше нет, а если она ещё
    # открыта — ей уже сказано «находки больше не примут». Доставка ей чужой находки кончалась
    # худшим из возможного: текст доставки прямо велит закрыть запись, если она к работе не
    # относится, а закрытие именного адреса ОБЩЕЕ — и сданный поток гасил находку живому соседу.
    $released = $claim -and ([string]$claim.state -eq 'released')
    $mine = if ($released) {
        @()
    } else {
        Select-ForStream -Records (Get-OpenRecords -Path $board -Viewer $keys) -Keys $keys -Claim $claim
    }
    if ($mine.Count -eq 0) {
        if ($blocks.Count -gt 0) {
            Send-Context -Text ($blocks -join "`n`n") -HookEvent $(if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' })
        }
        exit 0
    }

    if ($Stage -eq 'Start') {
        # Обнуляем намеренно: сжатие контекста поднимает то же событие, и открытая находка должна
        # вернуться в контекст, а не остаться отмеченной как показанная.
        $fresh = @($mine)
        Set-Content -Path $shownFile -Value '' -Encoding utf8
    } else {
        $seen = if (Test-Path $shownFile) {
            @(Get-Content $shownFile -Encoding utf8 | Where-Object { $_.Trim() })
        } else { @() }
        $fresh = @($mine | Where-Object { [string]$_.id -notin $seen })
    }
    if ($fresh.Count -eq 0) {
        if ($blocks.Count -gt 0) {
            Send-Context -Text ($blocks -join "`n`n") -HookEvent $(if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' })
        }
        exit 0
    }

    # Помечаем показанными РОВНО показанные. Пометив весь остаток, мы хоронили бы шестую запись и
    # дальше: их не принёс бы ни следующий ход, ни сжатие контекста — журнал уже считал бы их
    # доставленными.
    $shown = @($fresh | Select-Object -First $MaxRecords)
    Add-Content -Path $shownFile -Value (@($shown | ForEach-Object { [string]$_.id }) -join "`n") -Encoding utf8

    # Уведомление «вашу находку учли» закрывается САМО, как только показано: оно и заведено затем,
    # чтобы снять с автора вопрос, а не добавить ему работы по закрытию записей. Сорвалось — не
    # беда: придёт ещё раз и закроется в следующий.
    foreach ($record in @($shown | Where-Object { [string]$_.kind -eq 'ack' })) {
        try {
            Add-BoardLine -Path $board -Line (
                [ordered]@{ id = [string]$record.id; at = (Get-Date).ToString('s'); done = $true } |
                    ConvertTo-Json -Depth 2 -Compress)
        } catch {
            # Молчим намеренно: сторож не должен мешать работе вкладки.
        }
    }

    $lines = foreach ($record in $shown) { Format-BoardRecord -Record $record }
    $tail = if ($fresh.Count -gt $shown.Count) {
        "  … и ещё $($fresh.Count - $shown.Count) — придут следующими ходами"
    } else { $null }

    $mail = @(
        "На доске волны есть адресованное этому потоку ($($fresh.Count)):"
        ($lines -join "`n")
        $tail
        'Это дописки соседних вкладок, сделанные ПОСЛЕ того, как поток прочитал план: план он больше'
        'не перечитывает, и в его рабочем дереве файл остался прежним — потому находка приходит сюда.'
        'Учли (или решили, что к работе не относится) — закройте:'
        '  pwsh scripts/wave-board.ps1 -Mode Done -Id <метка>'
    ) | Where-Object { $_ } | Join-String -Separator "`n"
    $blocks.Insert(0, $mail)
    $text = ($blocks -join "`n`n")

    $hookEvent = if ($Stage -eq 'Start') { 'SessionStart' } else { 'UserPromptSubmit' }
    Send-Context -Text $text -HookEvent $hookEvent
} catch {
    exit 0
}
