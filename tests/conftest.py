import os


os.environ["AUTH_REQUIRED"] = "false"
os.environ.pop("DATABASE_URL", None)
