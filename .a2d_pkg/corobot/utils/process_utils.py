# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

import subprocess

"""
process_utils.py

This script is used to check if a port is in use.
"""


# check if port is in use
def check_port_using_ss(port):
    try:
        # check if iproute2 is installed
        result = subprocess.run(["ss", "-h"], capture_output=True, text=True)
        if result.returncode != 0:
            print(
                "iproute2 is not installed, please install it first:\n "
                "sudo apt-get install iproute2"
            )
            return False

        result = subprocess.run(
            f"ss -tulnp | grep {port}",
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(f"Port {port} is in use.")
            print(f"info for {port}:\r\n", result.stdout)
            return True
        return False
    except Exception as e:
        print(f"Error checking port {port}: {e}")
        return False
