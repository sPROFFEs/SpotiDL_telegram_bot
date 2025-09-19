#!/bin/bash

# 🎵 SpotiDL Telegram Bot Setup Script
# Sets up environment, dependencies, and API credentials

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Unicode symbols
CHECK="✅"
CROSS="❌"
INFO="ℹ️"
ROCKET="🚀"
GEAR="⚙️"
KEY="🔑"
MUSIC="🎵"

echo -e "${PURPLE}${MUSIC} SpotiDL Telegram Bot Setup${NC}"
echo -e "${PURPLE}================================${NC}"
echo ""

# Function to print colored output
print_status() {
    echo -e "${GREEN}${CHECK}${NC} $1"
}

print_error() {
    echo -e "${RED}${CROSS}${NC} $1"
}

print_info() {
    echo -e "${BLUE}${INFO}${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠️${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check Python version
check_python() {
    print_info "Checking Python installation..."

    if command_exists python3; then
        PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
        REQUIRED_VERSION="3.8"

        if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" = "$REQUIRED_VERSION" ]; then
            print_status "Python $PYTHON_VERSION found"
            PYTHON_CMD="python3"
        else
            print_error "Python 3.8+ required, found $PYTHON_VERSION"
            exit 1
        fi
    else
        print_error "Python 3 not found. Please install Python 3.8+"
        exit 1
    fi
}

# Setup virtual environment
setup_venv() {
    print_info "Setting up virtual environment..."

    if [ ! -d "venv" ]; then
        $PYTHON_CMD -m venv venv
        print_status "Virtual environment created"
    else
        print_status "Virtual environment already exists"
    fi

    # Activate virtual environment
    source venv/bin/activate
    print_status "Virtual environment activated"

    # Upgrade pip
    pip install --upgrade pip > /dev/null 2>&1
    print_status "pip updated"
}

# Install dependencies
install_dependencies() {
    print_info "Installing Python dependencies..."

    source venv/bin/activate

    # Install requirements
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
        print_status "Python packages installed"
    else
        print_error "requirements.txt not found"
        exit 1
    fi

    # Install and setup playwright
    print_info "Setting up Playwright browser..."
    playwright install chromium > /dev/null 2>&1
    print_status "Playwright browser installed"
}

# Create music directory
setup_directories() {
    print_info "Setting up directories..."

    # Create music directory
    if [ ! -d "/music/local" ]; then
        if [ "$EUID" -eq 0 ]; then
            mkdir -p /music/local
            chown $SUDO_USER:$SUDO_USER /music/local
        else
            sudo mkdir -p /music/local
            sudo chown $USER:$USER /music/local
        fi
        print_status "Music directory created: /music/local"
    else
        print_status "Music directory already exists: /music/local"
    fi

    # Create logs directory
    mkdir -p logs
    print_status "Logs directory ready"
}

# Configure Spotify API credentials
setup_spotify_credentials() {
    echo ""
    echo -e "${CYAN}${KEY} Spotify API Configuration${NC}"
    echo -e "${CYAN}==============================${NC}"
    echo ""
    echo -e "${YELLOW}To get the best results from the Custom Converter, you need Spotify API credentials.${NC}"
    echo -e "${YELLOW}This will allow precise track information extraction for better YouTube matches.${NC}"
    echo ""
    echo -e "${BLUE}📝 How to get Spotify API credentials:${NC}"
    echo -e "   1. Go to: ${CYAN}https://developer.spotify.com/dashboard${NC}"
    echo -e "   2. Log in with your Spotify account"
    echo -e "   3. Click 'Create an App'"
    echo -e "   4. Fill in app name (e.g., 'SpotiDL Bot') and description"
    echo -e "   5. Copy the 'Client ID' and 'Client Secret'"
    echo ""

    read -p "Do you want to configure Spotify API credentials now? (y/n): " setup_spotify

    if [[ $setup_spotify =~ ^[Yy]$ ]]; then
        echo ""
        echo -e "${BLUE}Please enter your Spotify API credentials:${NC}"

        # Get Client ID
        while true; do
            read -p "Spotify Client ID: " client_id
            if [ -n "$client_id" ]; then
                break
            else
                print_warning "Client ID cannot be empty"
            fi
        done

        # Get Client Secret
        while true; do
            read -s -p "Spotify Client Secret: " client_secret
            echo ""
            if [ -n "$client_secret" ]; then
                break
            else
                print_warning "Client Secret cannot be empty"
            fi
        done

        # Create .env file
        echo "# Spotify API Credentials for Custom Converter" > .env
        echo "SPOTIPY_CLIENT_ID=$client_id" >> .env
        echo "SPOTIPY_CLIENT_SECRET=$client_secret" >> .env
        echo "" >> .env
        echo "# Optional: YouTube Music Browser JSON for enhanced searches" >> .env
        echo "# YOUTUBE_MUSIC_BROWSER_JSON=/path/to/browser.json" >> .env

        chmod 600 .env  # Secure permissions
        print_status "Spotify credentials saved to .env file"

        echo ""
        echo -e "${GREEN}${CHECK} Custom Converter will now use Spotify API for precise track information!${NC}"
        echo -e "${BLUE}This will significantly improve YouTube search accuracy.${NC}"

    else
        print_info "Skipping Spotify API setup"
        echo -e "${YELLOW}Note: Custom Converter will use web scraping fallback (less accurate)${NC}"

        # Create minimal .env file
        echo "# Spotify API Credentials (Optional)" > .env
        echo "# Uncomment and fill in to enable precise track extraction:" >> .env
        echo "# SPOTIPY_CLIENT_ID=your_client_id_here" >> .env
        echo "# SPOTIPY_CLIENT_SECRET=your_client_secret_here" >> .env
        echo "" >> .env
        echo "# Optional: YouTube Music Browser JSON" >> .env
        echo "# YOUTUBE_MUSIC_BROWSER_JSON=/path/to/browser.json" >> .env

        print_status ".env template created"
    fi
}

# Configure Telegram Bot Token
setup_telegram_token() {
    echo ""
    echo -e "${CYAN}${ROCKET} Telegram Bot Configuration${NC}"
    echo -e "${CYAN}=============================${NC}"
    echo ""
    echo -e "${BLUE}📝 How to create a Telegram Bot:${NC}"
    echo -e "   1. Message @BotFather on Telegram"
    echo -e "   2. Send: /newbot"
    echo -e "   3. Choose a name and username for your bot"
    echo -e "   4. Copy the bot token from BotFather"
    echo ""

    read -p "Do you want to configure the Telegram bot token now? (y/n): " setup_telegram

    if [[ $setup_telegram =~ ^[Yy]$ ]]; then
        echo ""
        while true; do
            read -s -p "Enter your Telegram Bot Token: " bot_token
            echo ""
            if [[ $bot_token =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
                break
            else
                print_warning "Invalid token format. Should be like: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
            fi
        done

        # Update bot_spot.py with token
        if [ -f "bot_spot.py" ]; then
            sed -i "s/TELEGRAM_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN_HERE'/TELEGRAM_TOKEN = '$bot_token'/" bot_spot.py
            print_status "Bot token configured in bot_spot.py"
        else
            print_error "bot_spot.py not found"
            exit 1
        fi

        # Add to .env file for reference
        echo "" >> .env
        echo "# Telegram Bot Token (configured in bot_spot.py)" >> .env
        echo "TELEGRAM_BOT_TOKEN=$bot_token" >> .env

    else
        print_info "Skipping Telegram bot token setup"
        print_warning "Remember to edit bot_spot.py and replace 'YOUR_TELEGRAM_BOT_TOKEN_HERE' with your token"
    fi
}

# Create startup script
create_startup_script() {
    print_info "Creating startup script..."

    cat > start_bot.sh << 'EOF'
#!/bin/bash

# SpotiDL Bot Startup Script
source venv/bin/activate

# Load environment variables if .env exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

echo "🚀 Starting SpotiDL Telegram Bot..."
echo "📁 Music directory: /music/local/"
echo "📋 Logs: logs/"
echo ""

python3 bot_spot.py
EOF

    chmod +x start_bot.sh
    print_status "Startup script created: start_bot.sh"
}

# Create systemd service (optional)
create_systemd_service() {
    echo ""
    read -p "Do you want to create a systemd service for auto-start? (y/n): " create_service

    if [[ $create_service =~ ^[Yy]$ ]]; then
        CURRENT_DIR=$(pwd)
        CURRENT_USER=$(whoami)

        cat > spotidl-bot.service << EOF
[Unit]
Description=SpotiDL Telegram Bot
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$CURRENT_DIR
ExecStart=$CURRENT_DIR/start_bot.sh
Restart=always
RestartSec=10
Environment=PATH=$CURRENT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

        print_status "Systemd service file created: spotidl-bot.service"
        echo ""
        echo -e "${BLUE}To install the service:${NC}"
        echo -e "  sudo cp spotidl-bot.service /etc/systemd/system/"
        echo -e "  sudo systemctl daemon-reload"
        echo -e "  sudo systemctl enable spotidl-bot"
        echo -e "  sudo systemctl start spotidl-bot"
        echo ""
        echo -e "${BLUE}To check status:${NC}"
        echo -e "  sudo systemctl status spotidl-bot"
    fi
}

# Test installation
test_installation() {
    print_info "Testing installation..."

    source venv/bin/activate

    # Load environment variables
    if [ -f .env ]; then
        export $(cat .env | grep -v '^#' | xargs) 2>/dev/null || true
    fi

    # Test imports
    $PYTHON_CMD -c "
import sys
try:
    from bot_spot import *
    print('✅ Bot imports successfully')
    print(f'📦 Tubetify available: {TUBETIFY_AVAILABLE}')
    print(f'📦 Custom Converter available: {CUSTOM_CONVERTER_AVAILABLE}')
    print(f'📦 Ezconv available: {EZCONV_AVAILABLE}')
    print(f'📦 SpotDL available: {SPOTDL_AVAILABLE}')
except Exception as e:
    print(f'❌ Import error: {e}')
    sys.exit(1)
" 2>/dev/null

    if [ $? -eq 0 ]; then
        print_status "Installation test passed"
    else
        print_error "Installation test failed"
        exit 1
    fi
}

# Main setup function
main() {
    echo -e "${GEAR} Starting setup process..."
    echo ""

    check_python
    setup_venv
    install_dependencies
    setup_directories
    setup_spotify_credentials
    setup_telegram_token
    create_startup_script
    create_systemd_service
    test_installation

    echo ""
    echo -e "${GREEN}🎉 Setup completed successfully!${NC}"
    echo ""
    echo -e "${BLUE}📋 Summary:${NC}"
    echo -e "   ${CHECK} Python environment: venv/"
    echo -e "   ${CHECK} Dependencies: installed"
    echo -e "   ${CHECK} Music directory: /music/local/"
    echo -e "   ${CHECK} Configuration: .env"
    echo -e "   ${CHECK} Startup script: start_bot.sh"
    echo ""
    echo -e "${CYAN}🚀 To start the bot:${NC}"
    echo -e "   ./start_bot.sh"
    echo ""
    echo -e "${CYAN}📖 Download Strategy:${NC}"
    echo -e "   1. 🥇 Tubetify → Ezconv (Primary)"
    echo -e "   2. 🥈 Custom Converter → Ezconv (Self-hosted fallback)"
    echo -e "   3. 🥉 SpotDL (User preference)"
    echo -e "   4. 🔄 SpotDown API (Original method)"
    echo -e "   5. 🆘 SpotDL (Final fallback)"
    echo ""

    if [ -f ".env" ] && grep -q "SPOTIPY_CLIENT_ID=" .env && ! grep -q "^#.*SPOTIPY_CLIENT_ID=" .env; then
        echo -e "${GREEN}${KEY} Spotify API configured - Custom Converter will use precise track info!${NC}"
    else
        echo -e "${YELLOW}${KEY} Spotify API not configured - Custom Converter will use web scraping fallback${NC}"
        echo -e "${BLUE}   You can add credentials later by editing .env file${NC}"
    fi

    echo ""
    echo -e "${PURPLE}Happy downloading! 🎵${NC}"
}

# Run main function
main