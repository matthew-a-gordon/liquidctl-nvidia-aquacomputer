#!/usr/bin/env python3
"""
Test script to verify sensor readings and hardware detection
Run this before installing the main service to ensure everything works
"""

import sys
import time
import subprocess
from pynvml import nvmlInit, nvmlShutdown, nvmlDeviceGetHandleByIndex, nvmlDeviceGetTemperature, NVML_TEMPERATURE_GPU
import psutil
import liquidctl
import liquidctl.cli

def test_nvidia():
    """Test NVIDIA GPU temperature reading"""
    print("Testing NVIDIA GPU...")
    try:
        nvmlInit()
        handle = nvmlDeviceGetHandleByIndex(0)
        temp = nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU)
        print(f"✓ GPU Temperature: {temp}°C")
        nvmlShutdown()
        return True
    except Exception as e:
        print(f"✗ GPU Error: {e}")
        return False

def test_cpu():
    """Test CPU temperature reading"""
    print("Testing CPU temperature...")
    try:
        # Try to get temperature from sensors command first (most reliable)
        result = subprocess.run(['sensors', '-A'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.split('\n')
            for line in lines:
                # Look for Tctl (CPU temperature) or Tccd (core temperatures)
                if 'Tctl:' in line or 'Tccd' in line:
                    # Extract temperature from line like "Tctl:         +48.5°C"
                    if '°C' in line:
                        parts = line.split('°C')[0].split()
                        for part in reversed(parts):
                            try:
                                temp = float(part.replace('+', '').replace('*', ''))
                                if 20 <= temp <= 100:  # Reasonable temperature range
                                    print(f"✓ CPU Temperature (sensors): {temp:.1f}°C")
                                    return True
                            except ValueError:
                                continue
        
        # Fallback to thermal zones
        thermal_zones = []
        for zone in __import__('pathlib').Path("/sys/class/thermal").glob("thermal_zone*"):
            try:
                with open(zone / "type") as f:
                    zone_type = f.read().strip()
                if "cpu" in zone_type.lower() or "core" in zone_type.lower():
                    with open(zone / "temp") as f:
                        temp = float(f.read().strip()) / 1000.0
                        thermal_zones.append(temp)
            except:
                continue
        
        if thermal_zones:
            print(f"✓ CPU Temperature (thermal zones): {max(thermal_zones):.1f}°C")
            return True
        else:
            # Fallback to psutil
            temps = psutil.sensors_temperatures()
            if 'coretemp' in temps:
                max_temp = max([temp.current for temp in temps['coretemp']])
                print(f"✓ CPU Temperature (psutil): {max_temp:.1f}°C")
                return True
            else:
                print("✗ No CPU temperature sensors found")
                return False
    except Exception as e:
        print(f"✗ CPU Error: {e}")
        return False

def test_liquidctl():
    """Test liquidctl device detection (Quadro and D5 Next)"""
    print("Testing liquidctl devices...")
    try:
        from liquidctl.driver.aquacomputer import Aquacomputer
        
        # Find supported devices
        devices = Aquacomputer.find_supported_devices()
        if not devices:
            print("✗ No Aquacomputer devices found")
            return False
        
        print(f"✓ Found {len(devices)} Aquacomputer device(s):")
        quadro_found = False
        d5_found = False
        
        for device in devices:
            print(f"  - {device.description}")
            
            # Check if it's a Quadro
            if "quadro" in device.description.lower():
                quadro_found = True
                print(f"    → Quadro fan controller detected")
                try:
                    device.connect()
                    status = device.get_status()
                    print("✓ Quadro status:")
                    for key, value, unit in status:
                        print(f"  {key}: {value} {unit}")
                    device.disconnect()
                except Exception as e:
                    print(f"    Error connecting to Quadro: {e}")
            
            # Check if it's a D5 Next
            elif "d5" in device.description.lower() or "next" in device.description.lower():
                d5_found = True
                print(f"    → D5 Next pump controller detected")
                try:
                    device.connect()
                    status = device.get_status()
                    print("✓ D5 Next status:")
                    for key, value, unit in status:
                        print(f"  {key}: {value} {unit}")
                    device.disconnect()
                except Exception as e:
                    print(f"    Error connecting to D5 Next: {e}")
        
        return quadro_found or d5_found
    except Exception as e:
        print(f"✗ Liquidctl Error: {e}")
        return False

def main():
    print("Liquidctl Temperature Monitor - Hardware Test")
    print("=" * 50)
    
    results = []
    results.append(test_nvidia())
    results.append(test_cpu())
    results.append(test_liquidctl())
    
    print("\n" + "=" * 50)
    print("Test Results:")
    print(f"GPU: {'✓' if results[0] else '✗'}")
    print(f"CPU: {'✓' if results[1] else '✗'}")
    print(f"Liquidctl (Quadro/D5): {'✓' if results[2] else '✗'}")
    
    if all(results):
        print("\n✓ All tests passed! Ready to install the service.")
        print("\nHardware setup:")
        print("  - Quadro: Controls fan1+fan2 (radiator) and fan3 (motherboard)")
        print("  - D5 Next: Controls pump speed and provides coolant temperature")
        return 0
    else:
        print("\n✗ Some tests failed. Please check your hardware setup.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

