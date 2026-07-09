param(
    [string]$InputFile = "C:\Users\OrCon\.codex\attachments\cf87045d-fad8-4611-a28f-549e1447733d\pasted-text-2.txt"
)

$ErrorActionPreference = "Stop"

$python = "C:\Users\OrCon\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$secureKey = Read-Host "ProofHub API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)

try {
    $env:PROOFHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    & $python proofhub_import.py $InputFile --run
    exit $LASTEXITCODE
}
finally {
    $env:PROOFHUB_API_KEY = $null
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
