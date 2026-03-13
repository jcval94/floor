$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$outputPath = Join-Path $repoRoot "docs\\README_principal.pptx"

function Add-TextBox {
    param(
        $Slide,
        [string]$Text,
        [double]$Left,
        [double]$Top,
        [double]$Width,
        [double]$Height,
        [int]$FontSize = 20,
        [string]$FontName = "Aptos",
        [int]$Color = 0x1F1F1F,
        [switch]$Bold,
        [switch]$Mono,
        [int]$Alignment = 1
    )

    $shape = $Slide.Shapes.AddTextbox(1, $Left, $Top, $Width, $Height)
    $shape.TextFrame.TextRange.Text = $Text
    $shape.TextFrame.TextRange.Font.Size = $FontSize
    $shape.TextFrame.TextRange.Font.Name = $(if ($Mono) { "Consolas" } else { $FontName })
    $shape.TextFrame.TextRange.Font.Color.RGB = $Color
    $shape.TextFrame.TextRange.ParagraphFormat.Alignment = $Alignment
    $shape.TextFrame.WordWrap = -1
    if ($Bold) {
        $shape.TextFrame.TextRange.Font.Bold = -1
    }
    return $shape
}

function Add-Bullets {
    param(
        $Slide,
        [string[]]$Items,
        [double]$Left,
        [double]$Top,
        [double]$Width,
        [double]$Height,
        [int]$FontSize = 20,
        [int]$Color = 0x1F1F1F
    )

    $shape = $Slide.Shapes.AddTextbox(1, $Left, $Top, $Width, $Height)
    $shape.TextFrame.TextRange.Text = ($Items -join "`r")
    $shape.TextFrame.TextRange.Font.Name = "Aptos"
    $shape.TextFrame.TextRange.Font.Size = $FontSize
    $shape.TextFrame.TextRange.Font.Color.RGB = $Color
    $shape.TextFrame.WordWrap = -1

    for ($i = 1; $i -le $shape.TextFrame.TextRange.Paragraphs().Count; $i++) {
        $paragraph = $shape.TextFrame.TextRange.Paragraphs($i)
        $paragraph.ParagraphFormat.Bullet.Visible = -1
        $paragraph.ParagraphFormat.Bullet.Character = 8226
        $paragraph.SpaceAfter = 8
    }

    return $shape
}

function Add-AccentBar {
    param($Slide, [double]$Top, [double]$Height, [int]$Color)
    $bar = $Slide.Shapes.AddShape(1, 0.35, $Top, 12.6, $Height)
    $bar.Fill.ForeColor.RGB = $Color
    $bar.Line.Visible = 0
}

function Add-Panel {
    param($Slide, [double]$Left, [double]$Top, [double]$Width, [double]$Height, [int]$Color)
    $panel = $Slide.Shapes.AddShape(1, $Left, $Top, $Width, $Height)
    $panel.Fill.ForeColor.RGB = $Color
    $panel.Line.Visible = 0
    return $panel
}

$powerPoint = New-Object -ComObject PowerPoint.Application
$powerPoint.Visible = -1

$presentation = $powerPoint.Presentations.Add()
$presentation.PageSetup.SlideSize = 16

$bgLight = 0xF7F4EE
$bgDark = 0x193441
$accent = 0xD88C3A
$accentSoft = 0xE8B36C
$ink = 0x1E1E1E
$muted = 0x5C6770
$white = 0xFFFFFF

$slides = $presentation.Slides

