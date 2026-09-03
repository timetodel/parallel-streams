#Requires -Version 7
<#
Общая часть доски волны: где она лежит, как читается, кому адресована запись и чья вкладка жива.

Отдельным файлом, потому что потребителей два и они обязаны понимать доску ОДИНАКОВО: инструмент
(`../wave-board.ps1`, им кладут и закрывают записи) и сторож доставки
(`../hooks/wave-board-deliver.ps1`, он приносит записи во вкладку). Разъедься у них правило
адресации — находка молча не дойдёт, а выглядеть это будет как «соседу нечего сказать».

Сам по себе файл ничего не делает: только объявляет функции.
#>

# ‼️ Снимаем с окружения git-переменные ДО первого обращения к git. Иначе с заданным снаружи GIT_DIR
# доска уезжает в ЧУЖОЙ репозиторий (общий каталог спрашивают у git, и он отвечает заданным), имя
# ветки приходит от чужого дерева, а список живых вкладок — чужой целиком. Всё это молча: положивший
# уверен, что находку доставил, а соседу «нечего сказать» — то самое, ради чего доска и заведена.
# Разбор ловушки целиком — в подключаемом файле.
. (Join-Path $PSScriptRoot 'git-env-clean.ps1')

function Get-BoardPath {
    param([string]$Override)
    if ($Override) { return $Override }
    # Общий каталог, а не свой у рабочего дерева: он один на все деревья, лежит вне веток (в чужую
    # заявку запись не попадёт) и переживает удаление дерева вместе с закрытой вкладкой.
    # Из главной папки git отвечает относительным путём, из рабочего дерева — абсолютным.
    $common = (& git rev-parse --git-common-dir 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $common) { throw 'не репозиторий git — доске волны негде лежать' }
    $common = $common.Trim()
    if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $PWD $common }
    return Join-Path (Resolve-Path $common).Path 'wave-board/board.jsonl'
}

function Get-StreamKey {
    param([string]$Raw)
    # Один поток зовут тремя способами: веткой (`feat/wave3-plan-clock`), папкой рабочего дерева
    # (`wave3-plan-clock`) и папкой, где косая черта ветки заменена плюсом
    # (`feat+wave4-measure-and-accept`). Сводим к одному ключу, иначе находка, адресованная веткой,
    # не найдёт вкладку, которая знает себя по имени папки.
    if (-not $Raw) { return '' }
    $key = ($Raw -replace '\\', '/') -replace '\+', '/'
    $key = $key.Split('/')[-1]
    $key = $key -replace '^worktree-', ''
    return $key.Trim().ToLowerInvariant()
}

function Get-StreamKeys {
    param([string]$Raw)
    # Все ключи названного потока. Имя ветки и имя папки сводятся к одному ключу почти всегда, но
    # НЕ обязаны: дерево `oddfolder-tab` с веткой `feat/oddbranch-tab` даёт два разных. Закрытие
    # пишет один из них, а показ спрашивают другим — и он не узнаёт собственное закрытие потока.
    $key = Get-StreamKey -Raw $Raw
    $keys = [System.Collections.Generic.List[string]]::new()
    if ($key) { $keys.Add($key) }
    foreach ($tree in (Get-Worktrees)) {
        $branchKey = Get-StreamKey -Raw $tree.branch
        $folderKey = Get-StreamKey -Raw $tree.path
        if ($key -ne $branchKey -and $key -ne $folderKey) { continue }
        if ($branchKey) { $keys.Add($branchKey) }
        if ($folderKey) { $keys.Add($folderKey) }
    }
    return @($keys | Where-Object { $_ } | Select-Object -Unique)
}

function Get-CurrentKeys {
    # Ключей у вкладки два: по ветке и по имени рабочей папки. Совпало любое — находка её.
    #
    # Спрашиваем git ОДИН раз на запуск, как и про список деревьев. Сторож доставки зовётся на
    # каждом сообщении пользователя, а имена потока нужны ему дважды — при разборе реестра и при
    # отборе своих записей; второй запуск git был платой ни за что.
    if ($null -ne $script:WaveBoardCurrentKeys) { return $script:WaveBoardCurrentKeys }
    $keys = [System.Collections.Generic.List[string]]::new()
    try {
        $branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $branch) { $keys.Add((Get-StreamKey -Raw $branch.Trim())) }
    } catch {
        # Открепление от ветки или сорванный git — второго ключа хватит.
    }
    # Имя папки берём у КОРНЯ рабочего дерева, а не у текущей: вкладка, ушедшая в подкаталог, иначе
    # начинает звать себя именем этого подкаталога и перестаёт отзываться на собственное имя.
    $keys.Add((Get-StreamKey -Raw ((Get-TreeRoot) -split '/')[-1]))
    $script:WaveBoardCurrentKeys = @($keys | Where-Object { $_ } | Select-Object -Unique)
    return $script:WaveBoardCurrentKeys
}

function Get-FailureReason {
    param($Failure)
    # Причину читает человек, а системные сообщения приходят на языке системы — здесь по-английски.
    # Частый случай (файл кто-то держит) переводим сами; остальное отдаём как есть, но помечаем
    # «системное сообщение», чтобы чужую формулировку не приняли за нашу.
    $message = "$($Failure.Exception.Message)".Trim()
    if ($message -match 'being used by another process' -or $message -match 'используется другим процессом') {
        return 'файл занят другим процессом'
    }
    if ($message -match 'Access to the path .* is denied' -or $message -match 'Отказано в доступе') {
        return 'нет доступа к файлу'
    }
    return "системное сообщение: $message"
}

function Add-BoardLine {
    param([string]$Path, [string]$Line)
    # ‼️ Путь режем строкой, а каталог заводим внутри перехвата. Разбор пути средствами оболочки
    # спрашивает у неё про диск и на несуществующем диске (или отвалившейся сетевой шаре)
    # срывается насмерть — наружу выходило сырое английское системное сообщение вместо нашего
    # отказа. В объявлении это вылечено, а приём находки падал ровно так же (воспроизведено).
    $dir = [System.IO.Path]::GetDirectoryName($Path)
    try {
        if ($dir -and -not (Test-Path -LiteralPath $dir -PathType Container)) {
            New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
        }
    } catch {
        # Молчим: настоящую причину назовёт попытка записи ниже, и назовёт по-русски.
    }
    # Только дописывание, и с повтором: доску пишут несколько вкладок разом. Переписывание файла
    # целиком теряло бы чужую строку, а занятость файла соседом длится доли секунды.
    $reason = 'причина неизвестна'
    for ($try = 1; $try -le 10; $try++) {
        try {
            # Открываем на чтение-запись, а не на дописывание: перед своей строкой надо посмотреть,
            # чем кончается файл. Оборванная запись (вкладку закрыли на полуслове, диск кончился)
            # остаётся без перевода строки, и приклеенная к ней новая запись губит ОБЕ — не
            # разбирается ни та, ни другая, а инструмент рапортует об успехе.
            $stream = [System.IO.File]::Open($Path, 'OpenOrCreate', 'ReadWrite', 'Read')
            try {
                $prefix = ''
                if ($stream.Length -gt 0) {
                    $stream.Position = $stream.Length - 1
                    if ($stream.ReadByte() -ne 10) { $prefix = "`n" }
                }
                $stream.Position = $stream.Length
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($prefix + $Line + "`n")
                $stream.Write($bytes, 0, $bytes.Length)
            } finally {
                $stream.Dispose()
            }
            return
        } catch {
            # Настоящую причину сохраняем: после десяти попыток «доска занята» может оказаться
            # неправдой — место на диске, права, съёмный диск отвалился лечат по-разному.
            $reason = Get-FailureReason -Failure $_
            Start-Sleep -Milliseconds 50
        }
    }
    throw "не удалось дописать на доску ($Path). Последняя причина: $reason"
}

function Read-BoardContent {
    param([string]$Path)
    # Чтение доски, которое УМЕЕТ СООБЩИТЬ О НЕУДАЧЕ. «Не смогли прочитать» и «доска пуста» — вещи
    # противоположные, а выглядят одинаково: пустой список. На этой разнице стоит уплотнение: приняв
    # занятый файл за пустую доску, оно заменило бы её пустым файлом и стёрло все открытые находки.
    # Держат доску на доли секунды не только соседние вкладки — ещё антивирус, служба индексации и
    # резервное копирование, и в эти доли секунды размер файла как раз НЕ меняется.
    # ‼️ «Доски нет» узнаём ПО ОТКАЗУ ОТКРЫТЬ, а не отдельной проверкой существования. Проверка
    # существования отвечает «нет» и там, где на самом деле «не видно»: несуществующий диск,
    # отвалившаяся сетевая шара, каталог с закрытым заходом. Пустая доска и невидимая доска — вещи
    # противоположные, а выглядели одинаково; на сдаче потока это значит «ящик пуст, сдавайтесь»
    # там, где ящик на самом деле не прочитан.
    $reason = ''
    for ($try = 1; $try -le 5; $try++) {
        try {
            # Делимся файлом на чтение и запись: сосед в этот момент может дописывать свою строку.
            $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
            try {
                $length = $stream.Length
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
                $lines = @(($reader.ReadToEnd() -split "`r?`n") | Where-Object { $_.Trim() })
                return [pscustomobject]@{
                    Ok = $true; Missing = $false; Lines = $lines; Length = $length; Reason = ''
                }
            } finally {
                $stream.Dispose()
            }
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                return [pscustomobject]@{
                    Ok = $true; Missing = $true; Lines = @(); Length = 0; Reason = ''
                }
            }
            # Причину держим настоящую: «занята» и «нет прав» лечат по-разному.
            $reason = Get-FailureReason -Failure $_
            Start-Sleep -Milliseconds 30
        }
    }
    return [pscustomobject]@{ Ok = $false; Missing = $false; Lines = @(); Length = -1; Reason = $reason }
}

function Read-BoardLines {
    param([string]$Path)
    # Мягкий вид для тех, кому важнее не сорваться, чем узнать правду: сторож доставки при занятой
    # доске обязан промолчать, а не мешать работе. Всем остальным — Read-BoardContent.
    return @((Read-BoardContent -Path $Path).Lines)
}

function Get-BoardEntries {
    param([string[]]$Lines)
    # Пара «исходная строка + разобранная запись». Строка нужна уплотнению: оно переписывает доску
    # ТЕКСТОМ уцелевших строк, а не пересобранными записями, — обратная сборка тихо меняла бы вид
    # полей (то же время суток вернулось бы в другом написании), и доска расходилась бы сама с собой.
    $entries = [System.Collections.Generic.List[object]]::new()
    foreach ($line in $Lines) {
        $record = $null
        try { $record = $line | ConvertFrom-Json } catch { continue }
        if (-not $record.id) { continue }
        $entries.Add([pscustomobject]@{ Line = $line; Record = $record })
    }
    return @($entries)
}

function Get-BoardClosings {
    param($Entries)
    # Закрытие бывает двух видов, и путать их нельзя.
    #
    # Адресная находка закрывается ОБЩЕ: её учёл тот единственный, кому она адресована, — вопрос
    # исчерпан. Широковещательная (`-To *`) адресована многим, и закрытие у неё ПЕРСОНАЛЬНОЕ: строка
    # несёт ключ закрывшего потока и гасит запись только для него. Общее закрытие такой находки
    # прятало её от всех разом: первый же учтивший лишал её остальных, а поток, у которого с момента
    # добавления не было ни хода, ни перезапуска, не видел её никогда.
    #
    # Закрытие — отдельная строка ниже по файлу, поэтому сперва собираем всё, потом отсеиваем.
    $global = [System.Collections.Generic.HashSet[string]]::new()
    $by = @{}
    foreach ($entry in $Entries) {
        if (-not $entry.Record.done) { continue }
        $id = [string]$entry.Record.id
        $who = Get-StreamKey -Raw ([string]$entry.Record.by)
        if (-not $who) { [void]$global.Add($id); continue }
        if (-not $by.ContainsKey($id)) { $by[$id] = [System.Collections.Generic.List[string]]::new() }
        if ($who -notin $by[$id]) { $by[$id].Add($who) }
    }
    return [pscustomobject]@{ Global = $global; By = $by }
}

function Get-BroadcastLifetimeDays {
    # Срок давности записи «всем». Волна живёт недели: находка, не учтённая за две недели, устарела
    # вместе с волной. Платят же за неё контекстом ВСЕ вкладки проекта — включая деревья, заведённые
    # позже и к той волне отношения не имеющие. Это предохранитель на случай, что общее закрытие
    # забыли: у адресной записи выход есть всегда (её закрытие общее), у широковещательной — нет.
    return 14
}

function Get-BroadcastAgeState {
    param($Record)
    # Возраст записи «всем»: `live` — живая, `stale` — просрочена, `broken` — дату не разобрать.
    #
    # Срок давности касается записей «всем» и уведомлений «учтено»: у обычной адресной находки
    # закрытие общее, и заглушать её молча нельзя — её просто ещё не учли. Уведомление же гасится
    # само при показе, и срок ему нужен на случай, когда автор к своей вкладке уже не вернулся:
    # иначе оно лежало бы на доске вечно.
    # Оба широковещательных адреса, а не только одиночная звёздочка: у записи «всем вкладкам
    # проекта» дыра была бы шире — она приходит КАЖДОМУ новому рабочему дереву, включая заведённые
    # под другие волны, и без срока давности жила бы вечно.
    if ((Get-StreamKey -Raw ([string]$Record.to)) -notin @('*', '**') -and [string]$Record.kind -ne 'ack') {
        return 'live'
    }
    $raw = $Record.at
    if ($raw -is [datetime]) {
        return $(if ($raw -lt (Get-Date).AddDays(-(Get-BroadcastLifetimeDays))) { 'stale' } else { 'live' })
    }
    $text = [string]$raw
    $when = [datetime]::MinValue
    # Дату не разобрать (строку правили руками, принесла другая версия, поле пустое или числовое) —
    # считаем запись просроченной, а не бессрочной. Прежняя мягкость возвращала в узком случае ровно
    # ту дыру, ради которой срок давности и заводился: такая запись доставлялась каждому новому
    # дереву и переживала уплотнение. Сторона безопасная: находка с испорченной датой к учёту всё
    # равно не годится, а показ о ней скажет отдельно — молча она не пропадёт.
    if (-not $text -or -not [datetime]::TryParse($text, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return 'broken'
    }
    return $(if ($when -lt (Get-Date).AddDays(-(Get-BroadcastLifetimeDays))) { 'stale' } else { 'live' })
}

function Get-BoardStates {
    param($Entries, [string[]]$Viewer)
    # Раскладывает записи по состояниям: открыта, закрыта у смотрящего, просрочена, с испорченной
    # датой. Показ обязан их различать — иначе непонятно, почему запись лежит в файле, но нигде не
    # видна, и человек идёт уплотнять доску или заводить дубль находки.
    $closings = Get-BoardClosings -Entries $Entries
    $mine = @($Viewer | Where-Object { $_ })
    $open = [System.Collections.Generic.List[object]]::new()
    $closedForViewer = [System.Collections.Generic.List[object]]::new()
    $stale = [System.Collections.Generic.List[object]]::new()
    $broken = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in $Entries) {
        $record = $entry.Record
        if ($record.done -or -not $record.title) { continue }
        $id = [string]$record.id
        # Общее закрытие снимает запись у всех — и адресную, и ту, что закрыли ключом -ForAll.
        if ($closings.Global.Contains($id)) { continue }
        # Без switch намеренно: его `continue` переходит к следующему условию switch, а не к
        # следующей записи цикла, и запись попала бы разом в два состояния.
        $age = Get-BroadcastAgeState -Record $record
        if ($age -eq 'stale') { $stale.Add($entry); continue }
        if ($age -eq 'broken') { $broken.Add($entry); continue }
        $seen = $closings.By[$id]
        if ($mine.Count -gt 0 -and $seen -and @($seen | Where-Object { $_ -in $mine }).Count -gt 0) {
            $closedForViewer.Add($entry)
            continue
        }
        $open.Add($entry)
    }
    return [pscustomobject]@{
        Open            = @($open)
        ClosedForViewer = @($closedForViewer)
        Stale           = @($stale)
        Broken          = @($broken)
        Closings        = $closings
    }
}

function Select-OpenEntries {
    param($Entries, [string[]]$Viewer)
    # Без `-Viewer` — всё, что открыто хоть для кого-то (показ доски целиком, уплотнение). С ним —
    # то, что открыто ИМЕННО ДЛЯ ЭТОГО потока: свои персональные закрытия он больше не видит.
    return @((Get-BoardStates -Entries $Entries -Viewer $Viewer).Open)
}

function Select-KeepEntries {
    param($Entries)
    # Что переживает уплотнение: записи, открытые хоть для кого-то, и ИМЕННЫЕ закрытия этих записей
    # (без них поток, уже учтивший находку «всем», получит её заново). Всё остальное — закрытые
    # общей строкой, просроченные, их закрытия и нечитаемые обрывки — уходит.
    $liveIds = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($entry in (Get-BoardStates -Entries $Entries).Open) {
        [void]$liveIds.Add([string]$entry.Record.id)
    }
    return @($Entries | Where-Object {
            $id = [string]$_.Record.id
            if ($_.Record.done) { $_.Record.by -and $liveIds.Contains($id) } else { $liveIds.Contains($id) }
        })
}

function Get-OpenRecords {
    param([string]$Path, [string[]]$Viewer)
    $entries = Get-BoardEntries -Lines (Read-BoardLines -Path $Path)
    return @(Select-OpenEntries -Entries $entries -Viewer $Viewer | ForEach-Object { $_.Record })
}

