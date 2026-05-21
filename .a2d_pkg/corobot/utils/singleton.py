# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
Singleton pattern implementation
"""

# # Example usage
# @singleton
# class MyClass:
#     def __init__(self, value):
#         self.value = value

# # Testing the singleton behavior
# instance1 = MyClass(10)
# instance2 = MyClass(20)

# # Both instances should be the same
# print(instance1 is instance2)  # Output: True
# print(instance1.value)          # Output: 10
# print(instance2.value)          # Output: 10


def singleton(cls):
    instances = {}

    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return get_instance
