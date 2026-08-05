[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [ValidateRange(1024, 65535)]
    [int]$Port = 8035
)

$ErrorActionPreference = "Stop"
$LoopbackAddress = "127.0.0.1"

function Resolve-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    # A quoted Windows command-line argument may retain an outer quote when
    # its original value ends with a backslash. Quotes cannot be part of a
    # valid Windows path, so remove only outer quotes before normalization.
    $candidatePath = $Path.Trim().Trim([char]34)
    if ([string]::IsNullOrWhiteSpace($candidatePath)) {
        throw [System.ArgumentException]::new("项目路径不能为空。")
    }

    return [System.IO.Path]::GetFullPath($candidatePath).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Get-LoopbackListenerProcessIds {
    param([Parameter(Mandatory = $true)][int]$TargetPort)

    try {
        $connections = @(
            Get-NetTCPConnection `
                -LocalAddress $LoopbackAddress `
                -LocalPort $TargetPort `
                -State Listen `
                -ErrorAction Stop
        )
        return @(
            $connections |
                ForEach-Object { [int]$_.OwningProcess } |
                Sort-Object -Unique
        )
    }
    catch {
        # Older Windows installations may not expose Get-NetTCPConnection.
        # netstat is read-only and is available on supported Windows versions.
        $processIds = New-Object System.Collections.Generic.List[int]
        $netstatLines = & "$env:SystemRoot\System32\netstat.exe" -ano -p TCP 2>$null
        foreach ($line in $netstatLines) {
            if (
                $line -match "^\s*TCP\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s*$" -and
                $matches[1] -eq "${LoopbackAddress}:$TargetPort" -and
                $matches[3] -eq "LISTENING"
            ) {
                $processIds.Add([int]$matches[4])
            }
        }
        return @($processIds | Sort-Object -Unique)
    }
}