function Compress-Board {
    param([string]$Path)
    # Уплотнение: на доске остаются только открытые записи. Закрытая запись иначе лежит строкой
    # вечно, а разбирают доску целиком на каждом ходу каждой вкладки.
    #
    # Работа опасная — она ПЕРЕПИСЫВАЕТ доску целиком, поэтому каждый шаг сомневается: не сумели
    # прочитать — не трогаем, прочитали непустой файл и не разобрали ни одной записи — не трогаем.
    # Ошибка здесь стоит всех открытых находок сразу, а выглядит как бодрый рапорт об успехе.
    if (-not (Test-Path $Path)) { return [pscustomobject]@{ Before = 0; After = 0; Unreadable = 0 } }
    $reason = 'причина неизвестна'
    for ($try = 1; $try -le 10; $try++) {
        $content = Read-BoardContent -Path $Path
        if (-not $content.Ok) {
            throw "доску не удалось прочитать, уплотнять вслепую нельзя ($Path). Последняя причина: $($content.Reason)"
        }
        $entries = Get-BoardEntries -Lines $content.Lines
        if ($content.Length -gt 0 -and $entries.Count -eq 0) {
            throw "в доске $($content.Length) байт, а разобрать не удалось ни одной записи ($Path) — уплотнение стёрло бы её содержимое; разберитесь с файлом руками"
        }
        # Отбор один на уплотнение и на показ: иначе показ обещал бы убрать не то, что уберётся.
        $keep = @(Select-KeepEntries -Entries $entries | ForEach-Object { $_.Line })
        # Нечитаемые строки уплотнение выбрасывает молча — а это обрывок чьей-то находки. Считаем и
        # называем их в отчёте: стёртое молча неотличимо от того, чего не было.
        $unreadable = $content.Lines.Count - $entries.Count
        # Временный файл рядом с доской: замена в пределах тома идёт одним действием, и оборванного
        # полуфайла на месте доски не остаётся, чем бы работа ни кончилась.
        $temp = "$Path.compact-$PID-$(Get-Random).tmp"
        $text = if ($keep.Count -gt 0) { ($keep -join "`n") + "`n" } else { '' }
        [System.IO.File]::WriteAllText($temp, $text, [System.Text.UTF8Encoding]::new($false))
        try {
            # Размер сверяем с тем, что был у прочитанного файла: сосед мог дописать строку, пока мы
            # переписывали, — тогда заходим заново, иначе его строка пропала бы.
            if ((Get-Item $Path).Length -ne $content.Length) { throw 'доску дописали, пока мы её переписывали' }
            [System.IO.File]::Move($temp, $Path, $true)
            return [pscustomobject]@{ Before = $content.Lines.Count; After = $keep.Count; Unreadable = $unreadable }
        } catch {
            # Причина той же выделки, что и везде: человеку — по-русски, чужой текст — с пометкой.
            $reason = Get-FailureReason -Failure $_
            Remove-Item $temp -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 50
        }
    }
    throw "уплотнить доску не удалось ($Path). Последняя причина: $reason"
}

function Get-AliveBeaconPath {
    param([string]$TreePath)
    # Маячок живой вкладки. Его обновляет сторож доставки в СВОЁМ рабочем дереве на каждом ходу, а
    # читают отсюда все остальные. Адрес объявлен один раз: разъедься писатель с читателем — живые
    # вкладки стали бы невидимыми, и выглядело бы это как «все потоки закрыты».
    return (Join-Path $TreePath '.claude/.cache/wave-board-alive.txt')
}

function Test-AliveBeacon {
    param([string]$TreePath)
    # Порог намеренно щедрый: маячок обновляется на ходу ПОЛЬЗОВАТЕЛЯ, а вкладка может молча
    # работать часами — длинный прогон субагентов, ожидание сборки, ночная пауза в разговоре.
    # Ошибка в сторону «жива» безопасна: находка ляжет на доску и дождётся вкладки. Ошибка в другую
    # сторону уводит находку в «Хвосты волны» мимо живого соседа — то есть мимо всего механизма.
    #
    # ‼️ Порог берём ОБЩИЙ, а не своё число рядом. Комментарий у общего порога прямо обещает, что он
    # один для маячка дерева и для отметки в заявке; пока число стояло здесь вторым списком, любая
    # правка одного из них молча разводила два ответа на один вопрос — одно и то же дерево считалось
    # бы живым в показе и брошенным в подсказке.
    $aliveHours = Get-AliveHours
    $beacon = Get-AliveBeaconPath -TreePath $TreePath
    try {
        if (-not (Test-Path $beacon)) { return $false }
        return ((Get-Date) - (Get-Item $beacon).LastWriteTime).TotalHours -lt $aliveHours
    } catch {
        return $false
    }
}

function Get-Worktrees {
    # Рабочие деревья репозитория: путь, ветка и признак ЖИВОЙ вкладки (свежий маячок).
    #
    # Живость — не существование дерева: закрытая вкладка дерево за собой оставляет, и по нему
    # находке некуда прийти. Но и отсутствие маячка не значит «закрыта»: вкладка могла стартовать
    # до появления сторожа. Поэтому состояний три — «жива» (свежий маячок), «неизвестно» (дерево
    # есть, маячка нет) и «закрыта» (дерева нет вовсе), и говорить о них надо честно.
    #
    # Блокировка рабочего дерева как признак живости отвергнута: среда ставит её нерегулярно и
    # снимает — 21.08.2026 на 23 деревьях проекта не было НИ ОДНОЙ, включая дерево работавшей в тот
    # момент вкладки. Разбор — в реестре решений (platform-and-build.md).
    # Спрашиваем git ОДИН раз на запуск. Список нужен многим (имена потока, сверка адресата,
    # подсказки), внутри одного короткого запуска он не меняется, а без памятки набегал бы десяток
    # запусков git на каждый ход вкладки.
    if ($null -ne $script:WaveBoardWorktrees) { return $script:WaveBoardWorktrees }
    $lines = @()
    try {
        $lines = @(& git worktree list --porcelain 2>$null)
        if ($LASTEXITCODE -ne 0) { $script:WaveBoardWorktrees = @(); return @() }
    } catch {
        $script:WaveBoardWorktrees = @()
        return @()
    }
    $trees = [System.Collections.Generic.List[hashtable]]::new()
    $current = $null
    foreach ($line in $lines) {
        if ($line -like 'worktree *') {
            $current = @{
                path   = ($line.Substring(9) -replace '\\', '/').TrimEnd('/')
                branch = ''
                live   = $false
            }
            $trees.Add($current)
            continue
        }
        if (-not $current) { continue }
        if ($line -like 'branch *') { $current.branch = $line.Substring(7) -replace '^refs/heads/', '' }
    }
    foreach ($tree in $trees) { $tree.live = Test-AliveBeacon -TreePath $tree.path }
    $script:WaveBoardWorktrees = @($trees | ForEach-Object { [pscustomobject]$_ })
    return $script:WaveBoardWorktrees
}

function Get-KnownStreamKeys {
    param([switch]$AliveOnly)
    # Ключи заведённых деревьев — с ними сверяется адресат находки. Берём обе формы имени (ветку и
    # папку): вкладка знает себя по любой из них. `-AliveOnly` оставляет те, где вкладка недавно
    # отметилась, — это годится для подсказки, но НЕ для сверки адресата: положить находку впрок,
    # вкладке, которую поднимут через час, — обычное дело.
    $keys = [System.Collections.Generic.List[string]]::new()
    foreach ($tree in (Get-Worktrees)) {
        if ($AliveOnly -and -not $tree.live) { continue }
        if ($tree.branch) { $keys.Add((Get-StreamKey -Raw $tree.branch)) }
        $keys.Add((Get-StreamKey -Raw $tree.path))
    }
    return @($keys | Where-Object { $_ } | Select-Object -Unique)
}

function Select-ForStream {
    param($Records, [string[]]$Keys, $Claim)
    # Что из доски адресовано этой вкладке. Четыре вида адреса, и путать их нельзя:
    #   `**`          — всем вкладкам проекта, кроме положившей;
    #   `*`           — всем вкладкам СВОЕЙ волны (когда волна известна обеим сторонам), кроме
    #                   положившей: иначе запись уходит в два десятка деревьев, половина которых
    #                   заведена под другие волны и платит за неё контекстом ни за что;
    #   `волна/поток` — по заявке: это основной вид адреса, потому что так поток назван в плане;
    #   имя ветки или папки — запасной путь для потоков, которые не объявлялись.
    $mine = @($Keys)
    $myWave = if ($Claim) { Get-WaveKey -Raw ([string]$Claim.wave) } else { '' }
    $myStream = if ($Claim) { Get-StreamNumberKey -Raw ([string]$Claim.stream) } else { '' }
    return @($Records | Where-Object {
            $raw = [string]$_.to
            $to = Get-StreamKey -Raw $raw
            $fromMe = (Get-StreamKey -Raw ([string]$_.from)) -in $mine
            $address = Get-StreamAddress -Raw $raw
            if ($to -eq '**') {
                -not $fromMe
            } elseif ($to -eq '*') {
                $recordWave = Get-WaveKey -Raw ([string]$_.wave)
                # Волна не названа хоть у одной стороны — ведём себя как прежде и доставляем: молча
                # не доставить хуже, чем доставить лишнее.
                (-not $fromMe) -and (-not $recordWave -or -not $myWave -or $recordWave -eq $myWave)
            } elseif ($address) {
                [bool]$myWave -and $address.Wave -eq $myWave -and $address.Stream -eq $myStream
            } else {
                $to -in $mine
            }
        })
}

function Test-TaskInList {
    param([string]$Tasks, [string]$Task)
    # Входит ли задача в перечень потока. В плане их пишут как придётся: «10-13», «10, 11, 12»,
    # «6, 7 и 9», «1b». Отвечает на вопрос «чей это кусок», когда вкладку тянет взять соседнюю
    # задачу, а владелец за экраном не знает, что её планировали другому потоку.
    if (-not $Tasks -or -not $Task) { return $false }
    $wanted = Get-StreamNumberKey -Raw $Task
    if (-not $wanted) { return $false }
    foreach ($chunk in ($Tasks -split '[^\p{L}\p{Nd}\-–]+')) {
        $piece = $chunk.Trim()
        if (-not $piece) { continue }
        $range = [regex]::Match($piece, '^(\d+)\s*[-–]\s*(\d+)$')
        if ($range.Success -and $wanted -match '^\d+$') {
            $from = [int]$range.Groups[1].Value
            $till = [int]$range.Groups[2].Value
            if ([int]$wanted -ge [math]::Min($from, $till) -and [int]$wanted -le [math]::Max($from, $till)) {
                return $true
            }
            continue
        }
        if ((Get-StreamNumberKey -Raw $piece) -eq $wanted) { return $true }
    }
    return $false
}

function Format-BoardRecord {
    param($Record)
    $tail = if ($Record.where) { " — $($Record.where)" } else { '' }
    return "  • «$($Record.title)»$tail [метка $($Record.id)]"
}

# ─────────────────────────────────────────────────────────────────────────────────────────────
# Реестр заявок потоков: кто ведёт поток прямо сейчас.
#
# Доска отвечает на вопрос «что передали», реестр — на вопрос «кому и жив ли он». Без реестра
# адрес находки выводится из имени ветки или папки, а имена врут: в плане волны 6 у двух потоков
# объявлена одна ветка, а вкладки работают на других, и одну папку вкладка успела переиспользовать
# под другую задачу. Такой адрес доставляется молча и не тому.
#
# Устройство намеренно другое, чем у доски: файл на рабочее дерево, и пишет его ТОЛЬКО своя
# вкладка. Один писатель на файл — ни повторов, ни разбора всего журнала ради одного поля. Доска
# остаётся многописательным журналом, реестр — набором маленьких файлов рядом.
#
# ‼️ Замок всё же нужен, но не файлу заявки, а ВЫБОРУ НОМЕРА потока: номер выбирается по снимку
# всего реестра, и одновременно объявившиеся вкладки читают один и тот же снимок. Стережёт это
# `Enter-RegistryLock` — см. разбор сценария там же.
# ─────────────────────────────────────────────────────────────────────────────────────────────

function Get-AliveHours {
    # Порог свежести отметки — общий для маячка дерева и для заявки потока: разъедься они, одно и
    # то же дерево считалось бы живым в одном месте и молчащим в другом.
    return 12
}

function Get-SilentDaysBeforeStuck {
    # Столько молчит поток, прежде чем адресованная ему находка попадёт в сводку застрявшего.
    # Сутки — это не «вкладка закрылась», а «пора посмотреть глазами»: ночная пауза в разговоре
    # сюда уже не попадает, а брошенный поток попадает.
    return 1
}

function Get-RegistryDir {
    param([string]$BoardOverride)
    # Рядом с доской, в том же общем каталоге: те же три свойства (виден всем деревьям, вне веток,
    # переживает удаление дерева). Тесты подставляют свою доску — реестр уезжает за ней сам.
    #
    # ‼️ Отсекаем имя файла СТРОКОЙ, а не разбором пути через оболочку: разбор пути спрашивает у
    # оболочки про диск и на несуществующем диске срывается насмерть. Инструмент падал прямо здесь,
    # на первой же строке, и выносил наружу сырое английское системное сообщение — до честного
    # отказа про каталог заявок дело не доходило вовсе.
    $board = Get-BoardPath -Override $BoardOverride
    $parent = [System.IO.Path]::GetDirectoryName($board)
    if (-not $parent) { $parent = '.' }
    return [System.IO.Path]::Combine($parent, 'streams')
}

function Get-RepoMarkerState {
    param([string]$StartDir)
    # Есть ли здесь репозиторий ВООБЩЕ — вопрос к диску, а не к git. Нужен он затем, что git молчит
    # одинаково в двух противоположных случаях: репозитория тут нет вовсе (и тогда дерева нет, а
    # текущая папка и есть личность вкладки — расходиться нечему) и git сам не в себе при живом
    # репозитории (и тогда откат на текущую папку МЕНЯЕТ личность вкладки). Решения из этих двух
    # ответов следуют разные, поэтому и различаем.
    #
    # ‼️ Исходов ТРИ, а не два: `found` — маркер найден; `none` — его тут точно нет; `unknown` —
    # выяснить не удалось. Прежде третий выдавался за второй: разбор пути на недостижимом пути
    # (отвалился диск, пропала сетевая шара) срывался, ловушка молча отвечала «репозитория нет», а
    # вызывающий читал это как разрешение работать текущей папкой — то есть личность вкладки
    # менялась молча ровно там, где строгий режим это и запрещает. «Не видно» и «нет» смешивать
    # нельзя нигде в этом комплекте: на этой разнице стоят все отказы вслух.
    #
    # Спрашиваем систему тем же способом, что и всё остальное в комплекте (`Get-PathState`): он
    # отличает «этого нет» от «не смог посмотреть», а проверка существования путём оболочки —
    # нет. Путь режем строкой: разбор пути через оболочку на несуществующем диске срывается сам.
    #
    # В рабочем дереве `.git` — файл, а не каталог, поэтому вид не проверяем.
    if (-not $StartDir) { $StartDir = $PWD.Path }
    try {
        $dir = $StartDir
        while ($dir) {
            $state = Get-PathState -Path ([System.IO.Path]::Combine($dir, '.git'))
            if ($state.Kind -eq 'container' -or $state.Kind -eq 'leaf') {
                return [pscustomobject]@{ Kind = 'found'; Reason = '' }
            }
            if ($state.Kind -eq 'unknown') {
                return [pscustomobject]@{ Kind = 'unknown'; Reason = $state.Reason }
            }
            $parent = [System.IO.Path]::GetDirectoryName($dir)
            if (-not $parent -or $parent -eq $dir) { break }
            $dir = $parent
        }
    } catch {
        return [pscustomobject]@{ Kind = 'unknown'; Reason = (Get-FailureReason -Failure $_) }
    }
    # Дошли до самого верха и маркера не нашли. Это «нет» только там, где путь ДОСТИЖИМ: на мёртвом
    # диске и на пропавшей шаре каждый шаг вверх отвечает тем же «ничего нет», и молчаливый вывод
    # «репозитория тут не бывало» был бы выдумкой.
    if (-not (Test-PathReachable -Path ([System.IO.Path]::Combine($StartDir, '.git')))) {
        return [pscustomobject]@{
            Kind   = 'unknown'
            Reason = "путь недостижим целиком ($StartDir): выше него нет ни одного существующего каталога"
        }
    }
    return [pscustomobject]@{ Kind = 'none'; Reason = '' }
}

# Корень рабочего дерева, спрошенный ОДИН раз за запуск. Три поля вместо одного: неудачу нельзя
# запоминать удачей, иначе строгий читатель получил бы молча подставленную текущую папку — ровно то,
# от чего заводилась вся правка.
$script:WaveBoardTreeRootAsked = $false
$script:WaveBoardTreeRoot = ''
$script:WaveBoardTreeRootReason = ''

function Get-TreeRoot {
    param([switch]$Strict)
    # ‼️ Кто эта вкладка. Отсюда выводится ВСЁ её опознание: имя файла заявки, маячок живости, ключи
    # потока и сверка «это моя заявка». Поэтому ответ обязан быть один и тот же, из какой бы папки
    # дерева вкладку ни запустили. Прежде каждое из этих мест брало текущую папку — и вкладка,
    # стартовавшая в подкаталоге своего дерева, объявлялась под одним ключом, а сдавалась под
    # другим: сдача не находила своей заявки и отвечала «сдавать нечего» КОДОМ УСПЕХА. Вкладка
    # закрывалась, а соседи продолжали адресовать находки живому, как им кажется, потоку.
    #
    # Спрашиваем git один раз на запуск, как и про список деревьев: ответ в пределах запуска не
    # меняется, а спрашивают его многие — сторож доставки зовётся на каждом ходу пользователя.
    #
    # `-Strict` — тем, у кого от ключа зависит СУДЬБА потока (объявление и сдача): там неудача git
    # печатается вслух. Терпимым читателям (сторож доставки, показ) откат на текущую папку
    # безвреден: там пропуск стоит одной невидимой строки, а смена личности стоит потока.
    if (-not $script:WaveBoardTreeRootAsked) {
        $script:WaveBoardTreeRootAsked = $true
        try {
            $top = @(& git rev-parse --show-toplevel 2>$null)
            if ($LASTEXITCODE -eq 0 -and $top.Count -gt 0 -and $top[0]) {
                $script:WaveBoardTreeRoot = ("$($top[0])".Trim() -replace '\\', '/').TrimEnd('/')
            } else {
                $script:WaveBoardTreeRootReason = "git не назвал корня рабочего дерева (код $LASTEXITCODE)"
            }
        } catch {
            $script:WaveBoardTreeRootReason = Get-FailureReason -Failure $_
        }
    }
    if ($script:WaveBoardTreeRoot) { return $script:WaveBoardTreeRoot }
    $here = ($PWD.Path -replace '\\', '/').TrimEnd('/')
    # ‼️ Репозитория здесь нет вовсе — значит нет и дерева, а текущая папка и есть единственная
    # личность этой вкладки: разойтись объявлению и сдаче не с чем. Отказывать в этом случае нельзя
    # ни строгому, ни терпимому — иначе комплект перестал бы работать всюду, где доску подставляют
    # явно, а на месте репозитория обычная папка.
    #
    # ‼️ А вот «выяснить не удалось» — отказ строгому наравне с «репозиторий есть». Иначе выходило
    # бы худшее: на недостижимом пути вкладка молча меняла себе личность, и удар приходился в самое
    # дорогое место — сдача переставала находить свою заявку и выходила УСПЕХОМ. Терпимым читателям
    # (сторож доставки, показ) откат безвреден: там пропуск стоит одной невидимой строки.
    if ($Strict) {
        $marker = Get-RepoMarkerState
        if ($marker.Kind -eq 'found') {
            throw "корень рабочего дерева не вычислить: $($script:WaveBoardTreeRootReason). Репозиторий здесь есть, значит вкладка опознала бы себя текущей папкой ($here) — а под этим ключом её заявку не найдут ни сдача, ни соседи, и поток тихо потеряется. Повторите, когда git снова отвечает."
        }
        if ($marker.Kind -eq 'unknown') {
            throw "корень рабочего дерева не вычислить: $($script:WaveBoardTreeRootReason). Есть ли тут репозиторий, выяснить тоже не удалось ($($marker.Reason)) — а «не видно» это не «нет»: приняв одно за другое, вкладка опознала бы себя текущей папкой ($here) молча, и её заявку не нашли бы ни сдача, ни соседи. Повторите, когда путь снова читается."
        }
    }
    return $here
}

