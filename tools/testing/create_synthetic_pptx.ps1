param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$application = $null
$presentation = $null

function Add-SyntheticText {
    param(
        [Parameter(Mandatory = $true)]$Slide,
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][double]$Left,
        [Parameter(Mandatory = $true)][double]$Top,
        [Parameter(Mandatory = $true)][double]$Width,
        [Parameter(Mandatory = $true)][double]$Height,
        [Parameter(Mandatory = $true)][int]$FontSize
    )
    $shape = $Slide.Shapes.AddTextbox(
        1,
        $Left,
        $Top,
        $Width,
        $Height
    )
    $shape.TextFrame.TextRange.Text = $Text
    $shape.TextFrame.TextRange.Font.Size = $FontSize
    return $shape
}
try {
    $target = [IO.Path]::GetFullPath($OutputPath)
    if ([IO.File]::Exists($target)) {
        throw 'Synthetic PPTX target already exists'
    }
    $parent = [IO.Path]::GetDirectoryName($target)
    [IO.Directory]::CreateDirectory($parent) | Out-Null

    $application = New-Object -ComObject 'KWPP.Application'
    try {
        $application.Visible = $false
    }
    catch [System.Runtime.InteropServices.COMException] {
        # Some WPS builds reject visibility changes before the first deck opens.
    }
    $presentation = $application.Presentations.Add()

    $first = $presentation.Slides.Add(1, 12)
    $banner = $first.Shapes.AddShape(1, 35, 35, 620, 95)
    $banner.Fill.ForeColor.RGB = 12611584
    $banner.Line.Visible = $false
    Add-SyntheticText `
        -Slide $first `
        -Text 'SYNTHETIC SLIDE 1 - NUMBER LINE' `
        -Left 60 -Top 55 -Width 570 -Height 55 -FontSize 28 | Out-Null
    Add-SyntheticText `
        -Slide $first `
        -Text 'Acceptance fixture: original WPS-rendered pixels must be visible.' `
        -Left 65 -Top 175 -Width 610 -Height 90 -FontSize 20 | Out-Null
    $line = $first.Shapes.AddShape(1, 105, 330, 500, 8)
    $line.Fill.ForeColor.RGB = 2236962
    $line.Line.Visible = $false

    $second = $presentation.Slides.Add(2, 12)
    $panel = $second.Shapes.AddShape(1, 55, 45, 600, 390)
    $panel.Fill.ForeColor.RGB = 16768196
    $panel.Line.ForeColor.RGB = 33023
    Add-SyntheticText `
        -Slide $second `
        -Text 'SYNTHETIC SLIDE 2 - WORKED EXAMPLE' `
        -Left 85 -Top 80 -Width 540 -Height 65 -FontSize 27 | Out-Null
    Add-SyntheticText `
        -Slide $second `
        -Text 'x + 3 = 7    therefore    x = 4' `
        -Left 120 -Top 205 -Width 480 -Height 100 -FontSize 30 | Out-Null
    Add-SyntheticText `
        -Slide $second `
        -Text 'Teacher review fixture - no real lesson content.' `
        -Left 150 -Top 350 -Width 420 -Height 55 -FontSize 16 | Out-Null

    # 24 is the Open XML presentation format used by WPS/PowerPoint.
    $presentation.SaveAs($target, 24)
    $presentation.Close()
    $presentation = $null
    try {
        $application.Quit()
    }
    catch [System.Runtime.InteropServices.COMException] {
    }
    $application = $null

    if (-not [IO.File]::Exists($target)) {
        throw 'WPS did not create the synthetic PPTX'
    }
    if ((Get-Item -LiteralPath $target).Length -le 0) {
        throw 'WPS created an empty synthetic PPTX'
    }
}
finally {
    if ($null -ne $presentation) {
        try { $presentation.Close() } catch {}
    }
    if ($null -ne $application) {
        try { $application.Quit() } catch {}
    }
}
