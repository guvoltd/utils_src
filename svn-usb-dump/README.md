# Auto SVN Dump to USB (Windows)

This package gives you a **PowerShell-based** solution that **automatically dumps all Subversion repositories** to a USB drive as soon as it's plugged in.

## What you get
- `AutoSvnDump.ps1` — main script. Scans mounted **removable** drives and writes full/incremental dumps for all repos under your `-ReposRoot`.
- `Task-USB-AutoDump.xml` — a **Task Scheduler** definition that runs the script **on USB device arrival**.
- This README with setup instructions.

Tested on Windows 10/11 with PowerShell 5+.

---

## 1) Prerequisites

1. **Install Subversion** (or VisualSVN Server) so you have `svnadmin.exe`:
   - Typical paths:  
     - `C:\Program Files\Subversion\bin\svnadmin.exe`  
     - `C:\Program Files\VisualSVN Server\bin\svnadmin.exe`

2. Know your **repositories root folder**, e.g. `D:\svn_repos` where each repo is a folder containing `format` and `db`.

3. (Optional but recommended) Give your backup USB a **volume label** like `SVN_BACKUP`.

---

## 2) One-time script setup

1. Copy `AutoSvnDump.ps1` somewhere on the machine, e.g. `C:\Tools\AutoSvnDump.ps1`.
2. Allow script execution for your user:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
   ```
3. Try a manual run (plug in USB first):
   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\Tools\AutoSvnDump.ps1 -ReposRoot "D:\svn_repos" -UsbLabel "SVN_BACKUP" -ZipDumps


   powershell -ExecutionPolicy Bypass -File E:\utility_codes\svn-usb-dump\AutoSvnDump.ps1 -ReposRoot "E:\svn_mcc\REPOS" -UsbLabel "ME-HV320" -ZipDumps
   ```
   The script will create `SVN_DUMPS\repos\*.svndump(.zip)` on the USB and `SVN_DUMPS\backup_state.json` for incremental tracking.

**Defaults & flags**
- `-UsbMinFreeGB 2` (change if needed)
- `-ZipDumps` to compress each dump into `.zip`
- `-Full` forces a full dump ignoring prior state
- `-NoHotcopy` to dump directly from the live repo (default is to **hotcopy** to a temp folder first — safer)
- `-Verify` runs `svnadmin verify` on the hotcopy before dumping

---

## 3) Auto-run on USB plug-in (Task Scheduler)

We use a built-in Windows event:
- **Log:** `Microsoft-Windows-DriverFrameworks-UserMode/Operational`
- **Event ID:** `2003` (device arrival)

> Tip: If the operational log is disabled, enable it:
> ```
> wevtutil set-log Microsoft-Windows-DriverFrameworks-UserMode/Operational /e:true
> ```

### Import the provided task
1. Open **Task Scheduler** → **Action** → **Import Task...**
2. Select `Task-USB-AutoDump.xml` from this package.
3. After import, edit the **Actions** panel and adjust:
   - Program/script: `powershell.exe`
   - Arguments (example):
     ```
     -ExecutionPolicy Bypass -File "C:\Tools\AutoSvnDump.ps1" -ReposRoot "D:\svn_repos" -UsbLabel "SVN_BACKUP" -ZipDumps
     ```
4. Ensure **"Run whether user is logged on or not"** is set (if needed).
5. Grant **highest privileges** if your path or repos need admin rights.

> The task is triggered for **any device arrival**; the script itself filters for **removable drives** and an optional `-UsbLabel` match.

---

## 4) Where backups go

On the USB drive (e.g., `E:\`), the script writes:
```
E:\SVN_DUMPS\
  backup_state.json      # remembers last dumped revision per repo
  logs\                 # copies a run log here
  repos\                # .svndump or .zip files per repo dump
```

Dump filenames follow:
```
<repo>_r<start>-<end>_<YYYYMMDD_HHMMSS>.svndump(.zip)
```

---

## 5) Restore from a dump

Create/restore a new repository and load the dump:
```powershell
# Create empty repo
svnadmin create "D:\restore\myrepo"

# Load the dump
svnadmin load "D:\restore\myrepo" < "E:\SVN_DUMPS\repos\myrepo_r0-HEAD_20250101_120000.svndump"
```

If you used incrementals, load the **full** dump first, then each incremental in order.

---

## 6) Troubleshooting

- **"svnadmin.exe not found"**  
  Install Subversion or set `-SvnAdminPath` explicitly.

- **Task does not trigger**  
  Ensure the `Microsoft-Windows-DriverFrameworks-UserMode/Operational` log is **enabled**, and Event ID `2003` appears when you plug the USB.

- **No repositories detected**  
  Verify `-ReposRoot` contains folders with `format` and `db` inside each.

- **Performance**  
  Use `-NoHotcopy` if you accept dumping directly from live repos; otherwise hotcopy+dump is safer but slower.

---

## 7) Security & operational notes

- Dumps may contain all history; keep the USB **secure**.
- Consider **BitLocker** encrypting the USB.
- Keep at least **2 GB** free (configurable via `-UsbMinFreeGB`).

---

© 2025