function Get-FolderKey {
    param([string]$Path)
    # ‼️ Рабочая папка в ОДНОМ виде, и приём этот ОДИН на весь комплект: слэши вперёд, без
    # хвостового, без разницы в регистре букв. Сравнивают записанную в заявке папку с ключом вкладки
    # пятеро — соперники за номер, наследование прежних имён ветки, вторая дорога к своей заявке,
    # метка «это вы» и отсев своей же заявки при раздаче имён, — и каждый писал приведение своими
    # руками. Пока приведения совпадали буква в букву, это сходилось; но источник ключа сменился с
    # оболочки на git, а пять одинаковых с виду выражений — это пять мест, где они могут разойтись.
    # Разойдись хоть регистром буквы диска, и вкладка сочла бы соперником собственную заявку и
    # потеряла бы память прежних имён ветки.
    #
    # ‼️ Ключ этот только для СРАВНЕНИЯ. В заявку рабочая папка пишется как есть — приведённый к
    # нижнему регистру путь человеку показывать нельзя, он ищет его глазами в списке.
    #
    # Регистр гасим явно, а не полагаемся на то, что оболочка сравнивает строки без учёта регистра
    # сама: тем же ключом пользуются упорядочение (оно сравнивает строки ПОБАЙТОВО) и наборы, а там
    # уговор оболочки не действует. Пути в этом комплекте windows-овские, где регистр не значит
    # ничего; на системах, где значит, две папки-близнеца с разным регистром считались бы одной —
    # осознанная плата за то, чтобы вкладка узнавала себя в своей же заявке.
    return (([string]$Path) -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
}

function Get-PathKey {
    param([string]$TreePath)
    # Имя файла заявки. Читаемая часть — имя папки дерева, хвост — отпечаток полного пути: две
    # папки с одинаковым именем в разных местах диска не должны затирать заявки друг друга.
    $normalized = Get-FolderKey -Path $TreePath
    $leaf = ($normalized.Split('/')[-1] -replace '[^\p{L}\p{Nd}]+', '-').Trim('-')
    if (-not $leaf) { $leaf = 'tree' }
    $sha = [System.Security.Cryptography.SHA1]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalized))
    } finally {
        $sha.Dispose()
    }
    $tail = -join ($bytes[0..3] | ForEach-Object { $_.ToString('x2') })
    return "$leaf-$tail"
}

function Get-ClaimPath {
    param([string]$Dir, [string]$TreePath)
    # ‼️ Складываем строкой. Сложение путей средствами оболочки спрашивает у неё про диск и на
    # несуществующем диске срывается насмерть — приём находки падал ровно здесь и выносил наружу
    # сырое английское системное сообщение вместо нашего отказа (воспроизведено).
    return [System.IO.Path]::Combine($Dir, (Get-PathKey -TreePath $TreePath) + '.json')
}

function Get-WaveKey {
    param([string]$Raw)
    # Ключ волны. Волну зовут и «wave6», и «6», и именем файла плана — сводим к одному виду, иначе
    # заявка, поданная одним написанием, не найдётся по другому.
    if (-not $Raw) { return '' }
    $text = $Raw.Trim().ToLowerInvariant()
    # Имя файла плана: берём из него маркер волны, если он там есть.
    $matched = [regex]::Match($text, 'wave\s*(\d+)')
    if ($matched.Success) { return "wave$($matched.Groups[1].Value)" }
    if ($text -match '^\d+$') { return "wave$text" }
    return ($text -replace '[^\p{L}\p{Nd}]+', '-').Trim('-')
}

function Get-StreamNumberKey {
    param([string]$Raw)
    # Ключ номера потока. В плане его пишут и «3», и «П3», и «п3», и «3b» — все они об одном.
    if (-not $Raw) { return '' }
    $text = $Raw.Trim().ToLowerInvariant() -replace '^[пp#№]\s*', ''
    return ($text -replace '[^\p{L}\p{Nd}]+', '')
}

# Имена волн, которые РЕАЛЬНО объявлены в реестре заявок. Нужны разбору адреса: волна зовётся не
# только «wave6» — там, где волн нет вовсе, её подставляет сама заявка, и зовут её датой или словом.
# Список приносит тот, кто уже прочитал реестр (инструмент, сторож доставки), — сам разбор в реестр
# не ходит: его зовут по разу на каждую запись доски, и чтение папки на каждый вызов было бы платой
# ни за что. Список пуст — разбор ведёт себя как прежде и понимает волну из плана и волну-дату.
$script:WaveBoardKnownWaves = @()

function Set-KnownWaves {
    param([string[]]$Keys)
    $script:WaveBoardKnownWaves = @($Keys | Where-Object { $_ } | Select-Object -Unique)
}

function Get-KnownWaves {
    return @($script:WaveBoardKnownWaves)
}

function Set-KnownWavesFromRegistry {
    param([string]$Dir)
    # Удобная форма для тех, у кого реестр под рукой. Молчит при любой неудаче: не разобранный
    # список волн — это адрес, который не разберётся, а не поломка механизма у всех остальных.
    try {
        Set-KnownWaves -Keys @((Get-Claims -Dir $Dir) | ForEach-Object { $_.WaveKey })
    } catch {
        Set-KnownWaves -Keys @()
    }
}

function Get-StreamAddress {
    param([string]$Raw)
    # Разбор адреса «волна/поток»: `wave6/3`, `6/3`, `wave6/П3`, `2026-08-24/2`, `sprint-alpha/1`.
    # Возвращает $null, если это не он.
    #
    # Спутать адрес с именем ветки (`feat/wave6-compute-channel`) нельзя, и держится эта защита на
    # ПРАВОЙ стороне: там обязан стоять номер потока, а не слово. Ни одно имя ветки под это не
    # подходит, поэтому левую сторону можно было расширить, не ослабив сверку.
    #
    # Слева волну зовут тремя способами, и все три обязаны разбираться:
    #   `wave6`, `6`     — волна из плана;
    #   `2026-08-24`     — волна, подставленная по дате там, где волн нет вовсе (заявок на неё может
    #                      ещё не быть: находку кладут и потоку, который откроют завтра);
    #   любое другое имя — только если такая волна РЕАЛЬНО объявлена в реестре (`Set-KnownWaves`).
    # Без этого адрес волны-даты не разбирался бы вовсе и находка соседу просто не уходила бы.
    #
    # Имя волны сверяется ЦЕЛИКОМ, а не приведённым ключом: иначе `wave6-compute/3` (имя папки, а не
    # адрес) сводилось бы к волне `wave6` и разбиралось бы как чужой поток.
    if (-not $Raw) { return $null }
    $parts = @($Raw.Trim() -split '/')
    if ($parts.Count -ne 2) { return $null }
    $left = $parts[0].Trim()
    $right = $parts[1].Trim()
    if ($right -notmatch '^[пp#№]?\s*\d+[a-zа-яё]?$') { return $null }
    $wave = Get-WaveKey -Raw $left
    $isPlanWave = $left -match '^(wave\s*)?\d+$'
    $isDateWave = $left -match '^\d{4}-\d{2}-\d{2}$'
    $isKnownWave = $wave -and $wave -eq $left.ToLowerInvariant() -and $wave -in (Get-KnownWaves)
    if (-not ($isPlanWave -or $isDateWave -or $isKnownWave)) { return $null }
    return [pscustomobject]@{
        Wave   = $wave
        Stream = Get-StreamNumberKey -Raw $right
    }
}

function Get-DateWaveKey {
    # Имя волны, подставленной самой: сегодняшняя дата. Вид `ГГГГ-ММ-ДД` выбран за три свойства —
    # он читается человеком, сортируется как дата и разбирается адресом (`2026-08-24/2`).
    return (Get-Date).ToString('yyyy-MM-dd')
}

function Get-SeenTime {
    param($Claim)
    # Отметка заявки датой. Неразобранную считаем самой старой: по ней нельзя решать, что свежее.
    $raw = $Claim.seen_at
    if ($raw -is [datetime]) { return $raw }
    $when = [datetime]::MinValue
    if ([datetime]::TryParse([string]$raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return $when
    }
    return [datetime]::MinValue
}

function Get-AutoWaveKey {
    param($Claims)
    # К какой волне присоединиться вкладке, которая волну не назвала: к той, где ПРЯМО СЕЙЧАС идёт
    # работа. Иначе каждая вкладка заводила бы свою волну по дате, и соседи не видели бы друг друга.
    #
    # ‼️ Только волны, подставленные САМИ. У названной волны (`wave6`) номера потоков идут из плана:
    # присоединившись к ней, вкладка заняла бы чужой номер, и половина находок ушла бы не туда.
    $live = @($Claims | Where-Object { $_.Record.wave_auto -and $_.State -eq 'ведёт' })
    if ($live.Count -eq 0) { return '' }
    # Рядом может идти не одна работа — берём ту, где отметились последней.
    $freshest = @($live | Sort-Object -Property @{ Expression = { Get-SeenTime -Claim $_.Record } } -Descending)[0]
    return $freshest.WaveKey
}

function Get-NextStreamNumber {
    param($Claims, [string]$WaveKey, [string]$TreePath)
    # Следующий свободный номер потока в волне. Номер нужен всегда — им поток назван в адресе, — но
    # там, где плана нет, брать его человеку неоткуда: в плане он и не объявлялся.
    $here = Get-FolderKey -Path $TreePath
    $inWave = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })
    # Своя прежняя НЕЗАКРЫТАЯ заявка на эту волну — не сосед: повторное объявление той же вкладки
    # обязано остаться тем же потоком, иначе адрес, который она уже назвала соседям, менялся бы сам
    # собой.
    #
    # ‼️ Сданная своя в счёт не идёт. Пока она шла, папка ОТДАВАЛА свой прежний номер следующему
    # жильцу сама собой, без единого ключа: поток честно сдали, из той же папки объявился другой — и
    # получал тот же адрес, а вместе с ним и память имён сданного, и его почту. Инструмент при этом
    # обещал при сдаче, что находки сданному больше не примут. Решение говорит прямо: после честной
    # сдачи это обычное новое объявление и СВОБОДНЫЙ номер.
    #
    # ‼️ И перенесённая своя в счёт не идёт — по той же причине, по какой не идёт сданная: адрес у
    # неё забрала другая папка, и продолжать поток по нему эта вкладка больше не вправе. Состояние
    # спрашиваем ЕДИНЫМ признаком закрытости, а не полем файла: перенос живёт только в разобранном
    # реестре, и чтение поля напрямую его не увидит.
    $mine = @($inWave | Where-Object {
            (Get-FolderKey -Path $_.Record.worktree) -eq $here -and $_.StreamKey -and
            -not $_.Closed
        })
    if ($mine.Count -gt 0) { return $mine[0].StreamKey }
    return (Get-FreeStreamNumber -Claims $Claims -WaveKey $WaveKey -TreePath $TreePath)
}

function Get-FreeStreamNumber {
    param($Claims, [string]$WaveKey, [string]$TreePath)
    # Следующий свободный номер в волне, СВОЮ заявку не считая. Отдельно от предыдущей функции
    # потому, что при споре за номер своя заявка уже лежит в реестре — и, посчитай мы её, вкладка
    # «уступала» бы номер сама себе и осталась бы на том же месте.
    #
    # Считаем от наибольшего занятого, а не от количества заявок: сданный поток номер не
    # освобождает (находки к нему адресованы этим номером), да и номера в плане идут не подряд.
    $here = Get-FolderKey -Path $TreePath
    $used = 0
    foreach ($claim in @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })) {
        # ‼️ Не считаем только СВОЮ НЕЗАКРЫТУЮ. Своя СДАННАЯ номер по-прежнему занимает: он ушёл в
        # адрес, которым потоку слали находки, а её файл в реестре может остаться (заявку прежней
        # версии из подкаталога объявление пишет на месте, а не поверх каноничного имени). Не
        # посчитай мы её — в одной волне оказались бы два разных потока с одним адресом, и сторож
        # инвариантов назвал бы это выдачей номера второй раз.
        #
        # ‼️ И своя ПЕРЕНЕСЁННАЯ номер занимает: адрес у неё забрала другая папка, и там он сейчас
        # живой. Не посчитай мы его — свободным вышел бы номер, который прямо сейчас ведёт сосед.
        if ((Get-FolderKey -Path $claim.Record.worktree) -eq $here -and
            -not $claim.Closed) { continue }
        $digits = [regex]::Match([string]$claim.StreamKey, '^\d+')
        if (-not $digits.Success) { continue }
        $number = [int]$digits.Value
        if ($number -gt $used) { $used = $number }
    }
    return [string]($used + 1)
}

function Get-DistinctStreamNumber {
    param($Claims, [string]$WaveKey, [string]$StreamKey)
    # Различитель к занятому номеру: `2` → `2k`. Нужен безобидному выходу из отказа «адрес занят» —
    # тому самому, который печатается ПЕРВЫМ. Случай живой: в реестре прямо сейчас открыты `wave9/2`
    # и `wave9/2k`, и различитель там выдумали руками через полдня после столкновения.
    #
    # ‼️ Вид обязан РАЗБИРАТЬСЯ адресом (цифры и одна буква), иначе совет привёл бы вкладку к
    # потоку, которому находку основным способом не пошлют. Поэтому букву вешаем на цифры номера, а
    # не на него целиком: у `3b` вышло бы `3bk`, а такой адрес не разбирается вовсе.
    $digits = [regex]::Match([string]$StreamKey, '^\d+')
    if (-not $digits.Success) { return '' }
    $busy = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey } | ForEach-Object { $_.StreamKey })
    foreach ($letter in @('k', 'm', 'n', 'p', 'r', 's', 't')) {
        $candidate = "$($digits.Value)$letter"
        if ($candidate -notin $busy) { return $candidate }
    }
    return ''
}

function Test-ClaimHasPlan {
    param($Claim)
    # Есть ли у потока план волны. От ответа зависит, куда инструмент посылает человека с находкой и
    # с итогом работы: в раздел плана или в ответ владельцу. Совет вписать строку в файл, которого
    # нет, — тупик: вкладка не может ни выполнить его, ни понять, что делать вместо него.
    #
    # Признак ровно один: подставлена ли волна САМА. Волну назвали или взяли из имени плана — план
    # есть, даже если файл плана в заявке не записан: команда объявления называет его не всегда.
    # Заявка старого вида признака не несёт — у неё волна названная, значит план есть.
    #
    # ‼️ Существование файла плана НЕ проверяем. Заявку читают из чужого рабочего дерева, а план
    # лежит в дереве потока: его отсутствие «здесь» не говорит о нём ничего, и прежняя проверка
    # объявляла бы бесплановыми ровно те потоки, чей план лежит в соседней папке.
    if (-not $Claim) { return $false }
    if (Test-ClaimHasField -Claim $Claim -Name 'wave_auto') { return -not $Claim.wave_auto }
    # Заявка прежней версии признака не несёт вовсе, и «нет признака — значит план есть» врало ей
    # ровно там, где она заводилась ВНЕ волны: такому потоку советовали вписать строку в раздел
    # плана, которого у него нет. Поэтому без признака судим по имени волны: волна плана зовётся
    # `waveN`, а дату или слово вкладка завела себе сама.
    return ((Get-WaveKey -Raw ([string]$Claim.wave)) -match '^wave\d+$')
}

function Test-ClaimHasField {
    param($Claim, [string]$Name)
    # Есть ли у заявки поле ВООБЩЕ. «Поля нет» и «поле опущено» приходится различать: заявка,
    # заведённая прежней версией, признака не несёт, и судить её надо по другому правилу.
    if (-not $Claim) { return $false }
    if ($Claim -is [System.Collections.IDictionary]) { return $Claim.Contains($Name) }
    return [bool]$Claim.PSObject.Properties[$Name]
}

function Get-ClaimOrder {
    param($Claim)
    # Ключ порядка среди заявок: время объявления, при равенстве — путь рабочего дерева. Оба поля
    # заявки не меняются никогда, поэтому порядок один и тот же у всех вкладок и не пляшет от
    # прогона к прогону. На нём стоят два решения: кто остаётся с номером потока при споре за него
    # и чья заявка завела волну.
    #
    # Время не разобрать — считаем заявку самой поздней: неизвестное не должно вытеснять известное.
    $when = [datetime]::MaxValue
    $raw = $Claim.claimed_at
    if ($raw -is [datetime]) {
        $when = $raw
    } else {
        $parsed = [datetime]::MinValue
        if ([string]$raw -and [datetime]::TryParse([string]$raw, [cultureinfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
            $when = $parsed
        }
    }
    return [pscustomobject]@{
        When = $when
        Path = Get-FolderKey -Path $Claim.worktree
    }
}

function Compare-ClaimOrder {
    param($Left, $Right)
    # Тот же ПОЛНЫЙ ключ порядка, но в виде сравнения: он нужен там, где записи перебирают вручную,
    # а не сортируют выдачей. ‼️ Ключ обязан быть полным (время объявления, при равенстве — путь
    # дерева): по одному времени две заявки, поданные в одну секунду, встали бы в разном порядке у
    # разных вкладок — и разные вкладки погасили бы переносом разные записи.
    $a = Get-ClaimOrder -Claim $Left
    $b = Get-ClaimOrder -Claim $Right
    if ($a.When -lt $b.When) { return -1 }
    if ($a.When -gt $b.When) { return 1 }
    return [string]::CompareOrdinal($a.Path, $b.Path)
}

function Get-NumberRivals {
    param($Claims, [string]$WaveKey, [string]$StreamKey, [string]$TreePath)
    # Заявки ДРУГИХ деревьев на тот же адрес — ту же волну и тот же номер потока. Закрытые не в
    # счёт: у сданной вкладки, которая вела поток, больше нет, а у перенесённой адрес забран, и
    # спорить обеим не о чем. Состояние спрашиваем ЕДИНЫМ признаком: перенос виден только в
    # разобранном реестре.
    $here = Get-FolderKey -Path $TreePath
    return @($Claims | Where-Object {
            $_.WaveKey -eq $WaveKey -and $_.StreamKey -eq $StreamKey -and -not $_.Closed -and
            (Get-FolderKey -Path $_.Record.worktree) -ne $here
        })
}

function Test-YieldsStreamNumber {
    param($Mine, $Rivals)
    # Кто из объявившихся одним номером уступает его. Остаётся с номером тот, кто объявился
    # раньше; при равном времени — тот, чей путь дерева меньше по алфавиту. Порядок полный и
    # неизменный, поэтому две вкладки не сдвигаются одновременно и не меняются номерами без конца.
    $me = Get-ClaimOrder -Claim $Mine
    foreach ($rival in $Rivals) {
        $other = Get-ClaimOrder -Claim $rival.Record
        if ($other.When -lt $me.When) { return $true }
        if ($other.When -eq $me.When -and [string]::CompareOrdinal($other.Path, $me.Path) -lt 0) {
            return $true
        }
    }
    return $false
}

function Test-WaveIsAuto {
    param($Claims, [string]$WaveKey)
    # Подставлена ли волна сама — то есть плана у неё нет.
    #
    # Решает ПЕРВАЯ заявка волны, а не любая из них: волну заводит тот, кто объявился раньше всех,
    # остальные к ней присоединяются. Прежнее «есть хоть одна подставленная» делало бесплановой
    # целую волну плана, стоило одной вкладке объявиться в ней без волны, — и тексты про разделы
    # плана пропадали у всех её потоков разом. Порядок берём тот же, что и при споре за номер
    # потока (время объявления, при равенстве — путь дерева): он не меняется от прогона к прогону,
    # и ответ у всех вкладок один.
    if (-not $WaveKey) { return $false }
    $inWave = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })
    if ($inWave.Count -gt 0) {
        $eldest = @($inWave | Sort-Object -Property @(
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).When } }
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).Path } }
            ))[0]
        return -not (Test-ClaimHasPlan -Claim $eldest.Record)
    }
    # Заявок такой волны нет вовсе — известно только имя. Имя-дата бывает только у подставленной.
    return [bool]($WaveKey -match '^\d{4}-\d{2}-\d{2}$')
}