$slide = $slides.Add(1, 12)
$slide.FollowMasterBackground = 0
$slide.Background.Fill.ForeColor.RGB = $bgLight
Add-Panel $slide 0 0 4.15 7.5 $bgDark | Out-Null
Add-AccentBar $slide 6.65 0.18 $accent
Add-TextBox $slide "floor" 0.6 1.0 3.0 0.6 28 "Aptos Display" $white -Bold | Out-Null
Add-TextBox $slide "README principal" 4.45 1.05 4.8 0.45 26 "Aptos Display" $ink -Bold | Out-Null
Add-TextBox $slide "Bootstrap operativo para una plataforma IA/Finanzas enfocada en estimar floors y ceilings probabilisticos, generar senales accionables y operar ciclos intradia auditables." 4.45 1.7 7.7 1.5 21 "Aptos" $ink | Out-Null
Add-TextBox $slide "Base del repo" 4.45 4.0 2.0 0.3 13 "Aptos" $muted -Bold | Out-Null
Add-TextBox $slide "Python modular + configuracion centralizada + datos locales + automatizacion en GitHub Actions + sitio estatico" 4.45 4.35 7.1 1.4 18 "Aptos" $ink | Out-Null

$slide = $slides.Add(2, 12)
$slide.FollowMasterBackground = 0
$slide.Background.Fill.ForeColor.RGB = $bgLight
Add-AccentBar $slide 0.55 0.14 $accent
Add-TextBox $slide "Que incluye este bootstrap" 0.7 0.45 5.2 0.5 24 "Aptos Display" $ink -Bold | Out-Null
Add-Bullets $slide @(
    "Arquitectura modular Python en src/floor lista para produccion ligera.",
    "Configuracion centralizada por dominio en /config.",
    "Convenciones de nombres y particionado para datasets, modelos, reportes y snapshots.",
    "Politicas de versionado de datos para definir que entra y que no al repo.",
    "Backlog por fases desde MVP hasta paper trading automatizado y broker real.",
    "Capa de visualizacion estatica para GitHub Pages en site/."
) 0.95 1.25 10.8 5.3 21 $ink | Out-Null

$slide = $slides.Add(3, 12)
$slide.FollowMasterBackground = 0
$slide.Background.Fill.ForeColor.RGB = $bgDark
Add-TextBox $slide "Arbol de carpetas objetivo" 0.7 0.5 5.5 0.5 24 "Aptos Display" $white -Bold | Out-Null
$treePanel = Add-Panel $slide 0.65 1.1 11.95 5.85 0x24495A
$treePanel.Fill.Transparency = 0.03
Add-TextBox $slide @"
floor/
|- .github/workflows/
|- config/
|- data/
|  |- predictions/
|  |- signals/
|  |- orders/
|  |- trades/
|  |- metrics/
|  |- reports/
|  |- snapshots/
|  '- training/
|- docs/
|  |- 00_guia/
|  |- 10_resumenes/
|  |- 20_fuentes/
|  '- 01_bootstrap/
|- scripts/
|- site/
|  |- assets/
|  '- data/
|- src/floor/
|  |- external/
|  |- modeling/
|  |- pipeline/
|  |- reporting/
|  '- training/
|- tests/
|- Makefile
|- pyproject.toml
'- README.md
"@ 0.95 1.4 8.2 5.15 13 "Consolas" $white -Mono | Out-Null
Add-TextBox $slide "La estructura separa configuracion, datos operativos, documentacion, codigo de negocio, automatizacion y visualizacion." 9.35 1.6 2.7 3.8 18 "Aptos" $white | Out-Null
Add-TextBox $slide "Diseno orientado a iteracion rapida y trazabilidad." 9.35 5.55 2.6 0.8 16 "Aptos" $accentSoft -Bold | Out-Null

$slide = $slides.Add(4, 12)
$slide.FollowMasterBackground = 0
$slide.Background.Fill.ForeColor.RGB = $bgLight
Add-TextBox $slide "Guia rapida" 0.7 0.45 2.7 0.45 24 "Aptos Display" $ink -Bold | Out-Null
Add-Panel $slide 0.75 1.15 5.55 4.65 0xFFF7EA | Out-Null
Add-TextBox $slide @"
make test
make init-dbs
make yahoo-ingest
make build-training-from-db
make run-cycle SYMBOLS=AAPL,MSFT EVENT=OPEN
make review-training
make build-site
"@ 1.05 1.5 5.0 3.8 19 "Consolas" $ink -Mono | Out-Null
Add-Bullets $slide @(
    "El flujo parte por pruebas, inicializacion de bases e ingesta de mercado.",
    "Luego arma datos de entrenamiento y ejecuta ciclos intradia por evento.",
    "Cierra con revision del training y publicacion del sitio estatico."
) 6.65 1.5 5.3 3.7 20 $ink | Out-Null
Add-AccentBar $slide 6.65 0.16 $accent

