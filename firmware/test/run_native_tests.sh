#!/usr/bin/env bash
# Compile and run the portable firmware core tests on the host (no ESP32 needed).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build
g++ -std=c++17 -Wall -Wextra -Werror -O1 -fsanitize=address,undefined \
    -I lib/medgate_core lib/medgate_core/medgate_core.cpp test/native/test_core.cpp -o .build/test_core
./.build/test_core ../shared/gate_vectors.csv ../shared/lstm_vectors.csv
