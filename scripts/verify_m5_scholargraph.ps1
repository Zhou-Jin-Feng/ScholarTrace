param(
    [string]$ScholarGraphPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$documentsRoot = Split-Path -Parent $projectRoot
if (-not $ScholarGraphPath) {
    $ScholarGraphPath = Join-Path $documentsRoot "ScholarGraph"
}

$expectedCommit = "3aa5e2a0f57efa173cf633808bc2d370f519907c"
$expectedServiceVersion = "1.2.0"
$expectedGraphRagVersion = "3.1.2"
$expectedCorpusCount = 198
$expectedEligibleSha256 = "91d19770d3fee134036f88e0614f45b91d1a9d2642a00a5e5fae695482cb2a00"
$expectedBoundarySha256 = "6bc55ed002689ef165f8a6252c45dcee2f9583d05d43e55a6656f3405a0cf0fc"

if (-not (Test-Path -LiteralPath $ScholarGraphPath -PathType Container)) {
    throw "ScholarGraph repository not found"
}

$safePath = $ScholarGraphPath.Replace('\', '/')
git -c "safe.directory=$safePath" -C $ScholarGraphPath cat-file -e "$expectedCommit`^{commit}"
if ($LASTEXITCODE -ne 0) {
    throw "Frozen M5 ScholarGraph commit is unavailable"
}
$head = (git -c "safe.directory=$safePath" -C $ScholarGraphPath rev-parse HEAD).Trim()
if ($head -ne $expectedCommit) {
    throw "ScholarGraph HEAD differs from the frozen M5 baseline"
}
$status = git -c "safe.directory=$safePath" -C $ScholarGraphPath status --porcelain
if (-not [string]::IsNullOrWhiteSpace(($status -join ""))) {
    throw "ScholarGraph worktree is dirty"
}

$models = git -c "safe.directory=$safePath" -C $ScholarGraphPath show "$expectedCommit`:src/api_models.py"
$modelsText = $models -join "`n"
if ($modelsText -notmatch "SERVICE_VERSION\s*=\s*`"$expectedServiceVersion`"") {
    throw "ScholarGraph service version mismatch"
}
if ($modelsText -notmatch "GRAPHRAG_VERSION\s*=\s*`"$expectedGraphRagVersion`"") {
    throw "ScholarGraph GraphRAG version mismatch"
}

$formalInput = Join-Path $ScholarGraphPath "corpora\formal\workspace\input"
$corpusCount = (Get-ChildItem -LiteralPath $formalInput -Filter "openalex_*.txt" -File).Count
if ($corpusCount -ne $expectedCorpusCount) {
    throw "ScholarGraph formal corpus count mismatch"
}

$providerPath = Join-Path $ScholarGraphPath "docs\contracts\scholargraph-provider-v1.openapi.json"
$consumerPath = Join-Path $projectRoot "contracts\openapi\scholargraph-v1.openapi.json"
$provider = Get-Content -Raw -LiteralPath $providerPath | ConvertFrom-Json
$consumer = Get-Content -Raw -LiteralPath $consumerPath | ConvertFrom-Json
$providerCanonical = $provider | ConvertTo-Json -Depth 100 -Compress
$consumerCanonical = $consumer | ConvertTo-Json -Depth 100 -Compress
if ($providerCanonical -cne $consumerCanonical) {
    throw "ScholarTrace ScholarGraph Provider snapshot is incompatible"
}

$eligibleHash = (Get-FileHash -Algorithm SHA256 (
    Join-Path $projectRoot "evaluation\seeds\m5_scholargraph_eligible_eval.jsonl"
)).Hash.ToLowerInvariant()
$boundaryHash = (Get-FileHash -Algorithm SHA256 (
    Join-Path $projectRoot "evaluation\seeds\m5_scholargraph_boundary_eval.jsonl"
)).Hash.ToLowerInvariant()
if ($eligibleHash -ne $expectedEligibleSha256 -or $boundaryHash -ne $expectedBoundarySha256) {
    throw "M5 ScholarGraph evaluation seed hash mismatch"
}

Write-Output "PASS scholargraph.commit=$expectedCommit"
Write-Output "PASS scholargraph.service_version=$expectedServiceVersion"
Write-Output "PASS scholargraph.graphrag_version=$expectedGraphRagVersion"
Write-Output "PASS scholargraph.formal_corpus_count=$expectedCorpusCount"
Write-Output "PASS scholargraph.provider_consumer_exact_match=true"
Write-Output "PASS scholargraph.m5_question_sets=12"
