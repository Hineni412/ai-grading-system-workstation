param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
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
        $document = $application.Documents.Open(
            $source,
            $false,
            $true,
            $false
        )
        try {
            $document.ExportAsFixedFormat($destination, 17)
        }
        catch {
            $document.SaveAs2($destination, 17)
        }
        if ([IO.File]::Exists($destination) -and
            (Get-Item -LiteralPath $destination).Length -gt 0) {
            exit 0
        }
        throw "Office conversion produced no PDF"
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
