#!/usr/bin/env python3
"""
USB Image Writer with Filesystem Expansion and LED Status Indicator
Requires root privileges (run with sudo)
"""

import os
import sys
import time
import subprocess
import threading
import logging
import re
from pathlib import Path
import RPi.GPIO as GPIO
from pyudev import Context, Monitor, MonitorObserver

# Configuration
LED_PIN = 18                     # GPIO pin for LED (PWM capable pin)
IMAGE_PATH = "/path/to/your/image.img"  # Change to your image file path
MOUNT_DIR = "/mnt/usb"          # Temporary mount directory
LOG_FILE = "/var/log/usb_writer.log"
EXPAND_FS = True                # Set to False to skip filesystem expansion
EXPAND_PARTITION = 2            # Which partition to expand (usually 2 for rootfs)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

class USBCopier:
    def __init__(self):
        self.is_writing = False
        self.device_path = None
        self.led_thread = None
        self.stop_led = threading.Event()
        self.expansion_in_progress = False
        
        self.setup_gpio()
        self.setup_directories()
        
    def setup_gpio(self):
        """Initialize GPIO for LED control"""
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(LED_PIN, GPIO.OUT)
        self.pwm = GPIO.PWM(LED_PIN, 100)  # 100Hz frequency
        self.pwm.start(0)  # Start with LED off
        
    def setup_directories(self):
        """Create necessary directories"""
        Path(MOUNT_DIR).mkdir(parents=True, exist_ok=True)
        
    def breathing_led(self):
        """Create breathing LED effect during writing"""
        while not self.stop_led.is_set():
            for dc in range(0, 101, 5):  # Fade in
                if self.stop_led.is_set():
                    break
                self.pwm.ChangeDutyCycle(dc)
                time.sleep(0.05)
            for dc in range(100, -1, -5):  # Fade out
                if self.stop_led.is_set():
                    break
                self.pwm.ChangeDutyCycle(dc)
                time.sleep(0.05)
    
    def fast_blink_led(self, duration=5):
        """Fast blinking LED for expansion process"""
        start_time = time.time()
        while time.time() - start_time < duration and not self.stop_led.is_set():
            self.pwm.ChangeDutyCycle(100)
            time.sleep(0.2)
            self.pwm.ChangeDutyCycle(0)
            time.sleep(0.2)
    
    def solid_led(self, brightness=100):
        """Set LED to constant brightness"""
        self.pwm.ChangeDutyCycle(brightness)
    
    def find_usb_device(self):
        """Find the most recently connected USB storage device"""
        try:
            # List all block devices
            result = subprocess.run(
                ['lsblk', '-o', 'NAME,TYPE,MOUNTPOINT,SIZE,TRAN', '-J'],
                capture_output=True,
                text=True
            )
            
            import json
            devices = json.loads(result.stdout)
            
            # Look for USB disks
            for device in devices['blockdevices']:
                if device['type'] == 'disk' and device.get('tran') == 'usb':
                    return f"/dev/{device['name']}", device.get('size', 'Unknown')
                # Also check if it has 'children' and look for USB in transport
                if device['type'] == 'disk' and 'children' in device:
                    # Check sysfs for USB connection
                    sysfs_path = f"/sys/block/{device['name']}/device"
                    if os.path.exists(sysfs_path):
                        # Check if it's USB by looking for vendor/model
                        vendor_path = f"/sys/block/{device['name']}/device/vendor"
                        if os.path.exists(vendor_path):
                            with open(vendor_path, 'r') as f:
                                vendor = f.read().strip()
                                if vendor:  # USB devices usually have a vendor
                                    return f"/dev/{device['name']}", device.get('size', 'Unknown')
            
            return None, None
            
        except Exception as e:
            logging.error(f"Error finding USB device: {e}")
            return None, None
    
    def unmount_device(self, device_path):
        """Unmount all partitions of the device"""
        try:
            # Unmount any mounted partitions
            result = subprocess.run(
                ['umount', f'{device_path}?*', f'{device_path}[0-9]*'],
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL
            )
            time.sleep(1)  # Give time for unmount
            return True
        except Exception as e:
            logging.error(f"Error unmounting device: {e}")
            return False
    
    def get_partition_info(self, device_path):
        """Get information about partitions on the device"""
        try:
            # Use fdisk to list partitions
            result = subprocess.run(
                ['fdisk', '-l', device_path],
                capture_output=True,
                text=True
            )
            
            partitions = []
            lines = result.stdout.split('\n')
            
            for line in lines:
                if re.match(rf'^{device_path}\d', line):
                    parts = line.split()
                    if len(parts) >= 5:
                        partition = {
                            'device': parts[0],
                            'start': parts[1],
                            'end': parts[2],
                            'sectors': parts[3],
                            'size': parts[4],
                            'type': ' '.join(parts[5:]) if len(parts) > 5 else 'Unknown'
                        }
                        partitions.append(partition)
            
            return partitions
            
        except Exception as e:
            logging.error(f"Error getting partition info: {e}")
            return []
    
    def expand_partition(self, device_path, partition_number=2):
        """Expand the partition to fill the entire disk using parted"""
        try:
            logging.info(f"Expanding partition {partition_number} on {device_path}")
            
            # First, check current partition layout
            cmd_check = ['parted', '-s', device_path, 'print']
            result = subprocess.run(cmd_check, capture_output=True, text=True)
            logging.info(f"Current partition layout:\n{result.stdout}")
            
            # Get disk size in sectors
            cmd_disk_size = ['blockdev', '--getsz', device_path]
            result = subprocess.run(cmd_disk_size, capture_output=True, text=True)
            disk_sectors = int(result.stdout.strip())
            
            # Calculate end position (leaving 1MB at the end for safety)
            end_sector = disk_sectors - 2048  # 1MB = 2048 sectors (512 bytes/sector)
            
            # Resize partition using parted
            # Note: parted uses MB, not sectors
            cmd_resize = [
                'parted', '-s', device_path,
                'resizepart', str(partition_number),
                '100%'
            ]
            
            logging.info(f"Executing: {' '.join(cmd_resize)}")
            result = subprocess.run(cmd_resize, capture_output=True, text=True)
            
            if result.returncode != 0:
                logging.error(f"Parted failed: {result.stderr}")
                # Try alternative method using fdisk
                return self.expand_partition_fdisk(device_path, partition_number)
            
            # Reread partition table
            subprocess.run(['partprobe', device_path], check=False)
            time.sleep(2)  # Wait for kernel to update partition table
            
            logging.info("Partition expanded successfully")
            return True
            
        except Exception as e:
            logging.error(f"Error expanding partition: {e}")
            return False
    
    def expand_partition_fdisk(self, device_path, partition_number=2):
        """Alternative method to expand partition using fdisk in script mode"""
        try:
            logging.info(f"Trying fdisk method for partition expansion")
            
            # Create fdisk script
            fdisk_script = f"""
p
d
{partition_number}
n
p
{partition_number}


p
w
"""
            
            # Execute fdisk with script
            result = subprocess.run(
                ['fdisk', device_path],
                input=fdisk_script.encode(),
                capture_output=True
            )
            
            if result.returncode != 0:
                logging.error(f"Fdisk failed: {result.stderr.decode()}")
                return False
            
            # Reread partition table
            subprocess.run(['partprobe', device_path], check=False)
            time.sleep(3)
            
            logging.info("Partition expanded using fdisk")
            return True
            
        except Exception as e:
            logging.error(f"Error in fdisk expansion: {e}")
            return False
    
    def expand_filesystem(self, partition_device):
        """Expand the filesystem to fill the partition"""
        try:
            logging.info(f"Expanding filesystem on {partition_device}")
            
            # First, check filesystem type
            cmd_check_fs = ['blkid', '-o', 'value', '-s', 'TYPE', partition_device]
            result = subprocess.run(cmd_check_fs, capture_output=True, text=True)
            fs_type = result.stdout.strip().lower()
            
            logging.info(f"Detected filesystem type: {fs_type}")
            
            if fs_type in ['ext2', 'ext3', 'ext4']:
                # For ext filesystems, use resize2fs
                # First run fsck to ensure filesystem is clean
                logging.info("Running filesystem check...")
                cmd_fsck = ['e2fsck', '-f', '-y', partition_device]
                result = subprocess.run(cmd_fsck, capture_output=True, text=True)
                logging.info(f"fsck output: {result.stdout}")
                
                # Now resize the filesystem
                logging.info("Resizing filesystem...")
                cmd_resize = ['resize2fs', partition_device]
                result = subprocess.run(cmd_resize, capture_output=True, text=True)
                
                if result.returncode == 0:
                    logging.info("Filesystem expanded successfully")
                    return True
                else:
                    logging.error(f"resize2fs failed: {result.stderr}")
                    return False
                    
            elif fs_type == 'btrfs':
                # For btrfs filesystem
                cmd_resize = ['btrfs', 'filesystem', 'resize', 'max', partition_device]
                result = subprocess.run(cmd_resize, capture_output=True, text=True)
                
                if result.returncode == 0:
                    logging.info("Btrfs filesystem expanded successfully")
                    return True
                else:
                    logging.error(f"btrfs resize failed: {result.stderr}")
                    return False
                    
            elif fs_type == 'xfs':
                # For XFS filesystem (requires mounting)
                temp_mount = "/mnt/temp_xfs"
                Path(temp_mount).mkdir(parents=True, exist_ok=True)
                
                try:
                    # Mount the filesystem
                    subprocess.run(['mount', partition_device, temp_mount], check=True)
                    
                    # Expand XFS
                    cmd_resize = ['xfs_growfs', temp_mount]
                    result = subprocess.run(cmd_resize, capture_output=True, text=True)
                    
                    # Unmount
                    subprocess.run(['umount', temp_mount], check=True)
                    
                    if result.returncode == 0:
                        logging.info("XFS filesystem expanded successfully")
                        return True
                    else:
                        logging.error(f"xfs_growfs failed: {result.stderr}")
                        return False
                        
                except Exception as e:
                    logging.error(f"XFS expansion error: {e}")
                    return False
                    
            else:
                logging.warning(f"Unsupported filesystem type: {fs_type}")
                logging.info("Filesystem expansion not performed")
                return True  # Return True to continue without expansion
                
        except Exception as e:
            logging.error(f"Error expanding filesystem: {e}")
            return False
    
    def expand_to_full_size(self, device_path):
        """Main expansion function - expands partition and filesystem"""
        if not EXPAND_FS:
            logging.info("Filesystem expansion disabled in configuration")
            return True
        
        self.expansion_in_progress = True
        logging.info("Starting filesystem expansion process")
        
        # Fast blinking LED during expansion
        expansion_thread = threading.Thread(target=self.fast_blink_led, args=(30,))
        expansion_thread.start()
        
        try:
            # Wait a moment for the device to be ready
            time.sleep(3)
            
            # Unmount device first
            self.unmount_device(device_path)
            
            # Get partition info
            partitions = self.get_partition_info(device_path)
            if not partitions:
                logging.error("No partitions found to expand")
                return False
            
            # Find the partition to expand (usually the last one)
            if EXPAND_PARTITION <= len(partitions):
                target_partition = EXPAND_PARTITION
            else:
                target_partition = len(partitions)  # Last partition
            
            partition_device = f"{device_path}{target_partition}"
            logging.info(f"Target partition for expansion: {partition_device}")
            
            # Step 1: Expand the partition
            if not self.expand_partition(device_path, target_partition):
                logging.error("Failed to expand partition")
                return False
            
            # Wait for kernel to recognize new partition size
            time.sleep(2)
            
            # Step 2: Expand the filesystem
            if not self.expand_filesystem(partition_device):
                logging.error("Failed to expand filesystem")
                return False
            
            # Verify expansion
            cmd_verify = ['df', '-h', partition_device]
            result = subprocess.run(cmd_verify, capture_output=True, text=True)
            logging.info(f"Final filesystem size:\n{result.stdout}")
            
            self.expansion_in_progress = False
            self.stop_led.set()
            expansion_thread.join(timeout=1)
            
            logging.info("Filesystem expansion completed successfully")
            return True
            
        except Exception as e:
            logging.error(f"Expansion process error: {e}")
            self.expansion_in_progress = False
            self.stop_led.set()
            return False
    
    def write_image_to_usb(self, device_path):
        """Write image to USB device using dd"""
        logging.info(f"Starting image write to {device_path}")
        
        # Start breathing LED effect
        self.stop_led.clear()
        self.led_thread = threading.Thread(target=self.breathing_led)
        self.led_thread.start()
        
        try:
            # Verify image exists
            if not os.path.exists(IMAGE_PATH):
                logging.error(f"Image file not found: {IMAGE_PATH}")
                return False
            
            # Unmount device before writing
            self.unmount_device(device_path)
            
            # Write image using dd with progress
            logging.info(f"Writing image {IMAGE_PATH} to {device_path}")
            
            # Use pv for progress bar if available, otherwise use dd
            try:
                # Try to use pv for better progress display
                cmd = [
                    'pv', IMAGE_PATH,
                    '|', 'dd',
                    f'of={device_path}',
                    'bs=4M',
                    'conv=fsync'
                ]
                cmd_str = ' '.join(cmd)
                process = subprocess.Popen(
                    cmd_str,
                    shell=True,
                    executable='/bin/bash',
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
            except:
                # Fall back to dd with status
                cmd = [
                    'dd',
                    f'if={IMAGE_PATH}',
                    f'of={device_path}',
                    'bs=4M',
                    'status=progress',
                    'conv=fsync'
                ]
                logging.info(f"Executing: {' '.join(cmd)}")
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
            
            # Monitor progress
            for line in process.stdout:
                if line.strip():
                    logging.info(f"Write: {line.strip()}")
            
            process.wait()
            
            if process.returncode == 0:
                logging.info("Image write completed successfully")
                
                # Sync and verify
                subprocess.run(['sync'], check=True)
                logging.info("Data synchronized to disk")
                
                # Stop breathing LED
                self.stop_led.set()
                if self.led_thread:
                    self.led_thread.join(timeout=2)
                
                # Expand filesystem if enabled
                if EXPAND_FS:
                    # Solid yellow LED (50%) during expansion
                    self.solid_led(50)
                    
                    # Expand filesystem
                    if self.expand_to_full_size(device_path):
                        logging.info("Filesystem expansion successful")
                        # Green solid LED for success
                        self.solid_led(100)
                        
                        # Flash LED 3 times to indicate completion
                        for _ in range(3):
                            self.solid_led(0)
                            time.sleep(0.3)
                            self.solid_led(100)
                            time.sleep(0.3)
                    else:
                        logging.error("Filesystem expansion failed")
                        # Red blinking for expansion failure
                        for _ in range(5):
                            self.solid_led(100)
                            time.sleep(0.2)
                            self.solid_led(0)
                            time.sleep(0.2)
                        self.solid_led(100)  # Keep red on
                else:
                    # Set LED to solid ON (no expansion)
                    self.solid_led(100)
                    
                    # Flash LED 3 times to indicate completion
                    for _ in range(3):
                        self.solid_led(0)
                        time.sleep(0.3)
                        self.solid_led(100)
                        time.sleep(0.3)
                
                return True
            else:
                logging.error(f"Image write failed with code: {process.returncode}")
                # Set LED to error pattern (fast blinking)
                self.stop_led.set()
                for _ in range(10):
                    self.solid_led(100)
                    time.sleep(0.1)
                    self.solid_led(0)
                    time.sleep(0.1)
                return False
                
        except Exception as e:
            logging.error(f"Error during image write: {e}")
            self.stop_led.set()
            return False
    
    def handle_usb_insertion(self):
        """Main handler for USB insertion events"""
        if self.is_writing:
            logging.warning("Already writing to a USB device")
            return
        
        self.is_writing = True
        logging.info("USB device detected, searching for device...")
        
        # Find the USB device
        device_path, size = self.find_usb_device()
        
        if device_path:
            logging.info(f"Found USB device: {device_path} (Size: {size})")
            
            # Display warning about data loss
            logging.warning(f"WARNING: All data on {device_path} will be destroyed!")
            logging.warning("Image will be written and filesystem expanded automatically.")
            
            # Optional: Add delay for safety
            time.sleep(2)
            
            # Write the image and expand filesystem
            success = self.write_image_to_usb(device_path)
            
            if success:
                logging.info(f"Successfully processed {device_path}")
                # Keep LED solid for 10 seconds after completion
                time.sleep(10)
                self.solid_led(0)  # Turn off LED
            else:
                logging.error(f"Failed to process {device_path}")
                time.sleep(5)
                self.solid_led(0)  # Turn off LED
        else:
            logging.warning("No suitable USB storage device found")
            # Blink LED twice to indicate no device found
            for _ in range(2):
                self.solid_led(100)
                time.sleep(0.2)
                self.solid_led(0)
                time.sleep(0.2)
        
        self.is_writing = False
    
    def udev_monitor(self):
        """Monitor for USB insertion events using udev"""
        context = Context()
        monitor = Monitor.from_netlink(context)
        monitor.filter_by(subsystem='block', device_type='disk')
        
        observer = MonitorObserver(
            monitor,
            callback=self.udev_callback,
            name='usb-monitor'
        )
        
        logging.info("Starting udev monitor for USB devices...")
        observer.start()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            observer.join()
        finally:
            self.cleanup()
    
    def udev_callback(self, action, device):
        """Callback for udev events"""
        if action == 'add':
            # Check if it's a USB device
            if 'ID_BUS' in device and device['ID_BUS'] == 'usb':
                logging.info(f"USB storage device added: {device.device_node}")
                # Start writing in a separate thread to avoid blocking
                thread = threading.Thread(target=self.handle_usb_insertion)
                thread.start()
    
    def cleanup(self):
        """Clean up resources"""
        logging.info("Cleaning up...")
        self.stop_led.set()
        if self.led_thread:
            self.led_thread.join(timeout=1)
        self.pwm.stop()
        GPIO.cleanup()
        logging.info("Cleanup completed")

def main():
    # Check for root privileges
    if os.geteuid() != 0:
        print("This script must be run as root (use sudo)")
        sys.exit(1)
    
    # Check if image exists
    if not os.path.exists(IMAGE_PATH):
        print(f"Error: Image file not found at {IMAGE_PATH}")
        print("Please update IMAGE_PATH in the script")
        sys.exit(1)
    
    # Check for required tools
    required_tools = ['parted', 'fdisk', 'e2fsck', 'resize2fs']
    missing_tools = []
    
    for tool in required_tools:
        result = subprocess.run(['which', tool], capture_output=True)
        if result.returncode != 0:
            missing_tools.append(tool)
    
    if missing_tools:
        print(f"Missing required tools: {', '.join(missing_tools)}")
        print("Install with: sudo apt-get install parted fdisk e2fsprogs")
        sys.exit(1)
    
    copier = USBCopier()
    
    try:
        # Optional: Immediate check for already connected USB
        print("Checking for already connected USB devices...")
        copier.handle_usb_insertion()
        
        # Start monitoring for new USB devices
        copier.udev_monitor()
        
    except KeyboardInterrupt:
        logging.info("Script interrupted by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        copier.cleanup()

if __name__ == "__main__":
    main()