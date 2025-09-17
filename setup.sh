#!/bin/bash

# Spotify Playlist Downloader Bot Setup Script
# This script sets up the bot environment, installs dependencies, and creates a systemd service

set -e  # Exit on any error

echo "🚀 Setting up Spotify Playlist Downloader Bot..."

# Get current directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="spotify-bot"
PYTHON_VERSION="python3"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root for system service creation
if [[ $EUID -eq 0 ]]; then
    print_warning "Running as root. This is only needed for system service creation."
    INSTALL_SYSTEM_SERVICE=true
else
    print_status "Running as regular user. Will create user service instead."
    INSTALL_SYSTEM_SERVICE=false
fi

# Check Python version
print_status "Checking Python version..."
if ! command -v $PYTHON_VERSION &> /dev/null; then
    print_error "Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

PYTHON_VER=$($PYTHON_VERSION --version | cut -d' ' -f2 | cut -d'.' -f1-2)
print_status "Found Python $PYTHON_VER"

# Check if virtual environment already exists
if [ -d "$SCRIPT_DIR/venv" ]; then
    print_warning "Virtual environment already exists. Removing old one..."
    rm -rf "$SCRIPT_DIR/venv"
fi

# Create virtual environment
print_status "Creating virtual environment..."
$PYTHON_VERSION -m venv "$SCRIPT_DIR/venv"

# Activate virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Upgrade pip
print_status "Upgrading pip..."
pip install --upgrade pip

# Install Python requirements
print_status "Installing Python dependencies..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    pip install -r "$SCRIPT_DIR/requirements.txt"
else
    print_error "requirements.txt not found!"
    exit 1
fi

# Install Playwright browsers
print_status "Installing Playwright browsers..."
playwright install chromium

# Verify SpotDL fallback installation
print_status "Verifying SpotDL fallback installation..."
if python -c "import spotdl" 2>/dev/null; then
    print_status "✅ SpotDL fallback is available"
    # Test if spotdl command works
    if command -v spotdl &> /dev/null; then
        SPOTDL_VERSION=$(spotdl --version 2>&1 | head -1 || echo "unknown")
        print_status "SpotDL version: $SPOTDL_VERSION"
    else
        print_warning "SpotDL Python package found but command not available"
    fi
else
    print_error "❌ SpotDL fallback installation failed"
    print_error "This may cause issues with fallback downloads"
fi

# Install system dependencies for yt-dlp fallback
print_status "Checking system dependencies..."
if command -v apt-get &> /dev/null; then
    print_status "Detected Debian/Ubuntu system"
    if [[ $INSTALL_SYSTEM_SERVICE == true ]]; then
        apt-get update
        apt-get install -y ffmpeg yt-dlp
    else
        print_warning "Please install system dependencies manually:"
        print_warning "sudo apt-get update && sudo apt-get install -y ffmpeg yt-dlp"
    fi
elif command -v yum &> /dev/null; then
    print_status "Detected RHEL/CentOS system"
    if [[ $INSTALL_SYSTEM_SERVICE == true ]]; then
        yum install -y epel-release
        yum install -y ffmpeg yt-dlp
    else
        print_warning "Please install system dependencies manually:"
        print_warning "sudo yum install -y epel-release && sudo yum install -y ffmpeg yt-dlp"
    fi
elif command -v pacman &> /dev/null; then
    print_status "Detected Arch Linux system"
    if [[ $INSTALL_SYSTEM_SERVICE == true ]]; then
        pacman -Sy --noconfirm ffmpeg yt-dlp
    else
        print_warning "Please install system dependencies manually:"
        print_warning "sudo pacman -Sy ffmpeg yt-dlp"
    fi
else
    print_warning "Unknown system. Please install ffmpeg and yt-dlp manually."
fi

# Create directories
print_status "Creating directories..."
mkdir -p "$SCRIPT_DIR/logs"
mkdir -p "$SCRIPT_DIR/music/local"

# Check if bot_spot.py exists and has telegram token
print_status "Checking bot configuration..."
if [ ! -f "$SCRIPT_DIR/bot_spot.py" ]; then
    print_error "bot_spot.py not found!"
    exit 1
fi

# Check if telegram token is set
if grep -q "YOUR_BOT_TOKEN_HERE" "$SCRIPT_DIR/bot_spot.py" 2>/dev/null; then
    print_error "Please set your Telegram bot token in bot_spot.py before running setup!"
    print_error "Edit bot_spot.py and replace 'YOUR_BOT_TOKEN_HERE' with your actual bot token."
    exit 1
