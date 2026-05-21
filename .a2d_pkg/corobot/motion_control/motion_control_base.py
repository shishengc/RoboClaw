# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

from abc import ABC, abstractmethod

from corobot.protocol.protocol_schemas import STD_MODEL_OUTPUT


class MotionControlBase(ABC):
    @abstractmethod
    def start(self, ts=None):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def execute(self, std_model_output: STD_MODEL_OUTPUT):
        pass