function Test-AddresseeHasPlan {
    param($Claims, [string]$Raw, $Address, $Mine)
    # Есть ли план волны у ПОЛУЧАТЕЛЯ находки. Решение одно на все советы «положите в Хвосты волны»:
    # разъедься они по веткам — половина отказов посылала бы в раздел плана, которого нет.
    #
    # Порядок: заявка адресата (она знает про его план точно) → волна из адреса → своя волна. Не
    # знаем о получателе ничего — ведём себя как прежде и считаем, что план есть: посоветовать
    # раздел плана там, где он есть, безобиднее, чем промолчать о нём там, где он нужен.
    $found = @(Find-Claims -Claims $Claims -Raw $Raw)
    if ($found.Count -gt 0) {
        return (@($found | Where-Object { Test-ClaimHasPlan -Claim $_.Record }).Count -gt 0)
    }
    $waveKey = if ($Address) {
        [string]$Address.Wave
    } elseif ($Mine) {
        Get-WaveKey -Raw ([string]$Mine.wave)
    } else {
        ''
    }
    if (-not $waveKey) { return $true }
    return -not (Test-WaveIsAuto -Claims $Claims -WaveKey $waveKey)
}

function Get-ClaimReadTimeoutMs {
    param([switch]$Strict)
    # Сколько терпим, пока файл чужой заявки отпустят.
    #
    # Держат его на доли секунды не только соседние вкладки — ещё антивирус, служба поиска Windows,
    # резервное копирование и папка облачной синхронизации. Ровно так же и по той же причине читает
    # доску находок `Read-BoardContent`, и там повторы стоят с самого начала.
    #
    # Строгому читателю (выбор номера потока) отпущено на порядок больше: пропущенная заявка стоит
    # ему НАВСЕГДА совпавшего адреса, и лучше подождать две секунды. Терпимому (сторож доставки,
    # показ) столько ждать нельзя: он зовётся на каждом ходу вкладки и обязан быть быстрым.
    return $(if ($Strict) { 2500 } else { 150 })
}

function Test-MissingPathFailure {
    param($Failure)
    # Отличает «этого нет» от «не смог посмотреть». Первое — законный ответ, второе — беда, о
    # которой строгий читатель обязан сказать вслух.
    #
    # ‼️ Спрашивать об этом ИСКЛЮЧЕНИЕМ, а не отдельной проверкой существования пути. Проверка
    # существования отвечает «нет» и там, где на самом деле «не видно»: каталог, в который закрыт
    # заход, несуществующий диск, отвалившаяся сетевая шара. Такой ответ неотличим от честного
    # «заявок ещё нет» — и именно на нём стояли все найденные дыры этого класса.
    $inner = $Failure.Exception
    while ($inner.InnerException) { $inner = $inner.InnerException }
    return ($inner -is [System.IO.FileNotFoundException]) -or
        ($inner -is [System.IO.DirectoryNotFoundException])
}

function Read-ClaimRecord {
    param([string]$Path, [switch]$Strict)
    # Заявка соседа с ЧЕСТНЫМ различением исходов. Четыре состояния, и путать их нельзя:
    #   ok         — прочитали и разобрали;
    #   missing    — файла нет вовсе (дерево не объявлялось, заявку убрали);
    #   unreadable — файл ЕСТЬ, но прочитать не удалось;
    #   broken     — прочитали, но разобрать не смогли.
    #
    # ‼️ Прежде «не прочитали» и «файла нет» отвечали одинаково — пустотой, и с одной попытки.
    # Из-за этого сосед, чей файл в тот миг держал антивирус, становился НЕСУЩЕСТВУЮЩИМ: вторая
    # вкладка брала его номер, бодро рапортовала и не предупреждала ни словом, а круг разрешения
    # спора читал тот же испорченный снимок и соперника тоже не видел. Совпадение адресов
    # оставалось навсегда. Воспроизведено: файл заявки забрали на полторы секунды — и два потока
    # получили один номер.
    #
    # ‼️ «Файла нет» узнаём ПО ОТКАЗУ ОТКРЫТЬ, а не отдельной проверкой существования: та отвечает
    # «нет» и там, где просто не видно (см. `Test-MissingPathFailure`).
    $deadline = (Get-Date).AddMilliseconds((Get-ClaimReadTimeoutMs -Strict:$Strict))
    $reason = 'причина неизвестна'
    while ($true) {
        try {
            # Делимся файлом на чтение и запись: сосед в этот момент может обновлять свою отметку.
            $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
            $text = ''
            try {
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
                $text = $reader.ReadToEnd()
            } finally {
                $stream.Dispose()
            }
            $record = $null
            try {
                $record = $text | ConvertFrom-Json
            } catch {
                # Разбор не удался — это НЕ повод повторять: испорченный файл испорчен насовсем.
                return [pscustomobject]@{
                    State = 'broken'; Record = $null; Reason = (Get-FailureReason -Failure $_)
                }
            }
            if (-not $record.worktree) {
                return [pscustomobject]@{
                    State = 'broken'; Record = $null; Reason = 'в заявке не названо рабочее дерево'
                }
            }
            return [pscustomobject]@{ State = 'ok'; Record = $record; Reason = '' }
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                return [pscustomobject]@{ State = 'missing'; Record = $null; Reason = '' }
            }
            # Настоящую причину сохраняем: «файл занят» и «нет прав» лечат по-разному.
            $reason = Get-FailureReason -Failure $_
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 30
    }
    return [pscustomobject]@{ State = 'unreadable'; Record = $null; Reason = $reason }
}

function Read-ClaimFile {
    param([string]$Path)
    # Терпимый вид для тех, кому важнее не сорваться, чем узнать правду: своя заявка, отметка «на
    # ходу», сдача. Всем, кто по реестру ВЫБИРАЕТ НОМЕР, — `Read-ClaimRecord` со строгостью.
    return (Read-ClaimRecord -Path $Path).Record
}

function Get-ClaimState {
    param($Claim)
    # Четыре состояния вместо трёх — и все честные:
    #   ведёт      — заявка открыта, отметка свежая;
    #   молчит     — заявка открыта, отметка старая (вкладка могла закрыться без сдачи, а могла
    #                молча работать часами: прогон субагентов, ожидание сборки, пауза в разговоре);
    #   сдан       — поток сдан по правилам, вкладки нет;
    #   нет заявки — поток не объявлялся вовсе; это определяет не эта функция, а тот, кто ищет.
    #
    # ‼️ Пятое состояние — «перенесён» — здесь не считается и посчитано быть не может: оно живёт не
    # в файле заявки, а в реестре КАК ЦЕЛОМ (адрес забрала другая папка, и сказано об этом в ЕЁ
    # файле). Ставит его второй проход загрузчика — `Set-ClaimSupersessions`. Отсюда правило для
    # всего комплекта: состояние спрашивают у разобранной записи реестра (признак `Closed`), а не у
    # поля `state` своего файла, иначе перенос виден не будет.
    if (-not $Claim) { return 'нет заявки' }
    if ([string]$Claim.state -eq 'released') { return 'сдан' }
    $seen = [datetime]::MinValue
    $raw = [string]$Claim.seen_at
    if (-not $raw -or -not [datetime]::TryParse($raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$seen)) {
        return 'молчит'
    }
    if (((Get-Date) - $seen).TotalHours -lt (Get-AliveHours)) { return 'ведёт' }
    return 'молчит'
}

function Get-ClaimCurrentNames {
    param($Claim)
    # Имена, которые поток носит СЕЙЧАС. Их три источника: записанные в заявке при объявлении
    # (ветка и рабочая папка), нынешняя ветка его рабочего дерева и — если это наша вкладка — то,
    # что видно прямо сейчас, на случай, когда git молчит вовсе.
    if (-not $Claim) { return @(Get-CurrentKeys) }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($raw in @([string]$Claim.branch, [string]$Claim.worktree)) {
        $names.Add((Get-StreamKey -Raw $raw))
    }
    $here = Get-FolderKey -Path $Claim.worktree
    foreach ($tree in (Get-Worktrees)) {
        if ((Get-FolderKey -Path $tree.path) -ne $here) { continue }
        if ($tree.branch) { $names.Add((Get-StreamKey -Raw $tree.branch)) }
        $names.Add((Get-StreamKey -Raw $tree.path))
    }
    # Своя ли это заявка — сверяем с КОРНЕМ дерева: заявка записывает его, а не ту папку, из которой
    # вкладку случилось запустить.
    if ($here -eq (Get-FolderKey -Path (Get-TreeRoot))) {
        foreach ($key in (Get-CurrentKeys)) { $names.Add($key) }
    }
    return @($names | Where-Object { $_ } | Select-Object -Unique)
}

function Get-ClaimRememberedNames {
    param($Claim)
    # Имена, которые поток лишь ПОМНИТ: ветку переименовали, а вкладка объявилась заново, и
    # объявление переписало заявку целиком. Без памяти находка, УЖЕ ПРИНЯТАЯ по старому имени, не
    # дошла бы и сдачу не задержала — исчезла бы молча.
    #
    # ‼️ Помнить — не то же самое, что носить: права у этих имён разные, см. `Resolve-ClaimNames`.
    if (-not $Claim) { return @() }
    $current = @(Get-ClaimCurrentNames -Claim $Claim)
    $names = foreach ($raw in @($Claim.former_branches)) {
        $key = Get-StreamKey -Raw ([string]$raw)
        if ($key -and $key -notin $current) { $key }
    }
    return @($names | Select-Object -Unique)
}

function Get-ClaimTakenFrom {
    param($Claim)
    # Поле преемства: рабочая папка, У КОТОРОЙ эта заявка забрала адрес. Пусто — переноса не было.
    #
    # ‼️ Переезд записывается в СВОЮ заявку, а не правкой чужого файла. На этом стоит несущий
    # инвариант «один писатель на файл»: у чужой заявки писатель уже есть — сторож доставки той
    # папки, — он правит документ целиком на каждом ходу вкладки и замка не берёт. Пометка в чужом
    # файле стиралась бы его ближайшей отметкой живости уже ПОСЛЕ того, как нам отрапортовали об
    # успехе. Этим же куплена совместимость: чужой файл не тронут, поэтому старая копия комплекта в
    # двух десятках живых рабочих деревьев видит ровно то, что видела вчера.
    #
    # ‼️ Имя поля `taken_from` — точка сговора с набором проверок: там оно объявлено константой, и
    # инварианты реестра ищут перенос по нему же. Разойдутся — сторож перестанет видеть перенос и
    # замолчит ровно там, где его завели.
    #
    # Отсутствие поля читается как «переноса не было», а не как порча: заявки прежней версии его не
    # несут вовсе, а разновозрастные копии комплекта — норма, а не исключение.
    if (-not $Claim) { return '' }
    return (Get-FolderKey -Path ([string]$Claim.taken_from))
}

