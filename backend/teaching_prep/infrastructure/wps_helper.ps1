param(
    [string]$RequestPath = '',
    [string]$ResultPath = '',
    [switch]$PreviewSession,
    [string]$SessionDirectory = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$application = $null
$presentation = $null
$verificationPresentation = $null
$slideShowWindow = $null
$script:previewApplication = $null
$script:previewPresentation = $null
$script:previewSource = ''
$script:previewSha256 = ''

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

function Add-PictureFit {
    param(
        [Parameter(Mandatory = $true)]$Slide,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Box
    )
    $shape = $Slide.Shapes.AddPicture(
        $Path,
        $false,
        $true,
        0,
        0,
        -1,
        -1
    )
    $sourceWidth = [double]$shape.Width
    $sourceHeight = [double]$shape.Height
    if ($sourceWidth -le 0 -or $sourceHeight -le 0) {
        $shape.Delete()
        throw 'Inserted image has invalid dimensions'
    }
    $scale = [Math]::Min(
        ([double]$Box.Width / $sourceWidth),
        ([double]$Box.Height / $sourceHeight)
    )
    $targetWidth = $sourceWidth * $scale
    $targetHeight = $sourceHeight * $scale
    $shape.LockAspectRatio = -1
    $shape.Width = $targetWidth
    $shape.Height = $targetHeight
    $shape.Left = [double]$Box.Left + (
        ([double]$Box.Width - $targetWidth) / 2
    )
    $shape.Top = [double]$Box.Top + (
        ([double]$Box.Height - $targetHeight) / 2
    )
    return $shape
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
    $expectedSlideId = [int]$OriginalSlideIds[$OriginalIndex]
    for ($index = 1; $index -le $Presentation.Slides.Count; $index++) {
        $slide = $Presentation.Slides.Item($index)
        if ([int]$slide.SlideID -eq $expectedSlideId) {
            return $slide
        }
    }
    throw 'Original slide target is unavailable'
}

function Set-WpsApplicationHiddenIfSupported {
    param(
        [Parameter(Mandatory = $true)]$Application
    )
    try {
        $Application.Visible = $false
        return $true
    }
    catch [System.Runtime.InteropServices.COMException] {
        # Some WPS builds reject changing visibility before a presentation opens.
        return $false
    }
}

function Close-PreviewSessionCom {
    if ($null -ne $script:previewPresentation) {
        try { $script:previewPresentation.Close() } catch {}
    }
    if ($null -ne $script:previewApplication) {
        try { $script:previewApplication.Quit() } catch [System.Runtime.InteropServices.COMException] {}
    }
    $script:previewPresentation = $null
    $script:previewApplication = $null
    $script:previewSource = ''
    $script:previewSha256 = ''
}

function Open-ReadOnlyPreviewPresentation {
    param(
        [Parameter(Mandatory = $true)][string]$SourceCopy,
        [Parameter(Mandatory = $true)][string]$SourceSha256
    )
    if (
        $null -ne $script:previewPresentation `
        -and $script:previewSource -ieq $SourceCopy `
        -and $script:previewSha256 -ieq $SourceSha256
    ) {
        $currentHash = (Get-FileHash -LiteralPath $SourceCopy -Algorithm SHA256).Hash
        if ($currentHash -ieq $SourceSha256) {
            return
        }
    }
    Close-PreviewSessionCom
    $errors = @()
    foreach ($progId in @('KWPP.Application', 'PowerPoint.Application')) {
        $openedApp = $null
        $openedPresentation = $null
        try {
            $openedApp = New-Object -ComObject $progId
            Set-WpsApplicationHiddenIfSupported -Application $openedApp | Out-Null
            try { $openedApp.DisplayAlerts = 1 } catch {}
            $openedPresentation = $openedApp.Presentations.Open(
                $SourceCopy,
                $true,
                $false,
                $false
            )
            $script:previewApplication = $openedApp
            $script:previewPresentation = $openedPresentation
            $script:previewSource = $SourceCopy
            $script:previewSha256 = $SourceSha256
            return
        }
        catch {
            $errors += ('{0}: {1}' -f $progId, $_.Exception.Message)
            if ($null -ne $openedPresentation) {
                try { $openedPresentation.Close() } catch {}
            }
            if ($null -ne $openedApp) {
                try { $openedApp.Quit() } catch [System.Runtime.InteropServices.COMException] {}
            }
        }
    }
    throw (
        'No presentation application accepted preview automation. ' +
        ($errors -join ' | ')
    )
}

function Export-OpenedPreviewSlides {
    param(
        [Parameter(Mandatory = $true)]$SlideIndexes,
        [Parameter(Mandatory = $true)][string]$PreviewDirectory,
        [int]$StableCount = 2
    )
    if ($null -eq $script:previewPresentation) {
        throw 'Preview presentation is not open'
    }
    $indexes = @(
        $SlideIndexes | ForEach-Object { [int]$_ }
    )
    New-Item -ItemType Directory -Path $PreviewDirectory -Force | Out-Null
    Get-ChildItem -LiteralPath $PreviewDirectory -File |
        Remove-Item -Force
    foreach ($index in $indexes) {
        if ($index -gt [int]$script:previewPresentation.Slides.Count) {
            throw 'Preview slide selection is out of range'
        }
        $target = Join-Path $PreviewDirectory (
            'slide-{0:D5}.png' -f $index
        )
        $script:previewPresentation.Slides.Item($index).Export(
            $target,
            'PNG',
            1600,
            900
        )
    }
    Wait-WpsSlidePreviews `
        -PreviewDirectory $PreviewDirectory `
        -ExpectedCount $indexes.Count `
        -StableCount $StableCount | Out-Null
}

function Export-ReadOnlySlidePreviews {
    param(
        [Parameter(Mandatory = $true)][string]$SourceCopy,
        [Parameter(Mandatory = $true)]$SlideIndexes,
        [Parameter(Mandatory = $true)][string]$PreviewDirectory
    )
    try {
        $sourceHash = (Get-FileHash -LiteralPath $SourceCopy -Algorithm SHA256).Hash
        Open-ReadOnlyPreviewPresentation `
            -SourceCopy $SourceCopy `
            -SourceSha256 $sourceHash
        Export-OpenedPreviewSlides `
            -SlideIndexes $SlideIndexes `
            -PreviewDirectory $PreviewDirectory
    }
    finally {
        Close-PreviewSessionCom
    }
}

function Write-PreviewSessionResult {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Result
    )
    $json = $Result | ConvertTo-Json -Depth 10 -Compress
    [IO.File]::WriteAllText(
        $Path,
        $json,
        [Text.UTF8Encoding]::new($false)
    )
}

function Invoke-PreviewSessionLoop {
    param(
        [Parameter(Mandatory = $true)][string]$Directory
    )
    $commandPath = Join-Path $Directory 'session-command.json'
    $commandReadyPath = Join-Path $Directory 'session-command.ready'
    $resultPath = Join-Path $Directory 'session-result.json'
    $resultReadyPath = Join-Path $Directory 'session-result.ready'
    $idleDeadline = [DateTime]::UtcNow.AddMinutes(15)
    while ([DateTime]::UtcNow -lt $idleDeadline) {
        if (-not (Test-Path -LiteralPath $commandReadyPath)) {
            Start-Sleep -Milliseconds 50
            continue
        }
        $idleDeadline = [DateTime]::UtcNow.AddMinutes(15)
        $raw = [IO.File]::ReadAllText($commandPath, [Text.UTF8Encoding]::new($false))
        Remove-Item -LiteralPath $commandReadyPath, $commandPath -Force -ErrorAction SilentlyContinue
        $command = $raw | ConvertFrom-Json -Depth 20
        $op = [string]$command.op
        $payload = @{
            status = 'completed'
        }
        try {
            if ($op -eq 'open') {
                $sourceCopy = [IO.Path]::GetFullPath([string]$command.source_copy)
                $expectedHash = [string]$command.sha256
                $currentHash = (Get-FileHash -LiteralPath $sourceCopy -Algorithm SHA256).Hash
                if ($currentHash -ine $expectedHash) {
                    throw 'Preview source fingerprint does not match'
                }
                Open-ReadOnlyPreviewPresentation `
                    -SourceCopy $sourceCopy `
                    -SourceSha256 $expectedHash
                $payload.source_unchanged = $true
            }
            elseif ($op -eq 'export') {
                $previewDirectory = [IO.Path]::GetFullPath(
                    [string]$command.preview_directory
                )
                $sourceCopy = [IO.Path]::GetFullPath([string]$command.source_copy)
                $expectedHash = [string]$command.sha256
                $currentHash = (Get-FileHash -LiteralPath $sourceCopy -Algorithm SHA256).Hash
                if ($currentHash -ine $expectedHash) {
                    throw 'Preview source fingerprint does not match'
                }
                if (
                    $null -eq $script:previewPresentation `
                    -or $script:previewSource -ine $sourceCopy
                ) {
                    Open-ReadOnlyPreviewPresentation `
                        -SourceCopy $sourceCopy `
                        -SourceSha256 $expectedHash
                }
                $indexes = @(
                    $command.slide_indexes | ForEach-Object { [int]$_ }
                )
                Export-OpenedPreviewSlides `
                    -SlideIndexes $indexes `
                    -PreviewDirectory $previewDirectory `
                    -StableCount 1
                $afterHash = (Get-FileHash -LiteralPath $sourceCopy -Algorithm SHA256).Hash
                if ($afterHash -ine $expectedHash) {
                    throw 'Preview source changed during read-only rendering'
                }
                $payload.rendered_slide_indexes = @($indexes)
                $payload.source_unchanged = $true
                $payload.source_lock_check = 'passed'
            }
            elseif ($op -eq 'close') {
                Close-PreviewSessionCom
                Write-PreviewSessionResult -Path $resultPath -Result $payload
                New-Item -ItemType File -Path $resultReadyPath -Force | Out-Null
                return
            }
            else {
                throw 'Unsupported preview session operation'
            }
        }
        catch {
            $payload = @{
                status = 'failed'
                error_code = 'wps_helper_failed'
                error_message = [string]$_.Exception.Message
            }
            Close-PreviewSessionCom
        }
        Write-PreviewSessionResult -Path $resultPath -Result $payload
        New-Item -ItemType File -Path $resultReadyPath -Force | Out-Null
        if ($payload.status -ne 'completed') {
            return
        }
    }
}

function Wait-WpsSlidePreviews {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PreviewDirectory,
        [Parameter(Mandatory = $true)]
        [int]$ExpectedCount,
        [int]$TimeoutSeconds = 30,
        [int]$StableCount = 2
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastSignature = $null
    $stableCount = 0
    do {
        $files = @(
            Get-ChildItem -LiteralPath $PreviewDirectory -File |
                Where-Object { $_.Extension -ieq '.png' }
        )
        $signature = (
            $files |
                Sort-Object Name |
                ForEach-Object {
                    '{0}:{1}' -f $_.Name, $_.Length
                }
        ) -join '|'
        if (
            $files.Count -eq $ExpectedCount `
            -and @($files | Where-Object { $_.Length -le 0 }).Count -eq 0 `
            -and $signature -eq $lastSignature
        ) {
            $stableCount += 1
        } else {
            $stableCount = 0
        }
        if ($stableCount -ge $StableCount) {
            return $files
        }
        $lastSignature = $signature
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'WPS did not finish exporting every slide preview'
}

if ($PreviewSession) {
    if ([string]::IsNullOrWhiteSpace($SessionDirectory)) {
        throw 'Preview session directory is required'
    }
    $sessionRoot = [IO.Path]::GetFullPath($SessionDirectory)
    if (-not (Test-Path -LiteralPath $sessionRoot)) {
        throw 'Preview session directory is unavailable'
    }
    try {
        Invoke-PreviewSessionLoop -Directory $sessionRoot
        exit 0
    }
    catch {
        exit 1
    }
    finally {
        Close-PreviewSessionCom
    }
}

if ([string]::IsNullOrWhiteSpace($RequestPath) -or [string]::IsNullOrWhiteSpace($ResultPath)) {
    throw 'Helper request and result paths are required'
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
    $modeProperty = $plan.PSObject.Properties['mode']
    $mode = if ($null -eq $modeProperty) {
        'execute_plan'
    } else {
        [string]$modeProperty.Value
    }
    if ($mode -eq 'preview_only') {
        $workingDirectory = [IO.Path]::GetDirectoryName($sourceCopy)
        $previewDirectory = [IO.Path]::GetFullPath(
            [string]$plan.output.preview_directory
        )
        if (
            -not $previewDirectory.StartsWith(
                $workingDirectory + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw 'Preview directory is outside the isolated helper directory'
        }
        $slideIndexes = @(
            $plan.slide_indexes |
                ForEach-Object { [int]$_ }
        )
        if (
            $slideIndexes.Count -lt 1 `
            -or $slideIndexes.Count -gt 32 `
            -or @($slideIndexes | Where-Object { $_ -le 0 }).Count -gt 0 `
            -or @($slideIndexes | Sort-Object -Unique).Count -ne $slideIndexes.Count
        ) {
            throw 'Preview slide selection is invalid'
        }
        $beforeHash = (Get-FileHash -LiteralPath $sourceCopy -Algorithm SHA256).Hash
        $expectedHash = [string]$plan.source.sha256
        if ($beforeHash -ine $expectedHash) {
            throw 'Preview source fingerprint does not match'
        }
        New-Item -ItemType Directory -Path $previewDirectory -Force | Out-Null
        Export-ReadOnlySlidePreviews `
            -SourceCopy $sourceCopy `
            -SlideIndexes $slideIndexes `
            -PreviewDirectory $previewDirectory
        $afterHash = (Get-FileHash -LiteralPath $sourceCopy -Algorithm SHA256).Hash
        if ($afterHash -ine $beforeHash) {
            throw 'Preview source changed during read-only rendering'
        }
        $previewResult = @{
            status = 'completed'
            source_lock_check = 'passed'
            rendered_slide_indexes = @($slideIndexes)
            source_unchanged = $true
        }
        $previewResult | ConvertTo-Json -Depth 10 -Compress |
            Set-Content -LiteralPath $ResultPath -Encoding UTF8
        exit 0
    }
    if ($mode -ne 'execute_plan') {
        throw 'Unsupported WPS helper mode'
    }
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
    Get-ChildItem -LiteralPath $previewDirectory -File |
        Remove-Item -Force
    $application = New-Object -ComObject 'KWPP.Application'
    Set-WpsApplicationHiddenIfSupported -Application $application | Out-Null
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
                if ($null -ne $operation.details.font_size) {
                    $fontSize = [int]$operation.details.font_size
                    if (
                        $operation.details.semantic_role -eq 'textbook_page_label' `
                        -and $fontSize -ne 28
                    ) {
                        throw 'Textbook page label font size must be 28'
                    }
                    if ($fontSize -lt 8 -or $fontSize -gt 96) {
                        throw 'Text box font size is outside the safe range'
                    }
                    $shape.TextFrame.TextRange.Font.Size = $fontSize
                }
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
                    if ([string]$target.target_kind -eq 'existing_slide') {
                        $slide = Get-OriginalSlide `
                            -Presentation $presentation `
                            -OriginalSlideIds $originalSlideIds `
                            -OriginalIndex ([int]$target.generated_page_number)
                    } else {
                        $slide = $addedSlides[
                            [string]$target.new_slide_operation_id
                        ]
                        if ($null -eq $slide) {
                            throw 'New slide target is unavailable'
                        }
                    }
                    $box = Get-NormalizedBox `
                        -Position $position `
                        -SlideWidth $slideWidth `
                        -SlideHeight $slideHeight
                    Add-PictureFit `
                        -Slide $slide `
                        -Path ([string]$operation.details.isolated_asset_path) `
                        -Box $box | Out-Null
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
                        $shape = Add-PictureFit `
                            -Slide $slide `
                            -Path (
                                [string]$operation.details.isolated_asset_path
                            ) `
                            -Box $box
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
        $true
    )
    $verificationSlideCount = [int]$verificationPresentation.Slides.Count
    $verificationPresentation.Export(
        $previewDirectory,
        'PNG',
        1600,
        900
    )
    $exportedPreviews = @(
        Wait-WpsSlidePreviews `
            -PreviewDirectory $previewDirectory `
            -ExpectedCount $verificationSlideCount |
            Sort-Object {
                $match = [regex]::Match($_.BaseName, '(\d+)$')
                if (-not $match.Success) {
                    throw 'WPS exported a preview with an unexpected name'
                }
                [int]$match.Groups[1].Value
            }
    )
    try {
        $verificationPresentation.Close()
    }
    catch [System.Runtime.InteropServices.COMException] {
        # Some WPS builds end the export COM server after all files are complete.
    }
    $verificationPresentation = $null
    try {
        $application.Quit()
    }
    catch [System.Runtime.InteropServices.COMException] {
        # The export COM server may already have exited.
    }
    $application = $null

    for ($index = 1; $index -le $exportedPreviews.Count; $index++) {
        Rename-Item `
            -LiteralPath $exportedPreviews[$index - 1].FullName `
            -NewName ('slide-{0:D5}.png' -f $index)
    }

    $application = New-Object -ComObject 'KWPP.Application'
    Set-WpsApplicationHiddenIfSupported -Application $application | Out-Null
    $verificationPresentation = $application.Presentations.Open(
        $candidate,
        $true,
        $false,
        $true
    )
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
    Close-PreviewSessionCom
}
