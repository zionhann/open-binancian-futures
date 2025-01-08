from dotenv import load_dotenv
import os

load_dotenv()

KEY = {"client": os.getenv("API_KEY"), "secret": os.getenv("API_SECRET")}
KEY_TEST = {"client": os.getenv("API_KEY_TEST"), "secret": os.getenv("API_SECRET_TEST")}
