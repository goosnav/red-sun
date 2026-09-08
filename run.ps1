# Red Sun: start the GUI from a source checkout (Windows PowerShell).
# Uses uv when available (exact locked Pillow, managed Python 3.12); otherwise python + a local venv.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot "app\src"
if (Get-Command uv -ErrorAction SilentlyContinue) {
  uv run --project app --no-dev python -m red_sun.start @args
  exit $LASTEXITCODE
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Error "Red Sun needs either uv (https://docs.astral.sh/uv/) or Python 3.10+ on PATH."
}
if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\python -m pip install --quiet --disable-pip-version-check "pillow==12.0.0"
.\.venv\Scripts\python -m red_sun.start @args
