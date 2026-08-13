param(
    [Parameter(Mandatory=$true)]
    [string]$InputPath,
    [Parameter(Mandatory=$true)]
    [string]$OutputPath
)

try {
    try {
        $word = New-Object -ComObject KWPS.Application
    } catch {
        $word = New-Object -ComObject Word.Application
    }
    $word.Visible = $true
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($InputPath, $true, $true) # ConfirmConversions=True, ReadOnly=True
    try {
        $doc.ExportAsFixedFormat($OutputPath, 17) # 17 is wdExportFormatPDF
    } catch {
        $doc.SaveAs([ref]$OutputPath, [ref]17) # fallback
    }
    $doc.Close([ref]$false)
    $word.Quit()
    exit 0
} catch {
    Write-Error $_.Exception.Message
    if ($word -ne $null) {
        try { $word.Quit() } catch {}
    }
    exit 1
}
