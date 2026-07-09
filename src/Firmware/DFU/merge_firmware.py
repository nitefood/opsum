import sys
import os
import subprocess
from SCons.Script import Import

Import("env")

def post_program_action(source, target, env):
    proj_dir = env.subst("$PROJECT_DIR")
    build_dir = env.subst("$BUILD_DIR")
    
    # Define absolute paths for the 3 generated compilation components
    bootloader = os.path.join(build_dir, "bootloader.bin")
    partitions = os.path.join(build_dir, "partitions.bin")
    firmware = os.path.join(build_dir, "firmware.bin")
    
    # Calculate the target directory dynamically relative to PROJECT_DIR
    dest_dir = os.path.abspath(os.path.join(proj_dir, "..", "..", "..", "dist"))
    os.makedirs(dest_dir, exist_ok=True)
    output_file = os.path.join(dest_dir, "firmware-s3-FULL.bin")
    
    # Locate the compiled core OTA firmware inside the dist folder
    micropython_ota = os.path.join(dest_dir, "firmware-s3-OTA.bin")

    # Define flash properties
    flash_size = env.GetProjectOption("board_upload.flash_size", "4MB")
    
    print(f"\n[Custom Post-Build] Merging factory binaries into monolithic image...")
    print(f"[Custom Post-Build] Target Location: {output_file}")
    
    # Get the Python executable PlatformIO is using
    python_exe = env.subst("$PYTHONEXE")
    
    # Locate the actual script file in the PlatformIO core registry
    pio_home = os.path.expanduser("~/.platformio")
    esptool_script = os.path.join(pio_home, "packages", "tool-esptoolpy", "esptool.py")

    if not os.path.exists(esptool_script):
        esptool_script = "esptool.py"

    # Construct esptool args including the core OTA firmware at its designated 0x60000 slot
    cmd = [
        python_exe,
        esptool_script,
        "--chip", "esp32s3",
        "merge_bin",
        "-o", output_file,
        "--flash_mode", "dio",
        "--flash_size", flash_size,
        "--flash_freq", "80m",
        "0x0", bootloader,
        "0x8000", partitions,
        "0x20000", firmware
    ]

    # Dynamically inject the core firmware asset if it exists in the distribution folder
    if os.path.exists(micropython_ota):
        print(f"[Custom Post-Build] Found core OTA firmware asset. Injecting at 0x60000...")
        cmd.extend(["0x60000", micropython_ota])
    else:
        print(f"[Custom Post-Build] WARNING: {micropython_ota} not found. Building without core OTA firmware layer (use flashtool.exe to flash it separately).")

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if result.returncode == 0:
        print(f"[Custom Post-Build] SUCCESS: Created monolithic firmware image {output_file}\n")
    else:
        print(f"[Custom Post-Build] ERROR: Failed to execute flash merge binary step.")
        print(result.stderr)
        print(result.stdout)

env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", post_program_action)