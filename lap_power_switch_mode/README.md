# Laptop Power Switch Mode

This repository provides a script and udev rule to automatically change a laptop's
power plan when the AC adapter is plugged or unplugged.

---

## 🛠 Solution Overview

> Original idea from:  
> https://www.reddit.com/r/Ubuntu/comments/1hj7nik/is_there_a_way_to_automatically_switch-between/

The general flow:

1. Detect AC adapter status with `upower` or `/sys/class/power_supply`.
2. Run a shell script that sets the CPU frequency governor.
3. Trigger the script using a udev rule on power state changes.
4. (Optional) Ensure the correct plan is applied at boot via `@reboot` cron.

---

## Step 1: Identify Your AC Adapter

```bash
upower -e
```

Look for a device such as `/org/freedesktop/UPower/devices/line_power_AC`.

```bash
upower -i /org/freedesktop/UPower/devices/line_power_AC
```

Check the `online` property: `1` means charger connected.

Alternatively read directly:

```bash
cat /sys/class/power_supply/AC/online
```

> (Update `"AC"` if your system uses a different name.)

---

## Step 2: Script to Change the Power Plan

Create `/usr/local/bin/change-power-plan.sh`:

```bash
#!/bin/bash

AC_STATUS=$(cat /sys/class/power_supply/AC/online) # Update "AC" if needed

if [ "$AC_STATUS" -eq 1 ]; then
    echo "$(date) - Charger connected. Setting power plan to 'Performance'" >> /var/log/power-plan.log
    # Set to performance mode
    powerprofilesctl set performance
else
else
    echo "$(date) - Charger disconnected. Setting power plan to 'Power Saver'" >> /var/log/power-plan.log
    # Set to power saver mode
    powerprofilesctl set balanced
fi
```

Make it executable:

```bash
sudo chmod +x /usr/local/bin/change-power-plan.sh
```

---

## Step 3: Automate with udev Rules

Create a udev rule at `/etc/udev/rules.d/99-power-plan.rules`:

```rules
SUBSYSTEM=="power_supply", ATTR{online}=="1", RUN+="/usr/local/bin/change-power-plan.sh"
```

Reload rules and trigger:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

## Step 4: Test

Unplug/plug the charger and verify that:

```bash
cat /var/log/power-plan.log
```

shows the appropriate log entries and that the governor changes.

---

### Optional: Persistent Plan Across Reboots

Add to root's crontab:

```bash
sudo crontab -e
```

and append:

```
@reboot /usr/local/bin/change-power-plan.sh
```

---

This setup automatically adjusts the power plan based on AC adapter status.  
Let me know how it works or if you encounter any issues!





