param(
    [string]$InputFile = "C:\Users\OrCon\.codex\attachments\6b40874b-1c9b-4814-9ca1-a804c767f17b\goal-objective.md",
    [string]$LabelMap = "",
    [switch]$FetchLabels,
    [switch]$AutoCreateMissingLabels,
    [switch]$AllowDuplicateParentTasks,
    [switch]$AllowDuplicateSubtasks
)

$ErrorActionPreference = "Stop"

$python = "C:\Users\OrCon\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$secureKey = Read-Host "ProofHub API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)

try {
    $env:PROOFHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $arguments = @("proofhub_import.py", $InputFile, "--run")
    if ($LabelMap) {
        $arguments += @("--label-map", $LabelMap)
    }
    if ($FetchLabels) {
        $arguments += "--fetch-labels"
    }
    if ($AutoCreateMissingLabels) {
        $arguments += "--auto-create-missing-labels"
    }
    if ($AllowDuplicateParentTasks) {
        $arguments += "--no-update-matching-titles"
    }
    if ($AllowDuplicateSubtasks) {
        $arguments += "--no-skip-matching-subtasks"
    }
    & $python @arguments
    exit $LASTEXITCODE
}
finally {
    $env:PROOFHUB_API_KEY = $null
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
