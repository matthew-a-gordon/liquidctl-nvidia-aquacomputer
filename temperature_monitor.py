#!/usr/bin/env python3
"""
Temperature Monitoring and Fan/Pump Control System
Monitors CPU, GPU, and coolant temperatures and adjusts fan/pump speeds accordingly.
"""

import time
import json
import logging
import signal
import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import subprocess
import psutil
from py3nvml.py3nvml import nvmlInit, nvmlShutdown, nvmlDeviceGetHandleByIndex, nvmlDeviceGetTemperature, NVML_TEMPERATURE_GPU
import liquidctl
import liquidctl.cli

class TemperatureMonitor:
    def __init__(self, config_path: str = "/etc/liquidctl-monitor/config.json"):
        self.config_path = config_path
        self.config = self.load_config()
        self.running = True
        self.sensor_history = {
            'cpu': [],
            'gpu': [],
            'coolant': [],
            'motherboard': []
        }
        self.nvidia_handle = None
        self.quadro_device = None
        self.d5_device = None
        
        # Setup logging
        self.setup_logging()
        
        # Initialize hardware
        self.init_nvidia()
        self.init_liquidctl()
        
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
    
    def load_config(self) -> Dict:
        """Load configuration from JSON file"""
        default_config = {
            "monitoring": {
                "interval": 2.0,
                "history_size": 10,
                "smoothing_factor": 0.2  # Lower = more smoothing, especially important for CPU/GPU
            },
            "fan_curve": {
                "radiator_profile": [20, 20, 30, 40, 35, 60, 40, 80, 45, 100],  # Coolant temp based
                "motherboard_profile": [30, 30, 40, 50, 50, 70, 60, 85, 70, 100]  # Network controller temp based
            },
            "pump_curve": {
                "profile": [30, 5, 40, 25, 50, 60, 60, 85, 70, 100]  # Max(CPU, GPU) temp based
            },
            "hardware": {
                "quadro_device": "auto",
                "d5_device": "auto"
            },
            "temperature_limits": {
                "cpu_max": 95.0,
                "gpu_max": 90.0,
                "coolant_max": 50.0,
                "motherboard_max": 80.0
            }
        }
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    config = json.load(f)
                # Merge with defaults for missing keys
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
            except Exception as e:
                print(f"Error loading config: {e}, using defaults")
                return default_config
        else:
            # Create default config file
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(default_config, f, indent=4)
            return default_config
    
    def setup_logging(self):
        """Setup logging configuration"""
        log_dir = Path("/var/log/liquidctl-monitor")
        log_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / "monitor.log"),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def init_nvidia(self):
        """Initialize NVIDIA GPU monitoring"""
        try:
            nvmlInit()
            self.nvidia_handle = nvmlDeviceGetHandleByIndex(0)
            self.logger.info("NVIDIA GPU monitoring initialized")
        except Exception as e:
            self.logger.error(f"Failed to initialize NVIDIA monitoring: {e}")
            self.nvidia_handle = None
    
    def init_liquidctl(self):
        """Initialize liquidctl devices (Quadro and D5 Next)"""
        try:
            from liquidctl.driver.aquacomputer import Aquacomputer
            
            self.quadro_device = None
            self.d5_device = None
            
            # Find supported devices
            devices = Aquacomputer.find_supported_devices()
            if not devices:
                self.logger.error("No Aquacomputer devices found")
                return
            
            for device in devices:
                try:
                    device.connect()
                    
                    # Check if it's a Quadro
                    if "quadro" in device.description.lower():
                        self.quadro_device = device
                        self.logger.info(f"Quadro device found: {device.description}")
                    
                    # Check if it's a D5 Next
                    elif "d5" in device.description.lower() or "next" in device.description.lower():
                        self.d5_device = device
                        self.logger.info(f"D5 Next device found: {device.description}")
                    else:
                        device.disconnect()
                        
                except Exception as e:
                    self.logger.warning(f"Error connecting to device {device.description}: {e}")
                    try:
                        device.disconnect()
                    except:
                        pass
            
            if not self.quadro_device and not self.d5_device:
                self.logger.error("No supported devices found (Quadro or D5 Next)")
                return
                
        except Exception as e:
            self.logger.error(f"Failed to initialize liquidctl devices: {e}")
            self.quadro_device = None
            self.d5_device = None
    
    def get_cpu_temperature(self) -> Optional[float]:
        """Get CPU temperature from thermal sensors"""
        try:
            # Read Tccd die temperatures directly from hwmon (k10temp driver)
            # Tccd values are actual die temps; Tctl includes an AMD offset and is excluded
            tccd_temps = []
            for hwmon_dir in Path("/sys/class/hwmon").glob("hwmon*"):
                try:
                    if (hwmon_dir / "name").read_text().strip() != "k10temp":
                        continue
                    for label_file in hwmon_dir.glob("temp*_label"):
                        if label_file.read_text().strip().startswith("Tccd"):
                            input_file = label_file.with_name(
                                label_file.name.replace("_label", "_input")
                            )
                            temp = float(input_file.read_text().strip()) / 1000.0
                            if 20 <= temp <= 100:
                                tccd_temps.append(temp)
                except OSError:
                    continue
            if tccd_temps:
                return max(tccd_temps)
            
            # Fallback to thermal zones
            thermal_zones = []
            for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
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
                return max(thermal_zones)  # Return highest CPU temp
            else:
                # Fallback to psutil
                temps = psutil.sensors_temperatures()
                if 'coretemp' in temps:
                    return max([temp.current for temp in temps['coretemp']])
                return None
        except Exception as e:
            self.logger.error(f"Error getting CPU temperature: {e}")
            return None
    
    def get_gpu_temperature(self) -> Optional[float]:
        """Get GPU temperature from NVIDIA"""
        if not self.nvidia_handle:
            return None
        
        try:
            temp = nvmlDeviceGetTemperature(self.nvidia_handle, NVML_TEMPERATURE_GPU)
            return float(temp)
        except Exception as e:
            self.logger.error(f"Error getting GPU temperature: {e}")
            return None
    
    def get_coolant_temperature(self) -> Optional[float]:
        """Get coolant temperature from D5 Next device"""
        if not self.d5_device:
            return None
        
        try:
            status = self.d5_device.get_status()
            for key, value, unit in status:
                if "temperature" in key.lower() and unit == "°C":
                    return float(value)
            return None
        except Exception as e:
            self.logger.error(f"Error getting coolant temperature: {e}")
            return None
    
    def get_motherboard_temperature(self) -> Optional[float]:
        """Get motherboard/chipset temperature from sensors"""
        try:
            # Try to get temperature from sensors command
            result = subprocess.run(['sensors', '-A'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                temperatures = []
                
                for line in lines:
                    # Look for network card temperatures (PHY/MAC) or WiFi controller
                    if any(keyword in line for keyword in ['PHY Temperature:', 'MAC Temperature:', 'temp1:']):
                        # Extract temperature from line like "PHY Temperature:  +57.8°C"
                        if '°C' in line:
                            parts = line.split('°C')[0].split()
                            for part in reversed(parts):
                                try:
                                    temp = float(part.replace('+', '').replace('*', ''))
                                    if 20 <= temp <= 100:  # Reasonable temperature range
                                        temperatures.append(temp)
                                        break
                                except ValueError:
                                    continue
                
                if temperatures:
                    return max(temperatures)  # Return highest temperature as motherboard temp
            
            # Fallback to thermal zones
            for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
                try:
                    with open(zone / "type") as f:
                        zone_type = f.read().strip()
                    if any(keyword in zone_type.lower() for keyword in ['chipset', 'motherboard', 'system', 'pch']):
                        with open(zone / "temp") as f:
                            temp = float(f.read().strip()) / 1000.0
                            return temp
                except:
                    continue
            
            return None
        except Exception as e:
            self.logger.error(f"Error getting motherboard temperature: {e}")
            return None
    
    def smooth_temperature(self, sensor_type: str, new_temp: float) -> float:
        """Apply exponential smoothing to temperature readings"""
        history = self.sensor_history[sensor_type]
        smoothing_factor = self.config["monitoring"]["smoothing_factor"]
        
        if not history:
            history.append(new_temp)
            return new_temp
        
        # Exponential smoothing: smoothed = α * new + (1-α) * previous
        smoothed = smoothing_factor * new_temp + (1 - smoothing_factor) * history[-1]
        
        # Apply additional smoothing for highly variable sensors (CPU/GPU)
        if sensor_type in ['cpu', 'gpu']:
            # Use a smaller smoothing factor for these sensors
            cpu_gpu_smoothing = smoothing_factor * 0.5  # Even more smoothing
            smoothed = cpu_gpu_smoothing * new_temp + (1 - cpu_gpu_smoothing) * history[-1]
        
        # Keep history size limited
        max_history = self.config["monitoring"]["history_size"]
        if len(history) >= max_history:
            history.pop(0)
        
        history.append(smoothed)
        return smoothed
    
    
    def interpolate_curve(self, temperature: float, profile: list) -> int:
        """Interpolate fan/pump speed from temperature curve profile"""
        # Profile format: [temp1, duty1, temp2, duty2, ...]
        if len(profile) < 4 or len(profile) % 2 != 0:
            self.logger.error(f"Invalid profile format: {profile}")
            return 50  # Safe fallback
        
        # Convert to pairs: [(temp1, duty1), (temp2, duty2), ...]
        points = [(profile[i], profile[i+1]) for i in range(0, len(profile), 2)]
        points.sort(key=lambda x: x[0])  # Sort by temperature
        
        # If temperature is below the lowest point, return minimum duty
        if temperature <= points[0][0]:
            return int(points[0][1])
        
        # If temperature is above the highest point, return maximum duty
        if temperature >= points[-1][0]:
            return int(points[-1][1])
        
        # Find the two points to interpolate between
        for i in range(len(points) - 1):
            temp1, duty1 = points[i]
            temp2, duty2 = points[i + 1]
            
            if temp1 <= temperature <= temp2:
                # Linear interpolation
                if temp2 == temp1:  # Avoid division by zero
                    return int(duty1)
                
                ratio = (temperature - temp1) / (temp2 - temp1)
                duty = duty1 + ratio * (duty2 - duty1)
                return int(max(0, min(100, duty)))  # Clamp to 0-100%
        
        return 50  # Fallback
    
    def set_radiator_fan_speed(self, coolant_temp: float):
        """Set radiator fan speeds (fan1+fan2) via Quadro using curve interpolation and direct speed control"""
        if not self.quadro_device:
            return
        
        try:
            # Get profile from configuration and calculate speed
            fan_profile = self.config["fan_curve"]["radiator_profile"]
            fan_speed = self.interpolate_curve(coolant_temp, fan_profile)
            
            # Set both fan1 and fan2 to the same speed for coordinated radiator cooling
            # Use direct_access=True to bypass kernel driver limitations
            # Add small delay between commands to avoid USB communication issues
            self.quadro_device.set_fixed_speed('fan1', fan_speed, direct_access=True)
            time.sleep(0.1)  # Small delay between USB commands
            self.quadro_device.set_fixed_speed('fan2', fan_speed, direct_access=True)
            self.logger.info(f"Set radiator fans (1+2) to {fan_speed}% for {coolant_temp:.1f}°C")
        except Exception as e:
            self.logger.warning(f"Error setting radiator fan speed: {e}")
    
    def set_motherboard_fan_speed(self, motherboard_temp: float):
        """Set motherboard fan speed (fan3) via Quadro using curve interpolation and direct speed control"""
        if not self.quadro_device:
            return
        
        try:
            # Get profile from configuration and calculate speed
            motherboard_profile = self.config["fan_curve"]["motherboard_profile"]
            fan_speed = self.interpolate_curve(motherboard_temp, motherboard_profile)
            
            # Use direct_access=True to bypass kernel driver limitations
            # Add small delay before command to avoid USB communication conflicts
            time.sleep(0.1)  # Small delay to avoid USB conflicts
            self.quadro_device.set_fixed_speed('fan3', fan_speed, direct_access=True)
            self.logger.info(f"Set motherboard fan (3) to {fan_speed}% for {motherboard_temp:.1f}°C")
        except Exception as e:
            self.logger.warning(f"Error setting motherboard fan speed: {e}")
    
    def set_pump_speed(self, cpu_temp: float, gpu_temp: float):
        """Set pump speed via D5 Next using curve interpolation and direct speed control"""
        if not self.d5_device:
            return
        
        try:
            # Get profile from configuration and calculate speed based on max temp
            pump_profile = self.config["pump_curve"]["profile"]
            max_temp = max(cpu_temp, gpu_temp)
            pump_speed = self.interpolate_curve(max_temp, pump_profile)
            
            # Use direct_access=True to bypass kernel driver limitations
            # Add small delay before command to avoid USB communication conflicts
            time.sleep(0.1)  # Small delay to avoid USB conflicts
            self.d5_device.set_fixed_speed('pump', pump_speed, direct_access=True)
            self.logger.info(f"Set pump to {pump_speed}% for max temp {max_temp:.1f}°C (CPU: {cpu_temp:.1f}°C, GPU: {gpu_temp:.1f}°C)")
        except Exception as e:
            self.logger.warning(f"Error setting pump speed: {e}")
    
    def monitor_loop(self):
        """Main monitoring loop"""
        self.logger.info("Starting temperature monitoring")
        
        while self.running:
            try:
                # Get raw temperatures
                cpu_temp = self.get_cpu_temperature()
                gpu_temp = self.get_gpu_temperature()
                coolant_temp = self.get_coolant_temperature()
                motherboard_temp = self.get_motherboard_temperature()
                
                # Apply smoothing
                if cpu_temp is not None:
                    cpu_temp = self.smooth_temperature('cpu', cpu_temp)
                if gpu_temp is not None:
                    gpu_temp = self.smooth_temperature('gpu', gpu_temp)
                if coolant_temp is not None:
                    coolant_temp = self.smooth_temperature('coolant', coolant_temp)
                if motherboard_temp is not None:
                    motherboard_temp = self.smooth_temperature('motherboard', motherboard_temp)
                
                # Log temperatures (handle None values gracefully)
                cpu_str = f"{cpu_temp:.1f}°C" if cpu_temp is not None else "N/A"
                gpu_str = f"{gpu_temp:.1f}°C" if gpu_temp is not None else "N/A"
                coolant_str = f"{coolant_temp:.1f}°C" if coolant_temp is not None else "N/A"
                mb_str = f"{motherboard_temp:.1f}°C" if motherboard_temp is not None else "N/A"
                self.logger.info(f"Temps - CPU: {cpu_str}, GPU: {gpu_str}, Coolant: {coolant_str}, MB: {mb_str}")
                
                # Control radiator fans (fan1+fan2) based on coolant temperature
                if coolant_temp is not None:
                    self.set_radiator_fan_speed(coolant_temp)
                
                # Control motherboard fan (fan3) based on motherboard temperature
                if motherboard_temp is not None:
                    self.set_motherboard_fan_speed(motherboard_temp)
                
                # Control pump based on higher of CPU/GPU temperature
                if cpu_temp is not None and gpu_temp is not None:
                    self.set_pump_speed(cpu_temp, gpu_temp)
                
                # Wait for next reading
                time.sleep(self.config["monitoring"]["interval"])
                
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                time.sleep(5)  # Wait before retrying
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    def cleanup(self):
        """Cleanup resources"""
        if self.nvidia_handle:
            try:
                nvmlShutdown()
            except:
                pass
        
        if self.quadro_device:
            try:
                self.quadro_device.disconnect()
            except:
                pass
        
        if self.d5_device:
            try:
                self.d5_device.disconnect()
            except:
                pass
        
        self.logger.info("Cleanup completed")

def main():
    monitor = TemperatureMonitor()
    
    try:
        monitor.monitor_loop()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.cleanup()

if __name__ == "__main__":
    main()
