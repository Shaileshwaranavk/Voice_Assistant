"""Local OCR: Windows' own text recogniser (Windows.Media.Ocr). Runs on this PC - nothing is uploaded.

Works for the languages installed in Windows (Settings > Time & language > Language & region).
"""

import os
import subprocess
import tempfile

from . import tool

_SCRIPT = r"""
param([string[]]$Paths)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { Write-Output "NOENGINE"; exit }
foreach ($p in $Paths) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    Write-Output $res.Text
    Write-Output "<<<PAGE>>>"
}
"""
_ps1 = None


def _script_path():
    global _ps1
    if _ps1 is None or not os.path.exists(_ps1):
        _ps1 = os.path.join(tempfile.gettempdir(), "veera_ocr.ps1")
        with open(_ps1, "w", encoding="utf-8") as f:
            f.write(_SCRIPT)
    return _ps1


def ocr_files(paths):
    """List of recognised text, one per image path ('' where nothing was found)."""
    paths = [str(p) for p in paths]
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _script_path(),
                            *paths], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30 + 10 * len(paths), creationflags=0x08000000)
    except Exception as e:
        raise RuntimeError(f"OCR failed: {e}")
    out = r.stdout or ""
    if "NOENGINE" in out:
        raise RuntimeError("Windows has no text-recognition language installed.")
    pages = [p.strip() for p in out.split("<<<PAGE>>>")][:len(paths)]
    return pages + [""] * (len(paths) - len(pages))


def ocr_image(image):
    """OCR a PIL image (or a path) -> text."""
    if hasattr(image, "save"):
        path = os.path.join(tempfile.gettempdir(), "veera_ocr_img.png")
        image.save(path)
    else:
        path = str(image)
    return ocr_files([path])[0]


@tool("desktop")
def read_screen() -> str:
    """Read all the text that is visible on the screen right now (local OCR). Good for error messages,
    dialogs, and anything on screen that can't be copied."""
    import pyautogui
    try:
        text = ocr_image(pyautogui.screenshot())
    except RuntimeError as e:
        return str(e)
    return text or "I couldn't see any text on the screen."
