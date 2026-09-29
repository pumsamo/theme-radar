# Turn the display off after the scheduled 07:22 wake, but only when nobody is using the PC.
# Called at the start of run_morning.bat. Prints one line for the run log. ASCII only.
param([int]$IdleMinutes = 5)

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class MonitorIdle {
    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    public static double IdleMinutes() {
        LASTINPUTINFO info = new LASTINPUTINFO();
        info.cbSize = (uint)Marshal.SizeOf(info);
        GetLastInputInfo(ref info);
        return unchecked((uint)Environment.TickCount - info.dwTime) / 60000.0;
    }
}
'@

$idle = [MonitorIdle]::IdleMinutes()
if ($idle -ge $IdleMinutes) {
    # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2 = off. PostMessage so a hung window cannot block the run.
    [void][MonitorIdle]::PostMessage([IntPtr]0xFFFF, 0x0112, [IntPtr]0xF170, [IntPtr]2)
    "[monitor] off (no input for {0:N0} min)" -f $idle
} else {
    "[monitor] left on (input {0:N1} min ago)" -f $idle
}