fi

# Create systemd service file
SERVICE_FILE_CONTENT="[Unit]
Description=Spotify Playlist Downloader Bot
After=network.target
Wants=network.target

[Service]
Type=simple
User=$(whoami)
Group=$(id -gn)
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/run_bot_systemd.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Logging
SyslogIdentifier=spotify-bot

[Install]
WantedBy=multi-user.target"

if [[ $INSTALL_SYSTEM_SERVICE == true ]]; then
    # System service (requires root)
    print_status "Creating system service..."
    echo "$SERVICE_FILE_CONTENT" > "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    print_status "System service created and enabled."
    print_status "Start with: sudo systemctl start $SERVICE_NAME"
    print_status "Check status: sudo systemctl status $SERVICE_NAME"
    print_status "View logs: sudo journalctl -u $SERVICE_NAME -f"
else
    # User service
    print_status "Creating user service..."
    mkdir -p ~/.config/systemd/user
    echo "$SERVICE_FILE_CONTENT" > ~/.config/systemd/user/${SERVICE_NAME}.service
    systemctl --user daemon-reload
    systemctl --user enable $SERVICE_NAME

    # Enable user services to start at boot
    sudo loginctl enable-linger $(whoami) 2>/dev/null || print_warning "Could not enable linger. Service may not start at boot."

    print_status "User service created and enabled."
    print_status "Start with: systemctl --user start $SERVICE_NAME"
    print_status "Check status: systemctl --user status $SERVICE_NAME"
    print_status "View logs: journalctl --user -u $SERVICE_NAME -f"
fi

# Create start script for manual execution
START_SCRIPT="#!/bin/bash
cd \"$SCRIPT_DIR\"
source venv/bin/activate
python bot_spot.py"

echo "$START_SCRIPT" > "$SCRIPT_DIR/start_bot.sh"
chmod +x "$SCRIPT_DIR/start_bot.sh"

# Create systemd wrapper script that properly activates venv
SYSTEMD_WRAPPER="#!/bin/bash
cd \"$SCRIPT_DIR\"
source venv/bin/activate
exec python bot_spot.py"

echo "$SYSTEMD_WRAPPER" > "$SCRIPT_DIR/run_bot_systemd.sh"
chmod +x "$SCRIPT_DIR/run_bot_systemd.sh"

# Create log rotation config (optional)
if [[ $INSTALL_SYSTEM_SERVICE == true ]]; then
    LOGROTATE_CONFIG="/etc/logrotate.d/spotify-bot"
    cat > "$LOGROTATE_CONFIG" << EOF
$SCRIPT_DIR/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 $(whoami) $(id -gn)
}
EOF
    print_status "Log rotation configured."
fi

print_status "✅ Setup completed successfully!"
echo
print_status "📁 Bot directory: $SCRIPT_DIR"
print_status "🐍 Python virtual environment: $SCRIPT_DIR/venv"
print_status "📝 Logs directory: $SCRIPT_DIR/logs"
print_status "🎵 Music directory: $SCRIPT_DIR/music/local"
echo
print_status "🚀 To start the bot manually:"
print_status "   $SCRIPT_DIR/start_bot.sh"
print_status ""
print_status "📁 Created files:"
print_status "   start_bot.sh - Manual start script"
print_status "   run_bot_systemd.sh - Systemd wrapper script"
echo
if [[ $INSTALL_SYSTEM_SERVICE == true ]]; then
    print_status "🔧 To start the system service:"
    print_status "   sudo systemctl start $SERVICE_NAME"
else
    print_status "🔧 To start the user service:"
    print_status "   systemctl --user start $SERVICE_NAME"
fi
echo
print_status "🎯 Next steps:"
print_status "1. Make sure your Telegram bot token is set in bot_spot.py"
print_status "2. If updating from a previous version, reinstall dependencies:"
print_status "   pip install -r requirements.txt --upgrade"
print_status "3. Start the bot using one of the methods above"
print_status "4. Send /start to your bot in Telegram"
echo
print_status "🎵 Fallback Methods Available:"
print_status "• Primary: spotdown.app API (Playwright)"
print_status "• Secondary: SpotDL (YouTube source)"
print_status "• Tertiary: HTTP direct requests (last resort)"
echo
print_status "Happy downloading! 🎵"
