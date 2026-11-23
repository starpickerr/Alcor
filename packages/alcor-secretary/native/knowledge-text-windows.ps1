$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$storageFile = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$streamType = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$bitmapDecoder = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$softwareBitmap = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$transformType = [Windows.Graphics.Imaging.BitmapTransform, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$ocrType = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$ocrResult = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods().Where({
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
}, 'First')[0]

function Wait-Result($operation, [type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}

function Read-ImageText($stream) {
    $decoder = Wait-Result ($bitmapDecoder::CreateAsync($stream)) $bitmapDecoder
    $transform = $transformType::new()
    $scale = [Math]::Min(1, $ocrType::MaxImageDimension / [Math]::Max($decoder.PixelWidth, $decoder.PixelHeight))
    $transform.ScaledWidth = [uint32][Math]::Max(1, [Math]::Floor($decoder.PixelWidth * $scale))
    $transform.ScaledHeight = [uint32][Math]::Max(1, [Math]::Floor($decoder.PixelHeight * $scale))
    $bitmap = Wait-Result ($decoder.GetSoftwareBitmapAsync(
        'Bgra8', 'Ignore', $transform, 'RespectExifOrientation', 'DoNotColorManage'
    )) $softwareBitmap
    try {
        $result = Wait-Result ($engine.RecognizeAsync($bitmap)) $ocrResult
        $text = ($result.Lines | ForEach-Object Text) -join "`n"
        # The Chinese recognizer can emit spaces between individual Han characters.
        return $text -replace '(?<=[\u3400-\u9FFF\uF900-\uFAFF]) +(?=[\u3400-\u9FFF\uF900-\uFAFF])', ''
    } finally {
        $bitmap.Dispose()
    }
}

$engine = $ocrType::TryCreateFromUserProfileLanguages()
if (!$engine) { throw 'No OCR language is available for the current Windows user.' }
$file = Wait-Result ($storageFile::GetFileFromPathAsync($env:ALCOR_KNOWLEDGE_SOURCE)) $storageFile
$pages = [Collections.Generic.List[string]]::new()
$truncated = $false
if ($env:ALCOR_KNOWLEDGE_KIND -eq 'pdf') {
    $pdfType = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime]
    $renderType = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType=WindowsRuntime]
    $memoryStream = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $asActionTask = [System.WindowsRuntimeSystemExtensions].GetMethods().Where({
        $_.Name -eq 'AsTask' -and !$_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
    }, 'First')[0]
    $pdf = Wait-Result ($pdfType::LoadFromFileAsync($file)) $pdfType
    $pageLimit = [Math]::Min($pdf.PageCount, 12)
    $truncated = $pdf.PageCount -gt $pageLimit
    for ($i = 0; $i -lt $pageLimit; $i++) {
        $page = $pdf.GetPage($i)
        $stream = $memoryStream::new()
        try {
            $options = $renderType::new()
            $dimension = [Math]::Min(2000, $ocrType::MaxImageDimension)
            if ($page.Size.Width -gt $page.Size.Height) {
                $options.DestinationWidth = $dimension
            } else {
                $options.DestinationHeight = $dimension
            }
            $action = $page.RenderToStreamAsync($stream, $options)
            [void]$asActionTask.Invoke($null, @($action)).GetAwaiter().GetResult()
            $stream.Seek(0)
            $pages.Add((Read-ImageText $stream))
        } finally {
            $stream.Dispose()
            $page.Dispose()
        }
    }
} else {
    $stream = Wait-Result ($file.OpenAsync('Read')) $streamType
    try {
        $pages.Add((Read-ImageText $stream))
    } finally {
        $stream.Dispose()
    }
}
@{ pages = $pages.ToArray(); truncated = $truncated } | ConvertTo-Json -Compress