function Get-ProcessRecord {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    try {
        return Get-CimInstance `
            -ClassName Win32_Process `
            -Filter "ProcessId = $ProcessId" `
            -ErrorAction Stop
    }
    catch {
        throw [System.InvalidOperationException]::new(
            "Windows 不允许读取该进程的身份信息。为避免误关其他程序，本次没有关闭任何进程。请使用启动该系统的同一 Windows 账户再试。",
            $_.Exception
        )
    }
}

function Test-ServiceLauncherProcess {
    param(
        [Parameter(Mandatory = $true)]$ProcessRecord,
        [Parameter(Mandatory = $true)][string]$ExpectedRunner,
        [Parameter(Mandatory = $true)][string[]]$ExpectedPythonExecutables,
        [Parameter(Mandatory = $true)][int]$TargetPort
    )

    $commandLine = [string]$ProcessRecord.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }

    $normalizedCommand = $commandLine.Replace("/", "\")
    if ($normalizedCommand.IndexOf($ExpectedRunner, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $false
    }
    if ($commandLine -notmatch "(?i)(?:^|\s)backend\.api\.launcher(?:\s|$)") {
        return $false
    }
    if ($commandLine -notmatch "(?i)(?:^|\s)--host(?:\s+|=)127\.0\.0\.1(?:\s|$)") {
        return $false
    }
    if ($commandLine -notmatch "(?i)(?:^|\s)--port(?:\s+|=)$TargetPort(?:\s|$)") {
        return $false
    }

    $executablePath = [string]$ProcessRecord.ExecutablePath
    if (-not [string]::IsNullOrWhiteSpace($executablePath)) {
        $resolvedExecutable = Resolve-NormalizedPath -Path $executablePath
        $matchesExpectedPython = $false
        foreach ($candidate in $ExpectedPythonExecutables) {
            if ($resolvedExecutable.Equals($candidate, [System.StringComparison]::OrdinalIgnoreCase)) {
                $matchesExpectedPython = $true
                break
            }
        }
        if (-not $matchesExpectedPython) {
            return $false
        }
    }

    return $true
}

try {
    $resolvedRoot = Resolve-NormalizedPath -Path $ProjectRoot
    $expectedRunner = Resolve-NormalizedPath -Path (
        Join-Path $resolvedRoot "tools\run_project_module.py"
    )
    $pythonCandidates = @(
        (Join-Path $resolvedRoot "runtime\python\python.exe"),
        (Join-Path $resolvedRoot "..\..\runtime\python\python.exe")
    ) |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        ForEach-Object { Resolve-NormalizedPath -Path $_ } |
        Sort-Object -Unique

    if (-not (Test-Path -LiteralPath $expectedRunner -PathType Leaf)) {
        Write-Host "找不到当前 AI 阅卷系统的启动入口，本次没有关闭任何进程。" -ForegroundColor Red
        exit 2
    }
    if ($pythonCandidates.Count -eq 0) {
        Write-Host "找不到当前 AI 阅卷系统使用的 Python，本次没有关闭任何进程。" -ForegroundColor Red
        exit 2
    }

    $listenerProcessIds = @(Get-LoopbackListenerProcessIds -TargetPort $Port)
    if ($listenerProcessIds.Count -eq 0) {
        Write-Host "AI 阅卷系统当前没有运行（127.0.0.1:$Port 未被监听）。" -ForegroundColor Green
        Write-Host "无需重复关闭。"
        exit 0
    }
    if ($listenerProcessIds.Count -ne 1) {
        Write-Host "检测到端口 $Port 的监听状态异常，无法唯一确定服务进程。" -ForegroundColor Red
        Write-Host "为避免误关其他程序，本次没有关闭任何进程。"
        exit 3
    }

    $ownerProcessId = [int]$listenerProcessIds[0]
    $firstRecord = Get-ProcessRecord -ProcessId $ownerProcessId
    if ($null -eq $firstRecord) {
        Write-Host "服务刚刚已经退出，无需再次关闭。" -ForegroundColor Green
        exit 0
    }
    if (
        -not (Test-ServiceLauncherProcess `
            -ProcessRecord $firstRecord `
            -ExpectedRunner $expectedRunner `
            -ExpectedPythonExecutables $pythonCandidates `
            -TargetPort $Port)
    ) {
        Write-Host "端口 $Port 正被其他程序占用，不是当前工作区启动的 AI 阅卷系统。" -ForegroundColor Yellow
        Write-Host "为避免误关其他程序，本次没有关闭任何进程。"
        exit 3
    }

    # Check the listener and process identity again immediately before stopping.
    # This protects against the original process exiting and the PID/port being reused.
    $currentListenerProcessIds = @(Get-LoopbackListenerProcessIds -TargetPort $Port)
    if (
        $currentListenerProcessIds.Count -eq 0 -or
        $currentListenerProcessIds -notcontains $ownerProcessId
    ) {
        Write-Host "服务刚刚已经退出，无需再次关闭。" -ForegroundColor Green
        exit 0
    }
    if ($currentListenerProcessIds.Count -ne 1) {
        Write-Host "关闭前端口监听发生变化。为避免误关其他程序，本次没有执行关闭。" -ForegroundColor Yellow
        exit 3
    }

    $secondRecord = Get-ProcessRecord -ProcessId $ownerProcessId
    if ($null -eq $secondRecord) {
        Write-Host "服务刚刚已经退出，无需再次关闭。" -ForegroundColor Green
        exit 0
    }
    if (
        [string]$secondRecord.CreationDate -ne [string]$firstRecord.CreationDate -or
        [string]$secondRecord.CommandLine -ne [string]$firstRecord.CommandLine -or
        -not (Test-ServiceLauncherProcess `
            -ProcessRecord $secondRecord `
            -ExpectedRunner $expectedRunner `
            -ExpectedPythonExecutables $pythonCandidates `
            -TargetPort $Port)
    ) {
        Write-Host "关闭前进程身份发生变化。为避免误关其他程序，本次没有执行关闭。" -ForegroundColor Yellow
        exit 3
    }

    try {
        Stop-Process -Id $ownerProcessId -ErrorAction Stop
    }
    catch {
        $remaining = @(Get-LoopbackListenerProcessIds -TargetPort $Port)
        if ($remaining.Count -eq 0) {
            Write-Host "AI 阅卷系统已经关闭。" -ForegroundColor Green
            exit 0
        }
        Write-Host "Windows 拒绝关闭 AI 阅卷系统，本次没有影响其他程序。" -ForegroundColor Red
        Write-Host "请使用启动该系统的同一 Windows 账户重试；仍失败时，把此窗口内容发给 Codex。"
        exit 4
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        Start-Sleep -Milliseconds 200
        $remaining = @(Get-LoopbackListenerProcessIds -TargetPort $Port)
        if ($remaining.Count -eq 0) {
            Write-Host "AI 阅卷系统已安全关闭，端口 $Port 已释放。" -ForegroundColor Green
            exit 0
        }
        if ($remaining -notcontains $ownerProcessId) {
            Write-Host "原 AI 阅卷系统已经关闭，但端口 $Port 随即被另一个程序占用。" -ForegroundColor Yellow
            Write-Host "本脚本不会关闭后来占用端口的程序。"
            exit 0
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    Write-Host "已发出关闭命令，但端口 $Port 尚未释放。" -ForegroundColor Red
    Write-Host "请稍候再次运行“关闭系统.bat”；不要连续启动新的系统窗口。"
    exit 5
}
catch {
    Write-Host "关闭操作未能完成，本次没有主动关闭任何其他程序。" -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 6
}
