# Liquidctl Temperature Monitor

A comprehensive temperature monitoring and fan/pump control system for liquid cooling setups using liquidctl and NVIDIA GPUs.

## Features

- **Multi-sensor monitoring**: CPU, GPU, coolant, and motherboard temperature reading
- **Intelligent control**: 
  - Radiator fans (fan1+fan2) controlled by coolant temperature
  - Motherboard fan (fan3) controlled by motherboard/chipset temperature
  - Pump speed controlled by higher of CPU/GPU temperature
- **Data smoothing**: Exponential smoothing to prevent rapid fan/pump speed changes
- **Configurable curves**: Customizable temperature-to-speed mappings
- **Systemd integration**: Runs as a system service with automatic restart
- **Comprehensive logging**: Detailed logs for monitoring and debugging

## Hardware Requirements

- NVIDIA GPU (for GPU temperature monitoring)
- Aquacomputer Quadro (for fan control: fan1+fan2 radiator, fan3 motherboard)
- Aquacomputer D5 Next (for pump control and coolant temperature)
- Linux system with systemd
- lm-sensors package (for motherboard temperature)

## Installation

1. **Install dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

2. **Run the installation script**:
   ```bash
   sudo ./install.sh
   ```

The installation script will:
- Copy files to `/opt/liquidctl-monitor/`
- Create configuration at `/etc/liquidctl-monitor/config.json`
- Install and start the systemd service
- Set up logging in `/var/log/liquidctl-monitor/`

## Configuration

Edit `/etc/liquidctl-monitor/config.json` to customize:

### Monitoring Settings
```json
"monitoring": {
    "interval": 2.0,           // Seconds between readings
    "history_size": 10,        // Number of readings to keep for smoothing
    "smoothing_factor": 0.2    // Smoothing factor (0.0-1.0, lower = more smoothing)
                               // CPU/GPU get additional smoothing (×0.5) to prevent pump micro-adjustments
}
```

### Fan and Pump Curves (Temperature-Duty Profiles)
```json
"fan_curve": {
    "radiator_profile": [20, 20, 30, 40, 35, 60, 40, 80, 45, 100],  // [temp1, duty1, temp2, duty2, ...]
    "motherboard_profile": [30, 30, 40, 50, 50, 70, 60, 85, 70, 100]
},
"pump_curve": {
    "profile": [30, 30, 40, 50, 50, 70, 60, 85, 70, 100]
}
```

**Profile Format**: `[temp1, duty1, temp2, duty2, ...]`
- **Radiator fans**: 20°C→20%, 30°C→40%, 35°C→60%, 40°C→80%, 45°C→100%
- **Motherboard fan**: 30°C→30%, 40°C→50%, 50°C→70%, 60°C→85%, 70°C→100%
- **Pump**: 30°C→30%, 40°C→50%, 50°C→70%, 60°C→85%, 70°C→100%

**Benefits of Profile Approach**:
- **Smooth curves**: Liquidctl interpolates between points for gradual speed changes
- **Hardware-based**: Profiles are stored in device firmware for consistent control
- **No software dependency**: Curves work even if monitoring software stops
- **Better performance**: More responsive than fixed percentage ranges

### Hardware Settings
```json
"hardware": {
    "quadro_device": "auto",    // Auto-detect Quadro fan controller
    "d5_device": "auto"         // Auto-detect D5 Next pump controller
}
```

## Usage

### Service Management
```bash
# Check status
systemctl status liquidctl-monitor.service

# View logs
journalctl -u liquidctl-monitor.service -f

# Stop service
systemctl stop liquidctl-monitor.service

# Start service
systemctl start liquidctl-monitor.service

# Restart service
systemctl restart liquidctl-monitor.service
```

### Manual Testing
```bash
# Run manually for testing
sudo python3 /opt/liquidctl-monitor/temperature_monitor.py
```

## Logging

Logs are written to:
- Systemd journal: `journalctl -u liquidctl-monitor.service`
- File: `/var/log/liquidctl-monitor/monitor.log`

## Troubleshooting

### Common Issues

1. **Permission denied errors**:
   - Ensure the service is running as root
   - Check that the user has access to thermal sensors and GPU

2. **Liquidctl device not found**:
   - Verify your device is supported by liquidctl
   - Check device permissions (may need to add user to appropriate groups)

3. **Temperature readings are None**:
   - Verify thermal sensors are accessible
   - Check NVIDIA driver installation
   - Ensure liquidctl device is properly connected

### Debug Mode

To run with more verbose logging, modify the service file or run manually:
```bash
# Edit service file to add debug logging
sudo systemctl edit liquidctl-monitor.service
```

## Uninstallation

```bash
sudo systemctl stop liquidctl-monitor.service
sudo systemctl disable liquidctl-monitor.service
sudo rm /etc/systemd/system/liquidctl-monitor.service
sudo rm -rf /opt/liquidctl-monitor
sudo rm -rf /etc/liquidctl-monitor
sudo rm -rf /var/log/liquidctl-monitor
sudo systemctl daemon-reload
```

## Safety Notes

- The system includes safety limits to prevent damage
- Monitor logs during initial setup
- Test fan/pump curves in a safe environment
- Ensure proper cooling before running at high loads

## License

This project is provided as-is for educational and personal use.

