// Copy to secrets.h (git-ignored) and fill in. Never commit real values.
#pragma once
#define WIFI_SSID "your-ssid"
#define WIFI_PASS "your-password"
#define SERVER_BASE "http://192.168.1.50:8000"   // laptop running the backend, same Wi-Fi
#define DEVICE_ID "ESP32-001"                      // register it in Dashboard > Devices first
#define DEVICE_KEY "dev-key-change-me"             // must match MEDGATE_DEVICE_KEY on the server
#define CAREGIVER_PHONE "+910000000000"            // used only for offline fault SMS
