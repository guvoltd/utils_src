#!/bin/bash
echo "$(date) - Charger event detected.." >> /var/log/power-plan.log
AC_STATUS=$(cat /sys/class/power_supply/AC/online) # Update "AC" if needed based on your system

if [ "$AC_STATUS" -eq 1 ]; then
    echo "$(date) - Charger connected. Setting power plan to 'Performance'" >> /var/log/power-plan.log
    # Set to performance mode
    powerprofilesctl set performance
else
    echo "$(date) - Charger disconnected. Setting power plan to 'Power Saver'" >> /var/log/power-plan.log
    # Set to power saver mode
    powerprofilesctl set balanced
fi