function Get-ClaimMoment {
    param($Raw)
    # Время из поля заявки: дата — как есть (чтение JSON превращает строку в дату), строка —
    # разбором, всё прочее — ПУСТОТА. Пустота честно значит «не знаем», и решение, которое на ней
    # стоит, обязано само выбирать безопасную сторону, а не получать выдуманное значение.
    if ($Raw -is [datetime]) { return $Raw }
    $parsed = [datetime]::MinValue
    if ([string]$Raw -and [datetime]::TryParse([string]$Raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Get-ClaimTakenAt {
    param($Claim)
    # Момент, когда эта заявка ЗАБРАЛА адрес, — пишется рядом с полем преемства и наследуется
    # вместе с ним. Им и только им решается, действует ли ребро переноса.
    #
    # Поля нет вовсе — так выглядит заявка невыпущенной промежуточной версии, писавшая одну лишь
    # папку. Тогда моментом ребра служит момент ОБЪЯВЛЕНИЯ этой же заявки (разбор переносов
    # подставляет его сам), а нет и его — ребро действует безусловно: незнание не должно
    # воскрешать призрака.
    if (-not $Claim) { return $null }
    return (Get-ClaimMoment -Raw $Claim.taken_at)
}

function Get-ClaimTakeovers {
    param($Claim)
    # ВСЕ переезды этой записи: нынешний (поля `taken_from` и `taken_at`) и каждый прошлый из
    # списка `past_takeovers`. Каждый несёт СВОЙ адрес — тот, у которого забирали, — а не нынешний
    # адрес заявки: у прошлого переезда он другой.
    #
    # ‼️ Зачем список заведён. Память о переезде лежит в заявке ЗАБРАВШЕЙ папки, а заявка на папку
    # ОДНА: как только та же папка бралась за следующий поток, её файл переписывался, ребро
    # исчезало — и брошенная запись прежней папки снова становилась ведущей. Молча: показ не кричал,
    # приём рапортовал «дойдёт сама», сторож доставки нёс находку брошенной вкладке. Тем же убивало
    # и цепочку A→B→C, стоило переобъявиться СРЕДНЕЙ папке. Перенос — событие в истории АДРЕСА, и
    # переиспользование папки его не отменяет, ровно как не отменяет сдача.
    #
    # ‼️ Имена `past_takeovers` и полей внутри его записей — точка сговора с набором проверок: там
    # они объявлены константами, и инварианты реестра ищут перенос по ним же. Разойдутся — сторож
    # перестанет видеть перенос и замолчит ровно там, где его завели.
    #
    # Дубли ОДНОГО переезда (тот же адрес у той же папки) схлопываем, оставляя позднейший по
    # времени: ранний момент гасит меньше, чем нужно, — заявка потерпевшей, поданная между двумя
    # переездами, ушла бы из-под ребра. Неизвестный момент считаем самым поздним: ребро без момента
    # действует безусловно. А вот переезды одного адреса у РАЗНЫХ папок не схлопываются никогда:
    # это разные рёбра, и потеря любого воскрешает свою потерпевшую.
    if (-not $Claim) { return @() }
    $all = [System.Collections.Generic.List[object]]::new()
    $now = Get-ClaimTakenFrom -Claim $Claim
    if ($now) {
        $all.Add([pscustomobject]@{
                Wave   = (Get-WaveKey -Raw ([string]$Claim.wave))
                Stream = (Get-StreamNumberKey -Raw ([string]$Claim.stream))
                From   = $now
                At     = (Get-ClaimTakenAt -Claim $Claim)
            })
    }
    foreach ($past in @($Claim.past_takeovers)) {
        if (-not $past) { continue }
        $all.Add([pscustomobject]@{
                Wave   = (Get-WaveKey -Raw ([string]$past.wave))
                Stream = (Get-StreamNumberKey -Raw ([string]$past.stream))
                From   = (Get-FolderKey -Path ([string]$past.taken_from))
                At     = (Get-ClaimMoment -Raw $past.taken_at)
            })
    }
    $found = [ordered]@{}
    foreach ($move in $all) {
        # Безадресный переезд — это не переезд: гасить по нему нечего, а два безадресных соседа
        # сошлись бы «адресом» из двух пустот. Так же их пропускает и набор проверок.
        if (-not $move.Wave -or -not $move.Stream -or -not $move.From) { continue }
        $key = "$($move.Wave)/$($move.Stream)>$($move.From)"
        $known = $found[$key]
        if ($null -ne $known) {
            $a = if ($null -eq $known.At) { [datetime]::MaxValue } else { $known.At }
            $b = if ($null -eq $move.At) { [datetime]::MaxValue } else { $move.At }
            if ($a -ge $b) { continue }
        }
        $found[$key] = $move
    }
    return @($found.Values)
}

function Get-PastTakeoverLimit {
    # Сколько прошлых переездов заявка помнит. Двадцать — с большим запасом: за всю живую историю
    # реестра переезд случался единицами, а список едет из заявки в заявку и растёт только там, где
    # одну папку раз за разом берут под новые потоки. Предел нужен не ради места, а ради того, чтобы
    # файл заявки нельзя было раздуть бесконечно; лишнее отбрасывается с самого старого.
    return 20
}

function Get-ClaimPastTakeovers {
    param($Previous, [string]$Wave, [string]$Stream, [string]$From, [ref]$Dropped)
    # Список ПРОШЛЫХ переездов для новой заявки этой папки: всё, что помнила прежняя заявка, плюс
    # её собственный переезд. Так память о переезде переживает переиспользование папки: заявка на
    # папку одна, и без этого списка следующий поток стирал бы ребро — брошенная запись прежней
    # папки снова становилась бы ведущей, причём молча.
    #
    # `-Wave`, `-Stream` и `-From` называют переезд, который новая заявка несёт НЫНЕШНИМ: его в
    # список не кладём, иначе он лежал бы дважды. Ничего страшного в дубле нет (разбор их
    # схлопывает), но файл заявки читает человек, и повтор в нём — лишний вопрос.
    $keep = [System.Collections.Generic.List[object]]::new()
    # ‼️ Папку в ключе сверки приводим к одному виду. Заявка хранит её так, как её записал автор
    # (обратные слэши, свой регистр букв), а разбор переездов — приведённой; сравнивай мы их как
    # есть, нынешний переезд не узнавался бы в списке и ложился бы туда ВТОРЫМ.
    $here = Get-FolderKey -Path $From
    $skip = if ($Wave -and $Stream -and $here) { "$Wave/$Stream>$here" } else { '' }
    foreach ($move in (Get-ClaimTakeovers -Claim $Previous)) {
        if ($skip -and "$($move.Wave)/$($move.Stream)>$($move.From)" -eq $skip) { continue }
        $keep.Add($move)
    }
    if ($keep.Count -eq 0) { return @() }
    # Порядок — по моменту переезда, снизу вверх; неизвестный момент считаем самым поздним, как и
    # везде в разборе переносов. Отбрасываем сверху списка, то есть самые старые: ребро без момента
    # действует безусловно, и терять его первым было бы худшим из выборов.
    $sorted = @($keep | Sort-Object -Property @{ Expression = {
                if ($null -eq $_.At) { [datetime]::MaxValue } else { $_.At }
            }
        }, @{ Expression = { "$($_.Wave)/$($_.Stream)>$($_.From)" } })
    $limit = Get-PastTakeoverLimit
    if ($sorted.Count -gt $limit) {
        # ‼️ Отброшенное называем ВСЛУХ — тем же ключом `-Dropped`, каким его печатает объявление.
        # Молчаливых потерь в этом механизме быть не должно: с каждым отброшенным ребром брошенная
        # запись прежней папки снова становится ведущей, а показ, приём и сторож доставки об этом
        # не скажут ни слова. Сцена практически недостижима (нужен двадцать первый перехват одной
        # папкой), но недостижимость — не повод молчать.
        if ($Dropped) { $Dropped.Value = @($sorted[0..($sorted.Count - $limit - 1)]) }
        $sorted = @($sorted[($sorted.Count - $limit)..($sorted.Count - 1)])
    }
    return @($sorted | ForEach-Object {
            $entry = [ordered]@{
                wave       = $_.Wave
                stream     = $_.Stream
                taken_from = $_.From
            }
            # Момента может не быть вовсе: так выглядит переезд, унаследованный от заявки прежней
            # версии. Пустое поле читалось бы разными копиями по-разному — не пишем его вовсе.
            if ($null -ne $_.At) { $entry.taken_at = $_.At.ToString('s') }
            $entry
        })
}

function Compare-ClaimTakeover {
    param($LeftAt, [string]$LeftPath, $RightAt, [string]$RightPath)
    # Кто забрал адрес ПОЗЖЕ. Нужно там, где на одну запись ведёт сразу несколько действующих
    # рёбер: ведущим называем последнего забравшего — адрес сейчас у него. Момент неизвестен —
    # считаем самым поздним, иначе заявка прежней версии молча уступала бы имя нынешней.
    #
    # ‼️ Момент берётся у САМОГО ПЕРЕЕЗДА, а не у нынешних полей заявки: прошлый переезд той же
    # записи случился в другое время, и сравнивать его нынешним значило бы мерить не то.
    $a = if ($null -eq $LeftAt) { [datetime]::MaxValue } else { $LeftAt }
    $b = if ($null -eq $RightAt) { [datetime]::MaxValue } else { $RightAt }
    if ($a -lt $b) { return -1 }
    if ($a -gt $b) { return 1 }
    return [string]::CompareOrdinal($LeftPath, $RightPath)
}

function Set-ClaimSupersessions {
    param($Entries)
    # ‼️ ВТОРОЙ ПРОХОД загрузчика реестра: гасим записи, чей адрес забрала другая папка. Без него
    # перенос был бы только пометкой в одном файле, а адрес по-прежнему вели бы две записи — и кому
    # придёт находка, решал бы порядок описи каталога.
    #
    # Ребро ведёт от заявки, ЗАБРАВШЕЙ адрес, к заявке, у которой его забрали: незакрытая заявка
    # называет чужую рабочую папку, и адрес у обеих один. Погашенная теряет вместе с состоянием и
    # свои имена, и своё право отзываться — раздача имён идёт следом и считает её закрытой.
    #
    # ‼️ Ребро действует по ВРЕМЕНИ, а не по топологии: оно не действует ровно тогда, когда
    # ДОКАЗАНО, что заявка потерпевшей папки началась ПОЗЖЕ момента переноса. Прежнее правило
    # «перенос от уже перенесённой не действует» было костылём против циклов и стоило двух дыр
    # сразу: круг A↔B (механизм сам печатает проигравшей команду возврата, и обе записи начинали
    # ссылаться друг на друга) не давал нулевого счётчика ожидания ни одной — не гасился НИКТО, и
    # адрес снова вели двое; а цепочка переездов A→B→C воскрешала первую запись.
    #
    # Правило времени закрывает и третий, скрытый случай: адрес честно сдали, и ПРЕЖНЯЯ папка
    # объявилась на нём заново — её свежую заявку старое ребро погасило бы молча, кодом успеха.
    #
    # ‼️ Момента переноса нет — берём момент ОБЪЯВЛЕНИЯ забравшей записи. Прежде такое ребро
    # действовало безусловно, и это запирало адрес за потерпевшей навсегда: сколько бы раз она ни
    # объявлялась заново, ребро без момента гасило и каждую свежую её заявку. Выхода из положения
    # не было вовсе — напечатанный ключ переноса отвечал «не понадобился», потому что вести адрес
    # к тому времени было уже некому. Потерь от подстановки нет: поле преемства БЕЗ момента могла
    # написать только невыпущенная промежуточная версия (старые копии не пишут ни того, ни
    # другого), а она писала оба поля в один и тот же миг объявления.
    #
    # Момента нет и у объявления забравшей — тогда, как прежде, безусловно: незнание не должно
    # воскрешать призрака. Неизвестно время объявления потерпевшей — то же самое: гасим, пока не
    # доказано обратное.
    $records = @($Entries)
    $count = $records.Count
    if ($count -lt 2) { return $records }
    # ‼️ Списки собираем ОБЫЧНЫМ циклом, а не конвейером: конвейер разворачивает всё перечислимое,
    # и пустой список из него не выходит вовсе — набор рёбер получался пустым, а инструмент падал
    # на первом же обращении к нему.
    $edges = [System.Collections.Generic.List[object]]::new()
    $drawn = @{}
    # Переезды каждой записи разбираем ОДИН раз: их читают и рёбра, и разбор цепочки ниже.
    # ‼️ Раскладываем ОБЫЧНЫМ циклом по заранее размеченному месту, а не конвейером: конвейер
    # разворачивает вложенные списки, и переезды всех записей слиплись бы в один.
    $moves = [object[]]::new($count)
    for ($i = 0; $i -lt $count; $i++) {
        $when = Get-ClaimMoment -Raw $records[$i].Record.claimed_at
        $mine = [System.Collections.Generic.List[object]]::new()
        foreach ($move in (Get-ClaimTakeovers -Claim $records[$i].Record)) {
            $mine.Add([pscustomobject]@{
                    Wave   = $move.Wave
                    Stream = $move.Stream
                    From   = $move.From
                    # Момент, которым это ребро и живёт: свой, а нет своего — момент объявления
                    # забравшей записи (см. пояснение выше).
                    At     = if ($null -eq $move.At) { $when } else { $move.At }
                })
        }
        $moves[$i] = @($mine)
    }
    for ($i = 0; $i -lt $count; $i++) {
        # ‼️ СДАННАЯ заявка перенос всё равно держит. Соблазн пропустить её велик («вкладки нет,
        # значит и забирать некому»), но это ровно то самое дорогое следствие дефекта: поток
        # переехал, честно доработал и сдался — а брошенная запись в прежней папке снова стала бы
        # ведущей и опять держала бы адрес живым. Находку на такой адрес приняли бы с бодрым
        # рапортом об успехе, отправитель успокоился бы и запасного пункта не завёл, а достаться
        # она не могла бы никому. Перенос — событие в истории АДРЕСА, и сдача его не отменяет.
        #
        # ‼️ И по той же причине рёбра строятся по КАЖДОМУ переезду записи — по нынешнему и по
        # каждому прошлому. Адрес каждого ребра берётся у САМОГО ПЕРЕЕЗДА: у прошлого он другой,
        # а нынешний адрес заявки к нему отношения не имеет.
        foreach ($move in $moves[$i]) {
            for ($j = 0; $j -lt $count; $j++) {
                if ($i -eq $j) { continue }
                $loser = $records[$j]
                if ((Get-FolderKey -Path $loser.Record.worktree) -ne $move.From) { continue }
                # Адрес обязан совпасть целиком: поле называет папку, а не поток, и в той же папке
                # мог с тех пор объявиться СЛЕДУЮЩИЙ поток — гасить его мы не вправе.
                if ($loser.WaveKey -ne $move.Wave -or $loser.StreamKey -ne $move.Stream) { continue }
                $started = Get-ClaimMoment -Raw $loser.Record.claimed_at
                if ($null -ne $move.At -and $null -ne $started -and $started -gt $move.At) { continue }
                $edges.Add([pscustomobject]@{ Taker = $i; Loser = $j; At = $move.At })
                $drawn["$i>$j"] = $true
            }
        }
    }
    $takenBy = @{}
    foreach ($edge in $edges) {
        if ($drawn.ContainsKey("$($edge.Loser)>$($edge.Taker)")) {
            # ‼️ Рёбра ВЗАИМНЫ: обе заявки забрали адрес друг у друга, и ни про одну не доказано,
            # что она началась позже чужого переноса, — так выглядит круг возврата, уложившийся в
            # одну секунду. Развести их обязан ПОЛНЫЙ ключ порядка (время объявления, при равенстве
            # путь дерева): по одному времени две заявки одной секунды встали бы в разном порядке у
            # разных вкладок, и разные вкладки погасили бы разные записи. Остаётся ребро СТАРШЕЙ
            # записи — тем же правилом, каким разрешается спор за номер потока.
            if ((Compare-ClaimOrder -Left $records[$edge.Taker].Record `
                        -Right $records[$edge.Loser].Record) -gt 0) {
                continue
            }
        }
        $known = $takenBy[$edge.Loser]
        # Забравших может оказаться несколько: тогда ведущим называем последнего — адрес у него.
        if ($null -ne $known -and (Compare-ClaimTakeover -LeftAt $known.At `
                    -LeftPath (Get-FolderKey -Path $records[$known.Taker].Record.worktree) `
                    -RightAt $edge.At `
                    -RightPath (Get-FolderKey -Path $records[$edge.Taker].Record.worktree)) -ge 0) {
            continue
        }
        $takenBy[$edge.Loser] = $edge
    }
    foreach ($loser in @($takenBy.Keys)) {
        $entry = $records[$loser]
        $entry.Closed = $true
        # Сданную не переименовываем: она закрыта и без переноса, а человеку важнее знать, что
        # поток честно сдали, чем то, что его адрес потом забрали.
        if ($entry.State -ne 'сдан') {
            $entry.State = 'перенесён'
            # ‼️ Признак «адрес забрали» — ОДИН на весь комплект, как и признак закрытости. Прежде
            # три места (сдача, сторож доставки, показ) сравнивали состояние со словом «перенесён»
            # напрямую, и это же слово печаталось человеку: перепиши его кто-нибудь в показе — и
            # сдача с доставкой замолчали бы, ничем себя не выдав.
            $entry.Superseded = $true
        }
        $entry.TakenBy = $records[$takenBy[$loser].Taker]
        $entry.TakenAt = $takenBy[$loser].At
    }
    # ‼️ Кто ведёт адрес СЕЙЧАС — отдельный вопрос от того, кто забрал его у этой записи. В цепочке
    # переездов запись гасит средняя папка, а ведёт последняя; средняя же могла с тех пор взяться за
    # другой поток. Сказать потерпевшей «адрес забрала средняя папка, она ведёт уже другое» было бы
    # правдой ровно наполовину: адрес-то ведут, просто в третьем месте. Поэтому ведущего ищем по
    # адресу, а не по ребру.
    $leaders = @{}
    foreach ($entry in $records) {
        if ($entry.Closed -or -not $entry.WaveKey -or -not $entry.StreamKey) { continue }
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        # Две ведущих на адрес — это наследие дефекта, и выбирать из них за человека нельзя:
        # молчим, а кричит о задвоении показ.
        $leaders[$address] = if ($leaders.ContainsKey($address)) { $null } else { $entry }
    }
    # ‼️ Куда адрес уехал В КОНЦЕ КОНЦОВ — отдельный вопрос и от того, кто забрал его у этой
    # записи, и от того, кто ведёт его сейчас. Ведущей записи может не быть вовсе (поток переехал и
    # там закончился), и тогда человеку называют последнюю папку цепочки. Прежде цепочка обрывалась
    # на ПЕРВОМ же звене, чья запись сменила адрес: средняя папка цепочки A→B→C, взявшаяся за
    # следующий поток, гасится не была — и ответ «адрес забрала папка B» посылал человека туда, где
    # про этот адрес нет ничего. Идти дальше позволяет память переездов: она помнит, что адрес ушёл
    # из B, даже когда сама заявка B уже про другой поток.
    #
    # Указатель строится по КАЖДОМУ переезду реестра, а не по действующим рёбрам: ребро говорит,
    # погашена ли запись, а цепочка — куда ушёл АДРЕС, и второе остаётся верным даже там, где
    # первого нет.
    $passedTo = @{}
    for ($i = 0; $i -lt $count; $i++) {
        foreach ($move in $moves[$i]) {
            $key = "$($move.Wave)/$($move.Stream)>$($move.From)"
            $known = $passedTo[$key]
            # Забиравших из одной папки несколько — берём последнего: адрес у него.
            if ($null -ne $known -and (Compare-ClaimTakeover -LeftAt $known.At `
                        -LeftPath (Get-FolderKey -Path $records[$known.Index].Record.worktree) `
                        -RightAt $move.At `
                        -RightPath (Get-FolderKey -Path $records[$i].Record.worktree)) -ge 0) {
                continue
            }
            $passedTo[$key] = [pscustomobject]@{ Index = $i; At = $move.At }
        }
    }
    foreach ($loser in @($takenBy.Keys)) {
        $entry = $records[$loser]
        $entry.AddressLedBy = $leaders["$($entry.WaveKey)/$($entry.StreamKey)"]
        # Цепочку идём до записи, у которой этот адрес больше не забирали. Круг разрывается
        # оговоркой про уже виденные записи: реестр правку переживёт и грязным.
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        $seen = @{ "$($entry.File)" = $true }
        $at = $records[$takenBy[$loser].Taker]
        while ($null -ne $at -and -not $seen.ContainsKey([string]$at.File)) {
            $seen[[string]$at.File] = $true
            $next = $passedTo["$address>$(Get-FolderKey -Path $at.Record.worktree)"]
            if ($null -eq $next -or $seen.ContainsKey([string]$records[$next.Index].File)) { break }
            $at = $records[$next.Index]
        }
        $entry.AddressChainEnd = $at
    }
    return $records
}

function Resolve-ClaimNames {
    param($Entries)
    # ‼️ Кто на какое имя отзывается — решается ПО ВСЕМУ РЕЕСТРУ СРАЗУ, а не отдельно по каждой
    # заявке. Иначе одно имя законно указывает на два потока, а закрытие находки с именным адресом
    # ОБЩЕЕ, не персональное: кто закрыл первым — погасил её у всех.
    #
    # Что вышло, когда память имён появилась без этого правила (воспроизведено на настоящем
    # репозитории): имена веток в живом дереве переиспользуют, и новая вкладка взяла имя, которое
    # соседка лишь помнила. Находка ушла обеим, обеим сказали «учли — закройте», и та, что имя лишь
    # помнит, погасила чужую находку. Истинный адресат не увидел ничего, сдался зелёным с пустым
    # ящиком, а автору пришло «учтено» — от потока, которого он не называл. До памяти имён такого
    # случая не было вовсе: находка доходила до правильной вкладки одна.
    #
    # Правило, из которого это следует, одно: НА КАЖДОЕ ИМЯ ОТЗЫВАЕТСЯ НЕ БОЛЬШЕ ОДНОГО ПОТОКА.
    #   • носящий имя сейчас — отзывается всегда;
    #   • помнящий — только если имя не носит и не помнит НИКТО другой;
    #   • ЗАКРЫТЫЙ поток не отзывается вовсе: вкладки нет (сдан) или адрес у неё забрали
    #     (перенесён), а память закрытого не должна отнимать имя у живого соседа и гасить чужие
    #     находки. ‼️ Закрытость спрашиваем единым признаком: перенос виден только в реестре как
    #     целом, и, читай мы поле файла, проигравшая вкладка продолжала бы отзываться на имена,
    #     которые уже перешли новому владельцу адреса, — то есть перехватывала бы его почту.
    # Имя, которое помнят двое и не носит никто, не достаётся никому: приём такой находки отказывает
    # вслух (адресата нет), и это честнее тихой доставки наугад.
    $carried = @{}
    $recalled = @{}
    foreach ($entry in $Entries) {
        if ($entry.Closed) { continue }
        foreach ($name in $entry.Current) { $carried[$name] = 1 + [int]$carried[$name] }
        foreach ($name in $entry.Remembered) { $recalled[$name] = 1 + [int]$recalled[$name] }
    }
    foreach ($entry in $Entries) {
        if ($entry.Closed) {
            $entry.Keys = @()
            $entry.Silenced = @($entry.Current + $entry.Remembered | Select-Object -Unique)
            continue
        }
        $keys = [System.Collections.Generic.List[string]]::new()
        $silenced = [System.Collections.Generic.List[string]]::new()
        foreach ($name in $entry.Current) { $keys.Add($name) }
        foreach ($name in $entry.Remembered) {
            if ([int]$carried[$name] -eq 0 -and [int]$recalled[$name] -eq 1) {
                $keys.Add($name)
            } else {
                $silenced.Add($name)
            }
        }
        $entry.Keys = @($keys | Select-Object -Unique)
        $entry.Silenced = @($silenced)
    }
    return $Entries
}

function Get-NameRememberers {
    param($Claims, [string]$Name)
    # Кто ПОМНИТ это имя, никем сейчас не носимое. Нужен одному-единственному отказу: имя, которое
    # помнят двое и не носит никто, нарочно не достаётся никому (см. `Resolve-ClaimNames`), и
    # человеку надо сказать именно это, а не гадать про рабочие деревья.
    if (-not $Name) { return @() }
    $live = @($Claims | Where-Object { -not $_.Closed })
    if (@($live | Where-Object { $Name -in $_.Current }).Count -gt 0) { return @() }
    return @($live | Where-Object { $Name -in $_.Remembered })
}

function Get-StreamNames {
    param($Claim, $Claims)
    # Имена, на которые поток отзывается ПРЯМО СЕЙЧАС, с учётом всего реестра. Единственный ответ
    # на вопрос «как зовут этот поток» — им пользуются все трое: приём находки, сторож доставки и
    # сдача потока.
    #
    # Реестр обязателен: без него нельзя узнать, не носит ли запомненное имя живой сосед, — а
    # именно на этом и держится правило «на имя отзывается не больше одного потока».
    if (-not $Claim) { return @(Get-CurrentKeys) }
    $here = Get-FolderKey -Path $Claim.worktree
    foreach ($entry in @($Claims)) {
        if ((Get-FolderKey -Path $entry.Record.worktree) -ne $here) {
            continue
        }
        return @($entry.Keys)
    }
    # Заявки в реестре не нашлось (её только что положили, реестр читали раньше) — отзываемся хотя
    # бы носимыми именами: они не спорны по построению.
    return @(Get-ClaimCurrentNames -Claim $Claim)
}

function Get-ClaimFiles {
    param([string]$Dir, [switch]$Strict)
    # Опись каталога заявок — ПЕРВОЕ чтение реестра, и оно обязано срываться честно.
    #
    # ‼️ Здесь стояла самая тихая дыра всего механизма. Перечисление средствами оболочки с
    # образцом имени при закрытом доступе к содержимому каталога ВОЗВРАЩАЕТ ПУСТОЙ СПИСОК и не
    # сообщает об ошибке (проверено; три других способа перечисления в том же опыте срываются
    # честно). Ловить было нечего: исключения нет — значит ни повторы, ни отказ вслух не
    # срабатывали никогда, а строгий заслон выше по коду просто не вызывался. Наружу это выходило
    # так: сосед ведёт первый поток, вторая вкладка объявляется и получает ТОТ ЖЕ номер с кодом
    # успеха; вопрос «чей это кусок» отвечает «никто не взял»; находка живому потоку — «поток ещё
    # не объявлялся».
    #
    # Отсюда правило для всего комплекта: спрашивать файловую систему только теми способами, у
    # которых «не смог» отличается от «пусто». Здесь это исключение, а не пустой ответ.
    #
    # ‼️ «Каталога нет» — законный ответ «заявок ещё нет»: реестр заводит первая же объявившаяся
    # вкладка. Но тем же самым отказом отвечает и МЁРТВЫЙ ПУТЬ — отвалившийся диск, пропавшая
    # сетевая шара, — а это уже беда, и выдавать её за пустой реестр нельзя: на мёртвом диске
    # вопрос «чей это кусок» отвечал «задачу никто не взял», а сдача — «заявки на этой вкладке
    # нет», обе кодом успеха. Поэтому различаем: есть ли ХОТЬ ОДИН существующий каталог выше по
    # пути. Есть — путь жив, реестра просто ещё нет. Нет ни одного — говорим вслух.
    #
    # Всё прочее (нет прав, закрыт заход) — беда сразу, без разбирательств.
    $deadline = (Get-Date).AddMilliseconds((Get-ClaimReadTimeoutMs -Strict:$Strict))
    $reason = 'причина неизвестна'
    while ($true) {
        try {
            return @([System.IO.Directory]::GetFiles($Dir, '*.json'))
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                if (Test-PathReachable -Path $Dir) { return @() }
                $reason = "путь недостижим целиком: $(Get-FailureReason -Failure $_)"
                break
            }
            $reason = Get-FailureReason -Failure $_
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 30
    }
    if ($Strict) {
        throw "реестр заявок не перечислить ($Dir), а решать по нему вслепую нельзя — номер потока совпал бы с соседним, а чужая задача выглядела бы ничьей. Причина: $reason"
    }
    return @()
}

function Get-Claims {
    param([string]$Dir, [switch]$Strict)
    # Весь реестр, с посчитанным состоянием. Порядок — по волне и номеру потока, чтобы показ не
    # прыгал от запуска к запуску.
    #
    # ‼️ `-Strict` — для того, кто по этому списку ВЫБИРАЕТ НОМЕР потока, ищет владельца задачи или
    # решает судьбу находки. Ему нельзя не увидеть соседа: пропущенная заявка даёт два потока с
    # одним адресом, ответ «задачу никто не взял» и приём находки сданному потоку — всё молча и
    # навсегда. Терпимые читатели (сторож доставки на каждом ходу, показ доски) молчат и работают
    # тем, что прочиталось: там пропуск стоит одной невидимой строки, а не адреса.
    #
    # Существование каталога отдельно НЕ проверяем: проверка существования отвечает «нет» и там,
    # где на самом деле «не видно», и это ровно та подмена, на которой стояли все дыры этого
    # класса. Отвечает опись — она отличает «каталога нет» от «не смог посмотреть».
    $claims = [System.Collections.Generic.List[object]]::new()
    foreach ($file in (Get-ClaimFiles -Dir $Dir -Strict:$Strict)) {
        $read = Read-ClaimRecord -Path $file -Strict:$Strict
        # ‼️ Строгому читателю ЛЮБОЙ исход, кроме «прочитали», — отказ вслух. Для того, кто
        # выбирает номер или ищет владельца задачи, «файл занят» и «файл испорчен» — одно и то же:
        # файл лежит на месте, а поток из списка исчезает. Прежде вслух отказывал только первый, а
        # второй пропускался молча — и пустой файл, обрезанный на середине или пришедший от другой
        # версии комплекта давал ДВА ПОТОКА С ОДНИМ АДРЕСОМ без единого предупреждения
        # (воспроизведено на всех четырёх видах порчи).
        #
        # Повторять на испорченном незачем — испорчен он насовсем; но молчать нельзя тем более:
        # само собой это не пройдёт, в отличие от занятости.
        if ($Strict -and $read.State -eq 'unreadable') {
            throw "заявку соседа не прочитать ($file), а выбирать номер потока вслепую нельзя — он совпал бы с соседним. Причина: $($read.Reason). Повторите через несколько секунд."
        }
        if ($Strict -and $read.State -eq 'broken') {
            throw "заявка соседа испорчена и не разбирается ($file) — пока она такая, её поток невидим, и его номер выдался бы второй раз. Причина: $($read.Reason). Уберите файл или поправьте его."
        }
        $record = $read.Record
        if (-not $record) { continue }
        $state = Get-ClaimState -Claim $record
        $claims.Add([pscustomobject]@{
                File         = $file
                Record       = $record
                WaveKey      = Get-WaveKey -Raw ([string]$record.wave)
                StreamKey    = Get-StreamNumberKey -Raw ([string]$record.stream)
                Current      = @(Get-ClaimCurrentNames -Claim $record)
                Remembered   = @(Get-ClaimRememberedNames -Claim $record)
                Keys         = @()
                Silenced     = @()
                State        = $state
                # ‼️ ЕДИНЫЙ признак «запись закрыта» — на нём стоят ВСЕ решающие места комплекта:
                # соперники за номер, раздача имён, сверка адресата, судьба находки, сдача, закрытие
                # находки, обе отметки живости и сторож доставки. Прежде каждое спрашивало по-своему,
                # и часть читала поле `state` из СВОЕГО файла напрямую, минуя разобранный реестр.
                # Перенос в файле проигравшей не отражён вовсе (её файл не трогают), поэтому она
                # продолжала бы получать почту нового владельца адреса и гасить её у него — та же
                # беда, что и при задвоенном адресе, только с другой стороны.
                Closed       = ($state -eq 'сдан')
                # Отдельно от закрытости: «адрес забрали» и «поток сдан» лечат по-разному, и
                # говорят человеку разное. Заполняется вторым проходом ниже, как и `TakenBy`.
                Superseded   = $false
                # Запись, забравшая у этой адрес: заполняется вторым проходом ниже.
                TakenBy      = $null
                # Момент ТОГО САМОГО переезда, которым эта запись погашена. У забравшей записи
                # переездов может быть несколько (свой нынешний и каждый прошлый), и её нынешние
                # поля говорили бы о другом событии — а человеку называют именно это.
                TakenAt      = $null
                # Кто ведёт этот адрес СЕЙЧАС. Не то же самое, что забравшая запись: в цепочке
                # переездов гасит средняя папка, а ведёт последняя.
                AddressLedBy = $null
                # Последняя папка цепочки переездов этого адреса — куда он уехал в конце концов.
                # Нужна там, где ведущей записи не осталось вовсе: посылать человека в среднюю
                # папку цепочки нельзя, про этот адрес там уже нет ничего.
                AddressChainEnd = $null
            })
    }
    # ‼️ Имена раздаём ВТОРЫМ проходом, когда весь реестр уже собран: на кого отзовётся имя,
    # зависит от того, не носит ли и не помнит ли его кто-то ещё. Поодиночке этот вопрос не
    # решается, а неверный ответ на него отдаёт находку двум потокам сразу.
    #
    # ‼️ Порядок ПОЛНЫЙ: волна, номер, время объявления, путь дерева. Двух записей одного адреса в
    # исправном реестре не бывает, но правку выкатывают на грязный — а там волны с номером не хватало,
    # и порядок двух таких записей решала опись каталога. Отсюда шло всё остальное: показ выдавал их
    # то так, то этак, и человек не мог сверить два прогона глазами. Хвост ключа тот же, каким
    # разрешается спор за номер (`Get-ClaimOrder`), — иначе показ и разрешение спора называли бы
    # старшей разные записи.
    # ‼️ Перенос адреса разбираем ДО раздачи имён: погашенная запись теряет вместе с адресом и своё
    # право отзываться на имена, а раздача имён смотрит на признак закрытости. Поменяй проходы
    # местами — и проигравшая вкладка отняла бы имя ветки у нового владельца адреса.
    return @((Resolve-ClaimNames -Entries (Set-ClaimSupersessions -Entries @($claims))) |
            Sort-Object -Property @(
                @{ Expression = { $_.WaveKey } }
                @{ Expression = { $_.StreamKey } }
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).When } }
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).Path } }
            ))
}

function Find-Claims {
    param($Claims, [string]$Raw)
    # Кому адресована находка. Сперва как «волна/поток» — это основной вид адреса, потому что
    # именно им поток назван в плане; не разобралось — как имя ветки или папки.
    $address = Get-StreamAddress -Raw $Raw
    if ($address) {
        return @($Claims | Where-Object {
                $_.WaveKey -eq $address.Wave -and $_.StreamKey -eq $address.Stream
            })
    }
    $key = Get-StreamKey -Raw $Raw
    if (-not $key) { return @() }
    return @($Claims | Where-Object { $key -in $_.Keys })
}

function Get-CurrentClaim {
    param([string]$Dir, [switch]$Strict)
    # ‼️ `-Strict` — тому, для кого «заявки нет» и «файл в этот миг занят» решают разное. Сдача
    # потока по занятому файлу отвечала бы «заявки на этой вкладке нет — сдавать нечего», и вкладка
    # закрывалась бы, не сдав поток: соседи продолжали бы адресовать находки живому, как им кажется,
    # потоку. Тот же корень, что и у выбора номера, — одна попытка чтения и пустота вместо правды.
    #
    # Ключ — КОРЕНЬ рабочего дерева, а не текущая папка: иначе вкладка, ушедшая в подкаталог, ищет
    # свою заявку не там, где её положила, и получает пустоту, неотличимую от «заявки нет».
    $path = Get-ClaimPath -Dir $Dir -TreePath (Get-TreeRoot)
    $read = Read-ClaimRecord -Path $path -Strict:$Strict
    if ($Strict -and $read.State -eq 'unreadable') {
        throw "заявку этой вкладки не прочитать ($($read.Reason)) — сдавать поток вслепую нельзя, иначе он останется числиться за вами. Повторите через несколько секунд."
    }
    # ‼️ И испорченная — тоже отказ. Иначе владелец испорченной заявки невидим САМ СЕБЕ: сдача
    # отвечает «заявки на этой вкладке нет, сдавать нечего» и выходит успехом, вкладка закрывается,
    # а соседи продолжают адресовать находки потоку, который считают живым.
    if ($Strict -and $read.State -eq 'broken') {
        # ‼️ Путь называем ПОЛНОСТЬЮ, а выход даём выполнимый. Прежде отказ не называл файла вовсе
        # и советовал объявиться заново — а объявление читает весь реестр строго, натыкается на тот
        # же файл и тоже отказывает. Человек читал свой собственный отказ и выхода из него не имел.
        throw "заявка этой вкладки испорчена и не разбирается ($path) — пока она такая, поток невидим и соседям, и вам. Причина: $($read.Reason). Уберите этот файл, потом объявитесь заново."
    }
    return $read.Record
}

function Find-ClaimByWorktree {
    param($Claims, [string[]]$Paths)
    # Вторая дорога к СВОЕЙ заявке — по точному совпадению записанной в ней рабочей папки.
    #
    # Зачем. Имя файла заявки выводится из пути, и заявки, поданные ПРЕЖНЕЙ версией из подкаталога
    # дерева, лежат под ключом того подкаталога. По каноничному ключу (корень дерева) они не
    # находятся, и сдача отвечала бы «сдавать нечего» кодом успеха — то есть осиротила бы ровно те
    # потоки, ради которых правка и делается.
    #
    # ‼️ Это ЧТЕНИЕ, а не перенос: ни одного файла не заводится и не удаляется, найденная запись
    # правится на своём месте. И совпадение только ТОЧНОЕ — ни «начинается с», ни «лежит внутри»:
    # иначе вкладка забрала бы заявку соседнего дерева, вложенного в её папку.
    if (-not $Claims) { return $null }
    $wanted = @($Paths | ForEach-Object { Get-FolderKey -Path $_ } |
            Where-Object { $_ } | Select-Object -Unique)
    if ($wanted.Count -eq 0) { return $null }
    $mine = @($Claims | Where-Object {
            (Get-FolderKey -Path $_.Record.worktree) -in $wanted
        })
    # Незакрытость спрашиваем ЕДИНЫМ признаком: перенесённая запись в своём файле выглядит открытой
    # (её файл не трогают), и, читай мы поле напрямую, вкладка, у которой адрес забрали, считала бы
    # её своей живой заявкой — сдавала бы её и закрывала бы по ней чужие находки.
    $open = @($mine | Where-Object { -not $_.Closed })
    # Двух незакрытых заявок на одну папку не бывает по правилу реестра. Раз уж случилось —
    # выбирать за человека нельзя: сдали бы наугад одну, а вторая осталась бы держать адрес живым.
    if ($open.Count -gt 1) {
        $names = @($open | ForEach-Object { $_.File }) -join ', '
        throw "на эту рабочую папку в реестре сразу $($open.Count) незакрытых заявок ($names) — какую из них сдавать, механизм решать не вправе. Уберите лишнюю и повторите."
    }
    if ($open.Count -eq 1) { return $open[0] }
    if ($mine.Count -eq 1) { return $mine[0] }
    return $null
}

function Get-ClaimEntry {
    param($Claims, $Claim, [string]$Path)
    # РАЗОБРАННАЯ запись реестра, отвечающая этой заявке. Нужна там, где на руках только сам файл
    # заявки, а спросить надо о состоянии: перенос живёт не в файле, а в реестре как целом, и по
    # своему файлу вкладка о нём не узнает никогда.
    #
    # Ищем сперва по файлу (он точен даже там, где заявка лежит под неканоничным именем), потом по
    # записанной рабочей папке.
    if (-not $Claims) { return $null }
    if ($Path) {
        $wanted = Get-FolderKey -Path $Path
        foreach ($entry in @($Claims)) {
            if ((Get-FolderKey -Path $entry.File) -eq $wanted) { return $entry }
        }
    }
    if ($Claim -and $Claim.worktree) {
        $here = Get-FolderKey -Path $Claim.worktree
        # Незакрытая предпочтительнее: в переиспользованной папке рядом лежит и сданная запись.
        $mine = @($Claims | Where-Object { (Get-FolderKey -Path $_.Record.worktree) -eq $here })
        $open = @($mine | Where-Object { -not $_.Closed })
        if ($open.Count -eq 1) { return $open[0] }
        if ($mine.Count -eq 1) { return $mine[0] }
    }
    return $null
}

function Test-ClaimClosed {
    param($Claims, $Claim, [string]$Path)
    # Закрыта ли запись — ОДИН ответ на весь комплект. Сданность видна и в самом файле, перенос —
    # только в разобранном реестре; поэтому спрашиваем и то, и другое, а реестр не обязателен: без
    # него отвечаем ровно как прежде (сдана или нет).
    if ($Claim -and [string]$Claim.state -eq 'released') { return $true }
    $entry = Get-ClaimEntry -Claims $Claims -Claim $Claim -Path $Path
    if (-not $entry) { return $false }
    return [bool]$entry.Closed
}

function Get-RegistryLockPath {
    param([string]$Dir)
    # Замок лежит В САМОМ реестре заявок: он стережёт именно его и уезжает вместе с ним (тесты
    # подставляют свою доску — реестр и замок уходят за ней сами).
    #
    # ‼️ Имя нарочно не оканчивается на `.json`: выдачу заявок читают по этому образцу, и замок
    # оказался бы в ней потоком-призраком — без волны, без номера, зато занимающим место в списке
    # соседей и в счёте потоков волны.
    #
    # Складываем строкой: сложение путей средствами оболочки срывается на несуществующем диске.
    return [System.IO.Path]::Combine($Dir, '.claim.lock')
}

function Get-RegistryLockWaitSeconds {
    # Сколько всего ждём чужой замок, прежде чем пойти дальше без него (сказав об этом вслух).
    # Критическая часть — несколько чтений папки и запись одного файла, доли секунды, так что даже
    # десяток вкладок, объявившихся разом, укладывается в предел с запасом.
    return 30
}

function Get-RegistryLockSpeakAfterSeconds {
    # Через столько молчаливое ожидание начинает выглядеть зависанием инструмента: человек ждёт
    # ответа на свою команду и, не видя ничего, бьёт по клавишам.
    return 2
}

function Get-PathState {
    param([string]$Path)
    # Что лежит по пути: `container` — каталог, `leaf` — файл, `none` — ничего, `unknown` — узнать
    # не удалось. Отдельной функцией, потому что сама проверка пути умеет СРЫВАТЬСЯ: на
    # несуществующем диске она не отвечает «нет», а бросает ошибку, и при строгом режиме оболочки
    # эта ошибка выносила инструмент наружу сырым английским сообщением про диск, которого нет.
    # ‼️ Спрашиваем систему ОДНИМ вопросом, а не двумя подряд. Между двумя ответами мир меняется:
    # вкладки волны заводят каталог реестра разом, и если сосед успел создать его между вопросом
    # «это каталог?» и вопросом «а вообще есть?», выходил уверенный, но ЛОЖНЫЙ ответ «лежит не
    # каталог». Опасен был не сам ответ, а совет, который на нём строился: он указывал удалить
    # папку, где лежат заявки соседей. Под нагрузкой ложь ловилась в каждом пятом запуске.
    #
    # Свойства пути приходят одним ответом системы: есть каталог — значит каталог, есть без
    # признака каталога — файл, нет вовсе — своим видом отказа.
    try {
        $attributes = [System.IO.File]::GetAttributes($Path)
        $kind = if ($attributes -band [System.IO.FileAttributes]::Directory) { 'container' } else { 'leaf' }
        return [pscustomobject]@{ Kind = $kind; Reason = '' }
    } catch {
        if (Test-MissingPathFailure -Failure $_) {
            return [pscustomobject]@{ Kind = 'none'; Reason = '' }
        }
        return [pscustomobject]@{ Kind = 'unknown'; Reason = (Get-FailureReason -Failure $_) }
    }
}

function Test-PathReachable {
    param([string]$Path)
    # Жив ли путь: есть ли выше по нему хоть один существующий каталог. Нужен, чтобы отличить
    # «реестра ещё нет» (нормально: заведёт первая же вкладка) от «пути нет вовсе» (отвалился диск
    # или сетевая шара) — система отвечает на оба случая одним и тем же отказом.
    #
    # Проверка существования тут врёт только в сторону «нет», и это безопасная сторона: сомнение
    # уводит в отказ вслух, а не в тихое «заявок нет».
    $current = [System.IO.Path]::GetDirectoryName($Path)
    while ($current) {
        if ((Get-PathState -Path $current).Kind -eq 'container') { return $true }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return $false
}

function Get-BlockingAncestor {
    param([string]$Path)
    # Ближайший предок пути, который существует и каталогом НЕ является. Нужен ровно для честного
    # отказа: когда папку доски занял файл, каталог реестра создать нельзя, но виноват не он, а
    # файл выше по пути — и назвать надо именно его, иначе человеку нечего искать.
    # Путь режем строкой, а не разбором через оболочку: на несуществующем диске тот срывается, а
    # здесь мы как раз и разбираемся, что с путём не так.
    $current = [System.IO.Path]::GetDirectoryName($Path)
    while ($current) {
        $state = Get-PathState -Path $current
        if ($state.Kind -eq 'container') { return '' }
        if ($state.Kind -eq 'leaf') { return $current }
        if ($state.Kind -eq 'unknown') { return '' }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return ''
}

function Assert-RegistryDir {
    param([string]$Dir)
    # Каталог реестра обязан быть КАТАЛОГОМ, и сказать об этом надо сразу — своими словами и с
    # настоящей причиной.
    #
    # ‼️ Ловушек тут три, и все три уже сработали вживую.
    #
    # Первая: проверка «есть ли такой путь» отвечает «есть» и на файл, а `New-Item -ItemType
    # Directory -Force` поверх файла молча ничего не делает и рапортует успехом. Дальше замок
    # честно ждал бы полминуты, печатал пугающее «замок не отдали, вкладка рядом упала» — и всё
    # равно падал строкой ниже, на записи заявки.
    #
    # Вторая: судить надо ПО ИТОГУ, а не по двум отдельным пробам («каталога нет» + «путь занят» =
    # «лежит файл»). Проба и проба — это гонка: вкладки волны заводят каталог реестра разом, и
    # между двумя пробами сосед успевает его создать. Первая же вкладка обвиняла соседа в том, что
    # тот положил на место каталога файл, и объявление срывалось с уверенным, но ложным отказом.
    #
    # Третья: молчаливый рапорт об успехе оставлял отказ БЕЗ причины — «причина неизвестна», — хотя
    # причина известна и называется: путь перекрыт файлом выше по дереву. А на несуществующем диске
    # до этой развилки дело вообще не доходило: срывалась сама проверка пути.
    $state = Get-PathState -Path $Dir
    if ($state.Kind -eq 'container') { return }
    if ($state.Kind -eq 'leaf') {
        throw "путь для каталога заявок занят: по нему лежит файл, а не каталог ($Dir). Пока он там, объявиться не сможет ни одна вкладка — посмотрите, что это, и освободите путь"
    }
    if ($state.Kind -eq 'unknown') {
        throw "каталог заявок не проверить ($Dir). Причина: $($state.Reason)"
    }
    $reason = ''
    try {
        New-Item -ItemType Directory -Path $Dir -Force -ErrorAction Stop | Out-Null
    } catch {
        # Каталог мог завести сосед в тот же миг — это не отказ, а обычный ход дела; судим ниже, по
        # итогу. Но причину держим: не вышло — человеку нужна ОНА, а не наша догадка.
        $reason = Get-FailureReason -Failure $_
    }
    $state = Get-PathState -Path $Dir
    if ($state.Kind -eq 'container') { return }
    if ($state.Kind -eq 'leaf') {
        throw "путь для каталога заявок занят: по нему лежит файл, а не каталог ($Dir). Пока он там, объявиться не сможет ни одна вкладка — посмотрите, что это, и освободите путь"
    }
    if ($state.Kind -eq 'unknown') {
        throw "каталог заявок не проверить ($Dir). Причина: $($state.Reason)"
    }
    # Каталога нет, а создание о беде не сказало. Значит смотрим выше по пути: чаще всего там файл.
    $blocking = Get-BlockingAncestor -Path $Dir
    if ($blocking) {
        throw "путь к каталогу заявок перекрыт файлом ($blocking) — уберите или переименуйте его, иначе объявиться не сможет ни одна вкладка"
    }
    if (-not $reason) { $reason = 'создание каталога не сообщило о беде, но каталога нет' }
    throw "каталог заявок не завести ($Dir). Причина: $reason"
}

function Enter-RegistryLock {
    param([string]$Dir)
    # Кросс-процессный замок на ВЫБОР НОМЕРА потока. Отвечает открытым дескриптором файла замка —
    # его отдают потом в `Exit-RegistryLock`; $null значит «замок взять не удалось».
    #
    # ‼️ Что он ломает (без этого его уберут как лишний). Выбор номера идёт по снимку всего
    # реестра, и без замка он не атомарен. Шесть вкладок, открытых разом: все читают ещё пустой
    # реестр и берут номер 1; в круге разрешения спора двое одновременно считают следующий
    # свободный от одного и того же снимка — и обе берут 2; та из них, что перечитала реестр
    # раньше, чем вторая успела записать свой сдвиг, соперника не видит и выходит из круга с
    # номером 2, а вторая, сравнив себя со «старшей», тоже остаётся на 2. Повторные заходы тут не
    # спасают: из круга выходят по УСТАРЕВШЕМУ снимку. Под замком вторая вкладка читает реестр уже
    # с записанной заявкой первой, и номера расходятся.
    #
    # Замок — ДЕРЖИМЫЙ ДЕСКРИПТОР файла, открытого без права совместного доступа. Не именованный
    # мьютекс (комплект ставят в любой проект, а межпроцессные мьютексы за пределами Windows не
    # работают) и не «файл существует — значит занято».
    #
    # ‼️ Почему не по существованию файла — это главное. Тогда упавшая вкладка оставляет файл, и
    # чтобы доска не заперлась навсегда, нужен порог протухания и перехват. А перехват невозможно
    # сделать безопасным подручными средствами: решение «замок протух, отнимаю» принимается по
    # ОДНОМУ состоянию файла, а отнимается уже другое — держатель успел уйти, сосед успел завести
    # свежий замок, и отнимают именно его, пока сосед работает внутри. Собрано стендом на восьми
    # процессах: двойной вход ловится. У дескриптора этой развилки нет вовсе — система закрывает
    # его сама, когда процесс умирает, и замок освобождается в тот же миг (проверено убийством
    # держателя: следующий вошёл сразу, ждать порога не пришлось).
    #
    # ‼️ Файл замка НИКОГДА не удаляется — ни при выходе, ни при уборке. Держит замок дескриптор, а
    # не файл: удали его — и второй процесс заведёт файл заново и возьмёт замок на НОВОМ файле,
    # пока первый держит старый. Внутри окажутся двое. Пустой файл в каталоге реестра — плата
    # ничтожная, в выдачу заявок он не попадает (см. имя выше).
    #
    # ‼️ Насколько это надёжно — честно, без обещаний. На Windows взаимное исключение между
    # процессами держит сама система, и здесь это проверено опытом. На прочих системах .NET
    # изображает совместный доступ советующими блокировками ядра, а они действуют не везде: на
    # части сетевых файловых систем не работают вовсе, и их можно отключить настройкой среды
    # выполнения. Проверить это на машине разработки нечем.
    #
    # Стенд взаимного исключения в наборе тестов ловит такую поломку, но полагаться на него как на
    # гарантию нельзя: он идёт там, где гоняют тесты ЭТОГО репозитория, то есть на одной машине.
    # Комплект, поставленный в чужой проект, тестов не гоняет и Python не требует.
    #
    # Отсюда правило для того, кто понесёт комплект дальше: реестр заявок должен лежать на обычном
    # локальном диске. Лёг на сетевую шару — прогоните стенд взаимного исключения там же, прежде
    # чем считать замок работающим.

    # ‼️ Замок НЕ допускает повторного входа: второй заход того же процесса система отвергнет так
    # же, как чужой. Сегодня вложенного входа нет, и заводить его нельзя — вкладка заперла бы сама
    # себя, прождала предел и пошла дальше без замка, напечатав про застрявшего соседа, которого не
    # существует. Понадобится вложенный вход — передавайте уже взятый дескриптор внутрь, а не
    # берите замок второй раз.
    Assert-RegistryDir -Dir $Dir
    $path = Get-RegistryLockPath -Dir $Dir
    $started = Get-Date
    $deadline = $started.AddSeconds((Get-RegistryLockWaitSeconds))
    $spoke = $false
    while ($true) {
        try {
            $stream = [System.IO.File]::Open($path, 'OpenOrCreate', 'Write', 'None')
            try {
                # Кто держит — человеку, который заглянет в файл. Никакая работа замка на это не
                # опирается: держит его дескриптор, а не запись внутри.
                $stream.SetLength(0)
                $note = [System.Text.Encoding]::UTF8.GetBytes(
                    "процесс $PID@$([System.Environment]::MachineName), взят $((Get-Date).ToString('s'))")
                $stream.Write($note, 0, $note.Length)
                $stream.Flush()
            } catch {
                # Отметка не записалась — не беда, замок уже наш. Ронять из-за неё объявление
                # незачем: никакая работа замка на эту запись не опирается.
            }
            if ($spoke) { [Console]::Out.WriteLine('Замок реестра заявок освободился — продолжаю объявление.') }
            return $stream
        } catch {
            # ‼️ Спор за замок и неустранимая помеха выглядят тут одинаково — отказом открыть
            # файл, — а лечатся противоположно: спор надо переждать, помеху ждать бессмысленно
            # (полминуты ожидания, рассказ про соседнюю вкладку — и всё равно отказ строкой ниже,
            # на записи заявки; до замка отказ здесь был мгновенным и честным).
            #
            # Различаем по виду ошибки: «файл занят другим процессом» приходит обычной ошибкой
            # ввода-вывода, а негодный путь и нехватка прав — своими видами (проверено). Пробовать
            # вместо этого «а лежит ли файл на месте» нельзя: такая проверка гоночная — держатель
            # успевает уйти между отказом и проверкой, и обычный спор объявляется помехой.
            $failure = $_.Exception
            while ($failure.InnerException) { $failure = $failure.InnerException }
            #
            # ‼️ Проверка каталога ниже — НЕ та гоночная проверка «а лежит ли файл на
            # месте», о которой предупреждает абзац выше, и разница существенна: файл замка при
            # обычном споре появляется и исчезает, а КАТАЛОГ по пути замка при нормальной работе
            # не бывает никогда — замок всегда файл. Значит каталог там — помеха, а не спор, в
            # любом порядке событий. Проверяем его явно, потому что системы расходятся в виде
            # ошибки: windows отдаёт попытку открыть каталог как файл ошибкой доступа (она и так
            # безнадёжна по виду), а linux — обычной ошибкой ввода-вывода, неотличимой от замка,
            # занятого соседом. Без этой проверки вкладка на linux выжидает весь предел, винит
            # несуществующего соседа и берёт номер вообще без замка. Поймано прогоном публичного
            # хранилища на linux — на машине разработки этого было не увидеть.
            $hopeless = ($failure -isnot [System.IO.IOException]) -or
                ($failure -is [System.IO.DirectoryNotFoundException]) -or
                (Test-Path -LiteralPath $path -PathType Container)
            if ($hopeless) {
                throw "замок реестра заявок не завести ($path). Причина: $(Get-FailureReason -Failure $_)"
            }
        }
        if ((Get-Date) -ge $deadline) { return $null }
        if (-not $spoke -and ((Get-Date) - $started).TotalSeconds -ge (Get-RegistryLockSpeakAfterSeconds)) {
            # Молчаливое ожидание человек читает как зависание инструмента. Пишем в обычный
            # поток вывода, а не в поток ошибок: там в этом инструменте живут только отказы. И не
            # через обычный вывод оболочки — он вернулся бы вызывающему вместо дескриптора замка.
            $spoke = $true
            [Console]::Out.WriteLine("Жду освобождения замка реестра заявок: рядом объявляется другая вкладка ($path). Подожду до $(Get-RegistryLockWaitSeconds) с.")
        }
        # Пауза вразнобой: одинаковая свела бы вкладки в такт, и они толкались бы синхронно.
        Start-Sleep -Milliseconds (20 + (Get-Random -Maximum 40))
    }
}

function Exit-RegistryLock {
    param($Handle)
    # Отпустить замок — значит закрыть дескриптор. Файл при этом остаётся на месте намеренно: см.
    # разбор в `Enter-RegistryLock`, удаление файла впустило бы внутрь второго.
    #
    # Молчим при любой неудаче: даже не закройся дескриптор здесь, система закроет его на выходе
    # процесса, а ронять из-за этого уже состоявшееся объявление незачем.
    if (-not $Handle) { return }
    try {
        $Handle.Dispose()
    } catch {
        return
    }
}

function Write-ClaimFile {
    param([string]$Path, $Claim)
    # Пишем через временный файл рядом: читатель никогда не увидит полузаписанную заявку. Писатель
    # у файла один (своя вкладка), поэтому ни блокировок, ни повторов не нужно.
    # Путь режем строкой — по той же причине, что и на доске: разбор оболочкой срывается на
    # несуществующем диске сырым английским сообщением.
    $dir = [System.IO.Path]::GetDirectoryName($Path)
    if ($dir -and -not (Test-Path -LiteralPath $dir -PathType Container)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    # ‼️ Имя временного файла НЕ содержит `.json`: опись реестра берёт файлы по этому образцу, и
    # оборвавшаяся запись иначе легла бы в реестр обрывком — а обрывок теперь останавливает всё,
    # что зависит от адреса. Убираем его и при неудаче: мусор в каталоге реестра нам не нужен.
    $temp = [System.IO.Path]::ChangeExtension($Path, ".tmp-$PID")
    try {
        [System.IO.File]::WriteAllText($temp, ($Claim | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::Move($temp, $Path, $true)
    } catch {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Update-ClaimSeen {
    param([string]$Dir, [string]$TreePath, [string]$Path, $Claims)
    # Отметка «вкладка на ходу». Зовётся сторожем доставки на каждом ходу и обязана быть немой:
    # сорвавшаяся отметка не повод мешать работе.
    #
    # `-Path` — когда файл заявки уже найден и он НЕ каноничный: так лежат заявки, поданные прежней
    # версией из подкаталога дерева. Без этого такая заявка получала почту, но отметку живости — ни
    # разу: через сутки она попадала в сводку застрявшего у владельца, а соседи переставали считать
    # вкладку живой. ‼️ Второго писателя тут не появляется: найдена она по ТОЧНОМУ совпадению
    # рабочей папки, то есть принадлежит этой же вкладке. Запрет писать в ЧУЖОЙ файл заявки в силе.
    #
    # ‼️ `-Claims` — разобранный реестр, если он у зовущего уже есть. Штамповать запись в ЛЮБОМ
    # закрытом состоянии нельзя, а не только в сданном: перенесённая запись в своём файле выглядит
    # открытой, и любая новая сессия, открытая в старой папке, первым же ходом воскрешала бы
    # призрака — свежая отметка возвращает ему вид работающего потока вместе с его адресом и почтой.
    try {
        if (-not $Path) { $Path = Get-ClaimPath -Dir $Dir -TreePath $TreePath }
        $claim = Read-ClaimFile -Path $Path
        if (-not $claim) { return }
        if (Test-ClaimClosed -Claims $Claims -Claim $claim -Path $Path) { return }
        # Через `Add-Member -Force`, а не присваиванием: у заявки прежней версии поля отметки может
        # не быть вовсе, и присваивание сорвалось бы — то есть такая заявка молчала бы навсегда.
        $claim | Add-Member -NotePropertyName seen_at -NotePropertyValue ((Get-Date).ToString('s')) -Force
        Write-ClaimFile -Path $Path -Claim $claim
    } catch {
        return
    }
}

function Get-ProfilePlansFolder {
    param([string]$StartDir)
    # Где в ЭТОМ проекте лежат планы волн: профиль `.parallel-streams.md`, раздел `## Plans`, путь
    # обратными кавычками. Разбор ОДИН на весь комплект, и зовут его двое — сторож-подсказка (он
    # решает, что правится план волны) и отбор мест, которые потоки правят сообща. Разъедься они —
    # сторож считал бы планом одно, а список пересечений другое.
    #
    # Заголовок ищем БЕЗ учёта регистра: профиль пишет человек руками, и «## plans» — тот же раздел.
    #
    # ‼️ Папка не названа — отвечаем пусто, и никаких значений по умолчанию на путь одного проекта.
    # Зашитый путь в чужом проекте не сошёлся бы никогда: сторож молча выходил бы нулём, а план
    # волны попадал бы в список пересечений — и то, и другое неотличимо от исправной работы.
    if (-not $StartDir) { $StartDir = $PWD.Path }
    try {
        $dir = $StartDir
        while ($dir) {
            $profilePath = Join-Path $dir '.parallel-streams.md'
            if (Test-Path -LiteralPath $profilePath -PathType Leaf) {
                $text = [System.IO.File]::ReadAllText($profilePath, [System.Text.UTF8Encoding]::new($false))
                $section = [regex]::Match($text, '(?msi)^##\s+Plans\s*$(.*?)(?=^##\s|\z)')
                if (-not $section.Success) { return '' }
                foreach ($found in [regex]::Matches($section.Groups[1].Value, '`([^`]+)`')) {
                    $value = ($found.Groups[1].Value.Trim() -replace '\\', '/') -replace '^\./', ''
                    if ($value -match '\s') { continue }
                    # Имя файла папкой планов не бывает: в разделе кавычками помечают и файлы.
                    if ($value -notmatch '/$' -and $value -match '\.[A-Za-z0-9]{1,5}$') { continue }
                    if (-not $value.EndsWith('/')) { $value += '/' }
                    return $value
                }
                return ''
            }
            # Выше корня репозитория не поднимаемся: там начинается чужой проект со своим профилем.
            if (Test-Path -LiteralPath (Join-Path $dir '.git')) { break }
            $dir = Split-Path -Parent $dir
        }
    } catch {
        return ''
    }
    return ''
}

function Get-SharedByDesignPattern {
    # Места, которые потоки правят СООБЩА по устройству работы, а не по недосмотру: план волны
    # трогает каждая вкладка (строка состояния, дописка находки). Считать это пересечением —
    # значит сделать сторожа шумным и тем самым бесполезным.
    #
    # Папку планов берём из профиля тем же разбором, что и сторож-подсказка. Пусто — планов в
    # проекте нет, а значит нет и общих по устройству мест: тогда не отсеиваем НИЧЕГО. Пустой
    # образец совпал бы с любым именем и спрятал бы все пересечения разом.
    $plans = Get-ProfilePlansFolder
    if (-not $plans) { return '' }
    return "/$plans"
}

function Get-TouchedFiles {
    # Файлы, которые поток уже тронул: своя ветка против общего предка с основной плюс
    # незакоммиченное. По ним видно пересечение с соседом ДО того, как оно станет конфликтом
    # слияния, — и, что важнее, до того, как вкладка предложит владельцу взять чужую задачу.
    $files = [System.Collections.Generic.List[string]]::new()
    try {
        $base = (& git merge-base HEAD origin/main 2>$null)
        if ($LASTEXITCODE -eq 0 -and $base) {
            foreach ($name in @(& git diff --name-only $base.Trim() HEAD 2>$null)) { $files.Add($name) }
        }
        # Машинный вид сводки называет пути от КОРНЯ дерева, а не от папки запуска (проверено из
        # подкаталога): у вкладки, ушедшей в подкаталог, список тронутого сходится с соседским, и
        # править тут нечего.
        foreach ($line in @(& git status --porcelain 2>$null)) {
            if ($line.Length -le 3) { continue }
            $name = $line.Substring(3).Trim().Trim('"')
            # Переименование приходит как «было -> стало»: интересует то, что есть сейчас.
            if ($name -match ' -> ') { $name = ($name -split ' -> ')[-1].Trim().Trim('"') }
            $files.Add($name)
        }
    } catch {
        return @()
    }
    $shared = Get-SharedByDesignPattern
    $clean = foreach ($name in $files) {
        if (-not $name) { continue }
        $normalized = ($name -replace '\\', '/').Trim().ToLowerInvariant()
        if (-not $normalized) { continue }
        # Пустой образец — это «планов в проекте нет»: тогда не отсеиваем ничего, иначе он совпал
        # бы с любым именем и список тронутого вышел бы пустым у всех.
        if ($shared -and "/$normalized" -like "*$shared*") { continue }
        $normalized
    }
    # Потолок: список едет в заявку, которую читают все соседи на каждом ходу. Сквозная правка на
    # тысячу файлов пересечётся и по первым двум сотням.
    return @($clean | Select-Object -Unique | Select-Object -First 200)
}

function Update-ClaimFiles {
    param([string]$Dir, [string]$TreePath, [string]$Path, $Claims, [int]$MaxAgeMinutes = 5)
    # Список тронутого пересчитывается не чаще раза в несколько минут: сторож зовётся на КАЖДОМ
    # ходу вкладки, а два запроса к git на каждый ход — плата ни за что.
    #
    # `-Path` — та же вторая дорога, что и у отметки живости: заявка прежней версии из подкаталога
    # лежит под неканоничным именем, и без этого сосед никогда не увидел бы её пересечений по файлам.
    try {
        if (-not $Path) { $Path = Get-ClaimPath -Dir $Dir -TreePath $TreePath }
        $path = $Path
        $claim = Read-ClaimFile -Path $path
        if (-not $claim) { return }
        # ‼️ Закрыта в ЛЮБОМ смысле — не трогаем, тем же единым признаком и по той же причине, что и
        # отметка живости: свежий список тронутых файлов у перенесённой записи выглядит работой
        # живой вкладки и возвращает призрака в пересечения с соседями.
        if (Test-ClaimClosed -Claims $Claims -Claim $claim -Path $path) { return }
        $when = [datetime]::MinValue
        if ($claim.files_at -and [datetime]::TryParse([string]$claim.files_at, [cultureinfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
            if (((Get-Date) - $when).TotalMinutes -lt $MaxAgeMinutes) { return }
        }
        $claim | Add-Member -NotePropertyName files -NotePropertyValue (Get-TouchedFiles) -Force
        $claim | Add-Member -NotePropertyName files_at -NotePropertyValue ((Get-Date).ToString('s')) -Force
        Write-ClaimFile -Path $path -Claim $claim
    } catch {
        return
    }
}

function Get-Overlaps {
    param($Claims, $MyClaim)
    # Соседи, которые правят те же файлы. Ровно тот случай, о котором владелец узнать не может:
    # вкладка предлагает ему «сделать заодно ещё вот это», он не знает, что это кусок другого
    # потока, и подтверждает. Пересечение по файлам видно механически и заранее.
    if (-not $MyClaim -or -not $MyClaim.files) { return @() }
    # Общие по устройству работы места отсеиваем и здесь, а не только при сборке списка: заявку
    # мог написать сторож прежней версии, и тогда план волны снова считался бы пересечением.
    $shared = Get-SharedByDesignPattern
    $mine = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($name in @($MyClaim.files)) {
        $text = [string]$name
        if (-not $text) { continue }
        if ($shared -and "/$text" -like "*$shared*") { continue }
        [void]$mine.Add($text)
    }
    if ($mine.Count -eq 0) { return @() }
    $here = Get-FolderKey -Path $MyClaim.worktree
    $found = [System.Collections.Generic.List[object]]::new()
    foreach ($claim in $Claims) {
        if ($claim.State -ne 'ведёт') { continue }
        if ((Get-FolderKey -Path $claim.Record.worktree) -eq $here) { continue }
        $common = @(@($claim.Record.files) | Where-Object { $_ -and $mine.Contains([string]$_) })
        if ($common.Count -eq 0) { continue }
        $found.Add([pscustomobject]@{ Claim = $claim; Files = @($common) })
    }
    return @($found)
}

function Get-StuckRecords {
    param($Records, $Claims, [string[]]$KnownKeys)
    # Записи, которым некуда прийти. Сегодня их не видит НИКТО: адресата нет, а отправителю уже
    # отрапортовали об успехе. Молчание механизма неотличимо от «соседу нечего сказать».
    $stuck = [System.Collections.Generic.List[object]]::new()
    $deadline = (Get-Date).AddDays(-(Get-SilentDaysBeforeStuck))
    foreach ($record in $Records) {
        $raw = [string]$record.to
        # Запись «всем» сюда не попадает: у неё свой срок давности и свой смысл — её могли просто
        # ещё не все учесть. Уведомление «учтено» — тем более: гоняться за ним некому и незачем.
        if ((Get-StreamKey -Raw $raw) -in @('*', '**')) { continue }
        if ([string]$record.kind -eq 'ack') { continue }
        $addressed = @(Find-Claims -Claims $Claims -Raw $raw)
        $live = @($addressed | Where-Object { $_.State -eq 'ведёт' })
        if ($live.Count -gt 0) { continue }
        $when = [datetime]::MinValue
        $parsed = [datetime]::TryParse([string]$record.at, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)
        $reason = ''
        if (@($addressed | Where-Object { $_.State -eq 'сдан' }).Count -gt 0) {
            # Сдан — случай, где ждать нечего вовсе: вкладки нет и не будет.
            $reason = 'поток сдан'
        } elseif (@($addressed | Where-Object { $_.Closed }).Count -eq $addressed.Count -and
            $addressed.Count -gt 0) {
            # Все записи адреса перенесены, а ведущей не осталось. Цепочка переездов сюда больше не
            # приводит (в ней гасятся все, кроме последней), значит перед нами круг: записи забрали
            # адрес друг у друга, и ждать по нему нечего. Причина не «сдан», и врать про сдачу
            # нельзя — человек пошёл бы искать итог сданного потока, которого никто не писал.
            $reason = 'адрес перенесён, а ведущей записи у него не осталось'
        } elseif ($addressed.Count -gt 0) {
            if ($parsed -and $when -gt $deadline) { continue }
            $reason = "поток молчит с $(Format-Stamp -Raw $addressed[0].Record.seen_at)"
        } else {
            if ($parsed -and $when -gt $deadline) { continue }
            # Ключи сюда передают ОТМЕТИВШИХСЯ: дерево само по себе адресата не делает — вкладку
            # могли закрыть неделю назад, а находка адресована именем ветки и ждёт её.
            if ((Get-StreamKey -Raw $raw) -in $KnownKeys) { continue }
            $reason = 'заявки на такой поток нет, свежей отметки у дерева тоже'
        }
        $stuck.Add([pscustomobject]@{ Record = $record; Reason = $reason })
    }
    return @($stuck)
}

function Format-Stamp {
    param($Raw)
    # Время в заявке лежит строкой, но при чтении JSON превращается в дату, и тогда `[string]`
    # печатает его в виде системной локали («08/21/2026 23:34:13»). Человеку показываем один и тот
    # же вид независимо от того, каким путём значение пришло.
    if ($Raw -is [datetime]) { return $Raw.ToString('yyyy-MM-dd HH:mm') }
    $text = [string]$Raw
    if (-not $text) { return 'без отметки' }
    $when = [datetime]::MinValue
    if ([datetime]::TryParse($text, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return $when.ToString('yyyy-MM-dd HH:mm')
    }
    return $text
}

function Get-ClaimFolderMarks {
    param($Claim)
    # Улики о РАБОЧЕЙ ПАПКЕ записи — те же самые, которыми объясняются оба отказа («папка занята»,
    # «адрес занят»). Отказ называет человеку чужую папку, а увидеть её ему сегодня негде: показ
    # печатал адрес, имя и ветку, но не папку. Поэтому улики и метки живут в одном месте с показом —
    # прочитав отказ, человек находит ту же папку в списке и решает за секунду.
    $marks = [System.Collections.Generic.List[string]]::new()
    $here = Get-FolderKey -Path $Claim.Record.worktree
    if (-not $here) { return @($marks) }
    # «Это вы» — первая метка, потому что первый вопрос человека к отказу: «а не про меня ли это».
    # Сверяем с КОРНЕМ дерева, а не с текущей папкой: заявка записывает корень, и вкладка,
    # запущенная из подкаталога, иначе не узнала бы в списке собственную запись.
    if ($here -eq (Get-FolderKey -Path (Get-TreeRoot))) { $marks.Add('это вы') }
    # «Папки нет» говорим только тогда, когда путь при этом ДОСТИЖИМ. Отвалившийся диск и пропавшая
    # сетевая шара отвечают тем же отказом, что и удалённая папка, — а это «не видно», а не «нет»,
    # и выдавать одно за другое нельзя: на этой разнице стоит решение, можно ли трогать чужую
    # запись. Не знаем — молчим.
    $state = Get-PathState -Path $here
    if ($state.Kind -eq 'none' -and (Test-PathReachable -Path $here)) { $marks.Add('папки нет') }
    # «Давно не отмечалась» — отметка старше ОБЩЕГО порога живости. Про открытую запись то же самое
    # говорит и её состояние («молчит»), но метка стоит здесь намеренно: улики отказа человек
    # читает возле папки, а не собирает их по разным углам строки.
    if (-not $Claim.Closed) {
        $seen = [datetime]::MinValue
        $raw = [string]$Claim.Record.seen_at
        $known = $raw -and [datetime]::TryParse($raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$seen)
        if (-not $known -or ((Get-Date) - $seen).TotalHours -ge (Get-AliveHours)) {
            $marks.Add('давно не отмечалась')
        }
    }
    return @($marks)
}

function Get-DoubledAddresses {
    param($Claims)
    # Адреса, которые ведут сразу несколько незакрытых записей. Это наследие дефекта «переезд
    # раздваивает адрес»: правку выкатывают на грязный реестр, и там такие пары уже лежат.
    #
    # ‼️ Сказать об этом надо ВСЛУХ и отдельной строкой. Пока задвоение видно только тем, что в
    # списке две похожие строки, выбор «какую из двух считать настоящей» делает не человек, а
    # порядок описи каталога — и делает его приём находки, молча.
    #
    # Перенесённая запись задвоением не считается: адрес у неё забран явным ключом, ведущая на нём
    # одна, и кричать тут не о чем.
    $seen = [ordered]@{}
    foreach ($entry in @($Claims)) {
        if ($entry.Closed) { continue }
        if (-not $entry.WaveKey -or -not $entry.StreamKey) { continue }
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        if (-not $seen.Contains($address)) { $seen[$address] = [System.Collections.Generic.List[object]]::new() }
        $seen[$address].Add($entry)
    }
    $doubled = [System.Collections.Generic.List[object]]::new()
    foreach ($address in $seen.Keys) {
        if ($seen[$address].Count -lt 2) { continue }
        $doubled.Add([pscustomobject]@{ Address = $address; Claims = @($seen[$address]) })
    }
    return @($doubled)
}

function Get-ClaimAddressHolder {
    param($Claim)
    # Куда адрес уехал В КОНЦЕ КОНЦОВ: цепочку переездов проходит разбор реестра (там для этого
    # есть все записи разом) и кладёт конец цепочки в саму запись. Цепочка A→B→C законна, и
    # говорить потерпевшей A «перенесён в B» — полуправда: в B этого потока уже нет.
    if ($Claim.AddressChainEnd) { return $Claim.AddressChainEnd }
    # ‼️ Запасной ход — по одним рёбрам погашения, для записей, собранных не разбором реестра.
    # Он обрывается на первом же звене, чья запись сменила адрес, поэтому основной путь выше.
    $seen = @{}
    $at = $Claim
    while ($at.TakenBy -and -not $seen.ContainsKey([string]$at.File)) {
        $seen[[string]$at.File] = $true
        $at = $at.TakenBy
    }
    return $at
}

function Get-ClaimAddressFate {
    param($Claim)
    # Судьба адреса погашенной записи — ОДИН ответ на весь комплект: показ, сдача, сторож доставки
    # и приём находки говорят о ней одними словами и по одному признаку.
    #
    # ‼️ «Перенесён в папку X» годится ровно тогда, когда X ВЕДЁТ ТОТ ЖЕ АДРЕС. Забравшая папка
    # могла с тех пор сдать поток или взяться за следующий — тогда «перенесён в X» врёт дважды:
    # человек пойдёт слать находку в X, а там этого адреса не ведёт никто. Такому адресу ведущей
    # записи не остаётся вовсе, и это ПРАВИЛЬНЫЙ исход (поток переехал и закончился), но он обязан
    # быть виден, а не выглядеть переносом в живую вкладку.
    # Ведущего ищем ПО АДРЕСУ, а не по ребру: в цепочке переездов эту запись гасит средняя папка,
    # а адрес ведёт последняя — и человеку нужна именно она.
    if ($Claim.AddressLedBy) {
        return [pscustomobject]@{
            Holder   = $Claim.AddressLedBy
            StillLed = $true
            Text     = "перенесён в $([string]$Claim.AddressLedBy.Record.worktree)"
        }
    }
    $holder = Get-ClaimAddressHolder -Claim $Claim
    if (-not $holder -or $holder -eq $Claim) {
        return [pscustomobject]@{
            Holder = $null; StillLed = $false; Text = 'адрес забран другой рабочей папкой'
        }
    }
    $folder = [string]$holder.Record.worktree
    $text = if ($holder.State -eq 'сдан') {
        "адрес забрала папка $folder, и тот поток уже сдан"
    } elseif ($holder.WaveKey -and $holder.StreamKey) {
        "адрес забрала папка $folder, но она ведёт уже другой поток ($($holder.WaveKey)/$($holder.StreamKey))"
    } else {
        "адрес забрала папка $folder"
    }
    return [pscustomobject]@{ Holder = $holder; StillLed = $false; Text = $text }
}

function Get-ClaimTakenAwayText {
    param($Claim)
    # Одна строка о судьбе адреса — для тех мест, где развилка «ведут его ещё или уже нет» не
    # меняет остального текста.
    return (Get-ClaimAddressFate -Claim $Claim).Text
}

function Get-LeaderlessAddresses {
    param($Claims)
    # Адреса, у которых есть незакрытые в СВОИХ ФАЙЛАХ записи, а ведущей — ни одной. Так выглядит
    # вкладка, которой снаружи не существует: её файл заявки открыт, она считает, что ведёт поток,
    # а находку по этому адресу приём не примет и сторож доставки не принесёт.
    #
    # ‼️ Само по себе это не порча, а законный конец истории адреса: поток переехал и закончился.
    # Но молчать о нём нельзя ровно по той же причине, по какой не молчат о задвоенном адресе:
    # выбор «жива ли эта вкладка» человек делать не может, пока ему об этом не сказали.
    $records = @($Claims | Where-Object { $_.WaveKey -and $_.StreamKey })
    $byAddress = [ordered]@{}
    foreach ($entry in $records) {
        $address = "$($entry.WaveKey)/$($entry.StreamKey)"
        if (-not $byAddress.Contains($address)) {
            $byAddress[$address] = [System.Collections.Generic.List[object]]::new()
        }
        $byAddress[$address].Add($entry)
    }
    $found = [System.Collections.Generic.List[object]]::new()
    foreach ($address in $byAddress.Keys) {
        $group = @($byAddress[$address])
        if (@($group | Where-Object { -not $_.Closed }).Count -gt 0) { continue }
        # Сданная запись вопросов не вызывает: поток закончен, и спрашивать о нём некому. Ищем
        # именно ту, чей файл открыт, — за ней стоит вкладка, считающая себя ведущей.
        $orphans = @($group | Where-Object { $_.State -ne 'сдан' })
        if ($orphans.Count -eq 0) { continue }
        $found.Add([pscustomobject]@{ Address = $address; Claims = $orphans })
    }
    return @($found)
}

function Format-ClaimLine {
    param($Claim)
    $record = $Claim.Record
    $name = if ($record.name) { " «$($record.name)»" } else { '' }
    $tasks = if ($record.tasks) { ", задачи $($record.tasks)" } else { '' }
    # ‼️ Запомненные имена показываем. Поток отзывается не только на то имя, что носит сейчас, — и
    # пока эти имена не было видно нигде, человек не мог заметить ни того, что находка уйдёт по
    # старому адресу, ни того, что имя у потока отняли. Отнятые метим отдельно: по ним находка НЕ
    # придёт, потому что имя носит или помнит кто-то ещё.
    $kept = @($Claim.Remembered | Where-Object { $_ -in $Claim.Keys })
    $lost = @($Claim.Silenced)
    $memory = ''
    if ($kept.Count -gt 0) { $memory += ", помнит имена: $($kept -join ', ')" }
    if ($lost.Count -gt 0) { $memory += ", имена отняты: $($lost -join ', ')" }
    # Рабочая папка — рядом с веткой, а не в конце строки: оба отказа называют человеку именно её,
    # и найти её глазами он должен там же, где смотрел на ветку.
    $folder = if ($record.worktree) { ", папка $($record.worktree)" } else { '' }
    foreach ($mark in (Get-ClaimFolderMarks -Claim $Claim)) { $folder += ", $mark" }
    # ‼️ У перенесённой записи называем, КУДА уехал адрес. Без этого «перенесён» — тупик: человек
    # видит, что запись погашена, но не знает, в какой папке искать поток и кому слать находку.
    # Забравшая папка могла уехать дальше, сдаться или взяться за следующий поток — тогда и говорим
    # это прямо, а не выдаём за перенос в живую вкладку того же адреса.
    $state = if ($Claim.TakenBy -and $Claim.Superseded) {
        Get-ClaimTakenAwayText -Claim $Claim
    } else {
        $Claim.State
    }
    return "  $($record.wave)/$($record.stream)$name$tasks — $state (отметка $(Format-Stamp -Raw $record.seen_at), ветка $($record.branch)$folder$memory)"
}
