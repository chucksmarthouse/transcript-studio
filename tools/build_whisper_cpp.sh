#!/usr/bin/env bash
# Build vendor/whisper.cpp with the Vulkan backend (AMD/Intel/any Vulkan GPU)
# and fetch the default model. Needs: cmake glslc libvulkan-dev mesa-vulkan-drivers.
set -euo pipefail
mkdir -p "$(dirname "$0")/../vendor"
cd "$(dirname "$0")/../vendor"

# Pinned so the build is reproducible; bump deliberately and re-run the tests.
WHISPER_REF=60c0be6ac8fa71b1a2ae2dd938a31a34a508e774
if [ ! -d whisper.cpp ]; then
    git init whisper.cpp
    git -C whisper.cpp fetch --depth 1 https://github.com/ggml-org/whisper.cpp.git "$WHISPER_REF"
    git -C whisper.cpp checkout FETCH_HEAD
fi

# SPIRV-Headers: header-only, installed locally so no extra sudo apt package is
# needed. whisper.cpp's CMake finds the package but never adds its include dir,
# hence the explicit -I below.
if [ ! -d spirv-prefix ]; then
    [ -d SPIRV-Headers ] || git clone --depth 1 https://github.com/KhronosGroup/SPIRV-Headers.git
    cmake -S SPIRV-Headers -B SPIRV-Headers/build -DCMAKE_INSTALL_PREFIX="$PWD/spirv-prefix" -DSPIRV_HEADERS_ENABLE_TESTS=OFF
    cmake --install SPIRV-Headers/build
fi

cd whisper.cpp
cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$PWD/../spirv-prefix" -DCMAKE_CXX_FLAGS="-I$PWD/../spirv-prefix/include"
cmake --build build -j"$(nproc)" --config Release
bash models/download-ggml-model.sh medium.en
