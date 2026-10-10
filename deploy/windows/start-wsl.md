# Keep WSL running on the workstation without anyone logged in

systemd inside WSL starts `llama-server`, `cl-worker` and `cl-review` at boot of the WSL
distro, but Windows only boots the distro when something launches it, and by default
shuts it down when idle. Three settings fix that.

## 1. systemd in the distro

In WSL (`Ubuntu-24.04`), make sure `/etc/wsl.conf` contains:

```ini
[boot]
systemd=true
```

## 2. No idle shutdown

In Windows, edit `%UserProfile%\.wslconfig` (create it if missing) and add:

```ini
[general]
instanceIdleTimeout=-1
```

Microsoft's docs (learn.microsoft.com/windows/wsl/wsl-config, checked 2026-10-10):
`instanceIdleTimeout` is in milliseconds, and `-1` disables auto shutdown.

Then, in PowerShell: `wsl --shutdown` and open Ubuntu once to apply both settings.

## 3. Start the distro when Windows starts

Microsoft does not document an official "start at boot" switch. A Task Scheduler task
that runs a no-op command in the distro works; the Phase 0 acceptance test (restart, then
check the services) is how we confirm it on this machine.

1. Open **Task Scheduler** → **Create Task…** (not "Basic Task").
2. **General**: Name `Start WSL (Chosen Local)`. Select **Run whether user is logged on or
   not**. Leave "Run with highest privileges" unchecked. Configure for: Windows 11.
3. **Triggers** → New → **At startup**. Check **Delay task for: 1 minute** (lets Tailscale
   come up first).
4. **Actions** → New → Start a program:
   - Program: `C:\Windows\System32\wsl.exe`
   - Arguments: `-d Ubuntu-24.04 --exec /bin/true`
5. **Conditions**: uncheck "Start the task only if the computer is on AC power".
6. **Settings**: check "If the task fails, restart every 1 minute", up to 3 times.
7. Click OK and enter your Windows password when asked (stored by Windows, not by us).

The task must run as **your** Windows account (the one that owns the Ubuntu distro), not
SYSTEM.

## Check it

Restart Windows. Do not log in. From your phone on Tailscale, open
`https://<workstation tailnet name>:8443/healthz`. A JSON `{"ok": true, ...}` means the
distro booted, systemd started `cl-review`, and Windows Tailscale is serving it.
Then log in and run, in WSL:

```bash
systemctl is-active llama-server cl-worker cl-review
systemctl show -p User --value cl-worker     # clworker
```