$slide = $slides.Add(5, 12)
$slide.FollowMasterBackground = 0
$slide.Background.Fill.ForeColor.RGB = $bgLight
Add-TextBox $slide "Dataset + BBDD local (Yahoo)" 0.7 0.45 5.3 0.45 24 "Aptos Display" $ink -Bold | Out-Null
Add-Bullets $slide @(
    "Base SQLite de mercado: data/market/market_data.sqlite.",
    "Base SQLite de persistencia operativa: data/persistence/app.sqlite.",
    "Ambas se generan automaticamente y no se versionan.",
    "La ingesta desde Yahoo se hace con pausas para respetar el origen.",
    "El pipeline transforma filas crudas en un dataset modelable."
) 0.9 1.15 5.2 4.4 19 $ink | Out-Null
Add-Panel $slide 6.35 1.15 5.7 4.95 0xF1ECE3 | Out-Null
Add-TextBox $slide @"
PYTHONPATH=src python -m storage.yahoo_ingest --db data/market/market_data.sqlite --range 2y --interval 1d --sleep-seconds 0.4

PYTHONPATH=src python -m features.build_training_from_db --db data/market/market_data.sqlite --output data/training/yahoo_market_rows.jsonl

PYTHONPATH=src python -m features.run_features --input data/training/yahoo_market_rows.jsonl --output data/training/modelable_dataset.json
"@ 6.65 1.45 5.1 4.2 12 "Consolas" $ink -Mono | Out-Null

$slide = $slides.Add(6, 12)
$slide.FollowMasterBackground = 0
$slide.Background.Fill.ForeColor.RGB = $bgDark
Add-TextBox $slide "Documentacion y orquestacion" 0.7 0.45 5.8 0.45 24 "Aptos Display" $white -Bold | Out-Null
Add-Panel $slide 0.7 1.2 5.4 4.95 0x24495A | Out-Null
Add-TextBox $slide "Referencias clave" 1.0 1.45 2.5 0.35 18 "Aptos" $accentSoft -Bold | Out-Null
Add-Bullets $slide @(
    "Blueprint detallado: docs/01_bootstrap/BOOTSTRAP_PLAN.md",
    "Playbook operativo: docs/00_guia/WORKFLOW_BOOTSTRAP.md",
    "Checklist de alistamiento, DB, dataset y modelos.",
    "Orden recomendado de ejecucion de workflows."
) 1.0 1.9 4.55 3.6 18 $white | Out-Null
Add-Panel $slide 6.45 1.2 5.45 4.95 0xFFF7EA | Out-Null
Add-TextBox $slide "Workflows" 6.75 1.45 2.0 0.35 18 "Aptos" $ink -Bold | Out-Null
Add-Bullets $slide @(
    "db_bootstrap.yml",
    "ingest.yml",
    "intraday_engine.yml",
    "eod.yml",
    "retrain_assessment.yml",
    "retrain_execute.yml",
    "monitoring.yml",
    "archive.yml",
    "pages.yml"
) 6.75 1.9 4.7 3.8 17 $ink | Out-Null
Add-TextBox $slide "El README posiciona al repo como una base operacional completa para investigacion, ejecucion y monitoreo." 0.9 6.45 10.7 0.45 18 "Aptos" $white | Out-Null

$presentation.SaveAs($outputPath, 24)
$presentation.Close()
$powerPoint.Quit()

[System.Runtime.Interopservices.Marshal]::ReleaseComObject($presentation) | Out-Null
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($powerPoint) | Out-Null

Write-Output "Created $outputPath"
