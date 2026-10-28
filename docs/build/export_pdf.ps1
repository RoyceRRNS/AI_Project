# Exports a .pptx or .docx to PDF with the installed Microsoft Office (used for the final PDFs
# and for visual QA):   powershell -File docs\build\export_pdf.ps1 <in> <out.pdf>
param([Parameter(Mandatory)][string]$In, [Parameter(Mandatory)][string]$Out)
$In = (Resolve-Path $In).Path
$Out = [System.IO.Path]::GetFullPath($Out)
if ($In -like "*.pptx") {
    $app = New-Object -ComObject PowerPoint.Application
    try {
        $deck = $app.Presentations.Open($In, $true, $false, $false)  # read-only, no window
        $deck.SaveAs($Out, 32)                                        # ppSaveAsPDF
        $deck.Close()
    } finally { $app.Quit() }
} elseif ($In -like "*.docx") {
    $app = New-Object -ComObject Word.Application
    $app.Visible = $false
    try {
        $doc = $app.Documents.Open($In, $false, $true)
        $doc.Fields.Update() | Out-Null
        $doc.ExportAsFixedFormat($Out, 17)                            # wdExportFormatPDF
        $doc.Close($false)
    } finally { $app.Quit() }
} else { throw "Unsupported file: $In" }
Write-Output "exported $Out"
