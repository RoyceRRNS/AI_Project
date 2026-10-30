# Exports a .pptx or .docx to PDF with the installed Microsoft Office (used for the final PDFs
# and for visual QA):   powershell -File docs\build\export_pdf.ps1 <in> <out.pdf>
param([Parameter(Mandatory)][string]$In, [Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = "Stop"
$In = (Resolve-Path $In).Path
$Out = [System.IO.Path]::GetFullPath($Out)
# Work on a copy, so the same file open in Office (even in Protected View) does not block the export.
$copy = Join-Path ([System.IO.Path]::GetTempPath()) ("export_" + [guid]::NewGuid().ToString("N") + [System.IO.Path]::GetExtension($In))
Copy-Item $In $copy
try {
    if ($In -like "*.pptx") {
        $running = [bool](Get-Process POWERPNT -ErrorAction SilentlyContinue)
        $app = New-Object -ComObject PowerPoint.Application
        try {
            $deck = $app.Presentations.Open($copy, $true, $false, $false)  # read-only, no window
            $deck.SaveAs($Out, 32)                                          # ppSaveAsPDF
            $deck.Close()
        } finally { if (-not $running) { $app.Quit() } }                    # never close the user's PowerPoint
    } elseif ($In -like "*.docx") {
        $running = [bool](Get-Process WINWORD -ErrorAction SilentlyContinue)
        $app = New-Object -ComObject Word.Application
        if (-not $running) { $app.Visible = $false }
        try {
            $doc = $app.Documents.Open($copy, $false, $true)
            $doc.Fields.Update() | Out-Null
            $doc.ExportAsFixedFormat($Out, 17)                              # wdExportFormatPDF
            $doc.Close($false)
        } finally { if (-not $running) { $app.Quit() } }
    } else { throw "Unsupported file: $In" }
} finally { Remove-Item $copy -ErrorAction SilentlyContinue }
Write-Output "exported $Out"
