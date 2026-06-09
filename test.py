import time
import random

steps = ["INFO", "DEBUG", "WARN"]

while True:
    level = random.choice(steps)
    msg = random.choice([
        "Connected to service",
        "Request sent",
        "Response received",
        "Cache updated",
        "Worker started",
        "Worker finished",
        "Reconnecting...",
        "Timeout detected, retrying",
        "Error: Connection lost",
        "Error: Connection refused",
        "Error: Connection timeout",
        "Error: Connection reset",
        "Error: Connection closed",
        "Error: Connection failed",
        "Error: Connection refused",
        "Error: Connection timeout",
        "Error: Connection reset",
        "Critical: Connection lost",
        "Critical: Connection refused",
        "Critical: Connection timeout",
        "Critical: Connection reset",
        "Critical: Connection closed",
        "Critical: Connection failed",
        "Critical: Connection refused",
        "Critical: Connection timeout",
        "Critical: Connection reset",
        "Critical: Connection closed",
        "Critical: Connection failed",
        "Critical: Connection refused",
        "Critical: Connection timeout",
        "Critical: Connection reset",
        "Critical: Connection closed",
        "Critical: Connection failed",
        "Critical: Connection refused",
        "Critical: Connection timeout",
        "Critical: Connection reset",
        "Critical: Connection closed",
        "Critical: Connection failed",
        "Critical: Connection refused",
        "Critical: Connection timeout",
        "Critical: Connection reset"
    ])
    
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}")
    time.sleep(random.uniform(0.7, 3))