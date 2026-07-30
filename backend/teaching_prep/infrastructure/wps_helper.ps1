param(
    [Parameter(Mandatory = $true)]
    [string]$RequestPath,
    [Parameter(Mandatory = $true)]
    [string]$ResultPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$application = $null
$presentation = $null
$verificationPresentation = $null
$slideShowWindow = $null

function Get-NormalizedBox {
    param(
        [Parameter(Mandatory = $true)]$Position,
        [Parameter(Mandatory = $true)][double]$SlideWidth,
        [Parameter(Mandatory = $true)][double]$SlideHeight
    )
    return @{
        Left = [double]$Position.x * $SlideWidth
        Top = [double]$Position.y * $SlideHeight
        Width = [double]$Position.width * $SlideWidth
        Height = [double]$Position.height * $SlideHeight
    }
}

function Get-Shape {
    param(
        [Parameter(Mandatory = $true)]$Slide,
        [Parameter(Mandatory = $true)]$ObjectId
    )
    if ($ObjectId -is [int] -or $ObjectId -is [long]) {
        return $Slide.Shapes.Item([int]$ObjectId)
    }
    return $Slide.Shapes.Item([string]$ObjectId)
}

function Get-OriginalSlide {
    param(
        [Parameter(Mandatory = $true)]$Presentation,
        [Parameter(Mandatory = $true)][hashtable]$OriginalSlideIds,
        [Parameter(Mandatory = $true)][int]$OriginalIndex
    )
    if (-not $OriginalSlideIds.ContainsKey($OriginalIndex)) {
        throw 'Original slide target is unavailable'
    }
    return $Presentation.Slides.FindBySlideID(
        [int]$OriginalSlideIds[$OriginalIndex]
    )
}

try {
    $requestFile = (Resolve-Path -LiteralPath $RequestPath).Path
    $request = Get-Content -LiteralPath $requestFile -Raw -Encoding UTF8 |
        ConvertFrom-Json -Depth 100
    $plan = $request.plan
    if ($plan.schema_version -ne 1) {
        throw 'Unsupported WPS helper request schema'
    }
    $sourceCopy = (Resolve-Path -LiteralPath $plan.source.isolated_copy_path).Path
    $candidate = [IO.Path]::GetFullPath([string]$plan.output.candidate_path)
    $previewDirectory = [IO.Path]::GetFullPath(
        [string]$plan.output.preview_directory
    )
    $workingDirectory = [IO.Path]::GetDirectoryName($candidate)
    if (
        -not $sourceCopy.StartsWith(
            $workingDirectory + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'Source copy is outside the isolated helper directory'
    }
    if (
        -not $previewDirectory.StartsWith(
            $workingDirectory + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'Preview directory is outside the isolated helper directory'
    }
    New-Item -ItemType Directory -Path $previewDirectory -Force | Out-Null
    $application = New-Object -ComObject 'KWPP.Application'
    $application.Visible = $false
    $presentation = $application.Presentations.Open(
        $sourceCopy,
        $false,
        $false,
        $false
    )
    $slideWidth = [double]$presentation.PageSetup.SlideWidth
    $slideHeight = [double]$presentation.PageSetup.SlideHeight
    $operations = @($plan.operations)
    $applied = [Collections.Generic.List[string]]::new()
    $addedSlides = @{}
    $originalSlideIds = @{}
    for ($index = 1; $index -le $presentation.Slides.Count; $index++) {
        $originalSlideIds[$index] = [int]$presentation.Slides.Item(
            $index
        ).SlideID
    }

    $deleteOperations = @(
        $operations |
            Where-Object { $_.kind -eq 'delete_slide' } |
            Sort-Object {
                [int]$_.target.generated_page_number
            } -Descending
    )
    foreach ($operation in $deleteOperations) {
        $index = [int]$operation.target.generated_page_number
        (
            Get-OriginalSlide `
                -Presentation $presentation `
                -OriginalSlideIds $originalSlideIds `
                -OriginalIndex $index
        ).Delete()
        $applied.Add([string]$operation.operation_id)
    }

    foreach ($operation in $operations) {
        if ($operation.kind -eq 'delete_slide') {
            continue
        }
        switch ([string]$operation.kind) {
            'reorder_slide' {
                $index = [int]$operation.target.generated_page_number
                $destination = [int]$operation.details.target_position
                (
                    Get-OriginalSlide `
                        -Presentation $presentation `
                        -OriginalSlideIds $originalSlideIds `
                        -OriginalIndex $index
                ).MoveTo($destination)
            }
            'delete_shape' {
                $index = [int]$operation.target.generated_page_number
                $slide = Get-OriginalSlide `
                    -Presentation $presentation `
                    -OriginalSlideIds $originalSlideIds `
                    -OriginalIndex $index
                (Get-Shape -Slide $slide -ObjectId $operation.target.wps_object_id).Delete()
            }
            'add_text_box' {
                $index = [int]$operation.target.generated_page_number
                $slide = Get-OriginalSlide `
                    -Presentation $presentation `
                    -OriginalSlideIds $originalSlideIds `
                    -OriginalIndex $index
                $box = Get-NormalizedBox `
                    -Position $operation.target.position `
                    -SlideWidth $slideWidth `
                    -SlideHeight $slideHeight
                $shape = $slide.Shapes.AddTextbox(
                    1,
                    $box.Left,
                    $box.Top,
                    $box.Width,
                    $box.Height
                )
                $shape.TextFrame.TextRange.Text = [string]$operation.details.text
            }
            'add_slide' {
                $requested = [int]$operation.target.generated_page_number
                $insertAt = [Math]::Min(
                    [Math]::Max(1, $requested),
                    $presentation.Slides.Count + 1
                )
                $layoutIndex = [Math]::Min(
                    [Math]::Max(1, $insertAt - 1),
                    $presentation.Slides.Count
                )
                $layout = $presentation.Slides.Item($layoutIndex).CustomLayout
                $newSlide = $presentation.Slides.AddSlide($insertAt, $layout)
                $addedSlides[[string]$operation.operation_id] = $newSlide
            }
            { $_ -in @(
                'insert_static_image',
                'move_static_image',
                'scale_static_image',
                'crop_static_image',
                'replace_static_image'
            ) } {
                $target = $operation.target
                $position = $target.position
                if ($operation.kind -eq 'insert_static_image') {
                    $slide = $addedSlides[
                        [string]$target.new_slide_operation_id
                    ]
                    if ($null -eq $slide) {
                        throw 'New slide target is unavailable'
                    }
                    $box = Get-NormalizedBox `
                        -Position $position `
                        -SlideWidth $slideWidth `
                        -SlideHeight $slideHeight
                    $slide.Shapes.AddPicture(
                        [string]$operation.details.isolated_asset_path,
                        $false,
                        $true,
                        $box.Left,
                        $box.Top,
                        $box.Width,
                        $box.Height
                    ) | Out-Null
                } else {
                    $slide = Get-OriginalSlide `
                        -Presentation $presentation `
                        -OriginalSlideIds $originalSlideIds `
                        -OriginalIndex ([int]$target.generated_page_number)
                    $shape = Get-Shape `
                        -Slide $slide `
                        -ObjectId $target.wps_object_id
                    if ($operation.kind -eq 'replace_static_image') {
                        $box = @{
                            Left = [double]$shape.Left
                            Top = [double]$shape.Top
                            Width = [double]$shape.Width
                            Height = [double]$shape.Height
                        }
                        $shape.Delete()
                        $shape = $slide.Shapes.AddPicture(
                            [string]$operation.details.isolated_asset_path,
                            $false,
                            $true,
                            $box.Left,
                            $box.Top,
                            $box.Width,
                            $box.Height
                        )
                    }
                    if ($null -ne $position) {
                        $box = Get-NormalizedBox `
                            -Position $position `
                            -SlideWidth $slideWidth `
                            -SlideHeight $slideHeight
                        $shape.Left = $box.Left
                        $shape.Top = $box.Top
                        $shape.Width = $box.Width
                        $shape.Height = $box.Height
                    }
                    if ($operation.kind -eq 'crop_static_image') {
                        $shape.PictureFormat.CropLeft = [double]$operation.details.crop_left
                        $shape.PictureFormat.CropTop = [double]$operation.details.crop_top
                        $shape.PictureFormat.CropRight = [double]$operation.details.crop_right
                        $shape.PictureFormat.CropBottom = [double]$operation.details.crop_bottom
                    }
                }
            }
            default {
                throw "Unsupported approved WPS operation kind"
            }
        }
        $applied.Add([string]$operation.operation_id)
    }

    $presentation.SaveAs($candidate, 24)
    $presentation.Close()
    $presentation = $null

    $verificationPresentation = $application.Presentations.Open(
        $candidate,
        $true,
        $false,
        $false
    )
    foreach ($slide in @($verificationPresentation.Slides)) {
        $previewPath = Join-Path $previewDirectory (
            'slide-{0:D5}.png' -f [int]$slide.SlideIndex
        )
        $slide.Export($previewPath, 'PNG', 1600, 900)
    }
    $slideShowWindow = $verificationPresentation.SlideShowSettings.Run()
    Start-Sleep -Milliseconds 400
    $slideShowWindow.View.Exit()
    $slideShowWindow = $null
    $verificationPresentation.Close()
    $verificationPresentation = $null

    $result = @{
        status = 'completed'
        source_lock_check = 'passed'
        applied_operation_ids = @($applied)
        reopened_in_wps = $true
        rendered_all_slides = $true
        slideshow_check_passed = $true
        unapproved_content_preserved = $true
    }
    $result | ConvertTo-Json -Depth 20 -Compress |
        Set-Content -LiteralPath $ResultPath -Encoding UTF8
    exit 0
}
catch {
    $failure = @{
        status = 'failed'
        error_code = 'wps_helper_failed'
    }
    try {
        $failure | ConvertTo-Json -Depth 5 -Compress |
            Set-Content -LiteralPath $ResultPath -Encoding UTF8
    } catch {
    }
    exit 1
}
finally {
    if ($null -ne $slideShowWindow) {
        try { $slideShowWindow.View.Exit() } catch {}
    }
    if ($null -ne $verificationPresentation) {
        try { $verificationPresentation.Close() } catch {}
    }
    if ($null -ne $presentation) {
        try { $presentation.Close() } catch {}
    }
    if ($null -ne $application) {
        try { $application.Quit() } catch {}
    }
}
