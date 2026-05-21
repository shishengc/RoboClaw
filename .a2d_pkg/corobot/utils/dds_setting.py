# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

import os
import subprocess


# Must have: Environment variables setting for using G01 robot sdk
def dds_env_set():
    print("setting robot dds environment variables...")
    try:
        local_ip = (
            subprocess.check_output(["ip", "-o", "-4", "addr", "list"])
            .decode("utf-8")
            .strip()
            .splitlines()
        )
        local_ip = [line.split()[3].split("/")[0] for line in local_ip if "10.42.0." in line]
        local_ip = local_ip[0] if local_ip else None

        A2D_LOCATOR_IP = "10.42.0.101"
        if local_ip:
            LOCATOR_IP = local_ip
            AORTA_DISCOVERY_URI = f"http://{A2D_LOCATOR_IP}:2379"
            os.environ["LOCATOR_IP"] = LOCATOR_IP
            os.environ["AORTA_DISCOVERY_URI"] = AORTA_DISCOVERY_URI
            os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
        else:
            raise RuntimeError("no ip in 10.42.0.* found, can not communicate with robot")
    except Exception as e:
        print(f"Error setting environment variables: {e}")
