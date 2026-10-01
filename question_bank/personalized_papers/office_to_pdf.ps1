param(
    [string]$InputPath,
    [string]$OutputPath,
    [switch]$BatchMode
)

$ErrorActionPreference = 'Stop'
function Convert-OfficeDocument($Application, [string]$SourcePath, [string]$DestinationPath) {
    $source = [IO.Path]::GetFullPath($SourcePath)
    $destination = [IO.Path]::GetFullPath($DestinationPath)
    if (-not [IO.File]::Exists($source) -or [IO.Path]::GetExtension($source).ToLowerInvariant() -ne '.docx') {
        throw 'Input must be an existing DOCX file'
    }
    if ([IO.Path]::GetExtension($destination).ToLowerInvariant() -ne '.pdf') {
        throw 'Output must be a PDF file'
    }
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
    $document = $null
    try {
        $document = $Application.Documents.Open($source, $false, $true, $false)
        try { $document.ExportAsFixedFormat($destination, 17) }
        catch { $document.SaveAs2($destination, 17) }
        if (-not [IO.File]::Exists($destination) -or (Get-Item -LiteralPath $destination).Length -le 0) {
            throw 'Office conversion produced no PDF'
        }
    }
    finally {
        if ($null -ne $document) {
            try { $document.Close($false) } catch {}
            try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) | Out-Null } catch {}
        }
    }
}

if ($BatchMode) {
    [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $application = $null
    try {
        while ($null -ne ($line = [Console]::ReadLine()) -and $line.Length -gt 0) {
            try {
                $request = ConvertFrom-Json -InputObject $line
                if ($null -eq $application) {
                    foreach ($programId in @('kwps.Application', 'wps.Application', 'Word.Application')) {
                        try { $application = New-Object -ComObject $programId; break } catch {}
                    }
                    if ($null -eq $application) { throw 'No compatible Office application' }
                    $application.Visible = $false
                    try { $application.DisplayAlerts = 0 } catch {}
                }
                Convert-OfficeDocument $application $request.input $request.output
                [Console]::WriteLine('P4PDF:{"ok":true}')
            }
            catch {
                [Console]::WriteLine('P4PDF:{"ok":false}')
                break
            }
        }
    }
    finally {
        if ($null -ne $application) {
            try { $application.Quit() } catch {}
            try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) | Out-Null } catch {}
        }
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
    exit 0
}

$source = [IO.Path]::GetFullPath($InputPath)
$destination = [IO.Path]::GetFullPath($OutputPath)
if (-not [IO.File]::Exists($source)) {
    throw "Input DOCX does not exist"
}
if ([IO.Path]::GetExtension($source).ToLowerInvariant() -ne '.docx') {
    throw "Input must be a DOCX file"
}
if ([IO.Path]::GetExtension($destination).ToLowerInvariant() -ne '.pdf') {
    throw "Output must be a PDF file"
}
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null

$lastError = $null
foreach ($programId in @('kwps.Application', 'wps.Application', 'Word.Application')) {
    $application = $null
    $document = $null
    try {
        $application = New-Object -ComObject $programId
        $application.Visible = $false
        try { $application.DisplayAlerts = 0 } catch {}
        Convert-OfficeDocument $application $source $destination
        exit 0
    }
    catch {
        $lastError = $_.Exception.Message
    }
    finally {
        if ($null -ne $document) {
            try { $document.Close($false) } catch {}
            try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) | Out-Null } catch {}
        }
        if ($null -ne $application) {
            try { $application.Quit() } catch {}
            try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) | Out-Null } catch {}
        }
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

throw "No compatible WPS/Word PDF converter was available: $lastError"
