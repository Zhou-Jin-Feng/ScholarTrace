param(
    [string]$DocuMindPath = "",
    [string]$ScholarGraphPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$documentsRoot = Split-Path -Parent $projectRoot

if (-not $DocuMindPath) {
    $DocuMindPath = Join-Path $documentsRoot "DocuMind"
}
if (-not $ScholarGraphPath) {
    $ScholarGraphPath = Join-Path $documentsRoot "ScholarGraph"
}

$expected = @{
    DocuMindCommit = "32c5eb8d755065c7e8db9dae80973263aa77f196"
    DocuMindVersion = "2.1.0"
    ScholarGraphCommit = "953e40b155fe4d2e15002afcddc0e628f1524bf9"
    GraphRagVersion = "3.1.2"
    FormalCorpusCount = 198
}

function Assert-Equal {
    param(
        [string]$Name,
        [object]$Actual,
        [object]$Expected
    )
    if ($Actual -ne $Expected) {
        throw "$Name mismatch: expected '$Expected', got '$Actual'"
    }
    Write-Output "PASS $Name=$Actual"
}

foreach ($path in @($DocuMindPath, $ScholarGraphPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "Required upstream repository not found: $path"
    }
}

$docCommitExists = git -c "safe.directory=$($DocuMindPath.Replace('\', '/'))" -C $DocuMindPath cat-file -e "$($expected.DocuMindCommit)^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Frozen DocuMind commit does not exist locally: $($expected.DocuMindCommit)"
}
$docHead = (git -c "safe.directory=$($DocuMindPath.Replace('\', '/'))" -C $DocuMindPath rev-parse HEAD).Trim()
$docStatus = git -c "safe.directory=$($DocuMindPath.Replace('\', '/'))" -C $DocuMindPath status --porcelain
$docVersionText = git -c "safe.directory=$($DocuMindPath.Replace('\', '/'))" -C $DocuMindPath show "$($expected.DocuMindCommit):app/__init__.py"
$docVersionMatch = [regex]::Match(($docVersionText -join "`n"), '__version__\s*=\s*"([^"]+)"')
$retrieveRouteText = git -c "safe.directory=$($DocuMindPath.Replace('\', '/'))" -C $DocuMindPath show "$($expected.DocuMindCommit):app/api/routers/retrieval.py"

Write-Output "PASS documind.frozen_commit=$($expected.DocuMindCommit)"
Assert-Equal "documind.version" $docVersionMatch.Groups[1].Value $expected.DocuMindVersion
Assert-Equal "documind.retrieve_route" (($retrieveRouteText -join "`n") -match "(?m)^def retrieve_document\(") $true
if ($docHead -ne $expected.DocuMindCommit) {
    Write-Output "WARN documind.current_head=$docHead differs from frozen baseline"
}
if (-not [string]::IsNullOrWhiteSpace(($docStatus -join ""))) {
    Write-Output "WARN documind.current_worktree_is_dirty=true; frozen commit content was used"
}

$graphCommit = (git -c "safe.directory=$($ScholarGraphPath.Replace('\', '/'))" -C $ScholarGraphPath rev-parse HEAD).Trim()
$graphStatus = git -c "safe.directory=$($ScholarGraphPath.Replace('\', '/'))" -C $ScholarGraphPath status --porcelain
$lockPath = Join-Path $ScholarGraphPath "requirements.lock.txt"
$lockText = Get-Content -LiteralPath $lockPath -Raw
$formalInput = Join-Path $ScholarGraphPath "corpora\formal\workspace\input"
$corpusCount = (Get-ChildItem -LiteralPath $formalInput -Filter "openalex_*.txt" -File).Count
$demoService = Get-Content -LiteralPath (Join-Path $ScholarGraphPath "src\demo_service.py") -Raw

Assert-Equal "scholargraph.commit" $graphCommit $expected.ScholarGraphCommit
Assert-Equal "scholargraph.clean" ([string]::IsNullOrWhiteSpace(($graphStatus -join ""))) $true
Assert-Equal "scholargraph.graphrag" ($lockText -match "(?m)^graphrag==$($expected.GraphRagVersion)$") $true
Assert-Equal "scholargraph.formal_corpus_count" $corpusCount $expected.FormalCorpusCount
Assert-Equal "scholargraph.run_query" ($demoService -match "(?m)^def run_query\(") $true

Write-Output "PASS upstream baselines match M0 freeze"
