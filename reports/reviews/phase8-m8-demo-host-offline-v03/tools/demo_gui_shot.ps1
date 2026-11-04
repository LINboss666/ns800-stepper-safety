param([string]$PkgDir, [string]$Probe, [int]$Frame, [string]$Dump, [string]$Out, [string]$Geometry = "1180x820", [int]$HoldSec = 20)
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W32G {
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  public struct POINT { public int X; public int Y; }
}
"@
$p = Start-Process -FilePath "python" -ArgumentList $Probe,"--pkg",$PkgDir,"--frame",$Frame,"--dump",$Dump,"--geometry",$Geometry,"--hold",$HoldSec -PassThru
$h = [IntPtr]::Zero
for ($i = 0; $i -lt 80; $i++) {
  Start-Sleep -Milliseconds 250
  $q = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
  if ($null -eq $q) { break }
  $q.Refresh()
  if ($q.MainWindowHandle -ne [IntPtr]::Zero) { $h = $q.MainWindowHandle; break }
}
if ($h -eq [IntPtr]::Zero) { Write-Output "NO_WINDOW"; exit 2 }
# wait until the probe has written its dump (that happens right before the hold window opens)
for ($i = 0; $i -lt 80; $i++) {
  Start-Sleep -Milliseconds 250
  if (Test-Path $Dump) { break }
}
Start-Sleep -Milliseconds 1200
$cr = New-Object W32G+RECT; [void][W32G]::GetClientRect($h, [ref]$cr)
$pt = New-Object W32G+POINT; $pt.X = 0; $pt.Y = 0; [void][W32G]::ClientToScreen($h, [ref]$pt)
Write-Output ("HWND=" + $h + " CLIENT=" + $cr.Right + "x" + $cr.Bottom + " ORIGIN=" + $pt.X + "," + $pt.Y)
$bmp = New-Object System.Drawing.Bitmap($cr.Right, $cr.Bottom)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen([System.Drawing.Point]::new($pt.X, $pt.Y), [System.Drawing.Point]::Empty, [System.Drawing.Size]::new($cr.Right, $cr.Bottom))
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose(); $g.Dispose()
Write-Output ("SHOT " + $Out)
Start-Sleep -Milliseconds 1500
if (-not $p.HasExited) { [void]$p.Kill() }
Write-Output "DONE"
